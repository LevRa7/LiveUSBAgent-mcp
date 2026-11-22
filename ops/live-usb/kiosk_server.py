#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/live-usb/kiosk_server.py - Localhost-only LiveUSB kiosk HTTP server.

Stdlib-only (Python 3.8+) ThreadingHTTPServer bound strictly to 127.0.0.1:8787.
Serves the Russian kiosk UI from ops/live-usb/kiosk_ui/ and exposes JSON/SVG
endpoints for network status, Wi-Fi management, LiveUSB session state, and QR.
"""

from __future__ import annotations

import argparse
import html
import importlib
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_POST_BYTES = 16384

_STATIC_DIR = Path(__file__).resolve().parent / "kiosk_ui"

# Type alias for injectable command runner (makes unit testing deterministic).
CommandRunner = Callable[[List[str], float], Tuple[int, str, str]]


def default_run_cmd(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
    """Run a subprocess safely without shell=True. Never raises."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out: {' '.join(cmd)}"
    except Exception as exc:
        return 1, "", str(exc)


def split_nmcli_terse_line(line: str) -> List[str]:
    """Split an `nmcli -t` colon-delimited line, unescaping `\\:` and `\\\\`."""
    fields: List[str] = []
    buf: List[str] = []
    i = 0
    length = len(line)
    while i < length:
        ch = line[i]
        if ch == "\\" and i + 1 < length:
            nxt = line[i + 1]
            if nxt in (":", "\\"):
                buf.append(nxt)
                i += 2
                continue
        if ch == ":":
            fields.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    fields.append("".join(buf))
    return fields


def parse_nmcli_wifi_list(output: str) -> List[Dict[str, Any]]:
    """Parse `nmcli -t -f SSID,SIGNAL,SECURITY,IN-USE dev wifi list` output.

    Deduplicates by SSID keeping the strongest signal (and preserving in_use if
    any matching BSSID is active), then returns a list sorted by signal desc.
    """
    best_by_ssid: Dict[str, Dict[str, Any]] = {}
    for raw_line in (output or "").splitlines():
        line = raw_line.strip("\r\n")
        if not line:
            continue
        parts = split_nmcli_terse_line(line)
        if len(parts) < 4:
            continue
        ssid = parts[0].strip()
        if not ssid:
            continue
        try:
            signal = max(0, min(100, int(parts[1].strip() or "0")))
        except ValueError:
            signal = 0
        security = parts[2].strip() or "OPEN"
        if security == "--":
            security = "OPEN"
        in_use = parts[3].strip() == "*"

        existing = best_by_ssid.get(ssid)
        if existing is None:
            best_by_ssid[ssid] = {
                "ssid": ssid,
                "signal": signal,
                "security": security,
                "in_use": in_use,
            }
        else:
            combined_in_use = bool(existing["in_use"] or in_use)
            if signal > existing["signal"]:
                best_by_ssid[ssid] = {
                    "ssid": ssid,
                    "signal": signal,
                    "security": security,
                    "in_use": combined_in_use,
                }
            else:
                existing["in_use"] = combined_in_use

    return sorted(
        best_by_ssid.values(),
        key=lambda item: (-item["signal"], item["ssid"].lower()),
    )


def _format_bytes(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(num_bytes)
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.1f} {units[idx]}"


def _parse_ip_addr_json(raw_json: str) -> List[Dict[str, Any]]:
    """Parse `ip -j addr show` output into simplified interface dicts."""
    try:
        data = json.loads(raw_json)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    result: List[Dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        ifname = str(entry.get("ifname") or "")
        if not ifname or ifname == "lo":
            continue
        operstate = str(entry.get("operstate") or "UNKNOWN").upper()
        ipv4: Optional[str] = None
        for addr_info in entry.get("addr_info") or []:
            if isinstance(addr_info, dict) and addr_info.get("family") == "inet":
                local = addr_info.get("local")
                if local:
                    ipv4 = str(local)
                    break
        result.append(
            {
                "name": ifname,
                "operstate": operstate,
                "ipv4": ipv4,
                "driver": _read_sysfs_driver(ifname),
            }
        )
    return result


def _read_sysfs_driver(ifname: str) -> str:
    """Return Linux kernel driver name for a network interface if available."""
    try:
        driver_link = Path("/sys/class/net") / ifname / "device" / "driver"
        if driver_link.exists():
            return driver_link.resolve().name
    except Exception:
        pass
    return ""


def _detect_interfaces(
    run_cmd: CommandRunner = default_run_cmd,
    interfaces_override: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Detect Ethernet, Wi-Fi, Android USB tethering, and iPhone USB tethering."""
    if interfaces_override is not None:
        raw_ifaces = list(interfaces_override)
    else:
        rc, out, _ = run_cmd(["ip", "-j", "addr", "show"], 3.0)
        raw_ifaces = _parse_ip_addr_json(out) if rc == 0 else []

    ethernet: Dict[str, Any] = {
        "present": False,
        "connected": False,
        "interface": None,
        "ip": None,
        "state": "disconnected",
    }
    wifi: Dict[str, Any] = {
        "present": False,
        "connected": False,
        "interface": None,
        "ip": None,
        "ssid": None,
        "state": "disconnected",
    }
    android_usb: Dict[str, Any] = {
        "present": False,
        "connected": False,
        "interface": None,
        "ip": None,
        "state": "disconnected",
    }
    iphone_usb: Dict[str, Any] = {
        "present": False,
        "plugged_in": False,
        "paired": False,
        "needs_trust": False,
        "connected": False,
        "interface": None,
        "ip": None,
        "state": "disconnected",
        "trust_message": "",
    }

    for iface in raw_ifaces:
        name = str(iface.get("name") or "")
        driver = str(iface.get("driver") or "").lower()
        operstate = str(iface.get("operstate") or "").upper()
        ipv4 = iface.get("ipv4")
        is_up = bool(ipv4) or operstate == "UP"

        # 1. iPhone USB tethering (ipheth driver or interface name)
        if driver == "ipheth" or name.startswith("ipheth"):
            iphone_usb["present"] = True
            iphone_usb["plugged_in"] = True
            iphone_usb["interface"] = name
            iphone_usb["ip"] = ipv4
            if is_up and ipv4:
                iphone_usb["connected"] = True
                iphone_usb["state"] = "connected"
            elif iphone_usb["state"] == "disconnected":
                iphone_usb["state"] = "detected"
            continue

        # 2. Android USB tethering (usb*, rndis*, or rndis_host/cdc_ether/cdc_ncm)
        if (
            name.startswith("usb")
            or name.startswith("rndis")
            or driver in ("rndis_host", "cdc_ether", "cdc_ncm", "cdc_eem")
        ):
            android_usb["present"] = True
            if not android_usb["connected"] or (is_up and ipv4):
                android_usb["interface"] = name
                android_usb["ip"] = ipv4
                android_usb["connected"] = bool(is_up and ipv4)
                android_usb["state"] = "connected" if android_usb["connected"] else "detected"
            continue

        # 3. Wi-Fi (wlan*, wlp*, wlx*)
        if name.startswith(("wlan", "wlp", "wlx")):
            wifi["present"] = True
            if not wifi["connected"] or (is_up and ipv4):
                wifi["interface"] = name
                wifi["ip"] = ipv4
                wifi["connected"] = bool(is_up and ipv4)
                wifi["state"] = "connected" if wifi["connected"] else "disconnected"
            continue

        # 4. Ethernet (eth*, en*)
        if name.startswith(("eth", "en")):
            ethernet["present"] = True
            if not ethernet["connected"] or (is_up and ipv4):
                ethernet["interface"] = name
                ethernet["ip"] = ipv4
                ethernet["connected"] = bool(is_up and ipv4)
                ethernet["state"] = "connected" if ethernet["connected"] else "disconnected"

    # Active Wi-Fi SSID lookup via nmcli if Wi-Fi is connected
    if wifi["connected"]:
        rc_nm, out_nm, _ = run_cmd(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
            2.5,
        )
        if rc_nm == 0:
            for line in out_nm.splitlines():
                parts = split_nmcli_terse_line(line)
                if len(parts) >= 2 and parts[0].strip().lower() == "yes":
                    wifi["ssid"] = parts[1].strip() or None
                    break

    # iPhone trust check via idevicepair validate (and idevice_id -l)
    _check_iphone_trust(iphone_usb, run_cmd)

    return {
        "ethernet": ethernet,
        "wifi": wifi,
        "android_usb": android_usb,
        "iphone_usb": iphone_usb,
    }


def _check_iphone_trust(iphone_usb: Dict[str, Any], run_cmd: CommandRunner) -> None:
    """Populate iPhone plugged_in / paired / needs_trust via idevicepair validate."""
    rc_id, out_id, _ = run_cmd(["idevice_id", "-l"], 2.0)
    has_udid = rc_id == 0 and bool((out_id or "").strip())
    if has_udid:
        iphone_usb["plugged_in"] = True
        iphone_usb["present"] = True

    rc_val, out_val, err_val = run_cmd(["idevicepair", "validate"], 2.5)
    combined = f"{out_val}\n{err_val}".strip()
    lower = combined.lower()

    if rc_val == 0 and ("success" in lower or "validated" in lower or not lower):
        iphone_usb["plugged_in"] = True
        iphone_usb["present"] = True
        iphone_usb["paired"] = True
        iphone_usb["needs_trust"] = False
        iphone_usb["trust_message"] = "paired"
        if iphone_usb["connected"]:
            iphone_usb["state"] = "connected"
        else:
            iphone_usb["state"] = "paired"
        return

    # If idevicepair is missing (rc 127) or explicitly says no device found
    if rc_val == 127 or "no device found" in lower:
        if iphone_usb["plugged_in"] and not iphone_usb["connected"]:
            iphone_usb["paired"] = False
            iphone_usb["needs_trust"] = True
            iphone_usb["state"] = "needs_trust"
            iphone_usb["trust_message"] = combined or "unpaired"
        elif iphone_usb["connected"]:
            iphone_usb["paired"] = True
            iphone_usb["needs_trust"] = False
        return

    # Any other non-zero exit (e.g., Password protected, User denied, not paired,
    # ERROR: Device ... returned unhandled error) means device is plugged in
    # and requires trust confirmation on the iPhone screen.
    if combined or iphone_usb["plugged_in"]:
        iphone_usb["plugged_in"] = True
        iphone_usb["present"] = True
        iphone_usb["paired"] = False
        iphone_usb["needs_trust"] = True
        iphone_usb["state"] = "needs_trust"
        iphone_usb["trust_message"] = combined or "trust_required"


def _check_internet(timeout: float = 1.0) -> Dict[str, Any]:
    """Lightweight TCP reachability check. Never raises."""
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                local_ip = sock.getsockname()[0]
                return {"reachable": True, "ip": local_ip, "target": host}
        except Exception:
            continue
    return {"reachable": False, "ip": None, "target": None}


def _detect_disks(run_cmd: CommandRunner = default_run_cmd) -> Dict[str, Any]:
    """Summarize detected block disks via `lsblk -J`. Safe fallback on Windows."""
    rc, out, _ = run_cmd(
        ["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,MODEL,TRAN,MOUNTPOINT,FSTYPE,RM"],
        3.0,
    )
    if rc != 0 or not out.strip():
        return {"count": 0, "total_bytes": 0, "total_human": "0 B", "items": []}

    try:
        parsed = json.loads(out)
    except Exception:
        return {"count": 0, "total_bytes": 0, "total_human": "0 B", "items": []}

    devices = parsed.get("blockdevices") if isinstance(parsed, dict) else []
    if not isinstance(devices, list):
        devices = []

    items: List[Dict[str, Any]] = []
    total_bytes = 0
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        dev_type = str(dev.get("type") or "").lower()
        if dev_type != "disk":
            continue
        try:
            size_b = int(dev.get("size") or 0)
        except (TypeError, ValueError):
            size_b = 0
        total_bytes += size_b
        items.append(
            {
                "name": str(dev.get("name") or ""),
                "size_bytes": size_b,
                "size_human": _format_bytes(size_b),
                "model": str(dev.get("model") or "").strip(),
                "transport": str(dev.get("tran") or "").strip(),
                "removable": bool(dev.get("rm")),
            }
        )

    return {
        "count": len(items),
        "total_bytes": total_bytes,
        "total_human": _format_bytes(total_bytes),
        "items": items,
    }


def get_live_state_dir() -> Path:
    """Resolve LiveUSB runtime state directory."""
    env_dir = os.environ.get("MESH_LIVE_STATE_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return Path("/run/rhm-live")


def read_session_state(state_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Read LiveUSB session.json safely, returning normalized fallback fields."""
    base = state_dir if state_dir is not None else get_live_state_dir()
    session_file = base / "session.json"
    fallback: Dict[str, Any] = {
        "ready": False,
        "connect_url": "",
        "token": "",
        "node_id": "",
        "tunnel_status": "offline",
        "source": str(session_file),
    }
    try:
        if not session_file.is_file():
            return fallback
        data = json.loads(session_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return fallback
    except Exception:
        return fallback

    connect_url = str(data.get("connect_url") or data.get("pair_url") or "").strip()
    token = str(data.get("link_key") or data.get("token") or data.get("live_token") or "").strip()
    node_id = str(data.get("node_id") or data.get("hostname") or "").strip()
    tunnel_status = str(
        data.get("tunnel_status")
        or ("connected" if connect_url else "offline")
    ).strip()
    ready = bool(data.get("ready", bool(connect_url and token)))

    merged = dict(fallback)
    merged.update(data)
    merged["ready"] = ready
    merged["connect_url"] = connect_url
    merged["token"] = token
    merged["node_id"] = node_id
    merged["tunnel_status"] = tunnel_status
    merged["source"] = str(session_file)
    return merged


def trigger_session_regenerate(
    state_dir: Optional[Path] = None,
    run_cmd: CommandRunner = default_run_cmd,
) -> Dict[str, Any]:
    """Signal LiveUSB bootstrap to revoke and rotate/re-register the session token."""
    base = state_dir if state_dir is not None else get_live_state_dir()
    messages: List[str] = []

    # (a) If core.liveusb_bootstrap is importable, revoke the current ephemeral node
    #     and remove agent.env so the next start/registration issues fresh credentials.
    bootstrap_mod: Any = None
    try:
        bootstrap_mod = importlib.import_module("core.liveusb_bootstrap")
    except ModuleNotFoundError:
        bootstrap_mod = None
    except Exception as exc:
        messages.append(f"core.liveusb_bootstrap: {exc}")

    if bootstrap_mod is not None:
        revoke_fn = getattr(bootstrap_mod, "revoke_ephemeral_node", None)
        if callable(revoke_fn):
            try:
                rev_res = revoke_fn(str(base))
                if isinstance(rev_res, dict) and rev_res.get("ok"):
                    messages.append("Текущая сессия отозвана на шлюзе")
            except Exception as exc:
                messages.append(f"revoke_ephemeral_node: {exc}")
        try:
            env_file = base / "agent.env"
            if env_file.exists():
                env_file.unlink()
                messages.append("Файл agent.env удалён")
        except Exception as exc:
            messages.append(f"Не удалось удалить agent.env: {exc}")

    # (b) If systemctl is available and rhm-live-agent.service is active/known,
    #     restart the service so it re-registers cleanly.
    rc_chk, out_chk, _ = run_cmd(
        ["systemctl", "show", "-p", "LoadState", "--value", "rhm-live-agent.service"],
        3.0,
    )
    load_state = (out_chk or "").strip().lower()
    if rc_chk == 0 and load_state not in ("", "not-found", "masked"):
        rc_rst, out_rst, err_rst = run_cmd(
            ["systemctl", "restart", "rhm-live-agent.service"],
            15.0,
        )
        if rc_rst == 0:
            messages.append((out_rst or "Служба rhm-live-agent.service перезапущена").strip())
            return {
                "ok": True,
                "method": "systemctl_restart",
                "message": "; ".join(messages),
                "trigger_file": None,
            }
        messages.append((err_rst or out_rst or f"Ошибка перезапуска службы (код {rc_rst})").strip())

    # (c) Otherwise fall back to core.liveusb_bootstrap.register_ephemeral_node(...)
    #     if MESH_GATEWAY is set, or write regenerate.trigger.
    gateway_url = os.environ.get("MESH_GATEWAY", "").strip()
    if bootstrap_mod is not None and gateway_url:
        register_fn = getattr(bootstrap_mod, "register_ephemeral_node", None)
        if callable(register_fn):
            try:
                reg_res = register_fn(gateway_url, state_dir=str(base))
                if isinstance(reg_res, dict) and reg_res.get("ok"):
                    messages.append("Новая эфемерная сессия зарегистрирована")
                    return {
                        "ok": True,
                        "method": "register_ephemeral_node",
                        "message": "; ".join(messages),
                        "trigger_file": None,
                    }
                err_detail = reg_res.get("error") if isinstance(reg_res, dict) else "unknown error"
                messages.append(f"register_ephemeral_node: {err_detail}")
            except Exception as exc:
                messages.append(f"register_ephemeral_node: {exc}")

    # Fallback: write regenerate.trigger
    trigger_path: Optional[str] = None
    try:
        base.mkdir(parents=True, exist_ok=True)
        tfile = base / "regenerate.trigger"
        tfile.write_text(f"{int(time.time())}\n", encoding="utf-8")
        trigger_path = str(tfile)
        messages.append("Запрос на обновление токена записан")
    except Exception as exc:
        messages.append(f"Не удалось записать файл-триггер: {exc}")

    # Optional external command hook via environment variable
    regen_cmd = os.environ.get("RHM_REGENERATE_CMD", "").strip()
    if regen_cmd:
        rc, out, err = run_cmd(regen_cmd.split(), 10.0)
        if rc == 0:
            messages.append((out or "Команда перерегистрации выполнена").strip())
        else:
            messages.append((err or f"Ошибка перерегистрации (код {rc})").strip())

    ok = trigger_path is not None
    return {
        "ok": ok,
        "method": "trigger_file",
        "message": "; ".join(messages) if messages else "OK",
        "trigger_file": trigger_path,
    }


def render_qr_svg(
    connect_url: str,
    run_cmd: CommandRunner = default_run_cmd,
) -> Tuple[str, bool]:
    """Render an SVG QR code via `qrencode -t SVG -o -` or return a clean SVG fallback."""
    url = (connect_url or "").strip()
    if not url:
        return _placeholder_svg("Ссылка подключения ещё не готова"), False

    rc, out, _ = run_cmd(["qrencode", "-t", "SVG", "-m", "2", "-o", "-", "--", url], 5.0)
    if rc == 0 and "<svg" in out.lower():
        return out, True

    return _placeholder_svg("QR-код недоступен (установите qrencode)"), False


def _placeholder_svg(label: str) -> str:
    safe_label = html.escape(label)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 280" width="280" height="280" role="img">\n'
        '  <rect x="0" y="0" width="280" height="280" fill="#ffffff"/>\n'
        '  <rect x="12" y="12" width="256" height="256" fill="none" stroke="#2b313a" stroke-width="2"/>\n'
        '  <text x="140" y="132" text-anchor="middle" font-family="sans-serif" font-size="14" font-weight="600" fill="#0e1116">QR</text>\n'
        f'  <text x="140" y="156" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#4b5563">{safe_label}</text>\n'
        "</svg>\n"
    )


def connect_wifi(
    ssid: str,
    password: str = "",
    hidden: bool = False,
    run_cmd: CommandRunner = default_run_cmd,
) -> Dict[str, Any]:
    """Connect to a Wi-Fi network using nmcli."""
    clean_ssid = (ssid or "").strip()
    if not clean_ssid:
        return {"ok": False, "message": "Не указано имя сети (SSID)"}

    cmd = ["nmcli", "dev", "wifi", "connect", clean_ssid]
    if password:
        cmd.extend(["password", password])
    if hidden:
        cmd.extend(["hidden", "yes"])

    rc, out, err = run_cmd(cmd, 25.0)
    if rc == 0:
        msg = (out or f"Подключено к {clean_ssid}").strip()
        return {"ok": True, "message": msg}

    err_msg = (err or out or f"Ошибка подключения (код {rc})").strip()
    return {"ok": False, "message": err_msg}


def scan_wifi(run_cmd: CommandRunner = default_run_cmd) -> Dict[str, Any]:
    """Scan available Wi-Fi networks using nmcli."""
    cmd = [
        "nmcli",
        "-t",
        "-f",
        "SSID,SIGNAL,SECURITY,IN-USE",
        "dev",
        "wifi",
        "list",
        "--rescan",
        "yes",
    ]
    rc, out, err = run_cmd(cmd, 12.0)
    if rc != 0:
        return {
            "ok": False,
            "networks": [],
            "error": (err or out or f"nmcli завершился с кодом {rc}").strip(),
        }
    return {
        "ok": True,
        "networks": parse_nmcli_wifi_list(out),
        "error": None,
    }


class KioskServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying injectable runtime dependencies for tests."""

    allow_reuse_address = True

    def __init__(
        self,
        server_address: Tuple[str, int] = (DEFAULT_HOST, DEFAULT_PORT),
        *,
        static_dir: Optional[Path] = None,
        state_dir: Optional[Path] = None,
        run_cmd: CommandRunner = default_run_cmd,
        internet_checker: Optional[Callable[[], Dict[str, Any]]] = None,
        interfaces_override: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        host, port = server_address
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(f"kiosk_server must bind to loopback only, got {host!r}")
        self.static_dir = Path(static_dir) if static_dir is not None else _STATIC_DIR
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.run_cmd = run_cmd
        self.internet_checker = internet_checker or _check_internet
        self.interfaces_override = interfaces_override
        super().__init__((host, port), KioskRequestHandler)


class KioskRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for LiveUSB kiosk static UI and JSON/SVG API."""

    server: KioskServer

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy default stderr access logs unless debug is enabled
        if os.environ.get("RHM_KIOSK_DEBUG"):
            super().log_message(format, *args)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _read_json_body(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return None, "Некорректный заголовок Content-Length"
        if length < 0 or length > MAX_POST_BYTES:
            return None, "Слишком большой размер запроса"
        if length == 0:
            return {}, None
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            return None, "Некорректный JSON"
        if not isinstance(parsed, dict):
            return None, "Ожидался JSON-объект"
        return parsed, None

    def _serve_static(self, req_path: str) -> None:
        rel = "index.html" if req_path in ("", "/") else req_path.lstrip("/")
        if ".." in Path(rel).parts or rel.startswith(("/", "\\")):
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Forbidden"})
            return

        base_dir = self.server.static_dir.resolve()
        candidate = (base_dir / rel).resolve()
        try:
            common = os.path.commonpath([str(base_dir), str(candidate)])
        except ValueError:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Forbidden"})
            return

        if common != str(base_dir):
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Forbidden"})
            return

        if not candidate.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return

        ctype, _ = mimetypes.guess_type(str(candidate))
        if not ctype:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype = f"{ctype}; charset=utf-8"

        try:
            data = candidate.read_bytes()
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        self._send_bytes(HTTPStatus.OK, data, ctype)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/status":
                self._handle_status()
                return
            if path == "/api/wifi/scan":
                self._handle_wifi_scan()
                return
            if path == "/api/qr.svg":
                self._handle_qr_svg()
                return
            self._serve_static(path)
        except Exception as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": str(exc)},
            )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/wifi/connect":
                self._handle_wifi_connect()
                return
            if path == "/api/session/regenerate":
                self._handle_session_regenerate()
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        except Exception as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": str(exc)},
            )

    def _handle_status(self) -> None:
        interfaces = _detect_interfaces(
            run_cmd=self.server.run_cmd,
            interfaces_override=self.server.interfaces_override,
        )
        try:
            internet = self.server.internet_checker()
        except Exception:
            internet = {"reachable": False, "ip": None, "target": None}
        session = read_session_state(self.server.state_dir)
        disks = _detect_disks(self.server.run_cmd)
        payload = {
            "ok": True,
            "hostname": socket.gethostname(),
            "timestamp": int(time.time()),
            "interfaces": interfaces,
            "internet": internet,
            "session": session,
            "disks": disks,
        }
        self._send_json(HTTPStatus.OK, payload)

    def _handle_wifi_scan(self) -> None:
        result = scan_wifi(self.server.run_cmd)
        self._send_json(HTTPStatus.OK, result)

    def _handle_wifi_connect(self) -> None:
        body, err = self._read_json_body()
        if err is not None or body is None:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": err or "Bad request"})
            return
        ssid = str(body.get("ssid") or "")
        password = str(body.get("password") or "")
        hidden = bool(body.get("hidden", False))
        if not ssid.strip():
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "message": "Не указано имя сети (SSID)"},
            )
            return
        result = connect_wifi(ssid, password=password, hidden=hidden, run_cmd=self.server.run_cmd)
        self._send_json(HTTPStatus.OK, result)

    def _handle_session_regenerate(self) -> None:
        result = trigger_session_regenerate(
            state_dir=self.server.state_dir,
            run_cmd=self.server.run_cmd,
        )
        self._send_json(HTTPStatus.OK, result)

    def _handle_qr_svg(self) -> None:
        session = read_session_state(self.server.state_dir)
        svg_text, _ = render_qr_svg(session.get("connect_url", ""), self.server.run_cmd)
        self._send_bytes(
            HTTPStatus.OK,
            svg_text.encode("utf-8"),
            "image/svg+xml; charset=utf-8",
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LiveUSB Kiosk HTTP Server (127.0.0.1 only)")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Loopback bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port (default 8787)")
    parser.add_argument("--static-dir", default=None, help="Override kiosk_ui directory")
    parser.add_argument("--state-dir", default=None, help="Override LiveUSB state directory")
    args = parser.parse_args(argv)

    server = KioskServer(
        (args.host, args.port),
        static_dir=Path(args.static_dir) if args.static_dir else None,
        state_dir=Path(args.state_dir) if args.state_dir else None,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
