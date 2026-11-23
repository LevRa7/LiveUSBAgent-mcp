# -*- coding: utf-8 -*-
"""tests/test_kiosk_server.py - Unit, HTTP API, and unslop-ui surface audit tests."""

from __future__ import annotations

import importlib.util
import json
import re
import threading
import urllib.error
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
    assert ["nmcli", "dev", "wifi", "connect", "--", "Office:5G", "password", "secret-pass"] in calls

    # Connect to hidden SSID
    st_hid, _, body_hid = _http_request(
        base,
        "/api/wifi/connect",
        method="POST",
        payload={"ssid": "HiddenCorp", "password": "pw", "hidden": True},
    )
    assert st_hid == 200
    assert json.loads(body_hid.decode("utf-8"))["ok"] is True
    assert [
        "nmcli",
        "dev",
        "wifi",
        "connect",
        "--",
        "HiddenCorp",
        "password",
        "pw",
        "hidden",
        "yes",
    ] in calls

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
                "connect_url": "https://smart-server.online/connect/ABCD-1234",
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
    monkeypatch.setenv("MESH_GATEWAY", "https://smart-server.online")

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
    assert reg_calls == [("https://smart-server.online", str(state_dir))]


# --- 3b. Kiosk «Открыть терминал» local shell escape ---


def test_launch_local_terminal_without_display_is_unavailable(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)

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
    assert "shell" not in captured["kwargs"]
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
                "connect_url": "https://smart-server.online/connect/TUI-99",
                "link_key": "lk_tui_secret_99",
            }
        ),
        encoding="utf-8",
    )
    banner = tui.render_banner(state_dir=state_dir)
    assert "live-tui-99" in banner
    assert "https://smart-server.online/connect/TUI-99" in banner
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
        ["nmcli", "dev", "wifi", "connect", "--", "OfficeWifi", "password", "SuperSecretPass99"],
        timeout=1.0,
    )
    assert rc == 124
    assert out == ""
    assert err == "command timed out: nmcli"
    assert "SuperSecretPass99" not in err

