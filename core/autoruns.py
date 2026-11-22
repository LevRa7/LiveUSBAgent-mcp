#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/autoruns.py - Strictly read-only cross-OS offline autorun & persistence scanner.

Designed to follow the exact contract of :mod:`core.rescue`:
* Python 3.8+ standard library only;
* Never raises unhandled exceptions — returns structured dicts with ``ok``/``available``;
* Strictly read-only: inspects mounted offline Windows, Linux, and macOS root
  filesystems without modifying any target file, registry hive, or system state.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

_MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB safety cap per parsed text/XML/plist file
_HIVEXGET_TIMEOUT_SEC = 10

_OS_ALIASES: Dict[str, str] = {
    "auto": "auto",
    "windows": "windows",
    "win": "windows",
    "linux": "linux",
    "macos": "macos",
    "darwin": "macos",
    "osx": "macos",
}

_WINDOWS_HIVE_NAMES: Tuple[str, ...] = ("SOFTWARE", "SYSTEM", "SAM", "SECURITY", "DEFAULT")

_WINDOWS_RUN_KEYS: Tuple[str, ...] = (
    r"Microsoft\Windows\CurrentVersion\Run",
    r"Microsoft\Windows\CurrentVersion\RunOnce",
)

_HIVEX_VALUE_RE = re.compile(r'^\s*"([^"]+)"\s*=\s*(.*)\s*$')


def _resolve_ci(base: str, *parts: str) -> Optional[str]:
    """Resolve ``parts`` under ``base``, trying exact match first then case-insensitive."""
    cur = base
    for part in parts:
        exact = os.path.join(cur, part)
        if os.path.exists(exact):
            cur = exact
            continue
        try:
            entries = os.listdir(cur)
        except OSError:
            return None
        target_lower = part.lower()
        matched = None
        for item in entries:
            if item.lower() == target_lower:
                matched = os.path.join(cur, item)
                break
        if matched is None:
            return None
        cur = matched
    return cur


def _rel_posix(root_path: str, target_path: str) -> str:
    """Return a clean forward-slash relative path from ``root_path``."""
    try:
        rel = os.path.relpath(target_path, root_path)
    except ValueError:
        rel = target_path
    return rel.replace("\\", "/")


def _safe_read_bytes(file_path: str, max_bytes: int = _MAX_FILE_BYTES) -> Optional[bytes]:
    """Read up to ``max_bytes`` from a regular file, returning None on error."""
    try:
        if not os.path.isfile(file_path):
            return None
        size = os.path.getsize(file_path)
        if size > max_bytes:
            return None
        with open(file_path, "rb") as fh:
            return fh.read(max_bytes)
    except OSError:
        return None


def _safe_read_text(file_path: str, max_bytes: int = _MAX_FILE_BYTES) -> Optional[str]:
    """Read a small text file safely with UTF-8 fallback."""
    raw = _safe_read_bytes(file_path, max_bytes=max_bytes)
    if raw is None:
        return None
    for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _detect_os(root_path: str) -> str:
    """Auto-detect installed OS from characteristic directory/file markers."""
    win_sys32 = _resolve_ci(root_path, "Windows", "System32")
    if win_sys32 and os.path.isdir(win_sys32):
        return "windows"

    mac_sys_lib = _resolve_ci(root_path, "System", "Library")
    mac_daemons = _resolve_ci(root_path, "Library", "LaunchDaemons")
    if (mac_sys_lib and os.path.isdir(mac_sys_lib)) or (mac_daemons and os.path.isdir(mac_daemons)):
        return "macos"

    linux_systemd = _resolve_ci(root_path, "etc", "systemd")
    linux_os_rel = _resolve_ci(root_path, "etc", "os-release")
    if (linux_systemd and os.path.isdir(linux_systemd)) or (linux_os_rel and os.path.isfile(linux_os_rel)):
        return "linux"

    return "unknown"


# ---------------------------------------------------------------------------
# Windows Collectors (Strictly Read-Only)
# ---------------------------------------------------------------------------

def _scan_windows_startup_dir(
    root_path: str,
    startup_dir: str,
    scope: str,
    user: Optional[str],
    entries: List[Dict[str, Any]],
    warnings: List[str],
) -> None:
    try:
        items = sorted(os.listdir(startup_dir))
    except OSError as exc:
        warnings.append(f"Could not list startup folder {_rel_posix(root_path, startup_dir)}: {exc}")
        return

    for item in items:
        if item.lower() == "desktop.ini":
            continue
        full = os.path.join(startup_dir, item)
        if not os.path.isfile(full):
            continue
        details: Dict[str, Any] = {"scope": scope}
        if user:
            details["user"] = user
        entries.append(
            {
                "os": "windows",
                "category": "startup_folder",
                "name": item,
                "path": _rel_posix(root_path, full),
                "command": item,
                "arguments": "",
                "details": details,
            }
        )


def _xml_local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _parse_windows_task_xml(
    root_path: str,
    task_file: str,
    entries: List[Dict[str, Any]],
    warnings: List[str],
) -> None:
    raw = _safe_read_bytes(task_file)
    if raw is None or not raw.strip():
        return

    try:
        xml_root = ET.fromstring(raw)
    except ET.ParseError as exc:
        warnings.append(f"Skipping unreadable scheduled task XML {_rel_posix(root_path, task_file)}: {exc}")
        return
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"Error parsing scheduled task {_rel_posix(root_path, task_file)}: {exc}")
        return

    author = ""
    description = ""
    enabled: Optional[bool] = None
    exec_actions: List[Tuple[str, str]] = []

    for elem in xml_root.iter():
        tag = _xml_local_name(elem.tag)
        if tag == "Author" and elem.text and not author:
            author = elem.text.strip()
        elif tag == "Description" and elem.text and not description:
            description = elem.text.strip()
        elif tag == "Enabled" and elem.text and enabled is None:
            val = elem.text.strip().lower()
            if val in ("true", "false"):
                enabled = val == "true"
        elif tag == "Exec":
            cmd = ""
            args = ""
            for child in elem:
                ctag = _xml_local_name(child.tag)
                if ctag == "Command" and child.text:
                    cmd = child.text.strip()
                elif ctag == "Arguments" and child.text:
                    args = child.text.strip()
            if cmd or args:
                exec_actions.append((cmd, args))

    rel_path = _rel_posix(root_path, task_file)
    task_name = os.path.basename(task_file)
    if not exec_actions:
        return

    for idx, (cmd, args) in enumerate(exec_actions):
        details: Dict[str, Any] = {"action_index": idx}
        if author:
            details["author"] = author
        if description:
            details["description"] = description
        if enabled is not None:
            details["enabled"] = enabled
        entries.append(
            {
                "os": "windows",
                "category": "scheduled_task",
                "name": task_name,
                "path": rel_path,
                "command": cmd,
                "arguments": args,
                "details": details,
            }
        )


def _scan_windows_registry(
    root_path: str,
    entries: List[Dict[str, Any]],
    warnings: List[str],
) -> Dict[str, Any]:
    config_dir = _resolve_ci(root_path, "Windows", "System32", "config")
    hives_present: Dict[str, str] = {}
    if config_dir and os.path.isdir(config_dir):
        for hive in _WINDOWS_HIVE_NAMES:
            hive_path = _resolve_ci(config_dir, hive)
            if hive_path and os.path.isfile(hive_path):
                hives_present[hive] = _rel_posix(root_path, hive_path)

    info: Dict[str, Any] = {
        "config_dir_present": bool(config_dir and os.path.isdir(config_dir)),
        "hives_present": sorted(hives_present.keys()),
        "hive_paths": hives_present,
        "hivexget_available": False,
        "inspection_status": "skipped",
    }

    software_hive = _resolve_ci(config_dir, "SOFTWARE") if config_dir else None
    if not software_hive or not os.path.isfile(software_hive):
        info["reason"] = "SOFTWARE hive not found"
        return info

    hivexget_bin = shutil.which("hivexget")
    if not hivexget_bin:
        info["reason"] = "hivexget not installed"
        return info

    info["hivexget_available"] = True
    inspected_keys: List[str] = []
    for key_path in _WINDOWS_RUN_KEYS:
        try:
            proc = subprocess.run(
                [hivexget_bin, software_hive, key_path],
                capture_output=True,
                text=True,
                timeout=_HIVEXGET_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            warnings.append(f"hivexget failed for SOFTWARE\\{key_path}: {exc}")
            continue

        if proc.returncode != 0:
            # Missing RunOnce key is common and non-fatal
            continue

        inspected_keys.append(key_path)
        for line in proc.stdout.splitlines():
            match = _HIVEX_VALUE_RE.match(line)
            if not match:
                continue
            val_name = match.group(1)
            raw_val = match.group(2).strip()
            if len(raw_val) >= 2 and raw_val.startswith('"') and raw_val.endswith('"'):
                raw_val = raw_val[1:-1]
            entries.append(
                {
                    "os": "windows",
                    "category": "registry_run",
                    "name": val_name,
                    "path": f"SOFTWARE\\{key_path}",
                    "command": raw_val,
                    "arguments": "",
                    "details": {
                        "hive": "SOFTWARE",
                        "key": key_path,
                        "hive_path": _rel_posix(root_path, software_hive),
                    },
                }
            )

    info["inspection_status"] = "completed"
    info["inspected_keys"] = inspected_keys
    return info


def _scan_windows(
    root_path: str,
    entries: List[Dict[str, Any]],
    warnings: List[str],
) -> Dict[str, Any]:
    # 1. Machine-wide Startup folder
    progdata_startup = _resolve_ci(
        root_path, "ProgramData", "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )
    if progdata_startup and os.path.isdir(progdata_startup):
        _scan_windows_startup_dir(root_path, progdata_startup, "machine", None, entries, warnings)

    # 2. Per-user Startup folders
    users_dir = _resolve_ci(root_path, "Users")
    if users_dir and os.path.isdir(users_dir):
        try:
            user_names = sorted(os.listdir(users_dir))
        except OSError as exc:
            warnings.append(f"Could not list Users directory: {exc}")
            user_names = []
        for uname in user_names:
            user_root = os.path.join(users_dir, uname)
            if not os.path.isdir(user_root):
                continue
            user_startup = _resolve_ci(
                user_root, "AppData", "Roaming", "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
            )
            if user_startup and os.path.isdir(user_startup):
                _scan_windows_startup_dir(root_path, user_startup, "user", uname, entries, warnings)

    # 3. Scheduled Tasks XML under Windows/System32/Tasks/**
    tasks_dir = _resolve_ci(root_path, "Windows", "System32", "Tasks")
    if tasks_dir and os.path.isdir(tasks_dir):
        for dirpath, dirnames, filenames in os.walk(tasks_dir):
            dirnames.sort()
            for fname in sorted(filenames):
                full_task = os.path.join(dirpath, fname)
                if os.path.isfile(full_task):
                    _parse_windows_task_xml(root_path, full_task, entries, warnings)

    # 4. Offline Registry hive presence + optional hivexget inspection
    registry_info = _scan_windows_registry(root_path, entries, warnings)
    return {"registry_hives": registry_info}


# ---------------------------------------------------------------------------
# Linux Collectors (Strictly Read-Only)
# ---------------------------------------------------------------------------

def _parse_systemd_service(
    root_path: str,
    service_path: str,
    entries: List[Dict[str, Any]],
) -> None:
    text = _safe_read_text(service_path)
    if text is None:
        return

    description = ""
    exec_starts: List[str] = []
    user = ""
    current_section = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if current_section == "Unit" and key == "Description" and not description:
            description = val
        elif current_section == "Service":
            if key == "ExecStart" and val:
                exec_starts.append(val)
            elif key == "User" and val:
                user = val

    for idx, cmd in enumerate(exec_starts):
        details: Dict[str, Any] = {"exec_index": idx}
        if description:
            details["description"] = description
        if user:
            details["user"] = user
        entries.append(
            {
                "os": "linux",
                "category": "systemd_service",
                "name": os.path.basename(service_path),
                "path": _rel_posix(root_path, service_path),
                "command": cmd,
                "arguments": "",
                "details": details,
            }
        )


def _parse_cron_file(
    root_path: str,
    cron_path: str,
    category: str,
    entries: List[Dict[str, Any]],
) -> None:
    text = _safe_read_text(cron_path)
    if text is None:
        return

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Skip environment variable assignments like SHELL=/bin/sh or PATH=...
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", line):
            continue

        parts = line.split()
        schedule = ""
        user = ""
        command = line
        if line.startswith("@") and len(parts) >= 3:
            schedule = parts[0]
            user = parts[1]
            command = " ".join(parts[2:])
        elif len(parts) >= 7:
            schedule = " ".join(parts[:5])
            user = parts[5]
            command = " ".join(parts[6:])

        details: Dict[str, Any] = {"line": lineno}
        if schedule:
            details["schedule"] = schedule
        if user:
            details["user"] = user

        entries.append(
            {
                "os": "linux",
                "category": category,
                "name": f"{os.path.basename(cron_path)}:{lineno}",
                "path": _rel_posix(root_path, cron_path),
                "command": command,
                "arguments": "",
                "details": details,
            }
        )


def _parse_rc_local(
    root_path: str,
    rc_path: str,
    entries: List[Dict[str, Any]],
) -> None:
    text = _safe_read_text(rc_path)
    if text is None:
        return

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line in ("exit 0", "exit"):
            continue
        entries.append(
            {
                "os": "linux",
                "category": "rc_local",
                "name": f"rc.local:{lineno}",
                "path": _rel_posix(root_path, rc_path),
                "command": line,
                "arguments": "",
                "details": {"line": lineno},
            }
        )


def _parse_desktop_file(
    root_path: str,
    desktop_path: str,
    category: str,
    user: Optional[str],
    entries: List[Dict[str, Any]],
) -> None:
    text = _safe_read_text(desktop_path)
    if text is None:
        return

    in_desktop_entry = False
    name = ""
    exec_cmd = ""
    hidden = False
    enabled = True

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_desktop_entry = line == "[Desktop Entry]"
            continue
        if not in_desktop_entry or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if key == "Name" and not name:
            name = val
        elif key == "Exec" and not exec_cmd:
            exec_cmd = val
        elif key == "Hidden":
            hidden = val.lower() == "true"
        elif key == "X-GNOME-Autostart-enabled":
            enabled = val.lower() != "false"

    if not exec_cmd:
        return

    details: Dict[str, Any] = {"hidden": hidden, "enabled": enabled}
    if user:
        details["user"] = user

    entries.append(
        {
            "os": "linux",
            "category": category,
            "name": name or os.path.basename(desktop_path),
            "path": _rel_posix(root_path, desktop_path),
            "command": exec_cmd,
            "arguments": "",
            "details": details,
        }
    )


def _scan_linux(
    root_path: str,
    entries: List[Dict[str, Any]],
    warnings: List[str],
) -> Dict[str, Any]:
    # 1. /etc/systemd/system/*.service
    systemd_dir = os.path.join(root_path, "etc", "systemd", "system")
    if os.path.isdir(systemd_dir):
        try:
            for item in sorted(os.listdir(systemd_dir)):
                if item.endswith(".service"):
                    full = os.path.join(systemd_dir, item)
                    if os.path.isfile(full):
                        _parse_systemd_service(root_path, full, entries)
        except OSError as exc:
            warnings.append(f"Could not list etc/systemd/system: {exc}")

    # 2. /etc/cron.d/*
    cron_d = os.path.join(root_path, "etc", "cron.d")
    if os.path.isdir(cron_d):
        try:
            for item in sorted(os.listdir(cron_d)):
                if item.startswith(".") or item.endswith("~"):
                    continue
                full = os.path.join(cron_d, item)
                if os.path.isfile(full):
                    _parse_cron_file(root_path, full, "cron_d", entries)
        except OSError as exc:
            warnings.append(f"Could not list etc/cron.d: {exc}")

    # 3. /etc/crontab
    crontab_file = os.path.join(root_path, "etc", "crontab")
    if os.path.isfile(crontab_file):
        _parse_cron_file(root_path, crontab_file, "crontab", entries)

    # 4. /etc/rc.local
    rc_local = os.path.join(root_path, "etc", "rc.local")
    if os.path.isfile(rc_local):
        _parse_rc_local(root_path, rc_local, entries)

    # 5. /etc/xdg/autostart/*.desktop
    xdg_dir = os.path.join(root_path, "etc", "xdg", "autostart")
    if os.path.isdir(xdg_dir):
        try:
            for item in sorted(os.listdir(xdg_dir)):
                if item.endswith(".desktop"):
                    full = os.path.join(xdg_dir, item)
                    if os.path.isfile(full):
                        _parse_desktop_file(root_path, full, "xdg_autostart", None, entries)
        except OSError as exc:
            warnings.append(f"Could not list etc/xdg/autostart: {exc}")

    # 6. home/*/.config/autostart/*.desktop
    home_dir = os.path.join(root_path, "home")
    if os.path.isdir(home_dir):
        try:
            users = sorted(os.listdir(home_dir))
        except OSError as exc:
            warnings.append(f"Could not list home directory: {exc}")
            users = []
        for uname in users:
            user_autostart = os.path.join(home_dir, uname, ".config", "autostart")
            if not os.path.isdir(user_autostart):
                continue
            try:
                for item in sorted(os.listdir(user_autostart)):
                    if item.endswith(".desktop"):
                        full = os.path.join(user_autostart, item)
                        if os.path.isfile(full):
                            _parse_desktop_file(root_path, full, "user_autostart", uname, entries)
            except OSError as exc:
                warnings.append(f"Could not list home/{uname}/.config/autostart: {exc}")

    return {}


# ---------------------------------------------------------------------------
# macOS Collectors (Strictly Read-Only)
# ---------------------------------------------------------------------------

def _parse_macos_plist(
    root_path: str,
    plist_path: str,
    category: str,
    user: Optional[str],
    entries: List[Dict[str, Any]],
    warnings: List[str],
) -> None:
    raw = _safe_read_bytes(plist_path)
    if raw is None:
        return

    try:
        data = plistlib.loads(raw)
    except Exception as exc:
        warnings.append(f"Skipping unreadable plist {_rel_posix(root_path, plist_path)}: {exc}")
        return

    if not isinstance(data, dict):
        warnings.append(f"Unexpected non-dict plist root in {_rel_posix(root_path, plist_path)}")
        return

    label = data.get("Label") or os.path.basename(plist_path)
    program = data.get("Program")
    prog_args = data.get("ProgramArguments")
    run_at_load = data.get("RunAtLoad")
    keep_alive = data.get("KeepAlive")

    command = ""
    arguments: List[str] = []
    if isinstance(program, str) and program:
        command = program
        if isinstance(prog_args, list):
            arguments = [str(x) for x in prog_args]
    elif isinstance(prog_args, list) and prog_args:
        command = str(prog_args[0])
        arguments = [str(x) for x in prog_args[1:]]

    details: Dict[str, Any] = {
        "Label": label,
        "Program": program if isinstance(program, str) else None,
        "ProgramArguments": [str(x) for x in prog_args] if isinstance(prog_args, list) else None,
        "RunAtLoad": run_at_load if isinstance(run_at_load, bool) else None,
        "KeepAlive": keep_alive if isinstance(keep_alive, (bool, dict)) else None,
    }
    if user:
        details["user"] = user

    entries.append(
        {
            "os": "macos",
            "category": category,
            "name": str(label),
            "path": _rel_posix(root_path, plist_path),
            "command": command,
            "arguments": " ".join(arguments),
            "details": details,
        }
    )


def _scan_macos_plist_dir(
    root_path: str,
    plist_dir: str,
    category: str,
    user: Optional[str],
    entries: List[Dict[str, Any]],
    warnings: List[str],
) -> None:
    try:
        items = sorted(os.listdir(plist_dir))
    except OSError as exc:
        warnings.append(f"Could not list {_rel_posix(root_path, plist_dir)}: {exc}")
        return

    for item in items:
        if not item.endswith(".plist"):
            continue
        full = os.path.join(plist_dir, item)
        if os.path.isfile(full):
            _parse_macos_plist(root_path, full, category, user, entries, warnings)


def _scan_macos(
    root_path: str,
    entries: List[Dict[str, Any]],
    warnings: List[str],
) -> Dict[str, Any]:
    # 1. Library/LaunchDaemons/*.plist
    daemons_dir = _resolve_ci(root_path, "Library", "LaunchDaemons")
    if daemons_dir and os.path.isdir(daemons_dir):
        _scan_macos_plist_dir(root_path, daemons_dir, "launch_daemon", None, entries, warnings)

    # 2. Library/LaunchAgents/*.plist
    agents_dir = _resolve_ci(root_path, "Library", "LaunchAgents")
    if agents_dir and os.path.isdir(agents_dir):
        _scan_macos_plist_dir(root_path, agents_dir, "launch_agent", None, entries, warnings)

    # 3. Users/*/Library/LaunchAgents/*.plist
    users_dir = _resolve_ci(root_path, "Users")
    if users_dir and os.path.isdir(users_dir):
        try:
            users = sorted(os.listdir(users_dir))
        except OSError as exc:
            warnings.append(f"Could not list Users directory: {exc}")
            users = []
        for uname in users:
            user_agents = _resolve_ci(users_dir, uname, "Library", "LaunchAgents")
            if user_agents and os.path.isdir(user_agents):
                _scan_macos_plist_dir(root_path, user_agents, "user_launch_agent", uname, entries, warnings)

    return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_autoruns(root_path: str, os_hint: str = "auto") -> Dict[str, Any]:
    """Scan a mounted offline OS root directory for startup/persistence entries.

    Strictly read-only. Never raises unhandled exceptions.

    Args:
        root_path: Path to an existing mounted OS root directory.
        os_hint: ``"auto"`` (default), ``"windows"``, ``"linux"``, or ``"macos"``.

    Returns:
        Structured dictionary with ``ok``, ``available``, ``os``, ``entries``,
        ``count``, ``categories``, ``warnings``, and optional ``error``.
    """
    try:
        if not isinstance(root_path, str) or not root_path.strip():
            return {
                "available": False,
                "ok": False,
                "read_only": True,
                "root_path": "",
                "os": "unknown",
                "os_hint": str(os_hint),
                "entries": [],
                "count": 0,
                "categories": {},
                "warnings": [],
                "error": "root_path must be a non-empty directory path",
            }

        norm_root = os.path.abspath(root_path)
        if not os.path.exists(norm_root):
            return {
                "available": False,
                "ok": False,
                "read_only": True,
                "root_path": norm_root,
                "os": "unknown",
                "os_hint": str(os_hint),
                "entries": [],
                "count": 0,
                "categories": {},
                "warnings": [],
                "error": f"root_path does not exist: {norm_root}",
            }

        if not os.path.isdir(norm_root):
            return {
                "available": False,
                "ok": False,
                "read_only": True,
                "root_path": norm_root,
                "os": "unknown",
                "os_hint": str(os_hint),
                "entries": [],
                "count": 0,
                "categories": {},
                "warnings": [],
                "error": f"root_path is not a directory: {norm_root}",
            }

        hint_key = str(os_hint or "auto").strip().lower()
        normalized_hint = _OS_ALIASES.get(hint_key)
        if normalized_hint is None:
            return {
                "available": False,
                "ok": False,
                "read_only": True,
                "root_path": norm_root,
                "os": "unknown",
                "os_hint": str(os_hint),
                "entries": [],
                "count": 0,
                "categories": {},
                "warnings": [],
                "error": f"Unsupported os_hint: {os_hint!r} (expected auto, windows, linux, or macos)",
            }

        detected_os = _detect_os(norm_root) if normalized_hint == "auto" else normalized_hint
        if detected_os == "unknown":
            return {
                "available": False,
                "ok": False,
                "read_only": True,
                "root_path": norm_root,
                "os": "unknown",
                "os_hint": normalized_hint,
                "entries": [],
                "count": 0,
                "categories": {},
                "warnings": [],
                "error": f"Could not auto-detect OS at root_path: {norm_root}",
            }

        entries: List[Dict[str, Any]] = []
        warnings: List[str] = []
        extra: Dict[str, Any] = {}

        if detected_os == "windows":
            extra = _scan_windows(norm_root, entries, warnings)
        elif detected_os == "linux":
            extra = _scan_linux(norm_root, entries, warnings)
        elif detected_os == "macos":
            extra = _scan_macos(norm_root, entries, warnings)

        categories: Dict[str, int] = {}
        for entry in entries:
            cat = entry.get("category", "other")
            categories[cat] = categories.get(cat, 0) + 1

        result: Dict[str, Any] = {
            "available": True,
            "ok": True,
            "read_only": True,
            "root_path": norm_root,
            "os": detected_os,
            "os_hint": normalized_hint,
            "entries": entries,
            "count": len(entries),
            "categories": categories,
            "warnings": warnings,
            "error": None,
        }
        result.update(extra)
        return result
    except Exception as exc:  # pragma: no cover - final safety net
        return {
            "available": False,
            "ok": False,
            "read_only": True,
            "root_path": str(root_path) if root_path is not None else "",
            "os": "unknown",
            "os_hint": str(os_hint),
            "entries": [],
            "count": 0,
            "categories": {},
            "warnings": [],
            "error": f"Unexpected error during autoruns scan: {exc}",
        }
