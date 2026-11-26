# -*- coding: utf-8 -*-
"""tests/test_kiosk_server.py - Unit, HTTP API, and unslop-ui surface audit tests."""

from __future__ import annotations

import importlib.util
import base64
import json
import os
import re
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KIOSK_SERVER_PATH = REPO_ROOT / "ops" / "live-usb" / "kiosk_server.py"
KIOSK_UI_DIR = REPO_ROOT / "ops" / "live-usb" / "kiosk_ui"
START_KIOSK_PATH = REPO_ROOT / "ops" / "live-usb" / "start-kiosk.sh"
UDEV_RULES_PATH = REPO_ROOT / "ops" / "live-usb" / "udev" / "99-rhm-network-tethering.rules"


def _load_kiosk_module():
    spec = importlib.util.spec_from_file_location("rhm_kiosk_server", str(KIOSK_SERVER_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kiosk = _load_kiosk_module()


def _http_request(
    base_url: str,
    path: str,
    method: str = "GET",
    payload: Dict[str, Any] | None = None,
    extra_headers: Dict[str, str] | None = None,
    raw_body: bytes | None = None,
) -> Tuple[int, Dict[str, str], bytes]:
    data = raw_body
    headers: Dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


@pytest.fixture()
def running_server(tmp_path: Path):
    """Spin up KioskServer on 127.0.0.1:0 with injectable mocks."""
    calls: List[List[str]] = []
    state_dir = tmp_path / "live-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_data = {
        "ready": True,
        "connect_url": "https://mesh.example/l/abc123",
        "token": "lk_test_token_987654",
        "node_id": "live-rescue-01",
        "tunnel_status": "connected",
    }
    (state_dir / "session.json").write_text(json.dumps(session_data), encoding="utf-8")

    mock_state: Dict[str, Any] = {
        "qrencode_present": True,
        "nmcli_scan_output": (
            "Office\\:5G:55:WPA2:\n"
            "Office\\:5G:88:WPA2:*\n"
            "GuestNet:70:--:\n"
            ":99:WPA2:\n"
            "Lab\\\\Main:92:WPA3:\n"
        ),
        "idevice_id_out": "00008110-0011223344556677\n",
        "idevicepair_rc": 1,
        "idevicepair_out": "ERROR: Device 00008110-0011223344556677 returned unhandled error code -19\n",
        "lsblk_out": json.dumps(
            {
                "blockdevices": [
                    {
                        "name": "nvme0n1",
                        "size": 512110190592,
                        "type": "disk",
                        "model": "Samsung SSD 980",
                        "tran": "nvme",
                        "rm": False,
                    },
                    {
                        "name": "sda",
                        "size": 32000000000,
                        "type": "disk",
                        "model": "SanDisk Ultra",
                        "tran": "usb",
                        "rm": True,
                    },
                    {
                        "name": "sda1",
                        "size": 31990000000,
                        "type": "part",
                        "model": None,
                        "tran": None,
                        "rm": True,
                    },
                ]
            }
        ),
    }

    def fake_run_cmd(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        calls.append(list(cmd))
        if not cmd:
            return 1, "", "empty"
        exe = cmd[0]
        if exe == "nmcli" and cmd[:3] == ["nmcli", "radio", "wifi"]:
            return 0, "enabled\n", ""
        if exe == "nmcli" and "rescan" in cmd:
            return 0, "", ""
        if exe == "nmcli" and cmd[:5] == ["nmcli", "-t", "-f", "NAME,TYPE", "con"]:
            return 0, "", ""
        if exe == "nmcli" and cmd[:5] == ["nmcli", "-t", "-f", "DEVICE,TYPE", "dev"]:
            return 0, "wlan0:wifi\n", ""
        if exe == "nmcli" and cmd[:3] == ["nmcli", "con", "add"]:
            return 0, "Connection successfully added.", ""
        if exe == "nmcli" and cmd[:3] == ["nmcli", "con", "up"]:
            return 0, f"Connection successfully activated ({cmd[3]}).", ""
        if exe == "nmcli" and cmd[:3] == ["nmcli", "con", "delete"]:
            return 0, "Connection deleted.", ""
        if exe == "nmcli" and "list" in cmd:
            return 0, mock_state["nmcli_scan_output"], ""
        if exe == "nmcli" and "connect" in cmd:
            ssid_arg = cmd[5] if len(cmd) > 5 and cmd[4] == "--" else cmd[4]
            return 0, f"Device 'wlan0' successfully activated with '{ssid_arg}'.", ""
        if exe == "nmcli" and "ACTIVE,SSID" in cmd:
            return 0, "yes:Office\\:5G\n", ""
        if exe == "idevice_id":
            return 0, mock_state["idevice_id_out"], ""
        if exe == "idevicepair":
            return mock_state["idevicepair_rc"], mock_state["idevicepair_out"], ""
        if exe == "lsblk":
            return 0, mock_state["lsblk_out"], ""
        if exe == "qrencode":
            if mock_state["qrencode_present"]:
                return (
                    0,
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100"/></svg>',
                    "",
                )
            return 127, "", "command not found: qrencode"
        return 127, "", f"not mocked: {exe}"

    interfaces = [
        {"name": "enp3s0", "operstate": "DOWN", "ipv4": None, "driver": "r8169"},
        {"name": "wlan0", "operstate": "UP", "ipv4": "192.168.1.44", "driver": "iwlwifi"},
        {"name": "usb0", "operstate": "UP", "ipv4": "192.168.42.129", "driver": "rndis_host"},
    ]

    server = kiosk.KioskServer(
        ("127.0.0.1", 0),
        static_dir=KIOSK_UI_DIR,
        state_dir=state_dir,
        run_cmd=fake_run_cmd,
        internet_checker=lambda: {"reachable": True, "ip": "192.168.1.44", "target": "1.1.1.1"},
        interfaces_override=interfaces,
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "state_dir": state_dir,
            "calls": calls,
            "mock_state": mock_state,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# --- 1. nmcli parsing & deduplication tests ---


def test_split_nmcli_terse_line_escaped_colons_and_backslashes():
    line = r"My\:Home\:WiFi\\5G:79:WPA2 WPA3:*"
    parts = kiosk.split_nmcli_terse_line(line)
    assert parts == [r"My:Home:WiFi\5G", "79", "WPA2 WPA3", "*"]


def test_parse_nmcli_wifi_list_dedup_strongest_and_sort():
    raw = "\n".join(
        [
            r"Cafe\:24\:7:42:WPA2:*",
            r"Cafe\:24\:7:85:WPA2:",
            "OpenSpot:60:--:",
            ":99:WPA2:",
            r"Lab\\Net:91:WPA3:",
            "WeakNet:15:WPA2:",
        ]
    )
    parsed = kiosk.parse_nmcli_wifi_list(raw)
    assert [item["ssid"] for item in parsed] == [
        r"Lab\Net",
        "Cafe:24:7",
        "OpenSpot",
        "WeakNet",
    ]
    cafe = next(item for item in parsed if item["ssid"] == "Cafe:24:7")
    assert cafe["signal"] == 85
    assert cafe["in_use"] is True
    open_spot = next(item for item in parsed if item["ssid"] == "OpenSpot")
    assert open_spot["security"] == "OPEN"


# --- 2. iPhone & Android USB tethering detection tests ---


def test_detect_android_and_unpaired_iphone_tethering():
    def runner(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        if cmd[0] == "idevice_id":
            return 0, "00008110-ABCDEF\n", ""
        if cmd[0] == "idevicepair":
            return 1, "ERROR: Device 00008110-ABCDEF Password protected\n", ""
        return 127, "", ""

    ifaces = [
        {"name": "usb0", "operstate": "UP", "ipv4": "192.168.42.10", "driver": "rndis_host"},
        {"name": "eth0", "operstate": "DOWN", "ipv4": None, "driver": "e1000e"},
    ]
    detected = kiosk._detect_interfaces(run_cmd=runner, interfaces_override=ifaces)
    assert detected["android_usb"]["connected"] is True
    assert detected["android_usb"]["interface"] == "usb0"
    assert detected["android_usb"]["ip"] == "192.168.42.10"

    assert detected["iphone_usb"]["plugged_in"] is True
    assert detected["iphone_usb"]["paired"] is False
    assert detected["iphone_usb"]["needs_trust"] is True
    assert detected["iphone_usb"]["state"] == "needs_trust"


def test_detect_paired_iphone_ipheth_connected():
    def runner(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        if cmd[0] == "idevice_id":
            return 0, "00008110-ABCDEF\n", ""
        if cmd[0] == "idevicepair":
            return 0, "SUCCESS: Validated pairing with device 00008110-ABCDEF\n", ""
        return 127, "", ""

    ifaces = [
        {"name": "enp0s20f0u1c4i2", "operstate": "UP", "ipv4": "172.20.10.2", "driver": "ipheth"},
    ]
    detected = kiosk._detect_interfaces(run_cmd=runner, interfaces_override=ifaces)
    assert detected["iphone_usb"]["plugged_in"] is True
    assert detected["iphone_usb"]["paired"] is True
    assert detected["iphone_usb"]["needs_trust"] is False
    assert detected["iphone_usb"]["connected"] is True
    assert detected["iphone_usb"]["ip"] == "172.20.10.2"


def test_non_loopback_bind_rejected():
    with pytest.raises(ValueError, match="loopback only"):
        kiosk.KioskServer(("0.0.0.0", 0))


# --- 3. Live HTTP API route tests ---


def test_http_static_and_path_traversal(running_server):
    base = running_server["base_url"]
    status, headers, body = _http_request(base, "/")
    assert status == 200
    assert "text/html" in headers.get("Content-Type", "")
    assert "Сеть / Wi-Fi" in body.decode("utf-8")

    status_css, headers_css, _ = _http_request(base, "/styles.css")
    assert status_css == 200
    assert "text/css" in headers_css.get("Content-Type", "")

    status_js, _, _ = _http_request(base, "/app.js")
    assert status_js == 200

    status_bad, _, _ = _http_request(base, "/../kiosk_server.py")
    assert status_bad in (403, 404)


def test_http_api_status(running_server):
    base = running_server["base_url"]
    status, _, body = _http_request(base, "/api/status")
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is True
    assert data["internet"]["reachable"] is True
    assert data["interfaces"]["wifi"]["connected"] is True
    assert data["interfaces"]["wifi"]["ssid"] == "Office:5G"
    assert data["interfaces"]["android_usb"]["connected"] is True
    assert data["interfaces"]["iphone_usb"]["needs_trust"] is True
    assert data["session"]["ready"] is True
    assert data["session"]["token"] == "lk_test_token_987654"
    assert data["disks"]["count"] == 2
    assert data["disks"]["total_bytes"] == 512110190592 + 32000000000


def test_http_api_status_safe_fallback_when_tools_missing(tmp_path: Path):
    """On Windows/test hosts without ip/nmcli/lsblk/idevicepair, /api/status still returns 200."""
    def missing_runner(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        return 127, "", f"command not found: {cmd[0]}"

    srv = kiosk.KioskServer(
        ("127.0.0.1", 0),
        static_dir=KIOSK_UI_DIR,
        state_dir=tmp_path / "missing-dir",
        run_cmd=missing_runner,
        internet_checker=lambda: {"reachable": False, "ip": None, "target": None},
    )
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, _, body = _http_request(f"http://127.0.0.1:{port}", "/api/status")
        assert status == 200
        data = json.loads(body.decode("utf-8"))
        assert data["ok"] is True
        assert data["disks"]["count"] == 0
        assert data["session"]["ready"] is False
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)


def test_http_api_wifi_scan_and_connect(running_server):
    base = running_server["base_url"]
    calls = running_server["calls"]

    status, _, body = _http_request(base, "/api/wifi/scan")
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is True
    assert [n["ssid"] for n in data["networks"]] == [r"Lab\Main", "Office:5G", "GuestNet"]
    assert data["networks"][1]["signal"] == 88
    assert data["networks"][1]["in_use"] is True

    # Connect to regular SSID
    st_conn, _, body_conn = _http_request(
        base,
        "/api/wifi/connect",
        method="POST",
        payload={"ssid": "Office:5G", "password": "secret-pass", "hidden": False},
    )
    assert st_conn == 200
    assert json.loads(body_conn.decode("utf-8"))["ok"] is True
    assert ["nmcli", "dev", "wifi", "connect", "Office:5G", "password", "secret-pass"] in calls

    # Connect to hidden SSID (explicit NM profile + con up)
    st_hid, _, body_hid = _http_request(
        base,
        "/api/wifi/connect",
        method="POST",
        payload={"ssid": "HiddenCorp", "password": "hidden-pass-123", "hidden": True},
    )
    assert st_hid == 200
    assert json.loads(body_hid.decode("utf-8"))["ok"] is True
    assert [
        "nmcli",
        "con",
        "add",
        "type",
        "wifi",
        "ifname",
        "wlan0",
        "con-name",
        "HiddenCorp",
        "ssid",
        "HiddenCorp",
        "wifi.hidden",
        "yes",
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.psk",
        "hidden-pass-123",
    ] in calls
    assert ["nmcli", "con", "up", "HiddenCorp"] in calls

    # Missing SSID returns 400
    st_bad, _, _ = _http_request(
        base,
        "/api/wifi/connect",
        method="POST",
        payload={"ssid": "   ", "password": "pw"},
    )
    assert st_bad == 400


def test_http_api_session_regenerate(running_server):
    base = running_server["base_url"]
    state_dir = running_server["state_dir"]
    status, _, body = _http_request(base, "/api/session/regenerate", method="POST", payload={})
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is True
    assert (state_dir / "regenerate.trigger").is_file()


def test_http_api_terminal_unavailable_returns_503(running_server, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    status, _, body = _http_request(
        running_server["base_url"], "/api/terminal", method="POST", payload={}
    )
    assert status == 503
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert "Терминал" in data["message"]


def test_http_api_terminal_success_returns_200(running_server, monkeypatch):
    monkeypatch.setattr(
        kiosk,
        "launch_local_terminal",
        lambda: {"ok": True, "command": ["/usr/bin/xterm", "-e", "bash"], "pid": 777},
    )
    status, _, body = _http_request(
        running_server["base_url"], "/api/terminal", method="POST", payload={}
    )
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is True
    assert data["command"] == ["/usr/bin/xterm", "-e", "bash"]
    assert data["pid"] == 777


def test_http_api_terminal_rejects_malformed_json_without_spawning(
    running_server, monkeypatch
):
    spawns: List[int] = []

    def fake_launch():
        spawns.append(1)
        return {"ok": True, "command": ["/usr/bin/xterm", "-e", "bash"], "pid": 1}

    monkeypatch.setattr(kiosk, "launch_local_terminal", fake_launch)
    status, _, body = _http_request(
        running_server["base_url"],
        "/api/terminal",
        method="POST",
        extra_headers={"Content-Type": "application/json"},
        raw_body=b"{not valid json",
    )
    assert status == 400
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert "JSON" in data["message"]
    assert spawns == []


def test_http_api_qr_svg_real_and_fallback(running_server):
    base = running_server["base_url"]
    mock_state = running_server["mock_state"]

    # With qrencode available
    status, headers, body = _http_request(base, "/api/qr.svg")
    assert status == 200
    assert "image/svg+xml" in headers.get("Content-Type", "")
    assert "<svg" in body.decode("utf-8")

    # With qrencode absent -> clean SVG placeholder
    mock_state["qrencode_present"] = False
    status2, headers2, body2 = _http_request(base, "/api/qr.svg")
    assert status2 == 200
    assert "image/svg+xml" in headers2.get("Content-Type", "")
    text2 = body2.decode("utf-8")
    assert "<svg" in text2
    assert "qrencode" in text2


# --- 4. Supporting LiveUSB files & unslop-ui --surface automated regex audit ---


def test_start_kiosk_and_udev_files_exist():
    sh_text = START_KIOSK_PATH.read_text(encoding="utf-8")
    assert "kiosk_server.py" in sh_text
    assert "openbox" in sh_text
    assert "firstboot_tui.py" in sh_text
    assert "127.0.0.1" in sh_text
    assert sh_text.count("launch_browser()") == 1
    assert "-nocursor" not in sh_text
    assert "--no-sandbox" in sh_text
    assert "--disable-dev-shm-usage" in sh_text
    assert "export PYTHONPATH=" in sh_text

    udev_text = UDEV_RULES_PATH.read_text(encoding="utf-8")
    assert "rndis_host" in udev_text
    assert "ipheth" in udev_text
    assert "usbmuxd" in udev_text
    assert 'ENV{NM_UNMANAGED}="0"' in udev_text


def test_read_session_state_resolves_link_key_as_token(tmp_path: Path):
    """session.json written by core/liveusb_bootstrap.py uses link_key -> ready=True, token=link_key."""
    state_dir = tmp_path / "rhm-live"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "session.json").write_text(
        json.dumps(
            {
                "node_id": "live-abc123",
                "link_key": "lk_live_key_42",
                "connect_url": "https://racknerd-5a24bf9.merino-carob.ts.net/connect/ABCD-1234",
            }
        ),
        encoding="utf-8",
    )
    sess = kiosk.read_session_state(state_dir)
    assert sess["ready"] is True
    assert sess["token"] == "lk_live_key_42"
    assert sess["node_id"] == "live-abc123"


def test_trigger_session_regenerate_systemctl_and_agent_env_cleanup(tmp_path: Path, monkeypatch):
    state_dir = tmp_path / "rhm-live"
    state_dir.mkdir(parents=True, exist_ok=True)
    env_file = state_dir / "agent.env"
    env_file.write_text("MESH_USER=live-1\nMESH_TOKEN=tok-1\n", encoding="utf-8")
    (state_dir / "session.json").write_text(
        json.dumps({"node_id": "live-1", "link_key": "lk_1", "connect_url": "https://gw/c/1"}),
        encoding="utf-8",
    )

    revoked_dirs: List[str] = []

    class FakeBootstrap:
        @staticmethod
        def revoke_ephemeral_node(sd: str):
            revoked_dirs.append(sd)
            return {"ok": True}

    monkeypatch.setattr(kiosk.importlib, "import_module", lambda name: FakeBootstrap if name == "core.liveusb_bootstrap" else None)

    calls: List[List[str]] = []

    def fake_run(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        calls.append(list(cmd))
        if cmd[:2] == ["systemctl", "show"]:
            return 0, "loaded\n", ""
        if cmd[:2] == ["systemctl", "restart"]:
            return 0, "", ""
        return 127, "", "not found"

    res = kiosk.trigger_session_regenerate(state_dir=state_dir, run_cmd=fake_run)
    assert res["ok"] is True
    assert res["method"] == "systemctl_restart"
    assert revoked_dirs == [str(state_dir)]
    assert not env_file.exists()
    assert ["systemctl", "restart", "rhm-live-agent.service"] in calls


def test_trigger_session_regenerate_fallback_register_ephemeral_node(tmp_path: Path, monkeypatch):
    state_dir = tmp_path / "rhm-live"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MESH_GATEWAY", "https://racknerd-5a24bf9.merino-carob.ts.net")

    reg_calls: List[Tuple[str, str]] = []

    class FakeBootstrap:
        @staticmethod
        def revoke_ephemeral_node(sd: str):
            return {"ok": True}

        @staticmethod
        def register_ephemeral_node(gw: str, state_dir: str = ""):
            reg_calls.append((gw, state_dir))
            return {"ok": True, "node_id": "live-new"}

    monkeypatch.setattr(kiosk.importlib, "import_module", lambda name: FakeBootstrap if name == "core.liveusb_bootstrap" else None)

    def no_systemd(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        return 127, "", "command not found: systemctl"

    res = kiosk.trigger_session_regenerate(state_dir=state_dir, run_cmd=no_systemd)
    assert res["ok"] is True
    assert res["method"] == "register_ephemeral_node"
    assert reg_calls == [("https://racknerd-5a24bf9.merino-carob.ts.net", str(state_dir))]


# --- 3b. Kiosk «Открыть терминал» local shell escape ---


def test_launch_local_terminal_without_display_is_unavailable(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    # The emulator must exist so the DISPLAY check is the only failure cause.
    _which_map(monkeypatch, {"xterm": "/usr/bin/xterm"})

    def must_not_spawn(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Popen must not be called without DISPLAY")

    result = kiosk.launch_local_terminal(popen=must_not_spawn)
    assert result["ok"] is False
    assert "DISPLAY" in result["message"]


def test_launch_local_terminal_spawns_first_available_emulator(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    # Only xterm is installed: x-terminal-emulator/lxterminal/gnome-terminal are not.
    monkeypatch.setattr(
        kiosk.shutil,
        "which",
        lambda name: "/usr/bin/xterm" if name == "xterm" else None,
    )

    captured: Dict[str, Any] = {}

    class FakePopen:
        pid = 4321

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = dict(kwargs)
        return FakePopen()

    result = kiosk.launch_local_terminal(popen=fake_popen)

    assert result["ok"] is True
    assert result["command"] == ["/usr/bin/xterm", "-e", "bash"]
    assert result["pid"] == 4321
    assert captured["cmd"] == ["/usr/bin/xterm", "-e", "bash"]
    # shell=False (explicit or default) is required: never shell=True.
    assert captured["kwargs"].get("shell") is not True
    assert captured["kwargs"]["cwd"] == "/"
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["stdout"] is kiosk.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is kiosk.subprocess.DEVNULL
    assert captured["kwargs"]["env"]["DISPLAY"] == ":0"


def test_launch_local_terminal_without_emulator_is_unavailable(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(kiosk.shutil, "which", lambda name: None)

    def must_not_spawn(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Popen must not be called without an emulator")

    result = kiosk.launch_local_terminal(popen=must_not_spawn)
    assert result["ok"] is False
    assert "эмулятор" in result["message"]


def test_launch_local_terminal_popen_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(
        kiosk.shutil,
        "which",
        lambda name: "/usr/bin/xterm" if name == "xterm" else None,
    )

    def broken_popen(cmd, **kwargs):
        raise OSError("cannot open display")

    result = kiosk.launch_local_terminal(popen=broken_popen)
    assert result["ok"] is False
    assert "cannot open display" in result["message"]


# --- 3c. Allowlisted application launcher (/api/apps) ---------------------


def _which_map(monkeypatch, mapping: Dict[str, str]) -> None:
    """Patch shutil.which so only the mapped binaries resolve."""
    monkeypatch.setattr(kiosk.shutil, "which", lambda name: mapping.get(name))


def _fake_popen(captured: Dict[str, Any], pid: int = 2468):
    """Return a Popen stand-in that records its argv/kwargs and yields ``pid``."""

    class _FakeProc:
        pass

    proc = _FakeProc()
    proc.pid = pid

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = dict(kwargs)
        return proc

    return fake_popen


APP_LABELS = [
    "Рабочий стол (LXDE)",
    "Терминал",
    "Файловый менеджер",
    "Редактор разделов (GParted)",
    "Текстовый редактор",
]


def test_list_local_apps_allowlist_and_availability(monkeypatch):
    _which_map(monkeypatch, {"rhm-open-desktop": "/usr/local/bin/rhm-open-desktop", "xterm": "/usr/bin/xterm", "pcmanfm": "/usr/bin/pcmanfm"})

    apps = kiosk.list_local_apps()
    assert [a["id"] for a in apps] == ["desktop", "terminal", "files", "partitions", "editor"]
    assert [a["label"] for a in apps] == APP_LABELS
    assert [a["available"] for a in apps] == [True, True, True, False, False]
    assert apps[1]["reason"] == ""
    assert apps[2]["reason"] == ""
    assert "gparted" in apps[3]["reason"]
    assert "mousepad" in apps[4]["reason"]
    # Stable shape: exactly the four contract keys.
    assert all(set(a) == {"id", "label", "available", "reason"} for a in apps)

    # Nothing installed -> all unavailable, each with a non-empty Russian reason.
    _which_map(monkeypatch, {})
    missing = kiosk.list_local_apps()
    assert [a["available"] for a in missing] == [False, False, False, False, False]
    assert all(a["reason"] for a in missing)


def test_http_api_apps_returns_three_ids_and_availability(running_server, monkeypatch):
    _which_map(monkeypatch, {"rhm-open-desktop": "/usr/local/bin/rhm-open-desktop", "xterm": "/usr/bin/xterm", "gparted": "/usr/sbin/gparted", "mousepad": "/usr/bin/mousepad"})
    base = running_server["base_url"]

    status, _, body = _http_request(base, "/api/apps")
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is True
    apps = data["apps"]
    assert [a["id"] for a in apps] == ["desktop", "terminal", "files", "partitions", "editor"]
    assert [a["label"] for a in apps] == APP_LABELS
    assert [a["available"] for a in apps] == [True, True, False, True, True]
    assert "pcmanfm" in apps[2]["reason"]

    # The catalogue stays behind the existing Host/Origin/Sec-Fetch gate.
    st_evil, _, _ = _http_request(
        base, "/api/apps", extra_headers={"Origin": "https://evil.example.com"}
    )
    assert st_evil == 403


def test_http_api_app_launch_happy_path_uses_allowlisted_argv(
    running_server, monkeypatch
):
    monkeypatch.setenv("DISPLAY", ":0")
    _which_map(
        monkeypatch,
        {
            "xterm": "/usr/bin/xterm",
            "pcmanfm": "/usr/bin/pcmanfm",
            "mousepad": "/usr/bin/mousepad",
        },
    )
    real_isdir = kiosk.os.path.isdir
    monkeypatch.setattr(
        kiosk.os.path,
        "isdir",
        lambda p: True if p == "/mnt/rescue" else real_isdir(p),
    )
    captured: Dict[str, Any] = {}
    monkeypatch.setattr(kiosk.subprocess, "Popen", _fake_popen(captured, pid=2468))

    status, _, body = _http_request(
        running_server["base_url"],
        "/api/apps/launch",
        method="POST",
        payload={"id": "files"},
    )
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is True
    assert data["pid"] == 2468
    assert data["command"] == ["/usr/bin/pcmanfm", "/mnt/rescue"]

    assert captured["cmd"] == ["/usr/bin/pcmanfm", "/mnt/rescue"]
    kwargs = captured["kwargs"]
    assert kwargs["shell"] is False
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] is kiosk.subprocess.DEVNULL
    assert kwargs["stderr"] is kiosk.subprocess.DEVNULL
    assert kwargs["env"]["DISPLAY"] == ":0"


def test_http_api_app_launch_ignores_request_supplied_command(
    running_server, monkeypatch
):
    monkeypatch.setenv("DISPLAY", ":0")
    _which_map(monkeypatch, {"mousepad": "/usr/bin/mousepad"})
    captured: Dict[str, Any] = {}
    monkeypatch.setattr(kiosk.subprocess, "Popen", _fake_popen(captured, pid=99))

    status, _, body = _http_request(
        running_server["base_url"],
        "/api/apps/launch",
        method="POST",
        payload={
            "id": "editor",
            "command": ["/bin/sh", "-c", "id"],
            "argv": ["/bin/sh"],
            "path": "/etc/shadow",
            "exe": "/bin/sh",
        },
    )
    assert status == 200
    assert json.loads(body.decode("utf-8"))["command"] == ["/usr/bin/mousepad"]
    assert captured["cmd"] == ["/usr/bin/mousepad"]


def test_http_api_app_launch_unknown_id_returns_400(running_server, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    _which_map(monkeypatch, {"xterm": "/usr/bin/xterm"})
    captured: Dict[str, Any] = {}
    monkeypatch.setattr(kiosk.subprocess, "Popen", _fake_popen(captured))
    base = running_server["base_url"]

    for payload in (
        {"id": "shell"},
        {"id": "rm -rf /"},
        {"id": "../../bin/sh"},
        {"id": "TERMINAL"},
        {"id": 7},
        {"command": "bash"},
        {},
    ):
        status, _, body = _http_request(
            base, "/api/apps/launch", method="POST", payload=payload
        )
        assert status == 400, payload
        data = json.loads(body.decode("utf-8"))
        assert data["ok"] is False
        assert data["message"]
    # Unknown/missing ids must never reach Popen.
    assert captured == {}


def test_http_api_app_launch_unavailable_returns_503(running_server, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    _which_map(monkeypatch, {})

    def must_not_spawn(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Popen must not be called for an unavailable app")

    monkeypatch.setattr(kiosk.subprocess, "Popen", must_not_spawn)

    status, _, body = _http_request(
        running_server["base_url"],
        "/api/apps/launch",
        method="POST",
        payload={"id": "editor"},
    )
    assert status == 503
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert "mousepad" in data["message"]


def test_http_api_app_launch_popen_failure_returns_503_without_raising(
    running_server, monkeypatch
):
    monkeypatch.setenv("DISPLAY", ":0")
    _which_map(monkeypatch, {"mousepad": "/usr/bin/mousepad"})

    def boom(*args: Any, **kwargs: Any):
        raise OSError("cannot open display :0")

    monkeypatch.setattr(kiosk.subprocess, "Popen", boom)

    status, _, body = _http_request(
        running_server["base_url"],
        "/api/apps/launch",
        method="POST",
        payload={"id": "editor"},
    )
    assert status == 503
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert "cannot open display" in data["message"]


def test_launch_local_app_terminal_argv_and_display_check(monkeypatch):
    _which_map(monkeypatch, {"xterm": "/usr/bin/xterm"})

    monkeypatch.delenv("DISPLAY", raising=False)

    def must_not_spawn(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Popen must not be called without DISPLAY")

    no_display = kiosk.launch_local_app("terminal", popen=must_not_spawn)
    assert no_display["ok"] is False
    assert "DISPLAY" in no_display["message"]
    assert "Терминал" in no_display["message"]

    monkeypatch.setenv("DISPLAY", ":0")
    captured: Dict[str, Any] = {}
    result = kiosk.launch_local_app(
        "terminal", popen=_fake_popen(captured, pid=4321)
    )
    assert result["ok"] is True
    assert result["command"] == ["/usr/bin/xterm", "-e", "bash"]
    assert result["pid"] == 4321
    assert captured["cmd"] == ["/usr/bin/xterm", "-e", "bash"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["env"]["DISPLAY"] == ":0"

    # The legacy helper delegates to the same allowlisted terminal entry.
    legacy = kiosk.launch_local_terminal(popen=_fake_popen({}, pid=1))
    assert legacy["ok"] is True
    assert legacy["command"] == ["/usr/bin/xterm", "-e", "bash"]


def test_launch_local_app_files_uses_rescue_dir_or_root(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    _which_map(monkeypatch, {"pcmanfm": "/usr/bin/pcmanfm"})
    real_isdir = kiosk.os.path.isdir

    monkeypatch.setattr(
        kiosk.os.path,
        "isdir",
        lambda p: True if p == "/mnt/rescue" else real_isdir(p),
    )
    rescue = kiosk.launch_local_app("files", popen=_fake_popen({}))
    assert rescue["command"] == ["/usr/bin/pcmanfm", "/mnt/rescue"]

    monkeypatch.setattr(
        kiosk.os.path,
        "isdir",
        lambda p: False if p == "/mnt/rescue" else real_isdir(p),
    )
    fallback = kiosk.launch_local_app("files", popen=_fake_popen({}))
    assert fallback["command"] == ["/usr/bin/pcmanfm", "/root"]


def test_firstboot_tui_renders_session_json(tmp_path: Path):
    tui_path = REPO_ROOT / "ops" / "live-usb" / "firstboot_tui.py"
    spec = importlib.util.spec_from_file_location("rhm_firstboot_tui_test", str(tui_path))
    assert spec is not None and spec.loader is not None
    tui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tui)

    state_dir = tmp_path / "rhm-live"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "session.json").write_text(
        json.dumps(
            {
                "node_id": "live-tui-99",
                "connect_url": "https://racknerd-5a24bf9.merino-carob.ts.net/connect/TUI-99",
                "link_key": "lk_tui_secret_99",
            }
        ),
        encoding="utf-8",
    )
    banner = tui.render_banner(state_dir=state_dir)
    assert "live-tui-99" in banner
    assert "https://racknerd-5a24bf9.merino-carob.ts.net/connect/TUI-99" in banner
    assert "lk_tui_secret_99" in banner
    assert "FULL ACCESS" in banner
    assert callable(tui.run_loop)


def test_unslop_ui_surface_regex_audit():
    """Automated regex audit verifying zero unslop-ui --surface tells in kiosk_ui/."""
    ui_files = [
        KIOSK_UI_DIR / "index.html",
        KIOSK_UI_DIR / "styles.css",
        KIOSK_UI_DIR / "app.js",
    ]
    for path in ui_files:
        assert path.is_file(), f"Missing UI file: {path}"

    combined = "\n".join(p.read_text(encoding="utf-8") for p in ui_files)
    css_text = (KIOSK_UI_DIR / "styles.css").read_text(encoding="utf-8")
    html_text = (KIOSK_UI_DIR / "index.html").read_text(encoding="utf-8")

    # Forbidden unslop-ui --surface patterns (U1-U20)
    forbidden_patterns = [
        ("gradient", r"(?:linear|radial|conic)-gradient\s*\("),
        ("blur/backdrop-filter", r"(?:backdrop-filter|filter\s*:\s*blur)"),
        ("animate-pulse/bounce", r"(?:animate-pulse|animate-bounce|@keyframes\s+(?:pulse|bounce))"),
        ("transition-all", r"transition\s*:\s*all\b"),
        ("scale-transform", r"(?:hover:scale-|transform\s*:[^;]*scale\()"),
        ("heavy-font-weight", r"font-weight\s*:\s*(?:700|800|900|bold|extrabold|black)\b"),
        ("decorative-numbering", r"\b0[1-9]\s*//"),
        ("leaked-design-notes", r"\b(?:unslop|botsplain|eyebrow|wireframe)\b"),
        (
            "decorative-emoji",
            r"[\U0001F300-\U0001FAFF\u2600-\u27BF]",
        ),
    ]

    for rule_name, pattern in forbidden_patterns:
        matches = re.findall(pattern, combined, flags=re.IGNORECASE)
        assert not matches, f"unslop-ui violation ({rule_name}): {matches}"

    # Verify required dark surface tokens & transition timing
    assert "#0e1116" in css_text
    assert "#161b22" in css_text
    assert "#2b313a" in css_text
    assert "0 1px 2px rgb(0 0 0 / 0.05)" in css_text
    assert "150ms ease-out" in css_text

    # Verify required Russian UI strings
    for required_text in (
        "Сеть / Wi-Fi",
        "Готово к подключению MCP",
        "Нажмите «Доверять» на iPhone и введите код-пароль",
        "Обновить список",
        "Сменить Wi-Fi",
        "Новый токен",
        "Android USB-модем",
        "iPhone USB-модем",
        "Скрытая сеть",
        "Клавиатура",
    ):
        assert required_text in html_text, f"Missing required Russian label: {required_text}"


def test_kiosk_ui_exposes_open_terminal_control():
    html_text = (KIOSK_UI_DIR / "index.html").read_text(encoding="utf-8")
    js_text = (KIOSK_UI_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="btn-terminal"' in html_text
    assert "Открыть терминал" in html_text
    # The ready-view control sits next to the two existing head actions.
    ready_head = html_text.split('id="btn-change-wifi"', 1)[1].split("</div>", 1)[0]
    assert 'id="btn-terminal"' in ready_head
    assert 'id="btn-new-token"' in ready_head

    assert "btn-terminal" in js_text
    assert "/api/terminal" in js_text
    assert "openTerminal" in js_text


def test_kiosk_initial_view_prefers_network_until_a_ready_session_exists():
    """No internet -> Wi-Fi/LAN screen; a ready session -> QR + token screen."""
    html_text = (KIOSK_UI_DIR / "index.html").read_text(encoding="utf-8")
    js_text = (KIOSK_UI_DIR / "app.js").read_text(encoding="utf-8")

    # The network view is the visible default; the ready view starts hidden.
    assert '<section id="view-network" class="view-panel"' in html_text
    assert '<section id="view-ready" class="view-panel is-hidden"' in html_text

    # Both the status poll and the Wi-Fi scan run on startup.
    assert "fetchStatus();" in js_text
    assert "scanWifi();" in js_text
    assert "window.setInterval(fetchStatus, 3000);" in js_text

    # Only a registered session (internet + gateway) switches to the QR view.
    assert "session.ready" in js_text
    assert 'setView("ready", false)' in js_text

    # The ready view really contains the QR and the session token.
    ready_html = html_text.split('id="view-ready"', 1)[1]
    assert 'id="qr-image"' in ready_html
    assert 'id="session-token"' in ready_html
    assert "/api/qr.svg" in ready_html


def test_http_security_headers_and_host_origin_csrf_rejection(running_server):
    base = running_server["base_url"]

    # Security headers present on normal GET
    st_ok, headers, _ = _http_request(base, "/api/status")
    assert st_ok == 200
    low_headers = {k.lower(): v for k, v in headers.items()}
    assert low_headers.get("x-frame-options") == "DENY"
    assert low_headers.get("referrer-policy") == "no-referrer"
    csp = low_headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp

    # DNS rebinding / invalid Host header rejected with 403
    st_host, _, _ = _http_request(
        base,
        "/api/status",
        extra_headers={"Host": "evil.example.com:8787"},
    )
    assert st_host == 403

    # Cross-origin Origin header rejected with 403
    st_origin, _, _ = _http_request(
        base,
        "/api/status",
        extra_headers={"Origin": "https://evil.example.com"},
    )
    assert st_origin == 403

    # Cross-site Sec-Fetch-Site rejected with 403
    st_sfs, _, _ = _http_request(
        base,
        "/api/status",
        extra_headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert st_sfs == 403

    # POST without Content-Type: application/json rejected with 403 (CSRF protection)
    st_ct, _, _ = _http_request(
        base,
        "/api/session/regenerate",
        method="POST",
        extra_headers={"Content-Type": "text/plain"},
        raw_body=b"{}",
    )
    assert st_ct == 403


def test_connect_wifi_rejects_leading_dash_and_invalid_inputs():
    called = []

    def fail_if_called(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        called.append(cmd)
        return 0, "", ""

    # Leading '-' SSID rejected
    res_dash = kiosk.connect_wifi("-o", password="pw", run_cmd=fail_if_called)
    assert res_dash["ok"] is False
    res_flag = kiosk.connect_wifi("--help", password="pw", run_cmd=fail_if_called)
    assert res_flag["ok"] is False

    # SSID > 32 UTF-8 bytes rejected
    res_long_ssid = kiosk.connect_wifi("a" * 33, password="pw", run_cmd=fail_if_called)
    assert res_long_ssid["ok"] is False

    # Control char in SSID rejected
    res_ctrl_ssid = kiosk.connect_wifi("MyNet\nEvil", password="pw", run_cmd=fail_if_called)
    assert res_ctrl_ssid["ok"] is False

    # Password > 64 chars or control char rejected
    res_long_pw = kiosk.connect_wifi("MyNet", password="p" * 65, run_cmd=fail_if_called)
    assert res_long_pw["ok"] is False
    res_ctrl_pw = kiosk.connect_wifi("MyNet", password="pass\x00word", run_cmd=fail_if_called)
    assert res_ctrl_pw["ok"] is False

    assert called == []


def test_default_run_cmd_timeout_redacts_password_args(monkeypatch):
    def fake_timeout(*args, **kwargs):
        raise kiosk.subprocess.TimeoutExpired(cmd=args[0], timeout=1.0)

    monkeypatch.setattr(kiosk.subprocess, "run", fake_timeout)
    rc, out, err = kiosk.default_run_cmd(
        ["nmcli", "dev", "wifi", "connect", "OfficeWifi", "password", "SuperSecretPass99"],
        timeout=1.0,
    )
    assert rc == 124
    assert out == ""
    assert err == "command timed out: nmcli"
    assert "SuperSecretPass99" not in err


# --- 5. «Настройки сети»: GET /api/network snapshot ------------------------

NETWORK_IP_ADDR_JSON = json.dumps(
    [
        {
            "ifindex": 1,
            "ifname": "lo",
            "operstate": "UNKNOWN",
            "address": "00:00:00:00:00:00",
            "addr_info": [
                {"family": "inet", "local": "127.0.0.1", "prefixlen": 8},
            ],
        },
        {
            "ifindex": 2,
            "ifname": "eth0",
            "operstate": "UP",
            "address": "52:54:00:12:34:56",
            "addr_info": [
                {"family": "inet", "local": "192.168.1.50", "prefixlen": 24},
                {"family": "inet6", "local": "fe80::5054:ff:fe12:3456", "prefixlen": 64},
            ],
        },
        {
            "ifindex": 3,
            "ifname": "wlan0",
            "operstate": "DOWN",
            "address": "aa:bb:cc:dd:ee:ff",
            "addr_info": [],
        },
    ]
)

NETWORK_ROUTE_JSON = json.dumps(
    [
        {
            "dst": "default",
            "gateway": "192.168.1.1",
            "dev": "eth0",
            "protocol": "dhcp",
            "metric": 100,
            "pref": "medium",
        }
    ]
)

WG_PRIVATE_KEY = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
WG_PEER_KEY = "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg="

OPENVPN_CONFIG = (
    "client\n"
    "dev tun\n"
    "proto udp\n"
    "remote vpn.example.com 1194\n"
    "resolv-retry infinite\n"
    "nobind\n"
    "<ca>\n"
    "-----BEGIN CERTIFICATE-----\n"
    "MIIB\n"
    "-----END CERTIFICATE-----\n"
    "</ca>\n"
)


def _wireguard_payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": "office",
        "private_key": WG_PRIVATE_KEY,
        "address": "10.7.0.2",
        "prefix": 24,
        "peer_public_key": WG_PEER_KEY,
        "endpoint": "vpn.example.com:51820",
        "allowed_ips": "0.0.0.0/0, ::/0",
        "keepalive": 25,
    }
    payload.update(overrides)
    return payload


def _network_run_cmd(calls: List[List[str]], state: Dict[str, Any]):
    """Deterministic ip/nmcli stand-in for the network-settings endpoints."""

    def runner(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        calls.append(list(cmd))
        if not cmd:
            return 1, "", "empty"
        exe = cmd[0]
        if exe == "ip":
            if "-j" in cmd and "addr" in cmd:
                return 0, NETWORK_IP_ADDR_JSON, ""
            if "-j" in cmd and "route" in cmd:
                return 0, NETWORK_ROUTE_JSON, ""
            if "route" in cmd:
                return 0, "default via 192.168.1.1 dev eth0 metric 100\n", ""
            return 0, "", ""
        if exe == "nmcli":
            failure = state.get("fail") or ""
            if "connection" in cmd and "show" in cmd and "import" not in cmd:
                if failure == "show":
                    return 1, "", "Error: NetworkManager is not running"
                return 0, (state["active"] if "--active" in cmd else state["names"]), ""
            action = cmd[2] if len(cmd) > 2 else ""
            if action == "import":
                config_path = Path(cmd[-1])
                existed = config_path.is_file()
                state["openvpn_temp"] = {
                    "path": config_path,
                    "existed": existed,
                    "mode": (
                        stat.S_IMODE(config_path.stat().st_mode) if existed else None
                    ),
                }
            if failure and failure == action:
                detail = f"Error: nmcli could not {action} the connection"
                if action == "add":
                    detail += f" password={state.get('password', '')}"
                return 1, "", detail
            return 0, "Connection successfully activated\n", ""
        return 127, "", f"not mocked: {exe}"

    return runner


@pytest.fixture()
def network_server(tmp_path: Path, monkeypatch):
    """KioskServer with a mocked ip/nmcli and a real tmp resolv.conf/session."""
    state_dir = tmp_path / "live-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "session.json").write_text(
        json.dumps(
            {
                "ready": True,
                "connect_url": "https://racknerd-5a24bf9.merino-carob.ts.net/connect/NET-1",
                "node_id": "live-net-01",
                "link_key": "lk_net_secret_01",
                # Must never be published by GET /api/network.
                "device_token": "dev_tok_must_not_leak_01",
                "tunnel_status": "connected",
            }
        ),
        encoding="utf-8",
    )

    resolv = tmp_path / "resolv.conf"
    resolv.write_text(
        "# Generated by NetworkManager\n"
        "nameserver 1.1.1.1\n"
        "nameserver 8.8.8.8\n"
        "search lan\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(kiosk, "RESOLV_CONF_PATH", resolv)
    monkeypatch.setattr(kiosk, "NM_CONNECTIONS_DIR", tmp_path / "system-connections")

    calls: List[List[str]] = []
    state: Dict[str, Any] = {
        "active": "Wired connection 1:eth0:activated\n",
        "names": "rhm-pppoe\nWired connection 1\n",
        "fail": None,
        "password": "",
    }
    monkeypatch.setattr(
        kiosk.shutil,
        "which",
        lambda name: "/usr/bin/nmcli" if name == "nmcli" else None,
    )

    server = kiosk.KioskServer(
        ("127.0.0.1", 0),
        static_dir=KIOSK_UI_DIR,
        state_dir=state_dir,
        run_cmd=_network_run_cmd(calls, state),
        internet_checker=lambda: {
            "reachable": True,
            "ip": "192.168.1.50",
            "target": "1.1.1.1",
        },
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "calls": calls,
            "state": state,
            "tmp_path": tmp_path,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_api_network_snapshot_parses_ip_nmcli_and_resolv(
    network_server, monkeypatch
):
    base = network_server["base_url"]
    status, _, body = _http_request(base, "/api/network")
    assert status == 200
    data = json.loads(body.decode("utf-8"))

    assert data["ok"] is True
    assert data["hostname"] == kiosk.socket.gethostname()
    assert data["node_id"] == "live-net-01"
    assert data["link_key"] == "lk_net_secret_01"
    assert data["dns"] == ["1.1.1.1", "8.8.8.8"]
    assert data["nmcli_available"] is True
    assert data["routes"] == [
        {
            "destination": "default",
            "gateway": "192.168.1.1",
            "device": "eth0",
            "metric": 100,
        }
    ]

    interfaces = {item["name"]: item for item in data["interfaces"]}
    assert set(interfaces) == {"eth0", "wlan0"}  # loopback is never listed

    eth0 = interfaces["eth0"]
    assert eth0["mac"] == "52:54:00:12:34:56"
    assert eth0["state"] == "up"
    assert eth0["connection"] == "Wired connection 1"
    assert eth0["connection_state"] == "activated"
    assert eth0["ipv4"] == "192.168.1.50"
    assert eth0["prefix"] == 24
    assert eth0["gateway"] == "192.168.1.1"
    assert eth0["dns"] == ["1.1.1.1", "8.8.8.8"]

    wlan0 = interfaces["wlan0"]
    assert wlan0["state"] == "down"
    assert wlan0["connection"] == ""
    assert wlan0["ipv4"] is None
    assert wlan0["dns"] == []
    assert wlan0["gateway"] == ""

    # The device token from session.json must never reach the browser.
    raw = body.decode("utf-8")
    assert "dev_tok_must_not_leak_01" not in raw
    assert "device_token" not in raw
    assert "token" not in data

    # The snapshot sits behind the existing Host/Origin/Sec-Fetch gate.
    st_evil, _, _ = _http_request(
        base, "/api/network", extra_headers={"Origin": "https://evil.example.com"}
    )
    assert st_evil == 403


def test_network_snapshot_never_raises_without_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(kiosk, "RESOLV_CONF_PATH", tmp_path / "missing-resolv.conf")
    monkeypatch.setattr(kiosk.shutil, "which", lambda name: None)

    def missing(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        return 127, "", f"command not found: {cmd[0]}"

    snapshot = kiosk._network_snapshot(
        run_cmd=missing, state_dir=tmp_path / "no-session"
    )
    assert snapshot["ok"] is True
    assert snapshot["interfaces"] == []
    assert snapshot["dns"] == []
    assert snapshot["routes"] == []
    assert snapshot["node_id"] == ""
    assert snapshot["link_key"] == ""
    assert snapshot["nmcli_available"] is False


def test_read_resolv_conf_size_cap_and_dedup(tmp_path: Path):
    # Comments larger than the cap must hide anything after them.
    junk = "#" + ("x" * 4000) + "\n"
    capped = tmp_path / "capped.conf"
    capped.write_text(
        (junk * 3) + "nameserver 9.9.9.9\n", encoding="utf-8"
    )
    assert kiosk._read_resolv_conf(capped) == []

    parsed = tmp_path / "parsed.conf"
    parsed.write_text(
        "nameserver 9.9.9.9\n"
        "nameserver 9.9.9.9\n"
        "options edns0\n"
        "nameserver bad-value\n"
        "nameserver 1.1.1.1\n",
        encoding="utf-8",
    )
    assert kiosk._read_resolv_conf(parsed) == ["9.9.9.9", "1.1.1.1"]
    assert kiosk._read_resolv_conf(tmp_path / "absent.conf") == []


# --- 5b. POST /api/network/ip ----------------------------------------------


def test_network_ip_manual_and_auto_apply_through_nmcli(network_server):
    base = network_server["base_url"]
    calls = network_server["calls"]

    status, _, body = _http_request(
        base,
        "/api/network/ip",
        method="POST",
        payload={
            "interface": "eth0",
            "mode": "manual",
            "address": "192.168.1.77",
            "prefix": 24,
            "gateway": "192.168.1.1",
            "dns": "1.1.1.1, 8.8.8.8",
        },
    )
    data = json.loads(body.decode("utf-8"))
    assert status == 200, data
    assert data["ok"] is True
    assert data["mode"] == "manual"
    assert data["interface"] == "eth0"
    assert data["connection"] == "Wired connection 1"
    assert [
        "nmcli",
        "con",
        "mod",
        "Wired connection 1",
        "ipv4.method",
        "manual",
        "ipv4.addresses",
        "192.168.1.77/24",
        "ipv4.gateway",
        "192.168.1.1",
        "ipv4.dns",
        "1.1.1.1,8.8.8.8",
    ] in calls
    assert ["nmcli", "con", "up", "Wired connection 1"] in calls

    status_auto, _, body_auto = _http_request(
        base,
        "/api/network/ip",
        method="POST",
        payload={"interface": "eth0", "mode": "auto", "dns": ""},
    )
    auto_data = json.loads(body_auto.decode("utf-8"))
    assert status_auto == 200, auto_data
    assert auto_data["mode"] == "auto"
    assert [
        "nmcli",
        "con",
        "mod",
        "Wired connection 1",
        "ipv4.method",
        "auto",
        "ipv4.addresses",
        "",
        "ipv4.gateway",
        "",
        "ipv4.dns",
        "",
        "ipv4.ignore-auto-dns",
        "no",
    ] in calls
    assert calls.count(["nmcli", "con", "up", "Wired connection 1"]) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"interface": "eth0", "mode": "manual", "address": "999.1.1.1", "prefix": 24},
        {"interface": "eth0", "mode": "manual", "address": "192.168.1.10", "prefix": 33},
        {"interface": "eth0", "mode": "manual", "address": "", "prefix": 24},
        {"interface": "eth0; reboot", "mode": "auto"},
        {"interface": "eth 0", "mode": "auto"},
        {"interface": "", "mode": "auto"},
        {"interface": "eth0", "mode": "static"},
        {
            "interface": "eth0",
            "mode": "manual",
            "address": "192.168.1.10",
            "prefix": 24,
            "gateway": "ne-shlyuz",
        },
        {
            "interface": "eth0",
            "mode": "manual",
            "address": "192.168.1.10",
            "prefix": "24\nreboot",
        },
        {"interface": "eth0", "mode": "auto", "dns": "1.1.1.1; reboot"},
        {"interface": "eth0", "mode": "auto", "dns": "1.1.1.1 8.8.8.8"},
        {"interface": "eth0", "mode": "auto", "dns": "1.1.1.1,999.9.9.9"},
    ],
)
def test_network_ip_rejects_invalid_input(network_server, payload):
    base = network_server["base_url"]
    calls = network_server["calls"]

    status, _, body = _http_request(
        base, "/api/network/ip", method="POST", payload=payload
    )
    assert status == 400, body
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert data["message"]
    # Validation happens before anything reaches NetworkManager.
    assert not [cmd for cmd in calls if cmd[:3] == ["nmcli", "con", "mod"]]


# --- 5c. POST /api/network/pppoe -------------------------------------------


def test_network_pppoe_argv_and_password_redaction(network_server):
    base = network_server["base_url"]
    calls = network_server["calls"]

    status, _, body = _http_request(
        base,
        "/api/network/pppoe",
        method="POST",
        payload={
            "username": "user@isp.example",
            "password": "s3cr3t-Pass",
            "service": "internet",
        },
    )
    data = json.loads(body.decode("utf-8"))
    assert status == 200, data
    assert data["ok"] is True
    assert data["redacted"] is True
    assert data["connection"] == "rhm-pppoe"

    assert ["nmcli", "con", "delete", "rhm-pppoe"] in calls
    assert [
        "nmcli",
        "con",
        "add",
        "type",
        "pppoe",
        "con-name",
        "rhm-pppoe",
        "ifname",
        "*",
        "username",
        "user@isp.example",
        "password",
        "s3cr3t-Pass",
        # The PPPoE service is the pppoe.service property, in the
        # `<setting>.<property>` form `nmcli con add` documents.
        "pppoe.service",
        "internet",
    ] in calls
    assert ["nmcli", "con", "up", "rhm-pppoe"] in calls

    assert "s3cr3t-Pass" not in body.decode("utf-8")


def test_network_pppoe_without_service_skips_service_arg(network_server):
    calls = network_server["calls"]
    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/pppoe",
        method="POST",
        payload={"username": "user@isp", "password": "pw"},
    )
    assert status == 200, body
    add_calls = [cmd for cmd in calls if cmd[:5] == ["nmcli", "con", "add", "type", "pppoe"]]
    assert len(add_calls) == 1
    assert "service" not in add_calls[0]


def test_network_pppoe_failure_redacts_password(network_server):
    state = network_server["state"]
    state["fail"] = "add"
    state["password"] = "s3cr3t-Pass"

    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/pppoe",
        method="POST",
        payload={"username": "user@isp", "password": "s3cr3t-Pass"},
    )
    assert status == 503
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert "s3cr3t-Pass" not in body.decode("utf-8")
    assert "***" in data["message"]


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "", "password": "pw"},
        {"username": "user name", "password": "pw"},
        {"username": "user@isp", "password": ""},
        {"username": "user@isp", "password": "pass\nword"},
        {"username": "user@isp", "password": "pw", "service": "bad service"},
        {"username": "user@isp", "password": "pw", "service": "svc;reboot"},
    ],
)
def test_network_pppoe_rejects_invalid_input(network_server, payload):
    status, _, body = _http_request(
        network_server["base_url"], "/api/network/pppoe", method="POST", payload=payload
    )
    assert status == 400, body
    assert json.loads(body.decode("utf-8"))["ok"] is False
    assert not [
        cmd
        for cmd in network_server["calls"]
        if cmd[:5] == ["nmcli", "con", "add", "type", "pppoe"]
    ]


# --- 5d. POST /api/network/wireguard ---------------------------------------


def test_network_wireguard_writes_0600_keyfile_without_echoing_key(
    network_server, monkeypatch
):
    connections_dir = network_server["tmp_path"] / "system-connections"
    monkeypatch.setattr(kiosk, "NM_CONNECTIONS_DIR", connections_dir)

    opened: List[Tuple[str, int]] = []
    real_open = os.open

    def spy_open(path, flags, mode=0o777, **kwargs):
        opened.append((str(path), mode))
        return real_open(path, flags, mode, **kwargs)

    # Windows cannot express POSIX bits, so also record the requested mode.
    monkeypatch.setattr(kiosk.os, "open", spy_open)

    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/wireguard",
        method="POST",
        payload=_wireguard_payload(),
    )
    data = json.loads(body.decode("utf-8"))
    assert status == 200, data
    assert data["ok"] is True
    assert data["redacted"] is True
    assert data["connection"] == "rhm-wg-office"

    raw = body.decode("utf-8")
    assert WG_PRIVATE_KEY not in raw
    assert "private_key" not in raw

    keyfile = connections_dir / "rhm-wg-office.nmconnection"
    assert keyfile.is_file()
    content = keyfile.read_text(encoding="utf-8")
    for expected in (
        "[connection]",
        "id=rhm-wg-office",
        "type=wireguard",
        "autoconnect=true",
        "[wireguard]",
        f"private-key={WG_PRIVATE_KEY}",
        f"[wireguard-peer.{WG_PEER_KEY}]",
        "endpoint=vpn.example.com:51820",
        "allowed-ips=0.0.0.0/0;::/0;",
        "persistent-keepalive=25",
        "[ipv4]",
        "method=manual",
        "address1=10.7.0.2/24",
        "[ipv6]",
    ):
        assert expected in content, expected

    assert (str(keyfile), 0o600) in opened
    if os.name == "posix":
        assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600

    calls = network_server["calls"]
    assert ["nmcli", "con", "reload"] in calls
    assert ["nmcli", "con", "up", "rhm-wg-office"] in calls
    # The keyfile never travels through a shell or a formatted command line.
    assert not any(isinstance(cmd, str) for cmd in calls)


@pytest.mark.parametrize(
    "payload",
    [
        _wireguard_payload(private_key="not-a-key"),
        _wireguard_payload(peer_public_key="short"),
        _wireguard_payload(private_key=WG_PEER_KEY, peer_public_key=WG_PEER_KEY),
        _wireguard_payload(name="../etc/passwd"),
        _wireguard_payload(name="office;reboot"),
        _wireguard_payload(address="10.7.0.999"),
        _wireguard_payload(prefix=33),
        _wireguard_payload(endpoint="vpn.example.com"),
        _wireguard_payload(endpoint="vpn.example.com:0"),
        _wireguard_payload(endpoint="vpn.example.com:70000"),
        _wireguard_payload(allowed_ips="not-a-cidr"),
        _wireguard_payload(allowed_ips=""),
        _wireguard_payload(keepalive=70000),
        _wireguard_payload(keepalive="25;reboot"),
    ],
)
def test_network_wireguard_rejects_invalid_input(
    network_server, monkeypatch, payload
):
    connections_dir = network_server["tmp_path"] / "wg-invalid"
    monkeypatch.setattr(kiosk, "NM_CONNECTIONS_DIR", connections_dir)

    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/wireguard",
        method="POST",
        payload=payload,
    )
    assert status == 400, body
    assert json.loads(body.decode("utf-8"))["ok"] is False
    assert not connections_dir.exists()
    assert not [cmd for cmd in network_server["calls"] if cmd[:2] == ["nmcli", "con"]]


def test_network_wireguard_removes_keyfile_when_activation_fails(
    network_server, monkeypatch
):
    connections_dir = network_server["tmp_path"] / "wg-failing"
    monkeypatch.setattr(kiosk, "NM_CONNECTIONS_DIR", connections_dir)
    network_server["state"]["fail"] = "up"

    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/wireguard",
        method="POST",
        payload=_wireguard_payload(),
    )
    assert status == 503
    assert json.loads(body.decode("utf-8"))["ok"] is False
    assert not (connections_dir / "rhm-wg-office.nmconnection").exists()
    assert WG_PRIVATE_KEY not in body.decode("utf-8")


# --- 5e. POST /api/network/openvpn -----------------------------------------


def test_network_openvpn_imports_temp_file_and_cleans_up(network_server):
    calls = network_server["calls"]
    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/openvpn",
        method="POST",
        payload={"name": "corp", "config": OPENVPN_CONFIG},
    )
    data = json.loads(body.decode("utf-8"))
    assert status == 200, data
    assert data["ok"] is True
    assert data["redacted"] is True
    assert data["connection"] == "corp"

    imports = [
        cmd
        for cmd in calls
        if cmd[:5] == ["nmcli", "connection", "import", "type", "openvpn"]
    ]
    assert len(imports) == 1
    argv = imports[0]
    assert argv[5] == "file"
    config_path = Path(argv[6])
    assert config_path.name == "corp.ovpn"
    assert config_path.parent.name.startswith("rhm-ovpn-")
    assert ["nmcli", "con", "up", "corp"] in calls

    recorded = network_server["state"]["openvpn_temp"]
    assert recorded["existed"] is True
    if os.name == "posix":
        assert recorded["mode"] == 0o600
    # The temp config is deleted after the import, even on success.
    assert not config_path.exists()
    assert not config_path.parent.exists()
    # Its contents (certificates/keys) are never echoed back.
    raw = body.decode("utf-8")
    assert "BEGIN CERTIFICATE" not in raw
    assert "<ca>" not in raw


def test_network_openvpn_cleans_temp_file_when_import_fails(network_server):
    network_server["state"]["fail"] = "import"
    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/openvpn",
        method="POST",
        payload={"name": "corp", "config": OPENVPN_CONFIG},
    )
    assert status == 503
    assert json.loads(body.decode("utf-8"))["ok"] is False

    recorded = network_server["state"]["openvpn_temp"]
    config_path = Path(recorded["path"])
    assert not config_path.exists()
    assert not config_path.parent.exists()
    assert "BEGIN CERTIFICATE" not in body.decode("utf-8")


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "corp", "config": "dev tun\nremote vpn.example.com 1194\n"},
        {"name": "corp", "config": "client\ndev tun\n"},
        {"name": "corp", "config": "client\nremote\n"},
        {"name": "corp", "config": ""},
        {"name": "corp", "config": "client\nremote vpn.example.com 1194\n\x00"},
        {"name": "../evil", "config": OPENVPN_CONFIG},
        {"name": "corp;reboot", "config": OPENVPN_CONFIG},
    ],
)
def test_network_openvpn_rejects_invalid_config(network_server, payload):
    status, _, body = _http_request(
        network_server["base_url"], "/api/network/openvpn", method="POST", payload=payload
    )
    assert status == 400, body
    assert json.loads(body.decode("utf-8"))["ok"] is False
    assert "openvpn_temp" not in network_server["state"]
    assert not [
        cmd
        for cmd in network_server["calls"]
        if cmd[:5] == ["nmcli", "connection", "import", "type", "openvpn"]
    ]


# --- 5f. 503 paths ----------------------------------------------------------


def test_network_actions_return_503_when_nmcli_is_missing(network_server, monkeypatch):
    monkeypatch.setattr(kiosk.shutil, "which", lambda name: None)
    base = network_server["base_url"]
    calls = network_server["calls"]

    payloads = [
        ("/api/network/ip", {"interface": "eth0", "mode": "auto"}),
        ("/api/network/pppoe", {"username": "user@isp", "password": "pw"}),
        ("/api/network/wireguard", _wireguard_payload()),
        ("/api/network/openvpn", {"name": "corp", "config": OPENVPN_CONFIG}),
    ]
    for path, payload in payloads:
        status, _, body = _http_request(base, path, method="POST", payload=payload)
        assert status == 503, (path, body)
        data = json.loads(body.decode("utf-8"))
        assert data["ok"] is False
        assert "NetworkManager" in data["message"]

    # Nothing was executed and no keyfile was written without nmcli.
    assert calls == []
    assert not (network_server["tmp_path"] / "system-connections").exists()


@pytest.mark.parametrize("failure", ["show", "mod", "up"])
def test_network_ip_returns_503_when_nmcli_fails(network_server, failure):
    network_server["state"]["fail"] = failure
    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/ip",
        method="POST",
        payload={"interface": "eth0", "mode": "auto"},
    )
    assert status == 503, body
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert data["message"]


def test_network_openvpn_returns_503_when_nmcli_fails(network_server):
    network_server["state"]["fail"] = "import"
    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/openvpn",
        method="POST",
        payload={"name": "corp", "config": OPENVPN_CONFIG},
    )
    assert status == 503
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    # The temp path is scrubbed from the diagnostic message.
    assert "rhm-ovpn-" not in data["message"]
    assert "corp.ovpn" not in data["message"]


def test_network_ip_returns_503_without_an_active_connection(network_server):
    network_server["state"]["active"] = ""
    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/ip",
        method="POST",
        payload={"interface": "eth0", "mode": "auto"},
    )
    assert status == 503
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert "eth0" in data["message"]
    assert not [cmd for cmd in network_server["calls"] if cmd[:3] == ["nmcli", "con", "mod"]]


@pytest.mark.parametrize("raw_body", [b"{not json", b"[1, 2, 3]"])
def test_network_actions_reject_malformed_json_without_touching_nmcli(
    network_server, raw_body
):
    status, _, body = _http_request(
        network_server["base_url"],
        "/api/network/ip",
        method="POST",
        extra_headers={"Content-Type": "application/json"},
        raw_body=raw_body,
    )
    assert status == 400
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert data["message"]
    assert network_server["calls"] == []


# --- 6. AmneziaWG (awg-quick) + subscription import ------------------------

AWG_PRIVATE_KEY = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
AWG_PEER_KEY = "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg="
AWG_PRESHARED_KEY = "Fp6h8mS1p7wJd0dN2cQx9l3bYk5rT8uV0aZ4eG6iK1s="

AMNEZIA_CONFIG = (
    "[Interface]\n"
    f"PrivateKey = {AWG_PRIVATE_KEY}\n"
    "Address = 10.8.0.2/32, fd00::2/128\n"
    "DNS = 1.1.1.1\n"
    "Jc = 4\n"
    "Jmin = 50\n"
    "Jmax = 1000\n"
    "S1 = 20\n"
    "S2 = 20\n"
    "H1 = 1234567\n"
    "H2 = 2345678\n"
    "H3 = 3456789\n"
    "H4 = 4567890\n"
    "\n"
    "[Peer]\n"
    f"PublicKey = {AWG_PEER_KEY}\n"
    f"PresharedKey = {AWG_PRESHARED_KEY}\n"
    "Endpoint = vpn.example.com:51820\n"
    "AllowedIPs = 0.0.0.0/0, ::/0\n"
    "PersistentKeepalive = 25\n"
)

SUBSCRIPTION_URL = "https://sub.example.com/awg/office"
PUBLIC_ADDRESS = "93.184.216.34"
PRIVATE_ADDRESSES = [
    ["127.0.0.1"],
    ["10.0.0.5"],
    ["172.16.4.4"],
    ["192.168.1.10"],
    ["169.254.10.10"],
    ["100.64.0.7"],
    ["0.0.0.0"],
    ["::1"],
    ["fe80::1"],
    ["fc00::1"],
]


def _amnezia_payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"name": "office", "config": AMNEZIA_CONFIG}
    payload.update(overrides)
    return payload


def _subscription_payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"url": SUBSCRIPTION_URL, "name": "office"}
    payload.update(overrides)
    return payload


def _base64_variants(text: str) -> List[Tuple[str, str]]:
    """(label, encoded-body) pairs: base64/base64url with and without padding."""
    raw = text.encode("utf-8")
    standard = base64.b64encode(raw).decode("ascii")
    urlsafe = base64.urlsafe_b64encode(raw).decode("ascii")
    return [
        ("base64-padded", standard),
        ("base64-unpadded", standard.rstrip("=")),
        ("base64url-padded", urlsafe),
        ("base64url-unpadded", urlsafe.rstrip("=")),
    ]


@pytest.fixture()
def amnezia_server(tmp_path: Path, monkeypatch):
    """KioskServer with a fake awg-quick binary and a tmp AmneziaWG directory."""
    config_dir = tmp_path / "amneziawg"
    monkeypatch.setattr(kiosk, "AMNEZIA_CONFIG_DIR", config_dir)

    fake_awg = tmp_path / "bin" / "awg-quick"
    fake_awg.parent.mkdir(parents=True, exist_ok=True)
    fake_awg.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(
        kiosk.shutil,
        "which",
        lambda name: str(fake_awg) if name == "awg-quick" else None,
    )
    # Tests may run as an unprivileged user; root is asserted separately.
    monkeypatch.setattr(kiosk, "_is_root", lambda: True)

    calls: List[List[str]] = []
    state: Dict[str, Any] = {
        "fail": None,
        "fetch": None,
        "fetches": [],
        "show_out": "interface: awg0\n",
        "leak": False,
        "resolved": [PUBLIC_ADDRESS],
    }

    monkeypatch.setattr(
        kiosk,
        "_resolve_host_addresses",
        lambda host, port: list(state["resolved"]),
    )

    def fake_fetch(url: str) -> bytes:
        state["fetches"].append(url)
        outcome = state["fetch"]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, (bytes, bytearray)):
            return bytes(outcome)
        return AMNEZIA_CONFIG.encode("utf-8")

    monkeypatch.setattr(kiosk, "_fetch_subscription_bytes", fake_fetch)

    def runner(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        calls.append(list(cmd))
        if not cmd:
            return 1, "", "empty"
        if cmd[0] == str(fake_awg):
            verb = cmd[1] if len(cmd) > 1 else ""
            if state["fail"] == verb:
                detail = f"Error: unable to {verb} the interface"
                if state["leak"]:
                    detail += f"\nPrivateKey = {AWG_PRIVATE_KEY}"
                return 1, "", detail
            return 0, f"awg-quick: {verb} ok\n", ""
        if cmd[0] == "awg":
            if state["fail"] == "show":
                return 1, "", "Unable to access interface: Operation not permitted"
            return 0, state["show_out"], ""
        if cmd[0] == "qrencode":
            if state["fail"] == "qrencode":
                return 127, "", "command not found: qrencode"
            return (
                0,
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                '<rect width="100" height="100"/></svg>',
                "",
            )
        return 127, "", f"not mocked: {cmd[0]}"

    server = kiosk.KioskServer(
        ("127.0.0.1", 0),
        static_dir=KIOSK_UI_DIR,
        run_cmd=runner,
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "config_dir": config_dir,
            "fake_awg": fake_awg,
            "calls": calls,
            "state": state,
            "tmp_path": tmp_path,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _awg_calls(amnezia_server, verb: str) -> List[List[str]]:
    fake = str(amnezia_server["fake_awg"])
    return [
        cmd
        for cmd in amnezia_server["calls"]
        if cmd[:1] == [fake] and len(cmd) > 1 and cmd[1] == verb
    ]


def test_amnezia_get_empty_directory_and_status(amnezia_server):
    status, headers, body = _http_request(
        amnezia_server["base_url"], "/api/network/amnezia"
    )
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is True
    assert data["awg_available"] is True
    assert data["redacted"] is True
    assert data["files"] == []
    assert data["config_dir"] == str(amnezia_server["config_dir"])
    assert data["status"]["available"] is True
    assert data["status"]["ok"] is True
    assert data["status"]["interfaces"] == ["awg0"]


def test_amnezia_get_is_200_and_reports_missing_awg(amnezia_server, monkeypatch):
    monkeypatch.setattr(kiosk.shutil, "which", lambda name: None)
    status, _, body = _http_request(
        amnezia_server["base_url"], "/api/network/amnezia"
    )
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["awg_available"] is False
    assert data["status"]["available"] is False
    assert data["status"]["ok"] is False
    assert data["status"]["message"]


def test_amnezia_get_survives_awg_show_failure(amnezia_server):
    amnezia_server["state"]["fail"] = "show"
    status, _, body = _http_request(
        amnezia_server["base_url"], "/api/network/amnezia"
    )
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["awg_available"] is True
    assert data["status"]["ok"] is False
    assert "Operation not permitted" in data["status"]["message"]


def test_amnezia_post_writes_0600_config_and_runs_awg_quick(amnezia_server, monkeypatch):
    opened: List[Tuple[str, int]] = []
    real_open = os.open

    def spy_open(path, flags, mode=0o777, **kwargs):
        opened.append((str(path), mode))
        return real_open(path, flags, mode, **kwargs)

    # Windows cannot express POSIX bits, so also record the requested mode.
    monkeypatch.setattr(kiosk.os, "open", spy_open)

    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia",
        method="POST",
        payload=_amnezia_payload(),
    )
    data = json.loads(body.decode("utf-8"))
    assert status == 200, data
    assert data["ok"] is True
    assert data["name"] == "office"
    assert data["redacted"] is True
    assert data["output"]

    config_path = amnezia_server["config_dir"] / "office.conf"
    assert config_path.is_file()
    assert config_path.read_text(encoding="utf-8") == AMNEZIA_CONFIG
    content = config_path.read_text(encoding="utf-8")
    for expected in ("Jc = 4", "H4 = 4567890", f"PrivateKey = {AWG_PRIVATE_KEY}"):
        assert expected in content

    assert any(
        mode == 0o600 and name.endswith(".tmp")
        for name, mode in opened
    )
    if os.name == "posix":
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600

    ups = _awg_calls(amnezia_server, "up")
    assert ups == [[str(amnezia_server["fake_awg"]), "up", str(config_path)]]
    assert not any(isinstance(cmd, str) for cmd in amnezia_server["calls"])

    # No secret ever leaves the server: not the key value, not the field name.
    raw = body.decode("utf-8")
    assert AWG_PRIVATE_KEY not in raw
    assert AWG_PRESHARED_KEY not in raw
    assert "PrivateKey" not in raw
    assert "PresharedKey" not in raw


def test_amnezia_get_lists_the_written_config(amnezia_server):
    post_status, _, _ = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia",
        method="POST",
        payload=_amnezia_payload(name="home"),
    )
    assert post_status == 200

    status, _, body = _http_request(
        amnezia_server["base_url"], "/api/network/amnezia"
    )
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert len(data["files"]) == 1
    entry = data["files"][0]
    assert entry["name"] == "home"
    assert entry["path"] == str(amnezia_server["config_dir"] / "home.conf")
    assert entry["size"] == len(AMNEZIA_CONFIG.encode("utf-8"))
    assert isinstance(entry["mtime"], int)
    assert AWG_PRIVATE_KEY not in body.decode("utf-8")


MISSING_PEER_CONFIG = (
    "[Interface]\n"
    f"PrivateKey = {AWG_PRIVATE_KEY}\n"
    "Address = 10.8.0.2/32\n"
)

MISSING_INTERFACE_CONFIG = (
    "[Peer]\n"
    f"PublicKey = {AWG_PEER_KEY}\n"
    "Endpoint = vpn.example.com:51820\n"
)

INVALID_AMNEZIA_CONFIGS = [
    pytest.param("", id="empty"),
    pytest.param(MISSING_PEER_CONFIG, id="missing-peer"),
    pytest.param(MISSING_INTERFACE_CONFIG, id="missing-interface"),
    pytest.param(
        AMNEZIA_CONFIG.replace(f"PrivateKey = {AWG_PRIVATE_KEY}\n", ""),
        id="missing-private-key",
    ),
    pytest.param(
        AMNEZIA_CONFIG.replace("Address = 10.8.0.2/32, fd00::2/128\n", ""),
        id="missing-address",
    ),
    pytest.param(
        AMNEZIA_CONFIG.replace(f"PublicKey = {AWG_PEER_KEY}\n", ""),
        id="missing-public-key",
    ),
    pytest.param(
        AMNEZIA_CONFIG.replace("Endpoint = vpn.example.com:51820\n", ""),
        id="missing-endpoint",
    ),
    pytest.param(
        AMNEZIA_CONFIG.replace("Endpoint = vpn.example.com:51820", "Endpoint = vpn.example.com"),
        id="endpoint-without-port",
    ),
    pytest.param(
        AMNEZIA_CONFIG.replace("Address = 10.8.0.2/32, fd00::2/128", "Address = nope"),
        id="bad-address",
    ),
    pytest.param(
        AMNEZIA_CONFIG.replace("Jc = 4", "Jc = four"),
        id="non-numeric-jc",
    ),
    pytest.param(
        AMNEZIA_CONFIG.replace("AllowedIPs = 0.0.0.0/0, ::/0", "AllowedIPs = not-a-cidr"),
        id="bad-allowed-ips",
    ),
    pytest.param(
        AMNEZIA_CONFIG + "PostUp = touch /tmp/pwned\n",
        id="root-hook",
    ),
    pytest.param(
        AMNEZIA_CONFIG + "\x07",
        id="control-character",
    ),
    pytest.param(
        AMNEZIA_CONFIG + "#" + "x" * 5000 + "\n",
        id="line-too-long",
    ),
    pytest.param(
        AMNEZIA_CONFIG + "[Unknown]\nKey = value\n",
        id="unknown-section",
    ),
]


@pytest.mark.parametrize("config", INVALID_AMNEZIA_CONFIGS)
def test_amnezia_post_rejects_invalid_config(amnezia_server, config):
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia",
        method="POST",
        payload=_amnezia_payload(config=config),
    )
    assert status == 400, body
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert data["message"]
    assert _awg_calls(amnezia_server, "up") == []
    assert not amnezia_server["config_dir"].exists()
    # A rejected config never echoes its secrets either.
    if AWG_PRIVATE_KEY in config:
        assert AWG_PRIVATE_KEY not in body.decode("utf-8")


@pytest.mark.parametrize("name", ["../evil", "office;reboot", "", "-dash", "a" * 40])
def test_amnezia_post_rejects_unsafe_name(amnezia_server, name):
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia",
        method="POST",
        payload=_amnezia_payload(name=name),
    )
    assert status == 400, body
    assert json.loads(body.decode("utf-8"))["ok"] is False
    assert _awg_calls(amnezia_server, "up") == []


def test_amnezia_post_redacts_secrets_from_failure_output(amnezia_server):
    amnezia_server["state"]["fail"] = "up"
    amnezia_server["state"]["leak"] = True

    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia",
        method="POST",
        payload=_amnezia_payload(),
    )
    assert status == 503, body
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    raw = body.decode("utf-8")
    assert AWG_PRIVATE_KEY not in raw
    assert AWG_PRESHARED_KEY not in raw
    assert "***" in data["message"]
    # A failed activation never leaves a half-applied config behind.
    assert not (amnezia_server["config_dir"] / "office.conf").exists()


def test_amnezia_down_runs_awg_quick_down(amnezia_server):
    up_status, _, _ = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia",
        method="POST",
        payload=_amnezia_payload(),
    )
    assert up_status == 200
    amnezia_server["calls"].clear()

    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia/down",
        method="POST",
        payload={"name": "office"},
    )
    data = json.loads(body.decode("utf-8"))
    assert status == 200, data
    assert data["ok"] is True
    assert data["redacted"] is True
    config_path = amnezia_server["config_dir"] / "office.conf"
    assert _awg_calls(amnezia_server, "down") == [
        [str(amnezia_server["fake_awg"]), "down", str(config_path)]
    ]
    # The config stays on disk so it can be brought up again.
    assert config_path.is_file()
    assert AWG_PRIVATE_KEY not in body.decode("utf-8")


def test_amnezia_down_unknown_name_is_400(amnezia_server):
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia/down",
        method="POST",
        payload={"name": "ghost"},
    )
    assert status == 400
    assert json.loads(body.decode("utf-8"))["ok"] is False
    assert _awg_calls(amnezia_server, "down") == []


def test_amnezia_down_failure_is_503(amnezia_server):
    up_status, _, _ = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia",
        method="POST",
        payload=_amnezia_payload(),
    )
    assert up_status == 200
    amnezia_server["state"]["fail"] = "down"
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia/down",
        method="POST",
        payload={"name": "office"},
    )
    assert status == 503
    assert json.loads(body.decode("utf-8"))["ok"] is False


def test_amnezia_returns_503_without_awg_quick(amnezia_server, monkeypatch):
    monkeypatch.setattr(kiosk.shutil, "which", lambda name: None)

    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia",
        method="POST",
        payload=_amnezia_payload(),
    )
    assert status == 503
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert "awg-quick" in data["message"]
    assert not amnezia_server["config_dir"].exists()
    assert amnezia_server["calls"] == []

    down_status, _, _down_body = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia/down",
        method="POST",
        payload={"name": "office"},
    )
    assert down_status == 503


def test_amnezia_returns_503_without_root(amnezia_server, monkeypatch):
    monkeypatch.setattr(kiosk, "_is_root", lambda: False)
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/amnezia",
        method="POST",
        payload=_amnezia_payload(),
    )
    assert status == 503
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert "root" in data["message"]
    assert not amnezia_server["config_dir"].exists()


def test_subscription_plain_config_is_written_and_started(amnezia_server):
    amnezia_server["state"]["fetch"] = AMNEZIA_CONFIG.encode("utf-8")
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/subscription",
        method="POST",
        payload=_subscription_payload(name="sub-office"),
    )
    data = json.loads(body.decode("utf-8"))
    assert status == 200, data
    assert data["ok"] is True
    assert data["name"] == "sub-office"
    assert data["source"] == "subscription"
    assert data["format"] == "plain"
    assert data["host"] == "sub.example.com"
    assert data["redacted"] is True
    assert amnezia_server["state"]["fetches"] == [SUBSCRIPTION_URL]

    config_path = amnezia_server["config_dir"] / "sub-office.conf"
    assert config_path.is_file()
    assert _awg_calls(amnezia_server, "up") == [
        [str(amnezia_server["fake_awg"]), "up", str(config_path)]
    ]
    raw = body.decode("utf-8")
    assert AWG_PRIVATE_KEY not in raw
    assert AWG_PRESHARED_KEY not in raw


@pytest.mark.parametrize(
    "label,encoded", _base64_variants(AMNEZIA_CONFIG), ids=[item[0] for item in _base64_variants(AMNEZIA_CONFIG)]
)
def test_subscription_accepts_base64_and_base64url(amnezia_server, label, encoded):
    amnezia_server["state"]["fetch"] = encoded.encode("utf-8")
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/subscription",
        method="POST",
        payload=_subscription_payload(name="encoded"),
    )
    data = json.loads(body.decode("utf-8"))
    assert status == 200, (label, data)
    assert data["format"] == "base64"
    config_path = amnezia_server["config_dir"] / "encoded.conf"
    assert config_path.is_file()
    assert "Jc = 4" in config_path.read_text(encoding="utf-8")
    assert AWG_PRIVATE_KEY not in body.decode("utf-8")


def test_subscription_derives_name_from_host_when_absent(amnezia_server):
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/subscription",
        method="POST",
        payload={"url": SUBSCRIPTION_URL},
    )
    data = json.loads(body.decode("utf-8"))
    assert status == 200, data
    assert data["name"] == "sub.example.com"
    assert (amnezia_server["config_dir"] / "sub.example.com.conf").is_file()


def test_subscription_rejects_http_and_unsafe_urls(amnezia_server):
    for url in (
        "http://sub.example.com/awg",
        "ftp://sub.example.com/awg",
        "sub.example.com/awg",
        "https://user:pass@sub.example.com/awg",
        "https:///awg",
        "https://sub.example.com/" + "a" * 2100,
        "https://sub.example.com/aw g",
    ):
        status, _, body = _http_request(
            amnezia_server["base_url"],
            "/api/network/subscription",
            method="POST",
            payload={"url": url, "name": "office"},
        )
        assert status == 400, (url, body)
        assert json.loads(body.decode("utf-8"))["ok"] is False

    # Nothing was fetched and nothing was written for any rejected URL.
    assert amnezia_server["state"]["fetches"] == []
    assert not amnezia_server["config_dir"].exists()
    assert _awg_calls(amnezia_server, "up") == []


@pytest.mark.parametrize("addresses", PRIVATE_ADDRESSES)
def test_subscription_rejects_loopback_private_and_reserved_hosts(
    amnezia_server, addresses
):
    amnezia_server["state"]["resolved"] = addresses
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/subscription",
        method="POST",
        payload=_subscription_payload(),
    )
    assert status == 400, body
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert data["message"]
    assert amnezia_server["state"]["fetches"] == []
    assert not amnezia_server["config_dir"].exists()


def test_subscription_rejects_ip_literal_loopback_without_resolution(
    amnezia_server, monkeypatch
):
    # The real resolver is used here: no fake address is injected for the guard.
    monkeypatch.setattr(
        kiosk,
        "_resolve_host_addresses",
        lambda host, port: ["127.0.0.1"] if host == "127.0.0.1" else [PUBLIC_ADDRESS],
    )
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/subscription",
        method="POST",
        payload=_subscription_payload(url="https://127.0.0.1/awg"),
    )
    assert status == 400, body
    assert amnezia_server["state"]["fetches"] == []


def test_subscription_rejects_payload_over_one_mib(amnezia_server):
    amnezia_server["state"]["fetch"] = b"x" * (kiosk.SUBSCRIPTION_MAX_BYTES + 1)
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/subscription",
        method="POST",
        payload=_subscription_payload(),
    )
    assert status == 503, body
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert "1 МБ" in data["message"]
    assert not amnezia_server["config_dir"].exists()
    assert _awg_calls(amnezia_server, "up") == []


def test_fetch_subscription_bytes_enforces_one_mib_cap(monkeypatch):
    monkeypatch.setattr(
        kiosk, "_resolve_host_addresses", lambda host, port: [PUBLIC_ADDRESS]
    )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self, size=-1):
            return b"x" * (size if isinstance(size, int) and size > 0 else 16)

    class FakeOpener:
        def open(self, request, timeout=None):
            assert timeout == kiosk.SUBSCRIPTION_TIMEOUT_SECONDS
            return FakeResponse()

    monkeypatch.setattr(
        kiosk.urllib.request, "build_opener", lambda *args, **kwargs: FakeOpener()
    )
    with pytest.raises(kiosk.NetworkUnavailableError) as excinfo:
        kiosk._fetch_subscription_bytes(SUBSCRIPTION_URL)
    assert "1 МБ" in str(excinfo.value)


@pytest.mark.parametrize(
    "payload",
    [
        b"vless://example@host:443?type=tcp#node",
        b"just some text that is not a config",
        b"[Peer]\nPublicKey = abc\nEndpoint = vpn.example.com:51820\n",
        b"\xff\xfe\x00\x00",
        b"",
    ],
)
def test_subscription_rejects_unsupported_body(amnezia_server, payload):
    amnezia_server["state"]["fetch"] = payload
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/subscription",
        method="POST",
        payload=_subscription_payload(),
    )
    assert status == 400, body
    assert json.loads(body.decode("utf-8"))["ok"] is False
    assert not amnezia_server["config_dir"].exists()


def test_subscription_fetch_failure_is_503(amnezia_server):
    amnezia_server["state"]["fetch"] = kiosk.NetworkUnavailableError(
        "Не удалось загрузить подписку: timed out"
    )
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/subscription",
        method="POST",
        payload=_subscription_payload(),
    )
    assert status == 503, body
    data = json.loads(body.decode("utf-8"))
    assert data["ok"] is False
    assert "подписку" in data["message"].lower()
    assert not amnezia_server["config_dir"].exists()


def test_subscription_returns_503_without_awg_quick_before_fetching(
    amnezia_server, monkeypatch
):
    monkeypatch.setattr(kiosk.shutil, "which", lambda name: None)
    status, _, body = _http_request(
        amnezia_server["base_url"],
        "/api/network/subscription",
        method="POST",
        payload=_subscription_payload(),
    )
    assert status == 503
    assert json.loads(body.decode("utf-8"))["ok"] is False
    assert amnezia_server["state"]["fetches"] == []


def test_subscription_qr_returns_svg(amnezia_server):
    path = "/api/network/subscription/qr?url=" + urllib.parse.quote(
        SUBSCRIPTION_URL, safe=""
    )
    status, headers, body = _http_request(amnezia_server["base_url"], path)
    assert status == 200
    assert headers["Content-Type"].startswith("image/svg+xml")
    svg = body.decode("utf-8")
    assert svg.lstrip().startswith("<svg") or "<svg" in svg
    assert "PrivateKey" not in svg

    qr_calls = [cmd for cmd in amnezia_server["calls"] if cmd[:1] == ["qrencode"]]
    assert qr_calls == [
        ["qrencode", "-t", "SVG", "-m", "2", "-o", "-", "--", SUBSCRIPTION_URL]
    ]


def test_subscription_qr_rejects_http_private_and_long_urls(amnezia_server):
    bad_urls = [
        "http://sub.example.com/awg",
        "https://" + "a" * 2100,
        "",
    ]
    for url in bad_urls:
        path = "/api/network/subscription/qr?url=" + urllib.parse.quote(url, safe="")
        status, _, body = _http_request(amnezia_server["base_url"], path)
        assert status == 400, (url, body)
        assert json.loads(body.decode("utf-8"))["ok"] is False

    amnezia_server["state"]["resolved"] = ["192.168.1.5"]
    path = "/api/network/subscription/qr?url=" + urllib.parse.quote(
        SUBSCRIPTION_URL, safe=""
    )
    status, _, body = _http_request(amnezia_server["base_url"], path)
    assert status == 400, body
    assert not [cmd for cmd in amnezia_server["calls"] if cmd[:1] == ["qrencode"]]


def test_subscription_qr_returns_503_when_qrencode_fails(amnezia_server):
    amnezia_server["state"]["fail"] = "qrencode"
    path = "/api/network/subscription/qr?url=" + urllib.parse.quote(
        SUBSCRIPTION_URL, safe=""
    )
    status, _, body = _http_request(amnezia_server["base_url"], path)
    assert status == 503
    assert json.loads(body.decode("utf-8"))["ok"] is False


def test_amnezia_posts_require_the_json_csrf_gate(amnezia_server):
    body = json.dumps(_amnezia_payload()).encode("utf-8")
    for path in (
        "/api/network/amnezia",
        "/api/network/amnezia/down",
        "/api/network/subscription",
    ):
        status, _, _ = _http_request(
            amnezia_server["base_url"],
            path,
            method="POST",
            raw_body=body,
        )
        assert status == 403, path
    assert amnezia_server["calls"] == []
    assert amnezia_server["state"]["fetches"] == []


# --- Bug 3 (Wi-Fi connect) & Bug 4 (Kiosk offline detection) tests -----------


def test_connect_wifi_deletes_conflicting_profile_enables_radio_and_preserves_spaces():
    calls: List[Tuple[List[str], float]] = []
    ssid_with_space = "My Cafe "

    def fake_runner(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        calls.append((list(cmd), timeout))
        if cmd == ["nmcli", "radio", "wifi"]:
            return 0, "disabled\n", ""
        if cmd == ["nmcli", "radio", "wifi", "on"]:
            return 0, "", ""
        if cmd == ["nmcli", "dev", "wifi", "rescan"]:
            return 1, "", "Scanning not allowed immediately"
        if cmd == ["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"]:
            return (
                0,
                "My Cafe :802-11-wireless\nMy Cafe :802-3-ethernet\nOtherNet:802-11-wireless\n",
                "",
            )
        if cmd == ["nmcli", "con", "delete", ssid_with_space]:
            return 0, "Connection deleted", ""
        if cmd == ["nmcli", "dev", "wifi", "connect", ssid_with_space, "password", "validpass123"]:
            return 0, "Device 'wlan0' successfully activated.", ""
        return 1, "", f"unexpected: {cmd}"

    res = kiosk.connect_wifi(ssid_with_space, password="validpass123", run_cmd=fake_runner)
    assert res["ok"] is True

    cmds = [c for c, _ in calls]
    assert ["nmcli", "radio", "wifi", "on"] in cmds
    assert ["nmcli", "dev", "wifi", "rescan"] in cmds
    assert ["nmcli", "con", "delete", ssid_with_space] in cmds
    assert cmds.count(["nmcli", "con", "delete", ssid_with_space]) == 1

    connect_entries = [(c, t) for c, t in calls if c[:4] == ["nmcli", "dev", "wifi", "connect"]]
    assert len(connect_entries) == 1
    assert connect_entries[0][0] == [
        "nmcli",
        "dev",
        "wifi",
        "connect",
        ssid_with_space,
        "password",
        "validpass123",
    ]
    assert "--" not in connect_entries[0][0]
    assert connect_entries[0][1] == 35.0

    # parse_nmcli_wifi_list also preserves leading/trailing spaces in SSID
    parsed = kiosk.parse_nmcli_wifi_list("My Cafe :75:WPA2:\n   :80:WPA2:\n")
    assert [n["ssid"] for n in parsed] == ["My Cafe "]


def test_connect_wifi_hidden_network_uses_explicit_profile_and_up():
    calls: List[Tuple[List[str], float]] = []

    def fake_runner(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        calls.append((list(cmd), timeout))
        if cmd == ["nmcli", "radio", "wifi"]:
            return 0, "enabled\n", ""
        if cmd == ["nmcli", "dev", "wifi", "rescan"]:
            return 0, "", ""
        if cmd == ["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"]:
            return 0, "", ""
        if cmd == ["nmcli", "-t", "-f", "DEVICE,TYPE", "dev", "status"]:
            return 0, "eth0:ethernet\nwlp2s0:wifi\n", ""
        if cmd[:3] == ["nmcli", "con", "add"]:
            return 0, "Connection 'SecretLab' successfully added.", ""
        if cmd == ["nmcli", "con", "up", "SecretLab"]:
            return 0, "Connection successfully activated.", ""
        return 1, "", f"unexpected: {cmd}"

    res = kiosk.connect_wifi(
        "SecretLab", password="super-secret-password", hidden=True, run_cmd=fake_runner
    )
    assert res["ok"] is True
    cmds = [c for c, _ in calls]
    assert [
        "nmcli",
        "con",
        "add",
        "type",
        "wifi",
        "ifname",
        "wlp2s0",
        "con-name",
        "SecretLab",
        "ssid",
        "SecretLab",
        "wifi.hidden",
        "yes",
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.psk",
        "super-secret-password",
    ] in cmds
    up_entries = [(c, t) for c, t in calls if c == ["nmcli", "con", "up", "SecretLab"]]
    assert len(up_entries) == 1
    assert up_entries[0][1] == 35.0


def test_connect_wifi_fallback_on_key_mgmt_missing():
    calls: List[List[str]] = []

    def fake_runner(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        calls.append(list(cmd))
        if cmd == ["nmcli", "radio", "wifi"]:
            return 0, "enabled\n", ""
        if cmd == ["nmcli", "dev", "wifi", "rescan"]:
            return 0, "", ""
        if cmd == ["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"]:
            return 0, "", ""
        if cmd == ["nmcli", "dev", "wifi", "connect", "HomeNet", "password", "password123"]:
            return 4, "", "Error: Failed to add/activate new connection: key-mgmt: property is missing."
        if cmd == ["nmcli", "-t", "-f", "DEVICE,TYPE", "dev", "status"]:
            return 0, "wlan0:wifi\n", ""
        if cmd[:3] == ["nmcli", "con", "add"]:
            return 0, "Connection added.", ""
        if cmd == ["nmcli", "con", "up", "HomeNet"]:
            return 0, "Connection activated.", ""
        return 1, "", f"unexpected: {cmd}"

    res = kiosk.connect_wifi("HomeNet", password="password123", hidden=False, run_cmd=fake_runner)
    assert res["ok"] is True
    assert [
        "nmcli",
        "con",
        "add",
        "type",
        "wifi",
        "ifname",
        "wlan0",
        "con-name",
        "HomeNet",
        "ssid",
        "HomeNet",
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.psk",
        "password123",
    ] in calls
    assert ["nmcli", "con", "up", "HomeNet"] in calls


def test_connect_wifi_rejects_short_wpa_password_and_allows_64_hex():
    called: List[List[str]] = []

    def runner(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
        called.append(list(cmd))
        if cmd[:4] == ["nmcli", "dev", "wifi", "connect"]:
            return 0, "Activated", ""
        return 0, "", ""

    res_short = kiosk.connect_wifi("HomeNet", password="short7!", run_cmd=runner)
    assert res_short["ok"] is False
    assert "8" in res_short["message"]
    assert called == []

    # 64-char hex PSK is allowed
    hex_psk = "a1b2c3d4" * 8
    res_hex = kiosk.connect_wifi("HomeNet", password=hex_psk, run_cmd=runner)
    assert res_hex["ok"] is True
    assert ["nmcli", "dev", "wifi", "connect", "HomeNet", "password", hex_psk] in called


def test_wifi_connect_writes_tunnel_restart_trigger(running_server):
    trigger_wifi = running_server["state_dir"] / "tunnel-restart.trigger"
    assert not trigger_wifi.exists()

    # Short password failure does NOT write trigger
    st_fail, _, body_fail = _http_request(
        running_server["base_url"],
        "/api/wifi/connect",
        method="POST",
        payload={"ssid": "Office:5G", "password": "123", "hidden": False},
    )
    assert st_fail == 200
    assert json.loads(body_fail.decode("utf-8"))["ok"] is False
    assert not trigger_wifi.exists()

    # Valid Wi-Fi connect writes trigger with timestamp
    st_ok, _, body_ok = _http_request(
        running_server["base_url"],
        "/api/wifi/connect",
        method="POST",
        payload={"ssid": "Office:5G", "password": "valid-password", "hidden": False},
    )
    assert st_ok == 200
    assert json.loads(body_ok.decode("utf-8"))["ok"] is True
    assert trigger_wifi.is_file()
    assert int(trigger_wifi.read_text(encoding="utf-8").strip()) > 0


def test_network_action_writes_tunnel_restart_trigger(network_server):
    # Successful network action (/api/network/ip) also writes tunnel-restart.trigger
    trigger_net = network_server["tmp_path"] / "live-state" / "tunnel-restart.trigger"
    assert not trigger_net.exists()
    st_net, _, body_net = _http_request(
        network_server["base_url"],
        "/api/network/ip",
        method="POST",
        payload={"interface": "eth0", "mode": "auto", "dns": []},
    )
    assert st_net == 200
    assert json.loads(body_net.decode("utf-8"))["ok"] is True
    assert trigger_net.is_file()
    assert int(trigger_net.read_text(encoding="utf-8").strip()) > 0


def test_http_api_status_forces_offline_when_internet_unreachable(tmp_path: Path):
    state_dir = tmp_path / "live-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    # Stale session.json still claims ready=True and tunnel_status=connected
    (state_dir / "session.json").write_text(
        json.dumps(
            {
                "ready": True,
                "connect_url": "https://stale.trycloudflare.com/l/oldtoken",
                "token": "old_token_123",
                "node_id": "live-rescue-01",
                "tunnel_status": "connected",
            }
        ),
        encoding="utf-8",
    )

    srv = kiosk.KioskServer(
        ("127.0.0.1", 0),
        static_dir=KIOSK_UI_DIR,
        state_dir=state_dir,
        run_cmd=lambda cmd, timeout=5.0: (127, "", "not mocked"),
        internet_checker=lambda: {"reachable": False, "ip": None, "target": None},
    )
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, _, body = _http_request(f"http://127.0.0.1:{port}", "/api/status")
        assert status == 200
        data = json.loads(body.decode("utf-8"))
        assert data["ok"] is True
        assert data["internet"]["reachable"] is False
        assert data["session"]["ready"] is False
        assert data["session"]["tunnel_status"] == "offline"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)


def test_kiosk_ui_handles_network_loss_and_resets_qr_cache():
    js_text = (KIOSK_UI_DIR / "app.js").read_text(encoding="utf-8")
    assert "const effectiveReady = Boolean(sessionReady && internetReachable);" in js_text
    assert 'state.lastConnectUrl = "";' in js_text
    assert '!sessionReady && state.activeView !== "network"' not in js_text


