"""core/liveusb_tunnel.py - Cloudflare Quick Tunnel manager for LiveUSB standalone mode.

Zero third-party dependencies (Python 3.8+ standard library only).
Modeled after mrkaylabs/easy-mcp's zero-account Cloudflare Quick Tunnel flow:
  - Uses pre-bundled ``/usr/local/bin/cloudflared`` (or PATH / cache dir), with
    automatic download fallback from Cloudflare's official GitHub releases.
  - Runs ``cloudflared tunnel --url http://127.0.0.1:<port>`` and extracts the
    ephemeral ``https://<slug>.trycloudflare.com`` URL (explicitly ignoring
    ``api.trycloudflare.com`` via negative lookahead).
  - Supervises the tunnel in a background thread so LiveUSB sessions survive
    late Wi-Fi/tethering/VPN activation and transient network drops.
  - Atomically writes ``<state_dir>/session.json`` for the Kiosk UI and TUI.

Note: Cloudflare Quick Tunnel URLs change on every run/restart, so the MCP link
must be re-pasted after each restart, and some AI providers' servers may be
blocked by Cloudflare's bot protection.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote

from core import liveusb_bootstrap

logger = logging.getLogger("remote-harness-mcp.liveusb_tunnel")

# Negative lookahead (?!api\.) mirrors mrkaylabs/easy-mcp so cloudflared API
# error messages mentioning https://api.trycloudflare.com are never misread as
# the tunnel's public URL.
TRYCLOUDFLARE_URL_RE = re.compile(
    r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com",
    re.IGNORECASE,
)

CLOUDFLARED_RELEASE_BASE = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
)

DEFAULT_TUNNEL_MODE = "cloudflare"
CLOUDFLARE_BOT_PROTECTION_NOTE = (
    "Cloudflare Quick Tunnel (free, no account needed). "
    "The URL changes on every restart, so re-paste the MCP link after restarting. "
    "Note: some AI providers' servers may be blocked by Cloudflare's bot protection."
)


def parse_trycloudflare_url(text: str) -> Optional[str]:
    """Extract ``https://<slug>.trycloudflare.com`` from *text*, ignoring ``api.trycloudflare.com``."""
    if not text:
        return None
    match = TRYCLOUDFLARE_URL_RE.search(str(text))
    return match.group(0).rstrip("/") if match else None


def cloudflared_asset_name(
    system: Optional[str] = None,
    machine: Optional[str] = None,
) -> str:
    """Return the official Cloudflare release filename for the current OS/arch."""
    sys_name = (system or platform.system() or "linux").strip().lower()
    arch = (machine or platform.machine() or "x86_64").strip().lower()
    is_arm64 = arch in ("aarch64", "arm64")
    is_x86 = arch in ("i386", "i486", "i586", "i686", "x86", "386")

    if sys_name.startswith("win"):
        return "cloudflared-windows-386.exe" if is_x86 else "cloudflared-windows-amd64.exe"
    if sys_name == "darwin":
        return "cloudflared-darwin-arm64.tgz" if is_arm64 else "cloudflared-darwin-amd64.tgz"
    if is_arm64:
        return "cloudflared-linux-arm64"
    if is_x86:
        return "cloudflared-linux-386"
    return "cloudflared-linux-amd64"


def cloudflared_download_url(
    system: Optional[str] = None,
    machine: Optional[str] = None,
) -> str:
    """Return the HTTPS download URL for the official ``cloudflared`` binary."""
    return CLOUDFLARED_RELEASE_BASE + cloudflared_asset_name(system=system, machine=machine)


def default_cloudflared_cache_path(cache_dir: Optional[str] = None) -> str:
    """Return the fallback path where auto-downloaded ``cloudflared`` is cached."""
    base = cache_dir or os.environ.get("MESH_CLOUDFLARED_CACHE_DIR", "").strip()
    if not base:
        base = os.path.join(liveusb_bootstrap.state_dir_path(), "bin")
    exe = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    return os.path.join(base, exe)


def find_cloudflared(
    explicit_bin: Optional[str] = None,
    cache_dir: Optional[str] = None,
) -> Optional[str]:
    """Locate an existing ``cloudflared`` executable without downloading anything."""
    candidates = []
    if explicit_bin and str(explicit_bin).strip():
        candidates.append(str(explicit_bin).strip())
    env_bin = os.environ.get("MESH_CLOUDFLARED_BIN", "").strip()
    if env_bin:
        candidates.append(env_bin)
    if os.name != "nt":
        candidates.extend(["/usr/local/bin/cloudflared", "/usr/bin/cloudflared"])
    candidates.append(default_cloudflared_cache_path(cache_dir))

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    found = shutil.which("cloudflared")
    if found:
        return found
    return None


def ensure_cloudflared(
    explicit_bin: Optional[str] = None,
    cache_dir: Optional[str] = None,
    *,
    auto_download: bool = True,
    timeout_sec: float = 60.0,
) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(binary_path, error_or_None)``, downloading ``cloudflared`` if missing."""
    existing = find_cloudflared(explicit_bin=explicit_bin, cache_dir=cache_dir)
    if existing:
        return existing, None

    if not auto_download:
        return None, "cloudflared binary not found (auto-download disabled)"

    target = default_cloudflared_cache_path(cache_dir)
    asset = cloudflared_asset_name()
    url = CLOUDFLARED_RELEASE_BASE + asset
    parent = os.path.dirname(os.path.abspath(target))
    tmp_path = None

    try:
        os.makedirs(parent, mode=0o755, exist_ok=True)
        ctx = None
        try:
            from core import tls as _tls  # type: ignore

            ctx = _tls.ssl_context()
        except Exception:
            ctx = None

        req = urllib.request.Request(url, headers={"User-Agent": "rhm-liveusb-tunnel/1.0"})
        fd, tmp_path = tempfile.mkstemp(prefix=".cloudflared.", suffix=".tmp", dir=parent)
        os.close(fd)

        kwargs: Dict[str, Any] = {"timeout": max(5.0, float(timeout_sec))}
        if ctx is not None:
            kwargs["context"] = ctx
        with urllib.request.urlopen(req, **kwargs) as resp, open(tmp_path, "wb") as out:
            shutil.copyfileobj(resp, out, length=65536)

        if asset.endswith(".tgz"):
            with tarfile.open(tmp_path, "r:gz") as tf:
                member = next(
                    (m for m in tf.getmembers() if os.path.basename(m.name) == "cloudflared"),
                    None,
                )
                if member is None:
                    raise RuntimeError("cloudflared binary not found in downloaded archive")
                extracted = tf.extractfile(member)
                if extracted is None:
                    raise RuntimeError("failed to extract cloudflared from archive")
                fd2, bin_tmp = tempfile.mkstemp(prefix=".cloudflared-bin.", dir=parent)
                try:
                    with os.fdopen(fd2, "wb") as out2:
                        shutil.copyfileobj(extracted, out2, length=65536)
                    if os.name != "nt":
                        os.chmod(bin_tmp, 0o755)
                    os.replace(bin_tmp, target)
                finally:
                    if os.path.exists(bin_tmp):
                        try:
                            os.unlink(bin_tmp)
                        except OSError:
                            pass
        else:
            if os.name != "nt":
                os.chmod(tmp_path, 0o755)
            os.replace(tmp_path, target)
            tmp_path = None

        return target, None
    except Exception as exc:
        return None, f"cloudflared download failed ({url}): {type(exc).__name__}: {exc}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def build_session_document(
    *,
    public_url: str,
    token: str,
    node_id: Optional[str] = None,
    tunnel_mode: str = DEFAULT_TUNNEL_MODE,
    tunnel_status: str = "connected",
    tunnel_error: str = "",
) -> Dict[str, Any]:
    """Build the normalized ``session.json`` payload for Kiosk UI and TUI."""
    base = (public_url or "").strip().rstrip("/")
    tok = (token or "").strip()
    enc_tok = quote(tok, safe="") if tok else ""
    host_id = (node_id or "").strip()
    if not host_id:
        try:
            host_id = socket.gethostname().strip() or "liveusb"
        except Exception:
            host_id = "liveusb"

    mcp_url = f"{base}/mcp/{enc_tok}" if (base and enc_tok) else ""
    sse_url = f"{base}/sse?token={enc_tok}" if (base and enc_tok) else ""
    connect_url = f"{base}/connect?token={enc_tok}" if (base and enc_tok) else ""
    ready = bool(base and tok and tunnel_status == "connected")

    return {
        "node_id": host_id,
        "link_key": tok,
        "token": tok,
        "ready": ready,
        "session_code": "cloudflare" if tunnel_mode == "cloudflare" else "",
        "tunnel_mode": tunnel_mode,
        "tunnel_status": tunnel_status,
        "tunnel_error": tunnel_error or "",
        "public_url": base,
        "connect_url": connect_url,
        "mcp_url": mcp_url,
        "sse_url": sse_url,
        "expires_at": "",
        "gateway": base,
        "registered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def write_liveusb_session(
    state_dir: Optional[str] = None,
    *,
    public_url: str,
    token: str,
    node_id: Optional[str] = None,
    tunnel_mode: str = DEFAULT_TUNNEL_MODE,
    tunnel_status: str = "connected",
    tunnel_error: str = "",
) -> Dict[str, Any]:
    """Atomically write ``<state_dir>/session.json`` (mode 0600) and return the doc."""
    target_dir = liveusb_bootstrap.state_dir_path(state_dir)
    doc = build_session_document(
        public_url=public_url,
        token=token,
        node_id=node_id,
        tunnel_mode=tunnel_mode,
        tunnel_status=tunnel_status,
        tunnel_error=tunnel_error,
    )
    session_path = os.path.join(target_dir, "session.json")
    try:
        liveusb_bootstrap._atomic_write_text(
            session_path,
            json.dumps(doc, indent=2, sort_keys=False) + "\n",
            mode=0o600,
        )
    except Exception as exc:
        logger.warning("Failed to write LiveUSB session.json at %s: %s", session_path, exc)
    return doc


class CloudflareQuickTunnelSupervisor:
    """Background supervisor for a Cloudflare Quick Tunnel pointing to localhost.

    Automatically handles:
    - Waiting for ``cloudflared`` availability (pre-bundled or auto-downloaded);
    - Extracting the ephemeral ``https://<slug>.trycloudflare.com`` URL;
    - Notifying ``on_public_url`` so the HTTP server updates allowed hosts;
    - Writing ``session.json`` on every state transition;
    - Restarting ``cloudflared`` with backoff if the network drops or changes.
    """

    def __init__(
        self,
        *,
        port: int,
        token: str,
        host: str = "127.0.0.1",
        state_dir: Optional[str] = None,
        node_id: Optional[str] = None,
        cloudflared_bin: Optional[str] = None,
        on_public_url: Optional[Callable[[str], None]] = None,
        auto_download: bool = True,
        restart_delay: float = 3.0,
        max_restart_delay: float = 15.0,
    ) -> None:
        self.port = int(port)
        self.token = str(token)
        self.host = "127.0.0.1" if host in ("0.0.0.0", "") else str(host)
        self.state_dir = liveusb_bootstrap.state_dir_path(state_dir)
        self.node_id = node_id
        self.cloudflared_bin = cloudflared_bin
        self.on_public_url = on_public_url
        self.auto_download = bool(auto_download)
        self.restart_delay = max(0.5, float(restart_delay))
        self.max_restart_delay = max(self.restart_delay, float(max_restart_delay))

        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self.public_url: str = ""
        self.last_error: str = ""

    def start(self) -> None:
        """Start the background supervisor thread."""
        write_liveusb_session(
            self.state_dir,
            public_url="",
            token=self.token,
            node_id=self.node_id,
            tunnel_mode="cloudflare",
            tunnel_status="starting",
            tunnel_error="",
        )
        self._thread = threading.Thread(
            target=self._run_loop,
            name="rhm-liveusb-cloudflared",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the supervisor and terminate any running ``cloudflared`` process."""
        self._stop_event.set()
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        write_liveusb_session(
            self.state_dir,
            public_url="",
            token=self.token,
            node_id=self.node_id,
            tunnel_mode="cloudflare",
            tunnel_status="offline",
            tunnel_error="",
        )

    def _run_loop(self) -> None:
        backoff = self.restart_delay
        target_url = f"http://{self.host}:{self.port}"

        while not self._stop_event.is_set():
            binary, err = ensure_cloudflared(
                explicit_bin=self.cloudflared_bin,
                cache_dir=os.path.join(self.state_dir, "bin"),
                auto_download=self.auto_download,
            )
            if not binary:
                self.last_error = err or "cloudflared unavailable"
                logger.warning("Cloudflare tunnel waiting: %s", self.last_error)
                write_liveusb_session(
                    self.state_dir,
                    public_url="",
                    token=self.token,
                    node_id=self.node_id,
                    tunnel_mode="cloudflare",
                    tunnel_status="error",
                    tunnel_error=self.last_error,
                )
                self._stop_event.wait(backoff)
                backoff = min(self.max_restart_delay, backoff * 1.5)
                continue

            self.public_url = ""
            try:
                proc = subprocess.Popen(
                    [binary, "tunnel", "--no-autoupdate", "--url", target_url],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
            except Exception as exc:
                self.last_error = f"Failed to launch cloudflared: {exc}"
                logger.warning("%s", self.last_error)
                write_liveusb_session(
                    self.state_dir,
                    public_url="",
                    token=self.token,
                    node_id=self.node_id,
                    tunnel_mode="cloudflare",
                    tunnel_status="error",
                    tunnel_error=self.last_error,
                )
                self._stop_event.wait(backoff)
                backoff = min(self.max_restart_delay, backoff * 1.5)
                continue

            with self._lock:
                self._proc = proc

            found = False
            try:
                stream = proc.stdout
                if stream is not None:
                    for line in stream:
                        if self._stop_event.is_set():
                            break
                        url = parse_trycloudflare_url(line)
                        if url and not found:
                            found = True
                            backoff = self.restart_delay
                            self.public_url = url
                            self.last_error = ""
                            if self.on_public_url is not None:
                                try:
                                    self.on_public_url(url)
                                except Exception as cb_exc:
                                    logger.warning("on_public_url callback error: %s", cb_exc)
                            doc = write_liveusb_session(
                                self.state_dir,
                                public_url=url,
                                token=self.token,
                                node_id=self.node_id,
                                tunnel_mode="cloudflare",
                                tunnel_status="connected",
                                tunnel_error="",
                            )
                            logger.info(
                                "Cloudflare Quick Tunnel ready: %s (mode=cloudflare, state=%s)",
                                url,
                                self.state_dir,
                            )
                proc.wait(timeout=5.0)
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                with self._lock:
                    if self._proc is proc:
                        self._proc = None

            if self._stop_event.is_set():
                break

            rc = proc.returncode
            self.last_error = (
                f"cloudflared exited with code {rc}"
                if found
                else f"Cloudflare Quick Tunnel failed to connect (exit {rc}; check internet route)"
            )
            logger.warning("%s — restarting in %.1fs", self.last_error, backoff)
            write_liveusb_session(
                self.state_dir,
                public_url="",
                token=self.token,
                node_id=self.node_id,
                tunnel_mode="cloudflare",
                tunnel_status="reconnecting",
                tunnel_error=self.last_error,
            )
            self._stop_event.wait(backoff)
            backoff = min(self.max_restart_delay, backoff * 1.5)
