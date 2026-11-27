#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/live-usb/kiosk_server.py - Localhost-only LiveUSB kiosk HTTP server.

Stdlib-only (Python 3.8+) ThreadingHTTPServer bound strictly to 127.0.0.1:8787.
Serves the Russian kiosk UI from ops/live-usb/kiosk_ui/ and exposes JSON/SVG
endpoints for network status, Wi-Fi management, LiveUSB session state, QR, the
allowlisted local application launcher, and the «Настройки сети» dialog
(read-only snapshot plus IPv4/DNS, PPPoE, WireGuard and OpenVPN through
NetworkManager, and AmneziaWG awg-quick configs plus HTTPS subscription/QR
import).
"""

from __future__ import annotations

import argparse
import base64
import html
import importlib
import ipaddress
import json
import mimetypes
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_POST_BYTES = 16384

# --- «Настройки сети» integration points -----------------------------------
#
# Module-level so the unit tests can point them at tmp_path and a live ISO can
# override them with MESH_* environment variables if that is ever needed.
NM_CONNECTIONS_DIR = Path("/etc/NetworkManager/system-connections")
RESOLV_CONF_PATH = Path("/etc/resolv.conf")
MAX_RESOLV_CONF_BYTES = 8192
# Every credential-bearing file (WireGuard keyfile, OpenVPN temp config) is
# created with this mode; the response never echoes the secret itself.
PRIVATE_FILE_MODE = 0o600

_STATIC_DIR = Path(__file__).resolve().parent / "kiosk_ui"

# Type alias for injectable command runner (makes unit testing deterministic).
CommandRunner = Callable[[List[str], float], Tuple[int, str, str]]

# Terminal emulators tried in order by launch_local_terminal(), with the flag
# each one uses to pass a command line through to the shell it runs.
TERMINAL_CANDIDATES: Tuple[Tuple[str, str], ...] = (
    ("x-terminal-emulator", "-e"),
    ("xterm", "-e"),
    ("lxterminal", "-e"),
    ("gnome-terminal", "--"),
)


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
        return 127, "", f"command not found: {cmd[0] if cmd else 'command'}"
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out: {cmd[0] if cmd else 'command'}"
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
        ssid = parts[0]
        if not ssid.strip():
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
        "mcp_url": "",
        "sse_url": "",
        "public_url": "",
        "token": "",
        "node_id": "",
        "tunnel_mode": "cloudflare",
        "tunnel_status": "offline",
        "tunnel_error": "",
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
    merged["mcp_url"] = str(data.get("mcp_url") or "").strip()
    merged["sse_url"] = str(data.get("sse_url") or "").strip()
    merged["public_url"] = str(data.get("public_url") or "").strip()
    merged["token"] = token
    merged["node_id"] = node_id
    merged["tunnel_mode"] = str(data.get("tunnel_mode") or "cloudflare").strip()
    merged["tunnel_status"] = tunnel_status
    merged["tunnel_error"] = str(data.get("tunnel_error") or "").strip()
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
        for fname, label in (
            ("standalone.token", "Токен standalone.token сброшен"),
            ("session.json", "Файл session.json удалён"),
            ("agent.env", "Файл agent.env удалён"),
        ):
            try:
                fpath = base / fname
                if fpath.exists():
                    fpath.unlink()
                    messages.append(label)
            except Exception as exc:
                messages.append(f"Не удалось удалить {fname}: {exc}")

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
        try:
            cmd_args = shlex.split(regen_cmd)
        except ValueError:
            cmd_args = []
        if cmd_args:
            rc, out, err = run_cmd(cmd_args, 10.0)
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


# --- Allowlisted kiosk application launcher --------------------------------
#
# The kiosk UI may only ask the server to launch a *fixed* application id.
# Every argv is built here from this table (never from request data), so a
# browser can never supply a command, a binary, or an arbitrary path.
#
# Each entry has:
#   label          - Russian label rendered by the UI.
#   candidates     - ordered (binary name, argv tail) pairs; the first binary
#                    found by shutil.which wins. A tail of ``None`` means the
#                    argv is computed at launch time (file manager target dir).
#   missing_reason - Russian explanation returned when no binary is installed.
APP_LAUNCH_ORDER: Tuple[str, ...] = ("desktop", "terminal", "files", "partitions", "editor")

APP_LAUNCHERS: Dict[str, Dict[str, Any]] = {
    "desktop": {
        "label": "Рабочий стол (LXDE)",
        "candidates": (("rhm-open-desktop", None),),
        "missing_reason": "Рабочий стол LXDE недоступен: не установлен rhm-open-desktop",
    },
    "terminal": {
        "label": "Терминал",
        "candidates": tuple(
            (name, (flag, "bash")) for name, flag in TERMINAL_CANDIDATES
        ),
        "missing_reason": "Терминал недоступен: не найден эмулятор терминала",
    },
    "files": {
        "label": "Файловый менеджер",
        "candidates": (("rhm-open-files", None), ("xfe", None), ("pcmanfm", None)),
        "missing_reason": "Файловый менеджер недоступен: не установлены rhm-open-files/xfe/pcmanfm",
    },
    "partitions": {
        "label": "Редактор разделов (GParted)",
        "candidates": (("gparted", ()),),
        "missing_reason": "Редактор разделов недоступен: gparted не установлен",
    },
    "editor": {
        "label": "Текстовый редактор",
        "candidates": (("mousepad", ()),),
        "missing_reason": "Текстовый редактор недоступен: mousepad не установлен",
    },
}

# Directory the «Файловый менеджер» entry opens; the kiosk bootstrap creates
# /mnt/rescue, and /root is the safe fallback on hosts without it.
FILES_PRIMARY_DIR = "/mnt/rescue"
FILES_FALLBACK_DIR = "/root"


def _resolve_app_launcher(app_id: str) -> Tuple[Optional[str], Any]:
    """Resolve the first installed binary for ``app_id`` via shutil.which."""
    try:
        candidates = APP_LAUNCHERS[app_id]["candidates"]
    except KeyError:
        return None, None
    for name, argv_tail in candidates:
        try:
            exe = shutil.which(name)
        except Exception:
            exe = None
        if exe:
            return str(exe), argv_tail
    return None, None


def _files_target_dir() -> str:
    """Return the fixed directory opened by the file manager entry."""
    try:
        if os.path.isdir(FILES_PRIMARY_DIR):
            return FILES_PRIMARY_DIR
    except Exception:
        pass
    return FILES_FALLBACK_DIR


def _build_app_argv(exe: str, argv_tail: Any) -> List[str]:
    """Build the fixed argv for a resolved launcher (no request data)."""
    if argv_tail is None:
        return [exe, _files_target_dir()]
    return [exe, *[str(part) for part in argv_tail]]


def list_local_apps() -> List[Dict[str, Any]]:
    """Return the stable allowlisted application catalogue for GET /api/apps.

    Each item is ``{"id", "label", "available", "reason"}``. ``available`` is
    True only when the launcher binary is installed (resolved with
    shutil.which); ``reason`` is a Russian explanation otherwise, else "".
    """
    apps: List[Dict[str, Any]] = []
    for app_id in APP_LAUNCH_ORDER:
        spec = APP_LAUNCHERS[app_id]
        exe, _argv_tail = _resolve_app_launcher(app_id)
        available = bool(exe)
        apps.append(
            {
                "id": app_id,
                "label": str(spec["label"]),
                "available": available,
                "reason": "" if available else str(spec["missing_reason"]),
            }
        )
    return apps


def launch_local_app(
    app_id: str,
    popen: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Launch one allowlisted kiosk application. Never raises.

    The request may only choose an id; the argv comes from ``APP_LAUNCHERS``.
    Returns ``{"ok": True, "pid": ..., "command": [...]}`` on success and
    ``{"ok": False, "error": "unknown_app"|"unavailable", "message": "..."}``
    otherwise. The helper should not raise, but the HTTP layer still guards.
    """
    label = "Приложение"
    try:
        if not isinstance(app_id, str) or app_id not in APP_LAUNCHERS:
            return {
                "ok": False,
                "error": "unknown_app",
                "message": "Неизвестное приложение",
            }

        spec = APP_LAUNCHERS[app_id]
        label = str(spec["label"])

        exe, argv_tail = _resolve_app_launcher(app_id)
        if not exe:
            return {
                "ok": False,
                "error": "unavailable",
                "message": str(spec["missing_reason"]),
            }

        if not os.environ.get("DISPLAY"):
            return {
                "ok": False,
                "error": "unavailable",
                "message": f"{label} недоступен: нет графической сессии (DISPLAY не задан)",
            }

        cmd = _build_app_argv(exe, argv_tail)
        spawn = popen if popen is not None else subprocess.Popen
        proc = spawn(
            cmd,
            cwd="/",
            env=os.environ.copy(),
            shell=False,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "command": cmd, "pid": getattr(proc, "pid", None)}
    except Exception as exc:
        return {
            "ok": False,
            "error": "unavailable",
            "message": f"Не удалось запустить {label.lower()}: {exc}",
        }


def launch_local_terminal(
    popen: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Open a local bash terminal on the kiosk display (no shell=True).

    Thin wrapper around the allowlisted ``terminal`` launcher entry, kept so
    ``POST /api/terminal`` and the existing callers stay unchanged. Returns
    ``{"ok": True, "command": [...], "pid": ...}`` on success and
    ``{"ok": False, "message": "..."}`` on failure. Never raises.
    """
    return launch_local_app("terminal", popen=popen)


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


def _write_tunnel_restart_trigger(state_dir: Optional[Path]) -> None:
    """Write <state_dir>/tunnel-restart.trigger with current timestamp (non-fatal)."""
    if state_dir is None:
        return
    try:
        base = Path(state_dir)
        base.mkdir(parents=True, exist_ok=True)
        (base / "tunnel-restart.trigger").write_text(
            f"{int(time.time())}\n", encoding="utf-8"
        )
    except Exception:
        pass


def _detect_wifi_iface(run_cmd: CommandRunner = default_run_cmd) -> str:
    """Detect the primary wireless interface via nmcli or sysfs, falling back to wlan0."""
    rc, out, _ = run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE", "dev", "status"], 5.0)
    if rc == 0 and out:
        for raw_line in out.splitlines():
            parts = split_nmcli_terse_line(raw_line.strip("\r\n"))
            if len(parts) >= 2 and parts[1].strip() == "wifi" and parts[0].strip():
                return parts[0].strip()
    sys_net = Path("/sys/class/net")
    try:
        if sys_net.is_dir():
            for entry in sorted(sys_net.iterdir()):
                if entry.name.startswith(("wlan", "wlp", "wlx")):
                    return entry.name
    except Exception:
        pass
    return "wlan0"


def _delete_conflicting_wifi_profiles(
    ssid: str,
    run_cmd: CommandRunner = default_run_cmd,
) -> None:
    """Delete existing NetworkManager 802-11-wireless profiles matching the target SSID."""
    rc, out, _ = run_cmd(["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"], 5.0)
    if rc != 0 or not out:
        return
    for raw_line in out.splitlines():
        parts = split_nmcli_terse_line(raw_line.strip("\r\n"))
        if len(parts) >= 2 and parts[1].strip() == "802-11-wireless" and parts[0] == ssid:
            run_cmd(["nmcli", "con", "delete", parts[0]], 5.0)


def _connect_wifi_via_profile(
    ssid: str,
    password: str,
    hidden: bool,
    run_cmd: CommandRunner = default_run_cmd,
) -> Dict[str, Any]:
    """Create an explicit NetworkManager Wi-Fi profile and bring it up."""
    wlan_iface = _detect_wifi_iface(run_cmd)
    add_cmd = [
        "nmcli",
        "con",
        "add",
        "type",
        "wifi",
        "ifname",
        wlan_iface,
        "con-name",
        ssid,
        "ssid",
        ssid,
    ]
    if hidden:
        add_cmd.extend(["wifi.hidden", "yes"])
    if password:
        add_cmd.extend(["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password])

    rc_add, out_add, err_add = run_cmd(add_cmd, 15.0)
    if rc_add != 0:
        err_msg = (
            err_add or out_add or f"Ошибка создания профиля Wi-Fi (код {rc_add})"
        ).strip()
        return {"ok": False, "message": err_msg}

    rc_up, out_up, err_up = run_cmd(["nmcli", "con", "up", ssid], 35.0)
    if rc_up == 0:
        msg = (out_up or f"Подключено к {ssid}").strip()
        return {"ok": True, "message": msg}

    err_msg = (err_up or out_up or f"Ошибка подключения (код {rc_up})").strip()
    return {"ok": False, "message": err_msg}


def connect_wifi(
    ssid: str,
    password: str = "",
    hidden: bool = False,
    run_cmd: CommandRunner = default_run_cmd,
) -> Dict[str, Any]:
    """Connect to a Wi-Fi network using nmcli."""
    raw_ssid = ssid or ""
    if not raw_ssid.strip():
        return {"ok": False, "message": "Не указано имя сети (SSID)"}
    if len(raw_ssid.encode("utf-8")) > 32:
        return {"ok": False, "message": "Имя сети (SSID) превышает 32 байта"}
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_ssid):
        return {"ok": False, "message": "Имя сети (SSID) содержит недопустимые символы"}
    if raw_ssid.startswith("-") or raw_ssid.lstrip().startswith("-"):
        return {"ok": False, "message": "Имя сети (SSID) не может начинаться с '-'"}

    raw_password = password or ""
    if len(raw_password) > 64:
        return {"ok": False, "message": "Пароль Wi-Fi превышает 64 символа"}
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_password):
        return {"ok": False, "message": "Пароль Wi-Fi содержит недопустимые символы"}
    if raw_password:
        is_64_hex = len(raw_password) == 64 and all(
            ch in "0123456789abcdefABCDEF" for ch in raw_password
        )
        if len(raw_password) < 8 and not is_64_hex:
            return {
                "ok": False,
                "message": "Пароль Wi-Fi (WPA-PSK) должен содержать не менее 8 символов (или 64 hex-символа)",
            }

    # Ensure Wi-Fi radio is enabled and refresh scan list (non-fatal).
    rc_radio, out_radio, _ = run_cmd(["nmcli", "radio", "wifi"], 5.0)
    if rc_radio == 0 and out_radio.strip().lower() == "disabled":
        run_cmd(["nmcli", "radio", "wifi", "on"], 5.0)
    run_cmd(["nmcli", "dev", "wifi", "rescan"], 10.0)

    # Remove stale/conflicting NM Wi-Fi profile for the same SSID.
    _delete_conflicting_wifi_profiles(raw_ssid, run_cmd)

    if hidden:
        return _connect_wifi_via_profile(
            raw_ssid, raw_password, hidden=True, run_cmd=run_cmd
        )

    cmd = ["nmcli", "dev", "wifi", "connect", raw_ssid]
    if raw_password:
        cmd.extend(["password", raw_password])

    rc, out, err = run_cmd(cmd, 35.0)
    if rc == 0:
        msg = (out or f"Подключено к {raw_ssid}").strip()
        return {"ok": True, "message": msg}

    err_msg = (err or out or f"Ошибка подключения (код {rc})").strip()
    if "key-mgmt: property is missing" in err_msg.lower():
        _delete_conflicting_wifi_profiles(raw_ssid, run_cmd)
        return _connect_wifi_via_profile(
            raw_ssid, raw_password, hidden=False, run_cmd=run_cmd
        )

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


# --- «Настройки сети»: NetworkManager-backed network configuration ---------
#
# The dialog edits IPv4/DNS, PPPoE, WireGuard and OpenVPN through NetworkManager
# only. Every command is an explicit argv list (never shell=True), every value
# from the request is validated before it reaches argv, and every credential
# (PPPoE password, WireGuard private key, .ovpn body) is written to a 0600 file
# and never echoed back. Failures are 400 (bad input) or 503 (tool/root/NM
# unavailable); helpers never raise to the HTTP layer.

PPPOE_CONNECTION_NAME = "rhm-pppoe"
WIREGUARD_CONNECTION_PREFIX = "rhm-wg-"
OPENVPN_TEMP_PREFIX = "rhm-ovpn-"
OPENVPN_CONNECTION_MAX_LEN = 32
MAX_OPENVPN_CONFIG_BYTES = 12 * 1024
MAX_DNS_SERVERS = 8
MAX_ALLOWED_IPS = 16
WIREGUARD_KEY_BYTES = 32
DEFAULT_WIREGUARD_KEEPALIVE = 0

# Interface names: Linux limits IFNAMSIZ to 16 bytes, and the kiosk has no
# reason to accept anything but the conservative character set.
_IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$")
_CONNECTION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CONNECTION_NAME_SPACES_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*$"
)
_PPPOE_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$")
_PPPOE_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
# A WireGuard key is the base64 of exactly 32 bytes: 43 data chars + one "=".
_WIREGUARD_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


class NetworkValidationError(ValueError):
    """A network-settings request carried invalid or unsafe input (HTTP 400)."""


class NetworkUnavailableError(RuntimeError):
    """NetworkManager (or a required tool) is missing, failing or denied (503)."""


def _nmcli_path() -> str:
    """Return the resolved nmcli path, or "" when it is not installed."""
    try:
        return str(shutil.which("nmcli") or "")
    except Exception:
        return ""


def _require_nmcli() -> str:
    """Raise NetworkUnavailableError unless nmcli is installed."""
    path = _nmcli_path()
    if not path:
        raise NetworkUnavailableError(
            "NetworkManager (nmcli) не установлен или недоступен в этой системе"
        )
    return path


def _sanitize_detail(
    text: Any,
    secrets: Tuple[str, ...] = (),
    replacements: Tuple[Tuple[str, str], ...] = (),
    limit: int = 300,
) -> str:
    """One-line, length-capped diagnostic text with every secret redacted."""
    value = str(text or "")
    for secret in secrets:
        if secret:
            value = value.replace(str(secret), "***")
    for original, replacement in replacements:
        if original:
            value = value.replace(str(original), str(replacement))
    value = " ".join(value.split())
    if len(value) > limit:
        value = value[:limit].rstrip() + "…"
    return value


def _run_nmcli(
    args: List[str],
    timeout: float,
    run_cmd: CommandRunner,
    action: str,
    *,
    secrets: Tuple[str, ...] = (),
    replacements: Tuple[Tuple[str, str], ...] = (),
) -> str:
    """Run one nmcli invocation; raise NetworkUnavailableError on any failure."""
    argv = ["nmcli", *[str(part) for part in args]]
    try:
        rc, out, err = run_cmd(argv, timeout)
    except Exception as exc:
        detail = _sanitize_detail(str(exc), secrets, replacements)
        raise NetworkUnavailableError(
            f"Не удалось выполнить NetworkManager ({action}): {detail}"
        )
    if rc == 0:
        return out or ""
    if rc == 127:
        raise NetworkUnavailableError("NetworkManager (nmcli) не найден в системе")
    if rc == 124:
        raise NetworkUnavailableError(f"NetworkManager не ответил вовремя: {action}")
    detail = _sanitize_detail(err or out, secrets, replacements)
    message = f"NetworkManager не смог выполнить: {action}"
    if detail:
        message = f"{message} — {detail}"
    raise NetworkUnavailableError(message)


def _parse_ip_addr_details(raw_json: str) -> List[Dict[str, Any]]:
    """Parse `ip -j addr show` into name/mac/state/ipv4/prefix dicts."""
    try:
        data = json.loads(raw_json)
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    details: List[Dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        ifname = str(entry.get("ifname") or "")
        if not ifname or ifname == "lo":
            continue
        ipv4: Optional[str] = None
        prefix: Optional[int] = None
        for addr_info in entry.get("addr_info") or []:
            if not isinstance(addr_info, dict) or addr_info.get("family") != "inet":
                continue
            local = addr_info.get("local")
            if not local:
                continue
            ipv4 = str(local)
            try:
                raw_prefix = addr_info.get("prefixlen")
                prefix = int(raw_prefix) if raw_prefix is not None else None
            except (TypeError, ValueError):
                prefix = None
            break

        mac = str(entry.get("address") or "").strip()
        if not mac:
            mac = _read_sysfs_text(Path("/sys/class/net") / ifname / "address")

        details.append(
            {
                "name": ifname,
                "mac": mac,
                "state": str(entry.get("operstate") or "unknown").lower(),
                "ipv4": ipv4,
                "prefix": prefix,
            }
        )
    return details


def _read_sysfs_text(path: Path) -> str:
    """Read one small sysfs value, returning "" on any error."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _parse_ip_route_json(raw_json: str) -> List[Dict[str, Any]]:
    """Parse `ip -j route show default` output into simple route dicts."""
    try:
        data = json.loads(raw_json)
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    routes: List[Dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        device = str(entry.get("dev") or "")
        gateway = str(entry.get("gateway") or "")
        if not device and not gateway:
            continue
        metric: Optional[int]
        try:
            raw_metric = entry.get("metric")
            metric = int(raw_metric) if raw_metric is not None else None
        except (TypeError, ValueError):
            metric = None
        routes.append(
            {
                "destination": str(entry.get("dst") or "default"),
                "gateway": gateway,
                "device": device,
                "metric": metric,
            }
        )
    return routes


def _parse_ip_route_text(output: str) -> List[Dict[str, Any]]:
    """Fallback parser for hosts whose `ip` has no JSON support."""
    routes: List[Dict[str, Any]] = []
    for line in (output or "").splitlines():
        tokens = line.split()
        if not tokens or tokens[0] != "default":
            continue
        gateway = ""
        device = ""
        metric: Optional[int] = None
        index = 1
        while index < len(tokens) - 1:
            key = tokens[index]
            value = tokens[index + 1]
            if key == "via":
                gateway = value
            elif key == "dev":
                device = value
            elif key == "metric":
                try:
                    metric = int(value)
                except ValueError:
                    metric = None
            index += 2
        if gateway or device:
            routes.append(
                {
                    "destination": "default",
                    "gateway": gateway,
                    "device": device,
                    "metric": metric,
                }
            )
    return routes


def _collect_default_routes(run_cmd: CommandRunner) -> List[Dict[str, Any]]:
    """Return the current default routes. Never raises."""
    try:
        rc, out, _ = run_cmd(["ip", "-j", "route", "show", "default"], 3.0)
    except Exception:
        return []
    if rc == 0:
        return _parse_ip_route_json(out)
    try:
        rc_text, out_text, _ = run_cmd(["ip", "route", "show", "default"], 3.0)
    except Exception:
        return []
    if rc_text == 0:
        return _parse_ip_route_text(out_text)
    return []


def _read_resolv_conf(path: Optional[Path] = None) -> List[str]:
    """Read nameservers from /etc/resolv.conf with a hard size cap. Never raises."""
    target = Path(path) if path is not None else RESOLV_CONF_PATH
    try:
        with open(target, "rb") as handle:
            raw = handle.read(MAX_RESOLV_CONF_BYTES)
        text = raw.decode("utf-8", "replace")
    except Exception:
        return []

    servers: List[str] = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2 or parts[0].lower() != "nameserver":
            continue
        candidate = parts[1]
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if candidate not in servers:
            servers.append(candidate)
        if len(servers) >= MAX_DNS_SERVERS:
            break
    return servers


def _nmcli_active_connections(run_cmd: CommandRunner) -> Dict[str, Dict[str, str]]:
    """Map device name -> {"connection", "state"} from nmcli's active list."""
    try:
        rc, out, _ = run_cmd(
            ["nmcli", "-t", "-f", "NAME,DEVICE,STATE", "connection", "show", "--active"],
            5.0,
        )
    except Exception:
        return {}
    if rc != 0:
        return {}

    mapping: Dict[str, Dict[str, str]] = {}
    for line in (out or "").splitlines():
        parts = split_nmcli_terse_line(line)
        if len(parts) < 3:
            continue
        name = parts[0].strip()
        device = parts[1].strip()
        state = parts[2].strip()
        if not name or not device or device == "--":
            continue
        if device not in mapping:
            mapping[device] = {"connection": name, "state": state}
    return mapping


def _nmcli_connection_names(run_cmd: CommandRunner) -> List[str]:
    """Return every configured NetworkManager connection name. Never raises."""
    try:
        rc, out, _ = run_cmd(["nmcli", "-t", "-f", "NAME", "connection", "show"], 5.0)
    except Exception:
        return []
    if rc != 0:
        return []

    names: List[str] = []
    for line in (out or "").splitlines():
        parts = split_nmcli_terse_line(line)
        if not parts:
            continue
        name = parts[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def _network_snapshot(
    run_cmd: CommandRunner = default_run_cmd,
    state_dir: Optional[Path] = None,
    hostname: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect the read-only «Текущие» network picture for GET /api/network.

    Only ``node_id`` and ``link_key`` leave the session state: the device token
    from session.json/agent.env must never reach the browser. Every probe is
    guarded and degrades to an empty value, so this never raises.
    """
    snapshot: Dict[str, Any] = {
        "ok": True,
        "hostname": "",
        "node_id": "",
        "link_key": "",
        "interfaces": [],
        "dns": [],
        "routes": [],
        "nmcli_available": bool(_nmcli_path()),
        "timestamp": int(time.time()),
    }

    try:
        snapshot["hostname"] = str(hostname or socket.gethostname() or "")
    except Exception:
        snapshot["hostname"] = ""

    try:
        session = read_session_state(state_dir)
        snapshot["node_id"] = str(session.get("node_id") or "")
        snapshot["link_key"] = str(session.get("link_key") or session.get("token") or "")
    except Exception:
        pass

    try:
        snapshot["dns"] = _read_resolv_conf()
    except Exception:
        snapshot["dns"] = []

    routes = _collect_default_routes(run_cmd)
    snapshot["routes"] = routes

    details: List[Dict[str, Any]] = []
    try:
        rc, out, _ = run_cmd(["ip", "-j", "addr", "show"], 3.0)
        if rc == 0:
            details = _parse_ip_addr_details(out)
    except Exception:
        details = []

    active = _nmcli_active_connections(run_cmd)

    primary = str(routes[0].get("device") or "") if routes else ""
    if not primary:
        primary = next((item["name"] for item in details if item.get("ipv4")), "")

    interfaces: List[Dict[str, Any]] = []
    for item in details:
        name = item["name"]
        gateway = next(
            (
                str(route.get("gateway") or "")
                for route in routes
                if route.get("device") == name and route.get("gateway")
            ),
            "",
        )
        connection = active.get(name, {})
        interfaces.append(
            {
                "name": name,
                "mac": item.get("mac") or "",
                "state": item.get("state") or "unknown",
                "connection": str(connection.get("connection") or ""),
                "connection_state": str(connection.get("state") or ""),
                "ipv4": item.get("ipv4"),
                "prefix": item.get("prefix"),
                "gateway": gateway,
                # resolv.conf is global, so only the interface carrying the
                # default route is reported as the DNS owner.
                "dns": list(snapshot["dns"]) if name == primary else [],
            }
        )

    snapshot["interfaces"] = sorted(interfaces, key=lambda iface: iface["name"])
    return snapshot


# --- Validation helpers (raise NetworkValidationError -> HTTP 400) ----------


def validate_interface_name(raw: Any) -> str:
    """Validate a Linux interface name before it is used as a selector."""
    name = raw.strip() if isinstance(raw, str) else ""
    if not name:
        raise NetworkValidationError("Не указан сетевой интерфейс")
    if not _IFACE_RE.match(name):
        raise NetworkValidationError(
            "Недопустимое имя интерфейса: разрешены латиница, цифры, «.», «-», «_» "
            "и не длиннее 15 символов"
        )
    return name


def _validate_ipv4_text(
    raw: Any,
    field: str,
    *,
    optional: bool = False,
    allow_loopback: bool = True,
) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        if optional:
            return ""
        raise NetworkValidationError(f"{field}: значение не указано")
    if len(text) > 15:
        raise NetworkValidationError(f"{field}: слишком длинное значение")
    try:
        address = ipaddress.IPv4Address(text)
    except ValueError:
        raise NetworkValidationError(
            f"{field}: «{_sanitize_detail(text, limit=32)}» не является IPv4-адресом"
        )
    if address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
        raise NetworkValidationError(f"{field}: недопустимый адрес {address}")
    if not allow_loopback and address.is_loopback:
        raise NetworkValidationError(f"{field}: петлевой адрес недопустим")
    return str(address)


def validate_ipv4(raw: Any, field: str = "IPv4-адрес") -> str:
    """Validate one required IPv4 address (address of a static config)."""
    return _validate_ipv4_text(raw, field, allow_loopback=False)


def validate_gateway(raw: Any) -> str:
    """Validate an optional default gateway ("" means "no gateway")."""
    return _validate_ipv4_text(raw, "Шлюз", optional=True, allow_loopback=False)


def validate_prefix(raw: Any, field: str = "Префикс") -> int:
    """Validate an IPv4 prefix length (1..32)."""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        raise NetworkValidationError(f"{field}: значение не указано")
    if not re.match(r"^\d{1,2}$", text):
        raise NetworkValidationError(f"{field}: ожидается число от 1 до 32")
    value = int(text)
    if value < 1 or value > 32:
        raise NetworkValidationError(f"{field}: должен быть от 1 до 32")
    return value


def validate_dns_list(raw: Any) -> List[str]:
    """Validate a comma-separated DNS server list; "" clears the setting."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts = [str(item) for item in raw]
    else:
        text = str(raw).strip()
        if not text:
            return []
        parts = text.split(",")

    servers: List[str] = []
    for part in parts:
        candidate = part.strip()
        if not candidate:
            continue
        server = _validate_ipv4_text(candidate, "DNS-сервер", allow_loopback=True)
        if server not in servers:
            servers.append(server)
    if len(servers) > MAX_DNS_SERVERS:
        raise NetworkValidationError(
            f"DNS: не более {MAX_DNS_SERVERS} серверов"
        )
    return servers


def validate_connection_name(
    raw: Any,
    field: str = "Имя подключения",
    *,
    max_len: int = 48,
    allow_spaces: bool = False,
) -> str:
    """Validate a NetworkManager connection name (also used as a file stem)."""
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise NetworkValidationError(f"{field}: не указано")
    if len(text) > max_len:
        raise NetworkValidationError(f"{field}: не длиннее {max_len} символов")
    pattern = _CONNECTION_NAME_SPACES_RE if allow_spaces else _CONNECTION_NAME_RE
    if not pattern.match(text):
        allowed = "латиница, цифры, «.», «-», «_»"
        if allow_spaces:
            allowed += " и пробел"
        raise NetworkValidationError(f"{field}: допустимы только {allowed}")
    return text


def validate_wireguard_key(raw: Any, field: str = "Ключ WireGuard") -> str:
    """Validate the shape of a WireGuard base64 key (32 bytes, 44 chars)."""
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise NetworkValidationError(f"{field}: значение не указано")
    if not _WIREGUARD_KEY_RE.match(text):
        raise NetworkValidationError(
            f"{field}: ключ WireGuard должен быть base64-строкой из 44 символов"
        )
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise NetworkValidationError(
            f"{field}: ключ WireGuard содержит недопустимые символы base64"
        )
    if len(decoded) != WIREGUARD_KEY_BYTES:
        raise NetworkValidationError(
            f"{field}: ключ WireGuard должен содержать 32 байта"
        )
    return text


def _validate_host(raw: str, field: str = "Хост") -> str:
    host = raw.strip()
    if not host:
        raise NetworkValidationError(f"{field}: не указан")
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        pass
    if not _HOSTNAME_RE.match(host):
        raise NetworkValidationError(
            f"{field}: «{_sanitize_detail(host, limit=48)}» не является доменным именем"
        )
    return host


def _validate_port(raw: str, field: str = "Порт") -> int:
    text = raw.strip()
    if not re.match(r"^\d{1,5}$", text):
        raise NetworkValidationError(f"{field}: ожидается число от 1 до 65535")
    value = int(text)
    if value < 1 or value > 65535:
        raise NetworkValidationError(f"{field}: ожидается число от 1 до 65535")
    return value


def validate_endpoint(raw: Any) -> str:
    """Validate a WireGuard endpoint in host:port (or [ipv6]:port) form."""
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise NetworkValidationError("Endpoint: адрес сервера не указан")
    if len(text) > 300:
        raise NetworkValidationError("Endpoint: слишком длинное значение")

    if text.startswith("["):
        closing = text.find("]")
        if closing < 0 or not text[closing + 1 :].startswith(":"):
            raise NetworkValidationError("Endpoint: ожидается «[IPv6]:порт»")
        host_part = text[1:closing]
        port_part = text[closing + 2 :]
        try:
            host = str(ipaddress.IPv6Address(host_part))
        except ValueError:
            raise NetworkValidationError("Endpoint: некорректный IPv6-адрес")
        port = _validate_port(port_part, "Endpoint: порт")
        return f"[{host}]:{port}"

    if text.count(":") != 1:
        raise NetworkValidationError("Endpoint: ожидается «хост:порт»")
    host_part, port_part = text.split(":", 1)
    host = _validate_host(host_part, "Endpoint: хост")
    port = _validate_port(port_part, "Endpoint: порт")
    return f"{host}:{port}"


def validate_allowed_ips(raw: Any) -> List[str]:
    """Validate the WireGuard AllowedIPs list of IPv4/IPv6 CIDR networks."""
    if isinstance(raw, (list, tuple)):
        parts = [str(item) for item in raw]
    else:
        text = str(raw or "").strip()
        parts = re.split(r"[,\s]+", text) if text else []

    networks: List[str] = []
    for part in parts:
        candidate = part.strip()
        if not candidate:
            continue
        if "/" not in candidate:
            try:
                candidate = f"{ipaddress.IPv4Address(candidate)}/32"
            except ValueError:
                try:
                    candidate = f"{ipaddress.IPv6Address(candidate)}/128"
                except ValueError:
                    raise NetworkValidationError(
                        "Allowed IPs: ожидается список подсетей в формате CIDR"
                    )
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            raise NetworkValidationError(
                "Allowed IPs: ожидается список подсетей в формате CIDR"
            )
        rendered = str(network)
        if rendered not in networks:
            networks.append(rendered)

    if not networks:
        raise NetworkValidationError("Allowed IPs: укажите хотя бы одну подсеть")
    if len(networks) > MAX_ALLOWED_IPS:
        raise NetworkValidationError(f"Allowed IPs: не более {MAX_ALLOWED_IPS} подсетей")
    return networks


def validate_keepalive(raw: Any) -> int:
    """Validate persistent-keepalive seconds (0/blank means "not set")."""
    if raw is None:
        return DEFAULT_WIREGUARD_KEEPALIVE
    text = str(raw).strip()
    if not text:
        return DEFAULT_WIREGUARD_KEEPALIVE
    if not re.match(r"^\d{1,5}$", text):
        raise NetworkValidationError(
            "Постоянный keepalive: ожидается число от 0 до 65535"
        )
    value = int(text)
    if value > 65535:
        raise NetworkValidationError(
            "Постоянный keepalive: ожидается число от 0 до 65535"
        )
    return value


def validate_pppoe_username(raw: Any) -> str:
    """Validate a PPPoE login (user@provider is fine, whitespace/quotes are not)."""
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise NetworkValidationError("PPPoE: имя пользователя не указано")
    if not _PPPOE_USERNAME_RE.match(text):
        raise NetworkValidationError(
            "PPPoE: имя пользователя содержит недопустимые символы"
        )
    return text


def validate_pppoe_service(raw: Any) -> str:
    """Validate an optional PPPoE service name ("" means "not set")."""
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return ""
    if not _PPPOE_SERVICE_RE.match(text):
        raise NetworkValidationError("PPPoE: недопустимое имя сервиса")
    return text


def validate_secret(raw: Any, field: str, *, max_len: int = 128) -> str:
    """Validate a credential that is never echoed: length + no control chars."""
    text = raw if isinstance(raw, str) else ""
    if not text:
        raise NetworkValidationError(f"{field}: значение не указано")
    if len(text) > max_len:
        raise NetworkValidationError(f"{field}: не длиннее {max_len} символов")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise NetworkValidationError(f"{field}: содержит недопустимые управляющие символы")
    return text


def validate_openvpn_config(raw: Any) -> str:
    """Validate a pasted .ovpn body: minimal client/remote directives required."""
    text = raw if isinstance(raw, str) else ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text.strip():
        raise NetworkValidationError("OpenVPN: конфигурация не указана")
    if len(text.encode("utf-8")) > MAX_OPENVPN_CONFIG_BYTES:
        raise NetworkValidationError(
            "OpenVPN: конфигурация слишком большая (не более "
            f"{MAX_OPENVPN_CONFIG_BYTES // 1024} КБ)"
        )
    if "\x00" in text:
        raise NetworkValidationError("OpenVPN: конфигурация содержит недопустимые символы")

    directives: List[List[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        directives.append(stripped.split())

    names = [tokens[0].lower() for tokens in directives]
    if "client" not in names:
        raise NetworkValidationError(
            "OpenVPN: конфигурация должна содержать директиву «client»"
        )
    remote = next((tokens for tokens in directives if tokens[0].lower() == "remote"), None)
    if remote is None:
        raise NetworkValidationError(
            "OpenVPN: конфигурация должна содержать директиву «remote хост [порт]»"
        )
    if len(remote) < 2:
        raise NetworkValidationError("OpenVPN: директива «remote» должна указывать хост")
    _validate_host(remote[1], "OpenVPN: remote-хост")
    if len(remote) > 2:
        _validate_port(remote[2], "OpenVPN: remote-порт")

    return text + "\n"


# --- Credential-bearing file writers ---------------------------------------


def _write_private_file(path: Path, text: str) -> None:
    """Create/replace ``path`` with PRIVATE_FILE_MODE (0600) and UTF-8 content."""
    data = text.encode("utf-8")
    descriptor = os.open(
        str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"не удалось записать {path.name}")
            view = view[written:]
    finally:
        os.close(descriptor)
    try:
        os.chmod(str(path), PRIVATE_FILE_MODE)
    except OSError:
        # Windows has no POSIX bits; the os.open mode above is authoritative.
        pass


def _wireguard_keyfile_text(
    connection_id: str,
    private_key: str,
    address: str,
    prefix: int,
    peer_public_key: str,
    endpoint: str,
    allowed_ips: List[str],
    keepalive: int,
) -> str:
    """Render a NetworkManager WireGuard keyfile (written with mode 0600)."""
    peer: List[str] = [
        f"[wireguard-peer.{peer_public_key}]",
        f"endpoint={endpoint}",
        f"allowed-ips={';'.join(allowed_ips)};",
    ]
    if keepalive:
        peer.append(f"persistent-keepalive={keepalive}")

    lines = [
        "[connection]",
        f"id={connection_id}",
        f"uuid={uuid.uuid4()}",
        "type=wireguard",
        "autoconnect=true",
        "",
        "[wireguard]",
        f"private-key={private_key}",
        "fwmark=0",
        "",
        *peer,
        "",
        "[ipv4]",
        "method=manual",
        f"address1={address}/{prefix}",
        "",
        "[ipv6]",
        "method=disabled",
        "",
    ]
    return "\n".join(lines)


# --- POST /api/network/* actions (return (status, payload), never raise) ----


def _network_error(status: int, message: str) -> Tuple[int, Dict[str, Any]]:
    return int(status), {"ok": False, "message": message}


def _network_action(action: Callable[[], Tuple[int, Dict[str, Any]]]) -> Tuple[int, Dict[str, Any]]:
    """Run one network action, converting every failure into a JSON response."""
    try:
        return action()
    except NetworkValidationError as exc:
        return _network_error(HTTPStatus.BAD_REQUEST, str(exc))
    except NetworkUnavailableError as exc:
        return _network_error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
    except Exception:
        # Never leak a traceback or a secret from an unexpected failure.
        return _network_error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Не удалось применить настройки сети: непредвиденная ошибка",
        )


def apply_network_ip(
    body: Any,
    run_cmd: CommandRunner = default_run_cmd,
) -> Tuple[int, Dict[str, Any]]:
    """Apply IPv4/DNS settings to the interface's active NM connection.

    Returns ``(status, payload)``: 200 on success, 400 on invalid input, 503 when
    NetworkManager is unavailable or fails.
    """

    def action() -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            raise NetworkValidationError("Некорректное тело запроса")

        interface = validate_interface_name(body.get("interface"))
        mode = str(body.get("mode") or "auto").strip().lower()
        if mode not in ("auto", "manual"):
            raise NetworkValidationError("Режим должен быть «auto» или «manual»")

        address = ""
        prefix = 0
        gateway = ""
        dns = validate_dns_list(body.get("dns"))
        if mode == "manual":
            address = validate_ipv4(body.get("address"), "IPv4-адрес")
            prefix = validate_prefix(body.get("prefix"), "Префикс")
            gateway = validate_gateway(body.get("gateway"))

        _require_nmcli()
        active = _nmcli_active_connections(run_cmd)
        connection = str(active.get(interface, {}).get("connection") or "")
        if not connection:
            raise NetworkUnavailableError(
                f"Для интерфейса {interface} нет активного подключения NetworkManager"
            )

        dns_value = ",".join(dns)
        if mode == "manual":
            mod_args = [
                "con",
                "mod",
                connection,
                "ipv4.method",
                "manual",
                "ipv4.addresses",
                f"{address}/{prefix}",
                "ipv4.gateway",
                gateway,
                "ipv4.dns",
                dns_value,
            ]
        else:
            mod_args = [
                "con",
                "mod",
                connection,
                "ipv4.method",
                "auto",
                "ipv4.addresses",
                "",
                "ipv4.gateway",
                "",
                "ipv4.dns",
                dns_value,
                "ipv4.ignore-auto-dns",
                "yes" if dns else "no",
            ]

        _run_nmcli(mod_args, 20.0, run_cmd, "изменить настройки IPv4")
        _run_nmcli(["con", "up", connection], 45.0, run_cmd, "применить настройки IPv4")

        summary = (
            f"{address}/{prefix}" if mode == "manual" else "автоматически (DHCP)"
        )
        return int(HTTPStatus.OK), {
            "ok": True,
            "message": f"Настройки IPv4 для {interface} применены: {summary}",
            "interface": interface,
            "mode": mode,
            "connection": connection,
        }

    return _network_action(action)


def apply_network_pppoe(
    body: Any,
    run_cmd: CommandRunner = default_run_cmd,
) -> Tuple[int, Dict[str, Any]]:
    """Create/replace the rhm-pppoe NetworkManager connection and bring it up."""

    def action() -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            raise NetworkValidationError("Некорректное тело запроса")

        username = validate_pppoe_username(body.get("username"))
        password = validate_secret(body.get("password"), "Пароль PPPoE")
        service = validate_pppoe_service(body.get("service"))

        _require_nmcli()
        if PPPOE_CONNECTION_NAME in _nmcli_connection_names(run_cmd):
            _run_nmcli(
                ["con", "delete", PPPOE_CONNECTION_NAME],
                20.0,
                run_cmd,
                "удалить старое PPPoE-подключение",
                secrets=(password,),
            )

        add_args = [
            "con",
            "add",
            "type",
            "pppoe",
            "con-name",
            PPPOE_CONNECTION_NAME,
            "ifname",
            "*",
            "username",
            username,
            "password",
            password,
        ]
        if service:
            # `con add` takes `<setting>.<property> value` pairs; the PPPoE
            # service lives in the pppoe setting (bare "service" is not an
            # nmcli alias and would be rejected on a real ISO).
            add_args.extend(["pppoe.service", service])
        _run_nmcli(
            add_args,
            30.0,
            run_cmd,
            "создать PPPoE-подключение",
            secrets=(password,),
        )
        _run_nmcli(
            ["con", "up", PPPOE_CONNECTION_NAME],
            60.0,
            run_cmd,
            "включить PPPoE-подключение",
            secrets=(password,),
        )

        return int(HTTPStatus.OK), {
            "ok": True,
            "message": "PPPoE-подключение rhm-pppoe создано и включено",
            "connection": PPPOE_CONNECTION_NAME,
            "redacted": True,
        }

    return _network_action(action)


def apply_network_wireguard(
    body: Any,
    run_cmd: CommandRunner = default_run_cmd,
    connections_dir: Optional[Path] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Write a 0600 NM WireGuard keyfile, reload NetworkManager and bring it up."""

    def action() -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            raise NetworkValidationError("Некорректное тело запроса")

        name = validate_connection_name(
            body.get("name"), "Имя WireGuard-подключения", max_len=32
        )
        private_key = validate_wireguard_key(
            body.get("private_key"), "Приватный ключ WireGuard"
        )
        peer_public_key = validate_wireguard_key(
            body.get("peer_public_key"), "Публичный ключ пира"
        )
        if private_key == peer_public_key:
            raise NetworkValidationError(
                "Публичный ключ пира не должен совпадать с приватным ключом"
            )
        address = validate_ipv4(body.get("address"), "Адрес WireGuard")
        prefix = validate_prefix(body.get("prefix"), "Префикс")
        endpoint = validate_endpoint(body.get("endpoint"))
        allowed_ips = validate_allowed_ips(body.get("allowed_ips"))
        keepalive = validate_keepalive(body.get("keepalive"))

        _require_nmcli()

        target_dir = Path(connections_dir) if connections_dir is not None else NM_CONNECTIONS_DIR
        connection = f"{WIREGUARD_CONNECTION_PREFIX}{name}"
        keyfile = target_dir / f"{connection}.nmconnection"
        keyfile_text = _wireguard_keyfile_text(
            connection,
            private_key,
            address,
            prefix,
            peer_public_key,
            endpoint,
            allowed_ips,
            keepalive,
        )

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise NetworkUnavailableError(
                f"Не удалось создать каталог NetworkManager: {_sanitize_detail(str(exc))}"
            )
        try:
            _write_private_file(keyfile, keyfile_text)
        except Exception as exc:
            raise NetworkUnavailableError(
                "Не удалось записать ключевой файл WireGuard: "
                f"{_sanitize_detail(str(exc))}"
            )

        try:
            _run_nmcli(
                ["con", "reload"],
                20.0,
                run_cmd,
                "перечитать настройки NetworkManager",
                secrets=(private_key,),
            )
            _run_nmcli(
                ["con", "up", connection],
                45.0,
                run_cmd,
                "включить WireGuard-подключение",
                secrets=(private_key,),
            )
        except NetworkUnavailableError:
            # Do not leave a half-applied auto-connect profile behind.
            try:
                keyfile.unlink()
            except OSError:
                pass
            raise

        return int(HTTPStatus.OK), {
            "ok": True,
            "message": f"WireGuard-подключение {connection} создано и включено",
            "connection": connection,
            "redacted": True,
        }

    return _network_action(action)


def apply_network_openvpn(
    body: Any,
    run_cmd: CommandRunner = default_run_cmd,
    temp_root: Optional[Path] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Import a pasted .ovpn via nmcli and bring the connection up.

    The config is written to a 0600 temporary file that is always removed in a
    ``finally`` block; its contents are never echoed or logged.
    """

    def action() -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            raise NetworkValidationError("Некорректное тело запроса")

        name = validate_connection_name(
            body.get("name"), "Имя OpenVPN-подключения", max_len=OPENVPN_CONNECTION_MAX_LEN
        )
        config = validate_openvpn_config(body.get("config"))

        _require_nmcli()

        try:
            temp_dir = Path(
                tempfile.mkdtemp(
                    prefix=OPENVPN_TEMP_PREFIX,
                    dir=str(temp_root) if temp_root is not None else None,
                )
            )
        except Exception as exc:
            raise NetworkUnavailableError(
                f"Не удалось создать временный файл OpenVPN: {_sanitize_detail(str(exc))}"
            )

        try:
            try:
                os.chmod(str(temp_dir), 0o700)
            except OSError:
                pass
            config_path = temp_dir / f"{name}.ovpn"
            try:
                _write_private_file(config_path, config)
            except Exception as exc:
                raise NetworkUnavailableError(
                    "Не удалось записать временный файл OpenVPN: "
                    f"{_sanitize_detail(str(exc))}"
                )
            _run_nmcli(
                ["connection", "import", "type", "openvpn", "file", str(config_path)],
                60.0,
                run_cmd,
                "импортировать конфигурацию OpenVPN",
                replacements=((str(config_path), "<config>"), (str(temp_dir), "<tmp>")),
            )
            _run_nmcli(
                ["con", "up", name],
                60.0,
                run_cmd,
                "включить OpenVPN-подключение",
                replacements=((str(config_path), "<config>"), (str(temp_dir), "<tmp>")),
            )
        finally:
            shutil.rmtree(str(temp_dir), ignore_errors=True)

        return int(HTTPStatus.OK), {
            "ok": True,
            "message": f"OpenVPN-подключение {name} импортировано и включено",
            "connection": name,
            "redacted": True,
        }

    return _network_action(action)


# --- AmneziaWG (obfuscated WireGuard) and subscription import ---------------
#
# AmneziaWG keeps the WireGuard .conf format and adds the Jc/Jmin/Jmax/S1/S2/
# H1-H4 obfuscation keys to [Interface]; awg-quick activates such a file exactly
# like wg-quick. This section therefore mirrors the WireGuard action above:
# validate every field first (400), write the config with PRIVATE_FILE_MODE
# through a temp file + os.replace (atomic, 0600), then run
# `awg-quick up <path>` as an argv list (never shell=True). Missing awg-quick,
# missing root or a failing command is a 503; no response, message or diagnostic
# ever contains the PrivateKey/PresharedKey values.
#
# AMNEZIA_CONFIG_DIR is module-level on purpose: the unit tests point it at
# tmp_path and a real LiveUSB uses the default /etc/amnezia/amneziawg.

AMNEZIA_CONFIG_DIR = Path("/etc/amnezia/amneziawg")
AMNEZIA_CONFIG_SUFFIX = ".conf"
AMNEZIA_MAX_NAME_LEN = 32
AMNEZIA_MAX_CONFIG_BYTES = 64 * 1024
AMNEZIA_MAX_LINE_CHARS = 4096
AMNEZIA_REQUIRED_INTERFACE_KEYS = ("PrivateKey", "Address")
AMNEZIA_REQUIRED_PEER_KEYS = ("PublicKey", "Endpoint")
AMNEZIA_OPTIONAL_NUMERIC_KEYS = (
    "Jc",
    "Jmin",
    "Jmax",
    "S1",
    "S2",
    "H1",
    "H2",
    "H3",
    "H4",
)
AMNEZIA_SECRET_FIELDS = ("PrivateKey", "PresharedKey")
# awg-quick runs PreUp/PostUp/PreDown/PostDown through `sh -c` as root, so a
# pasted config must never be able to smuggle a hook in.
AMNEZIA_FORBIDDEN_KEYS = ("PreUp", "PostUp", "PreDown", "PostDown")
_AMNEZIA_KEY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,31}$")
_AMNEZIA_SECTION_HEADER_RE = re.compile(r"^\s*\[\s*interface\s*\]\s*$", re.IGNORECASE)
_AMNEZIA_SECRET_LINE_RE = re.compile(
    r"(?im)^(\s*)(privatekey|presharedkey)(\s*)=.*$"
)
_AMNEZIA_SECRET_VALUE_KEYS = tuple(name.lower() for name in AMNEZIA_SECRET_FIELDS)

SUBSCRIPTION_MAX_URL_LEN = 2048
SUBSCRIPTION_MAX_BYTES = 1024 * 1024
SUBSCRIPTION_TIMEOUT_SECONDS = 15.0
SUBSCRIPTION_USER_AGENT = "rhm-kiosk/1.0 (AmneziaWG subscription import)"
SUBSCRIPTION_QR_MAX_SVG_BYTES = 512 * 1024


def _is_root() -> bool:
    """True when the process runs as root (non-POSIX hosts cannot be checked)."""
    get_euid = getattr(os, "geteuid", None)
    if get_euid is None:
        return True
    try:
        return int(get_euid()) == 0
    except Exception:
        return True


def _awg_quick_path() -> str:
    """Return the resolved awg-quick path, or "" when it is not installed."""
    try:
        return str(shutil.which("awg-quick") or "")
    except Exception:
        return ""


def _require_awg_quick() -> str:
    """Raise NetworkUnavailableError unless awg-quick and root are available."""
    path = _awg_quick_path()
    if not path:
        raise NetworkUnavailableError(
            "AmneziaWG (awg-quick) не установлен или недоступен в этой системе"
        )
    if not _is_root():
        raise NetworkUnavailableError(
            "Для запуска AmneziaWG нужны права root: перезапустите киоск через sudo"
        )
    return path


def _redact_secret_assignment(match: "re.Match[str]") -> str:
    """Replace a `PrivateKey = ...` line with a marker that keeps the key name."""
    return f"{match.group(1)}{match.group(2)}{match.group(3)}= ***"


def _scrub_amnezia_secrets(
    value: Any, secrets: Tuple[str, ...] = ()
) -> str:
    """One-line diagnostic text with every AmneziaWG secret removed."""
    text = _AMNEZIA_SECRET_LINE_RE.sub(_redact_secret_assignment, str(value or ""))
    return _sanitize_detail(text, tuple(secrets))


def _amnezia_secret_values(config: str) -> Tuple[str, ...]:
    """Collect the PrivateKey/PresharedKey values so messages can redact them."""
    values: List[str] = []
    for raw_line in str(config or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() in _AMNEZIA_SECRET_VALUE_KEYS:
            secret = value.strip()
            if secret and secret not in values:
                values.append(secret)
    return tuple(values)


def _section_values(entries: List[Tuple[str, str]], key: str) -> List[str]:
    """All values of one INI key (WireGuard allows repeating some keys)."""
    lowered = key.lower()
    return [value for name, value in entries if name.lower() == lowered]


def _section_first_value(entries: List[Tuple[str, str]], key: str) -> str:
    values = _section_values(entries, key)
    return values[0] if values else ""


def _require_section_keys(
    entries: List[Tuple[str, str]], required: Tuple[str, ...], section: str
) -> None:
    """Raise 400 when a required key is missing from a section."""
    present = {name.lower() for name, _value in entries}
    for key in required:
        if key.lower() not in present:
            raise NetworkValidationError(
                f"Конфигурация AmneziaWG: в [{section}] отсутствует обязательный параметр {key}"
            )


def _validate_amnezia_key(value: Any, field: str) -> None:
    """Validate the shape of a WireGuard-style key value (never echoed)."""
    text = str(value or "").strip()
    if not text:
        raise NetworkValidationError(f"Конфигурация AmneziaWG: {field} не указан")
    if len(text) > 128 or any(char.isspace() for char in text):
        raise NetworkValidationError(
            f"Конфигурация AmneziaWG: {field} имеет недопустимый формат"
        )


def _validate_amnezia_addresses(value: Any) -> None:
    """Validate the Address list (one or more IP/prefix entries)."""
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    if not parts:
        raise NetworkValidationError(
            "Конфигурация AmneziaWG: Address должен содержать IP-адрес с префиксом"
        )
    for part in parts:
        try:
            ipaddress.ip_interface(part)
        except ValueError:
            raise NetworkValidationError(
                "Конфигурация AmneziaWG: Address "
                f"«{_sanitize_detail(part, limit=48)}» не является IP-адресом с префиксом"
            )


def _validate_amnezia_dns(value: Any) -> None:
    """Validate an optional DNS list: IP addresses or plain host names."""
    for part in re.split(r"[,\s]+", str(value or "")):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
            continue
        except ValueError:
            pass
        if not _HOSTNAME_RE.match(candidate):
            raise NetworkValidationError(
                "Конфигурация AmneziaWG: DNS "
                f"«{_sanitize_detail(candidate, limit=48)}» не является IP-адресом или доменом"
            )


def _validate_amnezia_allowed_ips(value: Any) -> None:
    """Validate one AllowedIPs value (comma/space separated CIDR networks)."""
    parts = [part for part in re.split(r"[,\s]+", str(value or "")) if part]
    if not parts:
        raise NetworkValidationError(
            "Конфигурация AmneziaWG: AllowedIPs должен содержать подсеть в формате CIDR"
        )
    for part in parts:
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError:
            raise NetworkValidationError(
                "Конфигурация AmneziaWG: AllowedIPs "
                f"«{_sanitize_detail(part, limit=48)}» не является подсетью в формате CIDR"
            )


def _parse_amnezia_ini(text: str) -> Dict[str, Any]:
    """Parse a WireGuard/AmneziaWG INI into [Interface] and [Peer] entry lists."""
    sections: List[Tuple[str, List[Tuple[str, str]]]] = []
    current: Optional[Tuple[str, List[Tuple[str, str]]]] = None
    forbidden = {name.lower() for name in AMNEZIA_FORBIDDEN_KEYS}

    for raw_line in text.split("\n"):
        if len(raw_line) > AMNEZIA_MAX_LINE_CHARS:
            raise NetworkValidationError(
                "Конфигурация AmneziaWG: строка длиннее "
                f"{AMNEZIA_MAX_LINE_CHARS} символов"
            )
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if section.lower() not in ("interface", "peer"):
                raise NetworkValidationError(
                    "Конфигурация AmneziaWG: неизвестная секция "
                    f"«{_sanitize_detail(section, limit=32)}»"
                )
            current = (section.lower(), [])
            sections.append(current)
            continue

        if "=" not in line:
            raise NetworkValidationError(
                "Конфигурация AmneziaWG: ожидается строка «Параметр = Значение»"
            )
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if current is None:
            raise NetworkValidationError(
                "Конфигурация AmneziaWG: параметр указан вне секции [Interface]/[Peer]"
            )
        if not _AMNEZIA_KEY_NAME_RE.match(key):
            raise NetworkValidationError(
                "Конфигурация AmneziaWG: недопустимое имя параметра "
                f"«{_sanitize_detail(key, limit=32)}»"
            )
        if key.lower() in forbidden:
            raise NetworkValidationError(
                f"Конфигурация AmneziaWG: параметр {key} запрещён "
                "(хуки awg-quick выполняются от root)"
            )
        if not value:
            raise NetworkValidationError(
                f"Конфигурация AmneziaWG: параметр {key} не заполнен"
            )
        current[1].append((key, value))

    interface_entries = [entries for name, entries in sections if name == "interface"]
    peer_entries = [entries for name, entries in sections if name == "peer"]
    if len(interface_entries) != 1:
        raise NetworkValidationError(
            "Конфигурация AmneziaWG: требуется ровно одна секция [Interface]"
        )
    if not peer_entries:
        raise NetworkValidationError(
            "Конфигурация AmneziaWG: требуется хотя бы одна секция [Peer]"
        )
    return {"interface": interface_entries[0], "peers": peer_entries}


def validate_amnezia_config(raw: Any) -> str:
    """Validate an AmneziaWG .conf body and return the text written to disk.

    Rejects control characters, lines longer than AMNEZIA_MAX_LINE_CHARS, a
    body larger than AMNEZIA_MAX_CONFIG_BYTES, unknown sections, awg-quick
    root hooks and malformed values. The original key order is preserved so
    newer AmneziaWG parameters survive untouched.
    """
    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        raise NetworkValidationError("Конфигурация AmneziaWG: не указана")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for char in text:
        if char in ("\n", "\t"):
            continue
        if ord(char) < 32 or ord(char) == 127:
            raise NetworkValidationError(
                "Конфигурация AmneziaWG: содержит недопустимые управляющие символы"
            )
    if len(text.encode("utf-8")) > AMNEZIA_MAX_CONFIG_BYTES:
        raise NetworkValidationError(
            "Конфигурация AmneziaWG: больше "
            f"{AMNEZIA_MAX_CONFIG_BYTES // 1024} КБ"
        )

    parsed = _parse_amnezia_ini(text)
    interface = parsed["interface"]
    peers = parsed["peers"]

    _require_section_keys(interface, AMNEZIA_REQUIRED_INTERFACE_KEYS, "Interface")
    for peer in peers:
        _require_section_keys(peer, AMNEZIA_REQUIRED_PEER_KEYS, "Peer")

    _validate_amnezia_key(
        _section_first_value(interface, "PrivateKey"), "PrivateKey"
    )
    _validate_amnezia_addresses(_section_first_value(interface, "Address"))
    _validate_amnezia_dns(_section_first_value(interface, "DNS"))
    for key in AMNEZIA_OPTIONAL_NUMERIC_KEYS:
        for value in _section_values(interface, key):
            if not re.match(r"^\d{1,10}$", value):
                raise NetworkValidationError(
                    f"Конфигурация AmneziaWG: {key} должен быть целым числом"
                )
    for key in ("MTU", "PersistentKeepalive"):
        for value in _section_values(interface, key):
            if not re.match(r"^\d{1,5}$", value):
                raise NetworkValidationError(
                    f"Конфигурация AmneziaWG: {key} должен быть числом"
                )

    for peer in peers:
        _validate_amnezia_key(_section_first_value(peer, "PublicKey"), "PublicKey")
        endpoint = _section_first_value(peer, "Endpoint")
        try:
            validate_endpoint(endpoint)
        except NetworkValidationError as exc:
            raise NetworkValidationError(f"Конфигурация AmneziaWG: {exc}")
        for value in _section_values(peer, "PresharedKey"):
            _validate_amnezia_key(value, "PresharedKey")
        for value in _section_values(peer, "AllowedIPs"):
            _validate_amnezia_allowed_ips(value)
        for value in _section_values(peer, "PersistentKeepalive"):
            if not re.match(r"^\d{1,5}$", value):
                raise NetworkValidationError(
                    "Конфигурация AmneziaWG: PersistentKeepalive должен быть числом"
                )

    normalized = [line.rstrip() for line in text.split("\n")]
    while normalized and not normalized[0]:
        normalized.pop(0)
    while normalized and not normalized[-1]:
        normalized.pop()
    return "\n".join(normalized) + "\n"


def _write_private_config_atomic(path: Path, text: str) -> None:
    """Write ``path`` with PRIVATE_FILE_MODE atomically (temp file + replace)."""
    data = text.encode("utf-8")
    temp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    # Without O_BINARY a Windows os.open would rewrite "\n" as "\r\n" and make
    # the config unreadable as the exact text that was validated.
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(str(temp_path), flags, PRIVATE_FILE_MODE)
    try:
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError(f"не удалось записать {path.name}")
                view = view[written:]
            try:
                os.fsync(descriptor)
            except OSError:
                pass
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.chmod(str(temp_path), PRIVATE_FILE_MODE)
        except OSError:
            # Windows has no POSIX bits; the os.open mode above is authoritative.
            pass
        os.replace(str(temp_path), str(path))
    except BaseException:
        try:
            os.unlink(str(temp_path))
        except OSError:
            pass
        raise


def _run_awg_quick(
    verb: str,
    config_path: Path,
    run_cmd: CommandRunner,
    *,
    secrets: Tuple[str, ...] = (),
) -> str:
    """Run `awg-quick <verb> <path>`; raise NetworkUnavailableError on failure."""
    executable = _awg_quick_path() or "awg-quick"
    argv = [executable, verb, str(config_path)]
    try:
        rc, out, err = run_cmd(argv, 30.0)
    except Exception as exc:
        raise NetworkUnavailableError(
            f"Не удалось выполнить awg-quick {verb}: "
            f"{_scrub_amnezia_secrets(exc, secrets)}"
        )
    if rc == 0:
        return _scrub_amnezia_secrets(out, secrets)
    if rc == 127:
        raise NetworkUnavailableError("AmneziaWG (awg-quick) не найден в системе")
    if rc == 124:
        raise NetworkUnavailableError(f"awg-quick не ответил вовремя: {verb}")
    detail = _scrub_amnezia_secrets(err or out, secrets)
    message = f"awg-quick не смог выполнить «{verb}»"
    if detail:
        message = f"{message} — {detail}"
    raise NetworkUnavailableError(message)


def _write_and_start_amnezia(
    name: str, config: str, run_cmd: CommandRunner
) -> Tuple[Path, str]:
    """Write <name>.conf 0600 and bring it up; remove it again on failure."""
    _require_awg_quick()

    target_dir = Path(AMNEZIA_CONFIG_DIR)
    path = target_dir / f"{name}{AMNEZIA_CONFIG_SUFFIX}"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise NetworkUnavailableError(
            "Не удалось создать каталог AmneziaWG: "
            f"{_sanitize_detail(str(exc))}"
        )
    try:
        _write_private_config_atomic(path, config)
    except Exception as exc:
        raise NetworkUnavailableError(
            "Не удалось записать конфигурацию AmneziaWG: "
            f"{_sanitize_detail(str(exc))}"
        )
    try:
        output = _run_awg_quick(
            "up", path, run_cmd, secrets=_amnezia_secret_values(config)
        )
    except NetworkUnavailableError:
        # Do not leave a half-applied config behind for the next boot.
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path, output


def list_amnezia_configs(directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List every ``*.conf`` in the AmneziaWG directory (never raises)."""
    base = Path(directory) if directory is not None else Path(AMNEZIA_CONFIG_DIR)
    try:
        entries = sorted(
            base.glob(f"*{AMNEZIA_CONFIG_SUFFIX}"),
            key=lambda item: item.name.lower(),
        )
    except Exception:
        return []

    files: List[Dict[str, Any]] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            info = entry.stat()
        except OSError:
            continue
        files.append(
            {
                "name": entry.name[: -len(AMNEZIA_CONFIG_SUFFIX)],
                "path": str(entry),
                "size": int(info.st_size),
                "mtime": int(info.st_mtime),
            }
        )
    return files


def _parse_awg_show_interfaces(output: str) -> List[str]:
    """Extract interface names from `awg show` / `wg show`-style output."""
    names: List[str] = []
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("interface:"):
            continue
        name = line.split(":", 1)[1].strip()
        if re.match(r"^[A-Za-z0-9_.:-]{1,32}$", name) and name not in names:
            names.append(name)
    return names


def amnezia_status(run_cmd: CommandRunner = default_run_cmd) -> Dict[str, Any]:
    """Best-effort `awg show` status for GET /api/network/amnezia (never raises)."""
    result: Dict[str, Any] = {
        "available": bool(_awg_quick_path()),
        "ok": False,
        "output": "",
        "interfaces": [],
        "message": "",
    }
    if not result["available"]:
        result["message"] = "AmneziaWG (awg-quick) не установлен в этой системе"
        return result

    try:
        rc, out, err = run_cmd(["awg", "show"], 5.0)
    except Exception as exc:
        result["message"] = (
            f"awg show недоступен: {_scrub_amnezia_secrets(exc)}"
        )
        return result

    if rc == 0:
        result["ok"] = True
        result["output"] = _scrub_amnezia_secrets(out)
        result["interfaces"] = _parse_awg_show_interfaces(out)
        return result

    detail = _scrub_amnezia_secrets(err or out)
    result["message"] = "awg show недоступен" + (f": {detail}" if detail else "")
    return result


def amnezia_overview(run_cmd: CommandRunner = default_run_cmd) -> Dict[str, Any]:
    """Read-only AmneziaWG picture for GET /api/network/amnezia (never raises)."""
    try:
        return {
            "ok": True,
            "awg_available": bool(_awg_quick_path()),
            "config_dir": str(AMNEZIA_CONFIG_DIR),
            "files": list_amnezia_configs(),
            "status": amnezia_status(run_cmd),
            "redacted": True,
            "timestamp": int(time.time()),
        }
    except Exception:
        return {
            "ok": True,
            "awg_available": False,
            "config_dir": str(AMNEZIA_CONFIG_DIR),
            "files": [],
            "status": {
                "available": False,
                "ok": False,
                "output": "",
                "interfaces": [],
                "message": "AmneziaWG недоступен в этой системе",
            },
            "redacted": True,
            "timestamp": int(time.time()),
        }


# --- Subscription import: HTTPS-only fetch with an SSRF guard ---------------


def _resolve_host_addresses(host: str, port: int) -> List[str]:
    """Resolve ``host`` to its IP addresses; 400 when the name cannot resolve."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError, ValueError):
        raise NetworkValidationError(
            "URL подписки: не удалось разрешить имя хоста"
        )
    addresses: List[str] = []
    for info in infos or []:
        try:
            sockaddr = info[4]
        except (IndexError, TypeError):
            continue
        if isinstance(sockaddr, tuple) and sockaddr:
            address = str(sockaddr[0])
            if address and address not in addresses:
                addresses.append(address)
    if not addresses:
        raise NetworkValidationError(
            "URL подписки: не удалось разрешить имя хоста"
        )
    return addresses


def _is_forbidden_address(address: str) -> bool:
    """True for loopback/private/link-local/reserved/multicast/subnet addresses."""
    try:
        ip = ipaddress.ip_address(str(address))
    except ValueError:
        return True

    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _is_forbidden_address(str(mapped))

    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or getattr(ip, "is_reserved", False)
        or getattr(ip, "is_site_local", False)
    ):
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        # CGNAT 100.64.0.0/10 is not covered by is_private on older Pythons.
        if ip in ipaddress.ip_network("100.64.0.0/10"):
            return True
    return False


def validate_subscription_url(raw: Any) -> str:
    """Validate an HTTPS subscription URL and reject non-public destinations.

    The host is resolved here, so a loopback/private/link-local/reserved answer
    is a 400 before any socket is opened (the redirect handler repeats the same
    check for each hop).
    """
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise NetworkValidationError("URL подписки: не указан")
    if len(text) > SUBSCRIPTION_MAX_URL_LEN:
        raise NetworkValidationError(
            f"URL подписки: не длиннее {SUBSCRIPTION_MAX_URL_LEN} символов"
        )
    for char in text:
        if ord(char) < 33 or ord(char) == 127:
            raise NetworkValidationError(
                "URL подписки: содержит недопустимые пробелы или управляющие символы"
            )

    try:
        parsed = urlparse(text)
        port = parsed.port
    except ValueError:
        raise NetworkValidationError("URL подписки: некорректный порт или адрес")
    if parsed.scheme.lower() != "https":
        raise NetworkValidationError("URL подписки: допустим только HTTPS")
    host = parsed.hostname
    if not host:
        raise NetworkValidationError("URL подписки: не указан хост")
    if parsed.username or parsed.password:
        raise NetworkValidationError(
            "URL подписки: не должен содержать логин или пароль"
        )
    effective_port = int(port) if port is not None else 443
    if effective_port < 1 or effective_port > 65535:
        raise NetworkValidationError("URL подписки: некорректный порт")

    for address in _resolve_host_addresses(host, effective_port):
        if _is_forbidden_address(address):
            raise NetworkValidationError(
                "URL подписки: адрес указывает на локальную, частную или "
                "служебную сеть — загрузка запрещена"
            )
    return text


class _SubscriptionRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-apply the HTTPS/SSRF guard to every redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        validate_subscription_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_subscription_bytes(url: str) -> bytes:
    """HTTPS GET with a 15 s timeout and a hard 1 MiB cap; failures are 503."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": SUBSCRIPTION_USER_AGENT,
            "Accept": "*/*",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_SubscriptionRedirectHandler())
    try:
        with opener.open(request, timeout=SUBSCRIPTION_TIMEOUT_SECONDS) as response:
            data = response.read(SUBSCRIPTION_MAX_BYTES + 1)
    except NetworkValidationError:
        raise
    except Exception as exc:
        raise NetworkUnavailableError(
            "Не удалось загрузить подписку: "
            f"{_sanitize_detail(str(exc))}"
        )
    if not data:
        raise NetworkUnavailableError("Подписка пуста: сервер вернул 0 байт")
    if len(data) > SUBSCRIPTION_MAX_BYTES:
        raise NetworkUnavailableError(
            "Подписка больше 1 МБ — загрузка отклонена"
        )
    return data


def _looks_like_amnezia_config(text: str) -> bool:
    """True when the first meaningful line is a [Interface] header."""
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        return bool(_AMNEZIA_SECTION_HEADER_RE.match(line))
    return False


def _decode_subscription_base64(text: str) -> Optional[str]:
    """Decode base64/base64url (padded or not); None when it is not base64."""
    compact = "".join(str(text or "").split())
    if not compact or len(compact) > SUBSCRIPTION_MAX_BYTES * 2:
        return None
    candidate = compact.replace("-", "+").replace("_", "/").rstrip("=")
    remainder = len(candidate) % 4
    if remainder == 1:
        return None
    try:
        data = base64.b64decode(
            candidate + "=" * ((4 - remainder) % 4), validate=True
        )
    except (ValueError, TypeError):
        return None
    if not data or len(data) > SUBSCRIPTION_MAX_BYTES:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def decode_subscription_config(raw: Any) -> Tuple[str, str]:
    """Return (validated config, "plain"|"base64") from a subscription body."""
    if isinstance(raw, str):
        payload = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray)):
        payload = bytes(raw)
    else:
        raise NetworkValidationError("Подписка: некорректное содержимое ответа")
    if len(payload) > SUBSCRIPTION_MAX_BYTES:
        raise NetworkUnavailableError("Подписка больше 1 МБ — загрузка отклонена")

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise NetworkValidationError(
            "Подписка: содержимое не является текстом UTF-8"
        )
    stripped = text.strip()
    if not stripped:
        raise NetworkValidationError("Подписка: сервер вернул пустой ответ")

    if _looks_like_amnezia_config(stripped):
        return validate_amnezia_config(stripped), "plain"

    decoded = _decode_subscription_base64(stripped)
    if decoded is not None and _looks_like_amnezia_config(decoded):
        return validate_amnezia_config(decoded), "base64"

    raise NetworkValidationError(
        "Подписка: неподдерживаемый формат — ожидается AmneziaWG .conf "
        "([Interface]) или base64/base64url от него"
    )


def _derive_amnezia_name(host: str) -> str:
    """Build a safe file stem from a subscription host name."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "-", str(host or "")).strip("-._")
    cleaned = cleaned[:AMNEZIA_MAX_NAME_LEN].strip("-._")
    if not cleaned or not re.match(r"^[A-Za-z0-9]", cleaned):
        return "amnezia"
    return cleaned


def _read_text_quietly(path: Path) -> str:
    """Read a config for secret extraction; "" when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --- POST /api/network/amnezia, /down and /subscription actions -------------


def apply_network_amnezia(
    body: Any,
    run_cmd: CommandRunner = default_run_cmd,
) -> Tuple[int, Dict[str, Any]]:
    """Write an AmneziaWG .conf 0600 and bring it up with `awg-quick up`."""

    def action() -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            raise NetworkValidationError("Некорректное тело запроса")

        name = validate_connection_name(
            body.get("name"), "Имя AmneziaWG-подключения", max_len=AMNEZIA_MAX_NAME_LEN
        )
        config = validate_amnezia_config(body.get("config"))
        path, output = _write_and_start_amnezia(name, config, run_cmd)

        return int(HTTPStatus.OK), {
            "ok": True,
            "name": name,
            "path": str(path),
            "output": output,
            "redacted": True,
            "message": f"AmneziaWG-подключение {name} поднято",
        }

    return _network_action(action)


def apply_network_amnezia_down(
    body: Any,
    run_cmd: CommandRunner = default_run_cmd,
) -> Tuple[int, Dict[str, Any]]:
    """Bring an existing AmneziaWG config down with `awg-quick down`."""

    def action() -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            raise NetworkValidationError("Некорректное тело запроса")

        name = validate_connection_name(
            body.get("name"), "Имя AmneziaWG-подключения", max_len=AMNEZIA_MAX_NAME_LEN
        )
        _require_awg_quick()

        path = Path(AMNEZIA_CONFIG_DIR) / f"{name}{AMNEZIA_CONFIG_SUFFIX}"
        if not path.is_file():
            raise NetworkValidationError(
                f"AmneziaWG: конфигурация «{name}» не найдена"
            )

        output = _run_awg_quick(
            "down",
            path,
            run_cmd,
            secrets=_amnezia_secret_values(_read_text_quietly(path)),
        )
        return int(HTTPStatus.OK), {
            "ok": True,
            "name": name,
            "path": str(path),
            "output": output,
            "redacted": True,
            "message": f"AmneziaWG-подключение {name} остановлено",
        }

    return _network_action(action)


def apply_network_amnezia_subscription(
    body: Any,
    run_cmd: CommandRunner = default_run_cmd,
) -> Tuple[int, Dict[str, Any]]:
    """Fetch an HTTPS subscription, decode it and bring the config up."""

    def action() -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict):
            raise NetworkValidationError("Некорректное тело запроса")

        url = validate_subscription_url(body.get("url"))
        host = str(urlparse(url).hostname or "").lower()

        raw_name = body.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            name = validate_connection_name(
                raw_name,
                "Имя AmneziaWG-подключения",
                max_len=AMNEZIA_MAX_NAME_LEN,
            )
        else:
            name = _derive_amnezia_name(host)

        # Fail fast before any outbound request when awg-quick/root is missing.
        _require_awg_quick()

        raw = _fetch_subscription_bytes(url)
        if len(raw) > SUBSCRIPTION_MAX_BYTES:
            raise NetworkUnavailableError(
                "Подписка больше 1 МБ — загрузка отклонена"
            )
        config, source_format = decode_subscription_config(raw)
        path, output = _write_and_start_amnezia(name, config, run_cmd)

        return int(HTTPStatus.OK), {
            "ok": True,
            "name": name,
            "source": "subscription",
            "format": source_format,
            "host": host,
            "path": str(path),
            "output": output,
            "redacted": True,
            "message": (
                f"Подписка загружена: AmneziaWG-подключение {name} поднято"
            ),
        }

    return _network_action(action)


def render_subscription_qr_svg(
    url: str,
    run_cmd: CommandRunner = default_run_cmd,
) -> str:
    """Render an SVG QR of an HTTPS subscription URL; 400/503 on failure."""
    validated = validate_subscription_url(url)
    try:
        rc, out, err = run_cmd(
            ["qrencode", "-t", "SVG", "-m", "2", "-o", "-", "--", validated],
            10.0,
        )
    except Exception as exc:
        raise NetworkUnavailableError(
            f"Не удалось построить QR-код: {_sanitize_detail(str(exc))}"
        )
    if rc == 127:
        raise NetworkUnavailableError("qrencode не установлен в этой системе")
    if rc == 124:
        raise NetworkUnavailableError("qrencode не ответил вовремя")
    if rc != 0 or "<svg" not in str(out or "").lower():
        detail = _sanitize_detail(err or out, limit=120)
        message = "qrencode не смог построить QR-код для этой ссылки"
        if detail:
            message = f"{message} — {detail}"
        raise NetworkUnavailableError(message)
    if len(out.encode("utf-8")) > SUBSCRIPTION_QR_MAX_SVG_BYTES:
        raise NetworkUnavailableError("QR-код получился слишком большим")
    return out


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
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
        )
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _validate_request_security(self, *, require_json: bool = False) -> bool:
        port = self.server.server_port
        allowed_hosts = {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        }
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in allowed_hosts:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Forbidden"})
            return False

        sec_fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if sec_fetch_site not in ("", "same-origin", "none"):
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Forbidden"})
            return False

        origin = (self.headers.get("Origin") or "").strip().lower()
        if origin:
            allowed_origins = {
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
                f"http://[::1]:{port}",
            }
            if origin not in allowed_origins:
                self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Forbidden"})
                return False

        if require_json:
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype != "application/json":
                self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Forbidden"})
                return False

        return True

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
        except Exception:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Internal server error"})
            return
        self._send_bytes(HTTPStatus.OK, data, ctype)

    def do_GET(self) -> None:
        if not self._validate_request_security(require_json=False):
            return
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
            if path == "/api/apps":
                self._handle_apps()
                return
            if path == "/api/i18n":
                self._handle_i18n()
                return
            if path == "/api/network":
                self._handle_network()
                return
            if path == "/api/network/amnezia":
                self._handle_network_amnezia()
                return
            if path == "/api/network/subscription/qr":
                self._handle_network_subscription_qr()
                return
            self._serve_static(path)
        except Exception:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Internal server error"},
            )

    def do_POST(self) -> None:
        if not self._validate_request_security(require_json=True):
            return
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/wifi/connect":
                self._handle_wifi_connect()
                return
            if path == "/api/session/regenerate":
                self._handle_session_regenerate()
                return
            if path == "/api/terminal":
                self._handle_terminal()
                return
            if path == "/api/apps/launch":
                self._handle_app_launch()
                return
            if path == "/api/network/ip":
                self._handle_network_ip()
                return
            if path == "/api/network/pppoe":
                self._handle_network_pppoe()
                return
            if path == "/api/network/wireguard":
                self._handle_network_wireguard()
                return
            if path == "/api/network/openvpn":
                self._handle_network_openvpn()
                return
            if path == "/api/network/amnezia":
                self._handle_network_amnezia_up()
                return
            if path == "/api/network/amnezia/down":
                self._handle_network_amnezia_down()
                return
            if path == "/api/network/subscription":
                self._handle_network_subscription()
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        except Exception:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Internal server error"},
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
        session = dict(read_session_state(self.server.state_dir))
        if not internet.get("reachable"):
            session["ready"] = False
            session["tunnel_status"] = "offline"
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
        if result.get("ok"):
            _write_tunnel_restart_trigger(self.server.state_dir)
        self._send_json(HTTPStatus.OK, result)

    def _handle_session_regenerate(self) -> None:
        result = trigger_session_regenerate(
            state_dir=self.server.state_dir,
            run_cmd=self.server.run_cmd,
        )
        self._send_json(HTTPStatus.OK, result)

    def _handle_terminal(self) -> None:
        """Open the local terminal escape advertised by the kiosk UI.

        The request body is optional and ignored; a malformed JSON body is
        refused with 400 before anything is spawned. A successful launch is
        200, and 503 with a Russian explanation is returned otherwise.
        """
        try:
            _body, err = self._read_json_body()
        except Exception:
            _body, err = None, "Некорректный JSON"
        if err is not None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "message": err or "Некорректный JSON"},
            )
            return
        try:
            result = launch_local_terminal()
        except Exception as exc:  # defence in depth: the helper should not raise
            result = {"ok": False, "message": f"Не удалось открыть терминал: {exc}"}
        if result.get("ok"):
            self._send_json(HTTPStatus.OK, result)
            return
        self._send_json(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "ok": False,
                "message": result.get("message") or "Терминал недоступен",
            },
        )

    def _handle_i18n(self) -> None:
        """Serve the 7-language localization dictionary (GET /api/i18n)."""
        try:
            from core.liveusb_i18n import SUPPORTED_LANGUAGES, get_all_locales

            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "languages": list(SUPPORTED_LANGUAGES),
                    "translations": get_all_locales(),
                },
            )
        except Exception:
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "languages": ["ru"], "translations": {}},
            )

    def _handle_apps(self) -> None:
        """List the allowlisted kiosk applications (GET /api/apps)."""
        try:
            apps = list_local_apps()
        except Exception:
            # The catalogue helper already tolerates a missing shutil.which;
            # this guard only keeps a broken host from 500-ing the UI poll.
            apps = []
        self._send_json(HTTPStatus.OK, {"ok": True, "apps": apps})

    def _handle_app_launch(self) -> None:
        """Launch one allowlisted application (POST /api/apps/launch).

        Only ``{"id": "<allowlisted id>"}`` is read from the body: any command,
        binary, or path the request might carry is ignored. A successful spawn
        is 200, an unknown id 400, and an unavailable launcher or failed spawn
        503 with a Russian explanation.
        """
        body, err = self._read_json_body()
        if err is not None or body is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "message": err or "Некорректный JSON"},
            )
            return

        raw_id = body.get("id")
        app_id = raw_id.strip() if isinstance(raw_id, str) else ""
        if not app_id:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "message": "Не указано приложение"},
            )
            return

        try:
            result = launch_local_app(app_id)
        except Exception as exc:  # defence in depth: the helper should not raise
            result = {
                "ok": False,
                "error": "unavailable",
                "message": f"Не удалось запустить приложение: {exc}",
            }

        if result.get("error") == "unknown_app":
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "message": result.get("message") or "Неизвестное приложение"},
            )
            return
        if result.get("ok"):
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "pid": result.get("pid"),
                    "command": result.get("command") or [],
                },
            )
            return
        self._send_json(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "ok": False,
                "message": result.get("message") or "Приложение недоступно",
            },
        )

    def _handle_network(self) -> None:
        """Read-only network picture for the «Текущие» tab (GET /api/network).

        Only ``node_id``/``link_key`` are published from the session state; the
        device token never reaches the browser. The helper degrades instead of
        raising, but the outer guard keeps a broken host from 500-ing the poll.
        """
        try:
            snapshot = _network_snapshot(
                run_cmd=self.server.run_cmd,
                state_dir=self.server.state_dir,
            )
        except Exception:
            snapshot = {
                "ok": True,
                "hostname": "",
                "node_id": "",
                "link_key": "",
                "interfaces": [],
                "dns": [],
                "routes": [],
                "nmcli_available": False,
            }
        self._send_json(HTTPStatus.OK, snapshot)

    def _handle_network_action(
        self,
        action: Callable[[Dict[str, Any], CommandRunner], Tuple[int, Dict[str, Any]]],
    ) -> None:
        """Shared JSON-body plumbing for the POST /api/network/* actions."""
        body, err = self._read_json_body()
        if err is not None or body is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "message": err or "Некорректный JSON"},
            )
            return
        try:
            status, payload = action(body, self.server.run_cmd)
        except Exception:
            status = HTTPStatus.SERVICE_UNAVAILABLE
            payload = {
                "ok": False,
                "message": "Не удалось применить настройки сети",
            }
        if int(status) < 400 and isinstance(payload, dict) and payload.get("ok"):
            _write_tunnel_restart_trigger(self.server.state_dir)
        self._send_json(int(status), payload)

    def _handle_network_ip(self) -> None:
        """POST /api/network/ip — DHCP/static IPv4 + DNS through nmcli."""
        self._handle_network_action(apply_network_ip)

    def _handle_network_pppoe(self) -> None:
        """POST /api/network/pppoe — create/replace the rhm-pppoe connection."""
        self._handle_network_action(apply_network_pppoe)

    def _handle_network_wireguard(self) -> None:
        """POST /api/network/wireguard — write a 0600 keyfile and bring it up."""
        self._handle_network_action(apply_network_wireguard)

    def _handle_network_openvpn(self) -> None:
        """POST /api/network/openvpn — import a pasted .ovpn and bring it up."""
        self._handle_network_action(apply_network_openvpn)

    def _handle_network_amnezia(self) -> None:
        """GET /api/network/amnezia — configured files plus best-effort status."""
        self._send_json(HTTPStatus.OK, amnezia_overview(self.server.run_cmd))

    def _handle_network_amnezia_up(self) -> None:
        """POST /api/network/amnezia — write a 0600 config and bring it up."""
        self._handle_network_action(apply_network_amnezia)

    def _handle_network_amnezia_down(self) -> None:
        """POST /api/network/amnezia/down — `awg-quick down` one config."""
        self._handle_network_action(apply_network_amnezia_down)

    def _handle_network_subscription(self) -> None:
        """POST /api/network/subscription — fetch, decode and bring up."""
        self._handle_network_action(apply_network_amnezia_subscription)

    def _handle_network_subscription_qr(self) -> None:
        """GET /api/network/subscription/qr?url=... — SVG QR of the URL."""
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            raw_url = (query.get("url") or [""])[0]
            svg = render_subscription_qr_svg(raw_url, self.server.run_cmd)
        except NetworkValidationError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "message": str(exc)}
            )
            return
        except NetworkUnavailableError as exc:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "message": str(exc)}
            )
            return
        except Exception:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "message": "Не удалось построить QR-код"},
            )
            return
        self._send_bytes(
            HTTPStatus.OK, svg.encode("utf-8"), "image/svg+xml; charset=utf-8"
        )

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
