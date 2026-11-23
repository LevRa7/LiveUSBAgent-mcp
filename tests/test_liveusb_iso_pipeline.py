"""Automated QA & pipeline contract tests for Remote-Harness LiveUSB ISO tooling.

Covers:
1. Pure LF line endings for all LiveUSB shell scripts, chroot hooks, and udev rules.
2. `bash -n` syntax validation across all 5 shell/hook scripts.
3. `ops/live-usb/build-iso.sh` contract assertions (no legacy entrypoint, runtime env,
   required packages, volatile journald cap, dual GRUB menu, udev rules).
4. `ops/live-usb/test-qemu.sh --dry-run` execution for both `--mode uefi` and `--mode bios`.
5. Cross-component contract integration test:
   gateway.py (/v1/liveusb/register) -> core/liveusb_bootstrap.py (register_ephemeral_node)
   -> ops/live-usb/kiosk_server.py (session.json + /api/status + /api/qr.svg).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from starlette.testclient import TestClient

import gateway
from core import liveusb_bootstrap

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVEUSB_DIR = REPO_ROOT / "ops" / "live-usb"
BUILD_ISO_SH = LIVEUSB_DIR / "build-iso.sh"
TEST_QEMU_SH = LIVEUSB_DIR / "test-qemu.sh"
KIOSK_SERVER_PY = LIVEUSB_DIR / "kiosk_server.py"
KIOSK_UI_DIR = LIVEUSB_DIR / "kiosk_ui"

LF_REQUIRED_FILES = [
    "ops/live-usb/build-iso.sh",
    "ops/live-usb/hooks/0100-build-apfs-and-drivers.hook.chroot",
    "ops/live-usb/test-qemu.sh",
    "ops/live-usb/start-kiosk.sh",
    "ops/live-usb/fetch-skills.sh",
    "ops/live-usb/udev/99-rhm-no-automount.rules",
    "ops/live-usb/udev/99-rhm-network-tethering.rules",
]

BASH_SCRIPTS = [
    "ops/live-usb/build-iso.sh",
    "ops/live-usb/hooks/0100-build-apfs-and-drivers.hook.chroot",
    "ops/live-usb/test-qemu.sh",
    "ops/live-usb/start-kiosk.sh",
    "ops/live-usb/fetch-skills.sh",
]


def _find_bash() -> str:
    """Locate Git Bash on Windows or `bash` on POSIX."""
    if sys.platform == "win32":
        candidates = [
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
            Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("bash")
    assert found, "bash executable not found (checked Git Bash on Windows and PATH)"
    return found


def _load_kiosk_module():
    spec = importlib.util.spec_from_file_location(
        "rhm_kiosk_server_pipeline_qa", str(KIOSK_SERVER_PY)
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeUrlopenResponse:
    """Minimal context-manager mock for urllib.request.urlopen."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeUrlopenResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


# ---------------------------------------------------------------------------
# 1. Pure LF line endings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel_path", LF_REQUIRED_FILES)
def test_liveusb_files_have_pure_lf_line_endings(rel_path: str) -> None:
    target = REPO_ROOT / rel_path
    assert target.is_file(), f"Required LiveUSB file missing: {rel_path}"
    raw = target.read_bytes()
    assert len(raw) > 0, f"LiveUSB file is empty: {rel_path}"
    assert b"\r" not in raw, f"CR (CRLF) byte found in {rel_path}; must be pure LF"


# ---------------------------------------------------------------------------
# 2. `bash -n` syntax validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel_path", BASH_SCRIPTS)
def test_liveusb_shell_scripts_pass_bash_syntax_check(rel_path: str) -> None:
    bash_exe = _find_bash()
    script_path = REPO_ROOT / rel_path
    assert script_path.is_file(), f"Shell script missing: {rel_path}"
    proc = subprocess.run(
        [bash_exe, "-n", str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, (
        f"bash -n failed for {rel_path} (rc={proc.returncode}):\n"
        f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    )


# ---------------------------------------------------------------------------
# 3. `ops/live-usb/build-iso.sh` contract assertions
# ---------------------------------------------------------------------------


def test_build_iso_contract_runtime_and_no_legacy_entrypoint() -> None:
    text = BUILD_ISO_SH.read_text(encoding="utf-8")

    # Zero references to legacy rhm_mcp_server.py
    assert "rhm_mcp_server.py" not in text

    # Ephemeral LiveUSB runtime command and environment
    required_runtime_tokens = [
        "python3 -m core.agent --ephemeral-liveusb",
        "MESH_SCENARIO=live_usb",
        "MESH_ACCESS_MODE=full",
        "MESH_WORKSPACE=/",
        "MESH_LIVE_STATE_DIR=/run/rhm-live",
    ]
    for token in required_runtime_tokens:
        assert token in text, f"Missing runtime contract token in build-iso.sh: {token!r}"


def test_build_iso_contract_required_packages() -> None:
    text = BUILD_ISO_SH.read_text(encoding="utf-8")

    required_packages = [
        # Firmware
        "firmware-linux",
        "firmware-linux-nonfree",
        "firmware-misc-nonfree",
        "firmware-iwlwifi",
        "firmware-realtek",
        "firmware-atheros",
        "firmware-brcm80211",
        "firmware-mediatek",
        # Mobile USB tethering
        "usbmuxd",
        "libimobiledevice-utils",
        "ipheth-utils",
        # Filesystems & storage
        "exfatprogs",
        "ntfs-3g",
        "dislocker",
        "btrfs-progs",
        "xfsprogs",
        "f2fs-tools",
        "zfsutils-linux",
        "libfshfs-utils", "apfs-dkms",
        "hfsplus",
        "libfsapfs-utils",
        "lvm2",
        "mdadm",
        "cryptsetup",
        # Hardware diagnostics & Kiosk runtime
        "memtester",
        "dmidecode",
        "chromium",
        "qrencode",
        "python3-websockets",
    ]
    for pkg in required_packages:
        assert pkg in text, f"Required package missing from build-iso.sh: {pkg!r}"


def test_build_iso_contract_journald_grub_and_udev_rules() -> None:
    text = BUILD_ISO_SH.read_text(encoding="utf-8")

    # Volatile journald configuration
    assert "Storage=volatile" in text
    assert "SystemMaxUse=50M" in text

    # Dual GRUB menu: toram + Low RAM + memtest86+
    assert "grub.cfg" in text
    assert "toram" in text
    assert "Low RAM" in text
    assert "memtest86+" in text
    assert text.count("menuentry ") >= 3

    # Both udev rules installed into chroot overlay
    assert "99-rhm-no-automount.rules" in text
    assert "99-rhm-network-tethering.rules" in text

    # Chroot build hook wired up
    assert "0100-build-apfs-and-drivers.hook.chroot" in text


def test_build_iso_check_passes_and_does_not_create_work_dir(tmp_path: Path) -> None:
    """Pin `build-iso.sh --check`: exit 0, a PASS summary, no WORK_DIR side effect."""
    try:
        bash_exe = _find_bash()
    except AssertionError as exc:  # pragma: no cover - environments without bash
        pytest.skip(str(exc))

    work_dir = tmp_path / "rhm-live-build-should-not-exist"
    proc = subprocess.run(
        [
            bash_exe,
            str(BUILD_ISO_SH),
            "--check",
            "--work-dir",
            str(work_dir),
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert proc.returncode == 0, (
        f"build-iso.sh --check failed (rc={proc.returncode}):\n"
        f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    )
    assert "PASS" in proc.stdout
    assert "asset checks passed" in proc.stdout
    assert not work_dir.exists(), "build-iso.sh --check must not touch --work-dir"


# ---------------------------------------------------------------------------
# 4. `ops/live-usb/test-qemu.sh --dry-run` execution tests (UEFI & BIOS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["uefi", "bios"])
def test_qemu_script_dry_run_uefi_and_bios(mode: str) -> None:
    bash_exe = _find_bash()
    proc = subprocess.run(
        [
            bash_exe,
            str(TEST_QEMU_SH),
            "--dry-run",
            "--mode",
            mode,
            "--iso",
            "/tmp/rhm-test-liveusb.iso",
            "--ram",
            "4096",
            "--cpus",
            "2",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, (
        f"test-qemu.sh --dry-run --mode {mode} failed (rc={proc.returncode}):\n"
        f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    )

    argv_line = proc.stdout.strip()
    assert "qemu-system-x86_64" in argv_line
    assert f"-name rhm-liveusb-{mode}" in argv_line
    assert "-cdrom /tmp/rhm-test-liveusb.iso" in argv_line
    assert "-boot order=d" in argv_line
    assert "virtio-net-pci" in argv_line

    if mode == "uefi":
        assert ("if=pflash" in argv_line and "OVMF" in argv_line) or "-bios" in argv_line
    else:
        assert "OVMF" not in argv_line
        assert "if=pflash" not in argv_line


def test_qemu_script_dry_run_with_fs_disks() -> None:
    bash_exe = _find_bash()
    proc = subprocess.run(
        [
            bash_exe,
            str(TEST_QEMU_SH),
            "--dry-run",
            "--mode",
            "uefi",
            "--iso",
            "/tmp/rhm-test-liveusb.iso",
            "--create-fs-disks",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, f"STDERR: {proc.stderr}"
    out = proc.stdout
    for fs_id in ("fs_exfat", "fs_ntfs", "fs_ext4", "fs_btrfs", "fs_xfs", "fs_vfat"):
        assert fs_id in out, f"Expected virtual FS disk {fs_id} in dry-run argv"


# ---------------------------------------------------------------------------
# 5. Cross-component contract integration test:
#    gateway.py -> core/liveusb_bootstrap.py -> ops/live-usb/kiosk_server.py
# ---------------------------------------------------------------------------


def test_cross_component_gateway_bootstrap_kiosk_contract(tmp_path: Path, monkeypatch) -> None:
    # Isolate gateway LiveUSB & registry state cleanly
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    gateway._REGISTRY_CACHE["key"] = None
    gateway._REGISTRY_CACHE["data"] = None
    gateway._rate_windows.clear()
    gateway.pending_pairs.clear()
    gateway.liveusb_sessions.clear()
    gateway.liveusb_nodes.clear()
    gateway.liveusb_revoked_sessions.clear()
    gateway.active_tunnels.clear()
    gateway.active_sse_subscribers.clear()

    try:
        # Step 1: Invoke gateway.py's LiveUSB register endpoint in-process
        client = TestClient(gateway.app)
        gw_resp = client.post(
            "/v1/liveusb/register",
            json={"hostname": "liveusb-qa-box", "os": "linux", "arch": "x64"},
        )
        assert gw_resp.status_code == 200
        raw_gw_bytes = gw_resp.content
        gw_payload = gw_resp.json()

        expected_node_id = gw_payload["node_id"]
        expected_link_key = gw_payload["link_key"]
        expected_connect_url = gw_payload["connect_url"]
        expected_device_token = gw_payload["device_token"]
        expected_session_code = gw_payload["session_code"]
        assert expected_node_id
        assert expected_link_key.startswith("lk_")
        assert expected_connect_url.endswith(f"/connect/{expected_session_code}")

        # Step 2: Feed the exact gateway response payload into core/liveusb_bootstrap.py
        captured_req: Dict[str, Any] = {}

        def fake_urlopen(req, timeout=None):
            captured_req["url"] = req.full_url
            captured_req["method"] = req.get_method()
            captured_req["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeUrlopenResponse(raw_gw_bytes, status=200)

        state_dir = tmp_path / "rhm-live"
        with monkeypatch.context() as mp:
            mp.setattr(liveusb_bootstrap.urllib.request, "urlopen", fake_urlopen)
            boot_res = liveusb_bootstrap.register_ephemeral_node(
                "https://smart-server.online",
                hostname="liveusb-qa-box",
                state_dir=str(state_dir),
                timeout_sec=10,
            )

        assert boot_res["ok"] is True
        assert captured_req["url"] == "https://smart-server.online/v1/liveusb/register"
        assert captured_req["method"] == "POST"
        assert captured_req["body"]["hostname"] == "liveusb-qa-box"
        assert captured_req["body"]["os"] == "linux"
        assert captured_req["body"].get("arch")

        # Verify agent.env and session.json written cleanly
        env_path = state_dir / "agent.env"
        session_path = state_dir / "session.json"
        assert env_path.is_file()
        assert session_path.is_file()

        env_vals = liveusb_bootstrap._parse_env_file(str(env_path))
        assert env_vals["MESH_USER"] == expected_node_id
        assert env_vals["MESH_TOKEN"] == expected_device_token
        assert env_vals["MESH_LINK_KEY"] == expected_link_key
        assert env_vals["MESH_SCENARIO"] == "live_usb"
        assert env_vals["MESH_ACCESS_MODE"] == "full"
        assert env_vals["MESH_WORKSPACE"] == "/"

        session_disk = json.loads(session_path.read_text(encoding="utf-8"))
        assert "device_token" not in session_disk

        # Step 3: Verify ops/live-usb/kiosk_server.py reads session.json cleanly
        kiosk = _load_kiosk_module()
        session_state = kiosk.read_session_state(state_dir)
        assert session_state["connect_url"] == expected_connect_url
        assert session_state["node_id"] == expected_node_id
        assert session_state["link_key"] == expected_link_key
        assert session_state["session_code"] == expected_session_code
        assert session_state["tunnel_status"] == "connected"

        # Step 4: Verify live KioskServer /api/status and /api/qr.svg expose the same values
        qr_calls: List[List[str]] = []

        def fake_run_cmd(cmd: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
            if cmd and cmd[0] == "qrencode":
                qr_calls.append(list(cmd))
                return (
                    0,
                    '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>',
                    "",
                )
            return 127, "", f"not mocked: {cmd[0] if cmd else ''}"

        server = kiosk.KioskServer(
            ("127.0.0.1", 0),
            static_dir=KIOSK_UI_DIR,
            state_dir=state_dir,
            run_cmd=fake_run_cmd,
            internet_checker=lambda: {"reachable": True, "ip": "192.168.1.50", "target": "1.1.1.1"},
            interfaces_override=[
                {"name": "eth0", "operstate": "UP", "ipv4": "192.168.1.50", "driver": "e1000e"},
            ],
        )
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as resp:
                assert resp.status == 200
                status_body = json.loads(resp.read().decode("utf-8"))

            assert status_body["ok"] is True
            sess = status_body["session"]
            assert sess["connect_url"] == expected_connect_url
            assert sess["node_id"] == expected_node_id
            assert sess["link_key"] == expected_link_key
            assert sess["session_code"] == expected_session_code
            assert sess["tunnel_status"] == "connected"

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/qr.svg", timeout=5) as qr_resp:
                assert qr_resp.status == 200
                assert "<svg" in qr_resp.read().decode("utf-8")

            assert len(qr_calls) == 1
            assert expected_connect_url in qr_calls[0]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    finally:
        gateway._rate_windows.clear()
        gateway.pending_pairs.clear()
        gateway.liveusb_sessions.clear()
        gateway.liveusb_nodes.clear()
        gateway.liveusb_revoked_sessions.clear()
        gateway.active_tunnels.clear()
        gateway.active_sse_subscribers.clear()
