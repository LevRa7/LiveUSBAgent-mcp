#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/live-usb/firstboot_tui.py - Zero-dependency ANSI console dashboard for tty1/tty2.

Displays LiveUSB session details from session.json (node_id, connect_url, link_key,
FULL ACCESS mode), network IPv4 connectivity, and an ASCII/UTF-8 QR code, with an
interactive key loop for Wi-Fi (nmtui), Shell (bash), and tunnel restart.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import termios
    import tty
except ImportError:  # Windows
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

DEFAULT_STATE_DIR = "/run/rhm-live"


def _get_state_dir(state_dir: Optional[Path] = None) -> Path:
    if state_dir is not None:
        return Path(state_dir)
    env_dir = os.environ.get("MESH_LIVE_STATE_DIR", "").strip()
    return Path(env_dir) if env_dir else Path(DEFAULT_STATE_DIR)


def _read_session(state_dir: Optional[Path] = None) -> Dict[str, Any]:
    session_file = _get_state_dir(state_dir) / "session.json"
    try:
        if not session_file.is_file():
            return {}
        data = json.loads(session_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_ipv4() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except Exception:
        return "OFFLINE (Connect Ethernet, Android USB Tethering, or press [W] for Wi-Fi)"


def _render_qr(url: str) -> str:
    if not url:
        return "  [Waiting for gateway registration — connect to the internet first]\n"
    if shutil.which("qrencode"):
        try:
            proc = subprocess.run(
                ["qrencode", "-t", "ANSIUTF8", url],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )
            if proc.returncode == 0:
                return proc.stdout
        except Exception:
            pass
    return f"  [QR Code unavailable - open URL manually]:\n  {url}\n"


def render_banner(state_dir: Optional[Path] = None) -> str:
    session = _read_session(state_dir)
    ip = _get_ipv4()
    hostname = socket.gethostname()
    node_id = str(session.get("node_id") or "").strip() or hostname
    connect_url = str(
        session.get("connect_url") or os.environ.get("MESH_PAIR_URL") or ""
    ).strip()
    link_key = str(session.get("link_key") or session.get("token") or "").strip()

    lines = [
        "\033[2J\033[H",
        "======================================================================",
        "   REMOTE-HARNESS MCP :: LIVE USB RESCUE & DIAGNOSTICS AGENT",
        "======================================================================",
        f"  Node ID       : {node_id}",
        "  Access Mode   : FULL ACCESS (live_usb)",
        f"  Network IPv4  : {ip}",
        f"  Connect URL   : {connect_url or 'WAITING FOR GATEWAY REGISTRATION...'}",
        f"  Link Key      : {link_key or '(pending)'}",
        "----------------------------------------------------------------------",
        _render_qr(connect_url),
        "----------------------------------------------------------------------",
        "  [W] Configure Wi-Fi (nmtui)   [S] Shell (bash)   [R] Restart Agent",
        "======================================================================",
    ]
    return "\n".join(lines)


def _read_key_with_timeout(timeout_sec: float) -> Optional[str]:
    """Wait up to *timeout_sec* for a single keypress on stdin (POSIX TTY)."""
    if termios is None or tty is None or not sys.stdin.isatty():
        time.sleep(timeout_sec)
        return None

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ready, _, _ = select.select([sys.stdin], [], [], timeout_sec)
        if ready:
            return sys.stdin.read(1)
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def run_loop(
    refresh_interval: float = 5.0,
    state_dir: Optional[Path] = None,
) -> None:
    """Render banner and handle interactive hotkeys with 5s auto-refresh."""
    while True:
        print(render_banner(state_dir), flush=True)
        try:
            key = _read_key_with_timeout(refresh_interval)
        except KeyboardInterrupt:
            break
        if not key:
            continue

        cmd_key = key.strip().lower()
        if cmd_key == "w":
            subprocess.run(["nmtui"], check=False)
        elif cmd_key == "s":
            subprocess.run(["bash"], check=False)
        elif cmd_key == "r":
            subprocess.run(["systemctl", "restart", "rhm-live-agent.service"], check=False)
        elif cmd_key == "q":
            break


if __name__ == "__main__":
    run_loop()
