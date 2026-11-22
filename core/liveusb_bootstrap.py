"""core/liveusb_bootstrap.py - Ephemeral LiveUSB registration and bootstrap helpers.

Zero third-party dependencies (Python 3.8+ standard library only).
All public functions are defensive and never raise unhandled exceptions so the
LiveUSB boot sequence, Kiosk UI, and agent lifecycle degrade cleanly even when
the network, gateway, or local filesystem is unavailable.
"""

from datetime import datetime, timezone
import json
import os
import platform
import socket
import ssl
import tempfile
import time
from typing import Any, Dict, Optional
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_STATE_DIR = "/run/rhm-live"
_USER_AGENT = "rhm-liveusb-bootstrap/1.0"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_DEFAULT_URLOPEN = urllib.request.urlopen


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block HTTP 3xx redirects so ephemeral tokens are never forwarded off-origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            f"HTTP redirect ({code}) blocked",
            headers,
            fp,
        )


def _build_secure_opener() -> urllib.request.OpenerDirector:
    """Create a urllib opener enforcing TLS 1.2+ and blocking HTTP redirects."""
    ctx = ssl.create_default_context()
    if hasattr(ssl, "TLSVersion"):
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return urllib.request.build_opener(
        _NoRedirectHandler(),
        urllib.request.HTTPSHandler(context=ctx),
    )


def _secure_urlopen(req: urllib.request.Request, timeout: float):
    """Open *req* with TLS 1.2+ and no-redirect opener (respects monkeypatched urlopen in tests)."""
    if urllib.request.urlopen is not _DEFAULT_URLOPEN:
        return urllib.request.urlopen(req, timeout=timeout)
    return _build_secure_opener().open(req, timeout=timeout)


def state_dir_path(state_dir: Optional[str] = None) -> str:
    """Resolve the LiveUSB runtime state directory."""
    try:
        if state_dir and str(state_dir).strip():
            return str(state_dir).strip()
        env_dir = os.environ.get("MESH_LIVE_STATE_DIR", "").strip()
        if env_dir:
            return env_dir
    except Exception:
        pass
    return DEFAULT_STATE_DIR


def _normalize_gateway_base(gateway_url: Optional[str]) -> str:
    """Normalize a gateway host or URL to an HTTP(S) base URL without trailing slash.

    Rejects plaintext ``http://`` and ``ws://`` for non-loopback hosts.
    """
    raw = str(gateway_url or "").strip().rstrip("/")
    if not raw:
        return ""
    lower = raw.lower()
    if lower.startswith("wss://"):
        raw = "https://" + raw[6:]
    elif lower.startswith("ws://"):
        raw = "http://" + raw[5:]
    elif "://" not in raw:
        raw = "https://" + raw
    parsed = urllib.parse.urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("https", "http") or not parsed.netloc:
        return ""
    hostname = (parsed.hostname or "").lower()
    if scheme == "http" and hostname not in _LOOPBACK_HOSTS:
        return ""
    return f"{scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def _gateway_host(gateway_url: Optional[str]) -> str:
    """Extract host[:port] from a gateway URL or bare hostname."""
    base = _normalize_gateway_base(gateway_url)
    if not base:
        return ""
    return urllib.parse.urlparse(base).netloc


def _clean_env_value(value: Any) -> str:
    """Sanitize a value for single-line KEY=VALUE storage."""
    return str(value or "").replace("\r", "").replace("\n", "").strip()


def _atomic_write_text(path: str, content: str, mode: Optional[int] = None) -> None:
    """Write text to *path* atomically via a same-directory temp file and os.replace."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
        if os.name == "posix":
            try:
                os.chmod(parent, 0o700)
            except OSError:
                pass
    basename = os.path.basename(path)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=f".{basename}.", suffix=".tmp", dir=parent)
        try:
            if mode is not None and os.name == "posix":
                os.fchmod(fd, mode)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                fd = -1
                handle.write(content)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        finally:
            if fd != -1:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if mode is not None and os.name == "posix":
            try:
                os.chmod(tmp_path, mode)
            except OSError:
                pass
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _parse_env_file(path: str) -> Dict[str, str]:
    """Parse KEY=VALUE lines from *path* (tolerates UTF-8 BOM and comments)."""
    values: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key:
                    continue
                val = value.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                values[key] = val
    except Exception:
        return {}
    return values


def export_env_file(path: str, overwrite: bool = True) -> Dict[str, str]:
    """Load KEY=VALUE pairs from *path* (e.g. agent.env) into os.environ.

    Never raises unhandled exceptions; returns the dict of exported entries.
    """
    exported: Dict[str, str] = {}
    try:
        values = _parse_env_file(path)
        for key, value in values.items():
            if overwrite or not os.environ.get(key):
                os.environ[key] = value
                exported[key] = value
    except Exception:
        return exported
    return exported


def wait_for_gateway(
    gateway_url: str,
    timeout_sec: float = 60,
    poll_interval: float = 2.0,
) -> bool:
    """Poll the gateway over HTTPS until reachable or *timeout_sec* elapses.

    Returns True as soon as the gateway responds with any non-5xx HTTP status
    (proving TLS/network reachability), or False on timeout or invalid URL.
    Never raises unhandled exceptions.
    """
    try:
        base = _normalize_gateway_base(gateway_url)
        if not base:
            return False
        timeout = max(0.0, float(timeout_sec))
        interval = max(0.05, float(poll_interval))
        deadline = time.monotonic() + timeout
        probe_url = f"{base}/health"

        while True:
            remaining = deadline - time.monotonic()
            req_timeout = min(5.0, max(1.0, remaining)) if timeout > 0 else 2.0
            req = urllib.request.Request(
                probe_url,
                method="GET",
                headers={"User-Agent": _USER_AGENT},
            )
            try:
                with _secure_urlopen(req, timeout=req_timeout) as resp:
                    status = getattr(resp, "status", None)
                    if status is None:
                        status = resp.getcode()
                    if status is None or int(status) < 500:
                        return True
            except urllib.error.HTTPError as exc:
                code = getattr(exc, "code", 500)
                try:
                    exc.close()
                except Exception:
                    pass
                if code is not None and int(code) < 500:
                    return True
            except Exception:
                pass

            now = time.monotonic()
            if now >= deadline:
                return False
            sleep_for = min(interval, max(0.0, deadline - now))
            if sleep_for > 0:
                time.sleep(sleep_for)
    except Exception:
        return False


def register_ephemeral_node(
    gateway_url: str,
    hostname: Optional[str] = None,
    state_dir: Optional[str] = None,
    timeout_sec: float = 15,
) -> Dict[str, Any]:
    """Register an ephemeral LiveUSB node and write agent.env + session.json.

    Calls ``POST https://<gateway>/v1/liveusb/register``, writes
    ``<state_dir>/agent.env`` (chmod 0600 on POSIX), and atomically writes
    ``<state_dir>/session.json`` for the Kiosk UI. Never raises.
    """
    try:
        base = _normalize_gateway_base(gateway_url)
        if not base:
            return {"ok": False, "error": "gateway_url is empty or invalid"}

        gw_host = _gateway_host(base)
        target_dir = state_dir_path(state_dir)

        host = str(hostname or "").strip()
        if not host:
            try:
                host = socket.gethostname().strip()
            except Exception:
                host = ""
        if not host:
            host = "liveusb"

        payload = json.dumps(
            {"hostname": host, "os": "linux", "arch": platform.machine()}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/v1/liveusb/register",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        with _secure_urlopen(req, timeout=max(1.0, float(timeout_sec))) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")

        data = json.loads(raw_body) if raw_body.strip() else {}
        if not isinstance(data, dict):
            return {"ok": False, "error": "unexpected non-object JSON response from gateway"}

        node_id = _clean_env_value(
            data.get("node_id") or data.get("user") or data.get("mesh_user")
        )
        device_token = _clean_env_value(
            data.get("device_token") or data.get("token") or data.get("mesh_token")
        )
        link_key = _clean_env_value(
            data.get("link_key") or data.get("mesh_link_key")
        )
        session_code = _clean_env_value(data.get("session_code"))
        connect_url = _clean_env_value(data.get("connect_url"))
        sse_url = _clean_env_value(
            data.get("sse_url")
            or (f"{base}/sse?user={node_id}&token={link_key}" if node_id and link_key else "")
        )
        mcp_url = _clean_env_value(
            data.get("mcp_url")
            or (f"{base}/mcp?user={node_id}&token={link_key}" if node_id and link_key else "")
        )
        expires_at = data.get("expires_at")
        if expires_at is None:
            expires_at = ""
        registered_at = str(
            data.get("registered_at")
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )

        if not node_id or not device_token:
            return {
                "ok": False,
                "error": "gateway registration response missing node_id or device_token",
                "response": data,
            }

        env_lines = [
            f"MESH_GATEWAY={gw_host}",
            f"MESH_USER={node_id}",
            f"MESH_TOKEN={device_token}",
            f"MESH_LINK_KEY={link_key}",
            "MESH_SCENARIO=live_usb",
            "MESH_ACCESS_MODE=full",
            "MESH_WORKSPACE=/",
        ]
        env_path = os.path.join(target_dir, "agent.env")
        _atomic_write_text(env_path, "\n".join(env_lines) + "\n", mode=0o600)

        session_doc = {
            "node_id": node_id,
            "link_key": link_key,
            "token": link_key,
            "ready": True,
            "session_code": session_code,
            "connect_url": connect_url,
            "sse_url": sse_url,
            "mcp_url": mcp_url,
            "expires_at": expires_at,
            "gateway": base,
            "registered_at": registered_at,
        }
        session_path = os.path.join(target_dir, "session.json")
        _atomic_write_text(
            session_path,
            json.dumps(session_doc, indent=2, sort_keys=False) + "\n",
            mode=0o600,
        )

        return {
            "ok": True,
            **session_doc,
            "device_token": device_token,
            "gateway_host": gw_host,
            "state_dir": target_dir,
            "env_file": env_path,
            "session_file": session_path,
        }
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        try:
            exc.close()
        except Exception:
            pass
        msg = f"HTTP {exc.code}: {detail}" if detail else f"HTTP {exc.code}: {exc.reason}"
        return {"ok": False, "error": msg}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def revoke_ephemeral_node(
    state_dir: Optional[str] = None,
    timeout_sec: float = 8,
) -> Dict[str, Any]:
    """Revoke the ephemeral LiveUSB node via gateway API and clean up session.json.

    Reads ``agent.env`` and ``session.json`` from *state_dir*, calls
    ``POST https://<gateway>/v1/liveusb/revoke`` with ``device_token``, and
    removes ``session.json``. Never raises unhandled exceptions.
    """
    target_dir = state_dir_path(state_dir)
    env_path = os.path.join(target_dir, "agent.env")
    session_path = os.path.join(target_dir, "session.json")

    env_vals = _parse_env_file(env_path)
    session_vals: Dict[str, Any] = {}
    try:
        if os.path.exists(session_path):
            with open(session_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
                if isinstance(loaded, dict):
                    session_vals = loaded
    except Exception:
        session_vals = {}

    device_token = _clean_env_value(
        env_vals.get("MESH_TOKEN") or os.environ.get("MESH_TOKEN")
    )
    node_id = _clean_env_value(
        session_vals.get("node_id") or env_vals.get("MESH_USER") or os.environ.get("MESH_USER")
    )
    gateway = (
        session_vals.get("gateway")
        or env_vals.get("MESH_GATEWAY")
        or os.environ.get("MESH_GATEWAY")
        or ""
    )
    base = _normalize_gateway_base(gateway)

    revoked = False
    error_msg: Optional[str] = None
    response_data: Any = None

    if not base or not device_token:
        error_msg = "missing gateway or device_token in state_dir"
    else:
        try:
            body_obj: Dict[str, Any] = {"device_token": device_token}
            if node_id:
                body_obj["node_id"] = node_id
            payload = json.dumps(body_obj).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/v1/liveusb/revoke",
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": _USER_AGENT,
                },
            )
            with _secure_urlopen(req, timeout=max(1.0, float(timeout_sec))) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
                if raw_body.strip():
                    try:
                        response_data = json.loads(raw_body)
                    except Exception:
                        response_data = raw_body.strip()
            revoked = True
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            try:
                exc.close()
            except Exception:
                pass
            error_msg = f"HTTP {exc.code}: {detail}" if detail else f"HTTP {exc.code}: {exc.reason}"
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"

    cleaned_session = False
    try:
        if os.path.exists(session_path):
            os.unlink(session_path)
            cleaned_session = True
    except Exception:
        cleaned_session = False

    cleaned_env = False
    try:
        if os.path.exists(env_path):
            os.unlink(env_path)
            cleaned_env = True
    except Exception:
        cleaned_env = False

    result: Dict[str, Any] = {
        "ok": revoked,
        "revoked": revoked,
        "cleaned_session": cleaned_session,
        "cleaned_env": cleaned_env,
        "node_id": node_id,
        "state_dir": target_dir,
    }
    if response_data is not None:
        result["response"] = response_data
    if error_msg:
        result["error"] = error_msg
    return result


def _active_ipv6_uplink_interface(ipv6_route_path: str = "/proc/net/ipv6_route") -> Optional[str]:
    """Return the Linux IPv6 default-route interface from /proc/net/ipv6_route."""
    try:
        if not ipv6_route_path or not os.path.isfile(ipv6_route_path):
            return None
        candidates = []
        with open(ipv6_route_path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        for idx, line in enumerate(lines):
            parts = line.strip().split()
            if len(parts) < 10:
                continue
            dest, prefix_len = parts[0], parts[1]
            if dest != "00000000000000000000000000000000" or prefix_len != "00":
                continue
            iface = parts[9]
            if not iface or iface == "lo":
                continue
            try:
                flags = int(parts[8], 16)
                if (flags & 0x1) == 0:
                    continue
            except ValueError:
                continue
            try:
                metric = int(parts[5], 16)
            except ValueError:
                metric = 0
            candidates.append((metric, idx, iface))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]
    except Exception:
        return None


def active_uplink_interface(
    route_path: str = "/proc/net/route",
    ipv6_route_path: Optional[str] = None,
) -> Optional[str]:
    """Return the Linux default-route network interface name from /proc/net/route.

    Parses ``Destination == 00000000`` entries and prefers the lowest metric
    among active (RTF_UP) routes. Falls back to ``/proc/net/ipv6_route`` when no
    IPv4 default route exists. Returns None on non-Linux systems or when no
    default route exists. Never raises unhandled exceptions.
    """
    try:
        if route_path and os.path.isfile(route_path):
            candidates = []
            with open(route_path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
            for idx, line in enumerate(lines):
                parts = line.strip().split()
                if idx == 0 and parts and parts[0].lower() == "iface":
                    continue
                if len(parts) < 4:
                    continue
                iface, dest = parts[0], parts[1]
                if dest != "00000000":
                    continue
                try:
                    flags = int(parts[3], 16)
                    if (flags & 0x1) == 0:
                        continue
                except ValueError:
                    pass
                metric = 0
                if len(parts) >= 7:
                    try:
                        metric = int(parts[6])
                    except ValueError:
                        metric = 0
                candidates.append((metric, idx, iface))
            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]))
                return candidates[0][2]

        v6_path = ipv6_route_path if ipv6_route_path is not None else (
            "/proc/net/ipv6_route" if route_path == "/proc/net/route" else None
        )
        if v6_path:
            return _active_ipv6_uplink_interface(v6_path)
        return None
    except Exception:
        return None
