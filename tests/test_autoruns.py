"""Unit tests for core/autoruns.py cross-OS read-only autorun scanner."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

import pytest

from core import autoruns


# ---------------------------------------------------------------------------
# Helpers to build mock OS directory trees
# ---------------------------------------------------------------------------

def _write_text(path: Path, content: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)
    return path


def _snapshot_tree(root: Path) -> dict:
    """Record relative paths and file contents to prove read-only behavior."""
    snap = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file():
            snap[rel] = p.read_bytes()
        elif p.is_dir():
            snap[rel] = "<DIR>"
    return snap


# ---------------------------------------------------------------------------
# 1. Windows Scanning (Startup Folders, Scheduled Tasks XML, Registry Hives)
# ---------------------------------------------------------------------------

def test_scan_windows_startup_tasks_and_registry_without_hivexget(tmp_path: Path, monkeypatch):
    # Create Windows OS markers
    (tmp_path / "Windows" / "System32" / "config").mkdir(parents=True)
    (tmp_path / "Windows" / "System32" / "config" / "SOFTWARE").write_bytes(b"regf-mock")
    (tmp_path / "Windows" / "System32" / "config" / "SAM").write_bytes(b"regf-sam")

    # Machine-wide Startup folder (plus desktop.ini which should be ignored)
    pd_startup = tmp_path / "ProgramData" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    _write_text(pd_startup / "MonitorAgent.lnk", "shortcut")
    _write_text(pd_startup / "desktop.ini", "[.ShellClassInfo]")

    # Per-user Startup folder
    user_startup = (
        tmp_path / "Users" / "Alice" / "AppData" / "Roaming" / "Microsoft" / "Windows"
        / "Start Menu" / "Programs" / "Startup"
    )
    _write_text(user_startup / "SyncTray.bat", "@echo off\n")

    # Scheduled Task XML (with standard Windows namespace, no .xml extension)
    task_xml = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>Alice</Author>
    <Description>Nightly backup task</Description>
  </RegistrationInfo>
  <Settings>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>C:\\Tools\\backup.exe</Command>
      <Arguments>--silent --verify</Arguments>
    </Exec>
  </Actions>
</Task>
"""
    _write_text(
        tmp_path / "Windows" / "System32" / "Tasks" / "Custom" / "NightlyBackup",
        task_xml,
        encoding="utf-16",
    )

    # Also add a malformed XML file to verify graceful skip without raising
    _write_text(
        tmp_path / "Windows" / "System32" / "Tasks" / "BrokenTask",
        "<Task><NotClosed>",
    )

    # Simulate hivexget not installed
    monkeypatch.setattr(autoruns.shutil, "which", lambda cmd: None)

    before = _snapshot_tree(tmp_path)
    res = autoruns.scan_autoruns(str(tmp_path))
    after = _snapshot_tree(tmp_path)

    assert before == after, "scan_autoruns must remain strictly read-only"
    assert res["ok"] is True
    assert res["available"] is True
    assert res["read_only"] is True
    assert res["os"] == "windows"
    assert res["error"] is None

    by_cat = {}
    for entry in res["entries"]:
        by_cat.setdefault(entry["category"], []).append(entry)

    # Startup folders: MonitorAgent.lnk (machine) + SyncTray.bat (user Alice); desktop.ini excluded
    startup_entries = by_cat["startup_folder"]
    assert len(startup_entries) == 2
    startup_names = {e["name"]: e for e in startup_entries}
    assert "desktop.ini" not in startup_names
    assert startup_names["MonitorAgent.lnk"]["details"]["scope"] == "machine"
    assert startup_names["SyncTray.bat"]["details"]["scope"] == "user"
    assert startup_names["SyncTray.bat"]["details"]["user"] == "Alice"

    # Scheduled task XML extracted Command + Arguments
    task_entries = by_cat["scheduled_task"]
    assert len(task_entries) == 1
    task = task_entries[0]
    assert task["name"] == "NightlyBackup"
    assert task["command"] == r"C:\Tools\backup.exe"
    assert task["arguments"] == "--silent --verify"
    assert task["details"]["author"] == "Alice"
    assert task["details"]["enabled"] is True

    # Registry hives detected, hivexget gracefully skipped
    reg = res["registry_hives"]
    assert reg["config_dir_present"] is True
    assert "SOFTWARE" in reg["hives_present"]
    assert "SAM" in reg["hives_present"]
    assert reg["hivexget_available"] is False
    assert reg["inspection_status"] == "skipped"

    # Malformed XML produced a warning rather than an exception
    assert any("BrokenTask" in w for w in res["warnings"])


def test_scan_windows_registry_with_mocked_hivexget(tmp_path: Path, monkeypatch):
    (tmp_path / "Windows" / "System32" / "config").mkdir(parents=True)
    (tmp_path / "Windows" / "System32" / "config" / "SOFTWARE").write_bytes(b"regf-mock")

    monkeypatch.setattr(autoruns.shutil, "which", lambda cmd: "/usr/bin/hivexget" if cmd == "hivexget" else None)

    def fake_run(cmd, capture_output=True, text=True, timeout=10, check=False):
        key = cmd[2]
        if key.endswith(r"\Run"):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='"SecurityHealth"="%windir%\\system32\\SecurityHealthSystray.exe"\n',
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")

    monkeypatch.setattr(autoruns.subprocess, "run", fake_run)

    res = autoruns.scan_autoruns(str(tmp_path), os_hint="windows")
    assert res["ok"] is True
    assert res["registry_hives"]["hivexget_available"] is True
    assert res["registry_hives"]["inspection_status"] == "completed"

    reg_entries = [e for e in res["entries"] if e["category"] == "registry_run"]
    assert len(reg_entries) == 1
    assert reg_entries[0]["name"] == "SecurityHealth"
    assert reg_entries[0]["command"] == r"%windir%\system32\SecurityHealthSystray.exe"


# ---------------------------------------------------------------------------
# 2. Linux Scanning (systemd, cron.d, crontab, rc.local, .desktop autostart)
# ---------------------------------------------------------------------------

def test_scan_linux_autoruns(tmp_path: Path):
    _write_text(tmp_path / "etc" / "os-release", 'NAME="Ubuntu"\nVERSION_ID="24.04"\n')

    # systemd service
    _write_text(
        tmp_path / "etc" / "systemd" / "system" / "backup-agent.service",
        """[Unit]
Description=Backup Sync Service
After=network.target

[Service]
User=backup
ExecStart=/usr/local/bin/backup-agent --daemon

[Install]
WantedBy=multi-user.target
""",
    )

    # /etc/cron.d/*
    _write_text(
        tmp_path / "etc" / "cron.d" / "healthcheck",
        """# Periodic health check
SHELL=/bin/sh
*/15 * * * * root /usr/local/bin/check-disk.sh
""",
    )

    # /etc/crontab
    _write_text(
        tmp_path / "etc" / "crontab",
        """# system crontab
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
@reboot root /usr/local/bin/boot-notify.sh
""",
    )

    # /etc/rc.local
    _write_text(
        tmp_path / "etc" / "rc.local",
        """#!/bin/sh -e
# Local startup
/usr/local/bin/mount-extra.sh
exit 0
""",
    )

    # /etc/xdg/autostart/*.desktop
    _write_text(
        tmp_path / "etc" / "xdg" / "autostart" / "nm-applet.desktop",
        """[Desktop Entry]
Name=Network Manager Applet
Exec=nm-applet --indicator
X-GNOME-Autostart-enabled=true
""",
    )

    # home/*/.config/autostart/*.desktop
    _write_text(
        tmp_path / "home" / "bob" / ".config" / "autostart" / "notes.desktop",
        """[Desktop Entry]
Name=Notes Sync
Exec=/usr/bin/notes-sync --tray
""",
    )

    before = _snapshot_tree(tmp_path)
    res = autoruns.scan_autoruns(str(tmp_path))
    after = _snapshot_tree(tmp_path)

    assert before == after
    assert res["ok"] is True
    assert res["os"] == "linux"
    assert res["count"] == 6

    by_cat = {e["category"]: e for e in res["entries"]}
    assert by_cat["systemd_service"]["command"] == "/usr/local/bin/backup-agent --daemon"
    assert by_cat["systemd_service"]["details"]["user"] == "backup"
    assert by_cat["cron_d"]["command"] == "/usr/local/bin/check-disk.sh"
    assert by_cat["cron_d"]["details"]["schedule"] == "*/15 * * * *"
    assert by_cat["crontab"]["command"] == "/usr/local/bin/boot-notify.sh"
    assert by_cat["crontab"]["details"]["schedule"] == "@reboot"
    assert by_cat["rc_local"]["command"] == "/usr/local/bin/mount-extra.sh"
    assert by_cat["xdg_autostart"]["name"] == "Network Manager Applet"
    assert by_cat["xdg_autostart"]["command"] == "nm-applet --indicator"
    assert by_cat["user_autostart"]["name"] == "Notes Sync"
    assert by_cat["user_autostart"]["command"] == "/usr/bin/notes-sync --tray"
    assert by_cat["user_autostart"]["details"]["user"] == "bob"


# ---------------------------------------------------------------------------
# 3. macOS Scanning (plistlib XML & Binary LaunchDaemons / LaunchAgents)
# ---------------------------------------------------------------------------

def test_scan_macos_launchd_plists(tmp_path: Path):
    (tmp_path / "System" / "Library").mkdir(parents=True)
    daemons = tmp_path / "Library" / "LaunchDaemons"
    agents = tmp_path / "Library" / "LaunchAgents"
    user_agents = tmp_path / "Users" / "carol" / "Library" / "LaunchAgents"
    daemons.mkdir(parents=True)
    agents.mkdir(parents=True)
    user_agents.mkdir(parents=True)

    # XML LaunchDaemon
    daemon_plist = {
        "Label": "com.example.updater",
        "ProgramArguments": ["/Library/Application Support/Example/updater", "--check"],
        "RunAtLoad": True,
        "KeepAlive": False,
    }
    with open(daemons / "com.example.updater.plist", "wb") as fh:
        plistlib.dump(daemon_plist, fh, fmt=plistlib.FMT_XML)

    # Binary LaunchAgent
    agent_plist = {
        "Label": "com.example.menubar",
        "Program": "/Applications/Example.app/Contents/MacOS/MenuBar",
        "RunAtLoad": True,
        "KeepAlive": True,
    }
    with open(agents / "com.example.menubar.plist", "wb") as fh:
        plistlib.dump(agent_plist, fh, fmt=plistlib.FMT_BINARY)

    # User LaunchAgent
    user_plist = {
        "Label": "com.carol.sync",
        "ProgramArguments": ["/Users/carol/bin/sync.sh", "--fast"],
        "RunAtLoad": True,
    }
    with open(user_agents / "com.carol.sync.plist", "wb") as fh:
        plistlib.dump(user_plist, fh, fmt=plistlib.FMT_XML)

    # Corrupted plist should warn and not crash
    (user_agents / "corrupt.plist").write_bytes(b"not-a-valid-plist")

    before = _snapshot_tree(tmp_path)
    res = autoruns.scan_autoruns(str(tmp_path))
    after = _snapshot_tree(tmp_path)

    assert before == after
    assert res["ok"] is True
    assert res["os"] == "macos"
    assert res["count"] == 3

    by_label = {e["name"]: e for e in res["entries"]}

    d = by_label["com.example.updater"]
    assert d["category"] == "launch_daemon"
    assert d["command"] == "/Library/Application Support/Example/updater"
    assert d["arguments"] == "--check"
    assert d["details"]["RunAtLoad"] is True
    assert d["details"]["KeepAlive"] is False

    a = by_label["com.example.menubar"]
    assert a["category"] == "launch_agent"
    assert a["command"] == "/Applications/Example.app/Contents/MacOS/MenuBar"
    assert a["details"]["Program"] == "/Applications/Example.app/Contents/MacOS/MenuBar"
    assert a["details"]["KeepAlive"] is True

    u = by_label["com.carol.sync"]
    assert u["category"] == "user_launch_agent"
    assert u["details"]["user"] == "carol"
    assert u["command"] == "/Users/carol/bin/sync.sh"
    assert u["arguments"] == "--fast"

    assert any("corrupt.plist" in w for w in res["warnings"])


# ---------------------------------------------------------------------------
# 4. Invalid / Missing Root & Error Handling
# ---------------------------------------------------------------------------

def test_scan_invalid_and_missing_roots(tmp_path: Path):
    # Missing directory
    missing = tmp_path / "does_not_exist"
    res_missing = autoruns.scan_autoruns(str(missing))
    assert res_missing["ok"] is False
    assert res_missing["available"] is False
    assert res_missing["entries"] == []
    assert "does not exist" in res_missing["error"]

    # File instead of directory
    a_file = _write_text(tmp_path / "not_a_dir.txt", "hello")
    res_file = autoruns.scan_autoruns(str(a_file))
    assert res_file["ok"] is False
    assert "not a directory" in res_file["error"]

    # Empty path
    res_empty = autoruns.scan_autoruns("")
    assert res_empty["ok"] is False
    assert res_empty["error"]

    # Existing empty directory with no OS signatures
    empty_dir = tmp_path / "empty_root"
    empty_dir.mkdir()
    res_unknown = autoruns.scan_autoruns(str(empty_dir))
    assert res_unknown["ok"] is False
    assert res_unknown["os"] == "unknown"
    assert "auto-detect" in res_unknown["error"]

    # Unsupported os_hint
    res_bad_hint = autoruns.scan_autoruns(str(empty_dir), os_hint="solaris")
    assert res_bad_hint["ok"] is False
    assert "Unsupported os_hint" in res_bad_hint["error"]
