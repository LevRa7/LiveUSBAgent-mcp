"""Automated QA & pipeline contract tests for Remote-Harness LiveUSB ISO tooling.

Covers:
1. Pure LF line endings for all LiveUSB shell scripts, chroot hooks, and udev rules.
2. `bash -n` syntax validation across all 5 shell/hook scripts.
3. `ops/live-usb/build-iso.sh` contract assertions (no legacy entrypoint, runtime env,
   required packages, network-settings tooling, volatile journald cap, dual GRUB menu,
   udev rules, AmneziaWG source-build hook coverage).
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
START_KIOSK_SH = LIVEUSB_DIR / "start-kiosk.sh"
KIOSK_SERVER_PY = LIVEUSB_DIR / "kiosk_server.py"
KIOSK_UI_DIR = LIVEUSB_DIR / "kiosk_ui"

LF_REQUIRED_FILES = [
    "ops/live-usb/build-iso.sh",
    "ops/live-usb/hooks/0100-build-apfs-and-drivers.hook.chroot",
    "ops/live-usb/hooks/0300-purge-build-deps.hook.chroot",
    "ops/live-usb/test-qemu.sh",
    "ops/live-usb/start-kiosk.sh",
    "ops/live-usb/start-lxde.sh",
    "ops/live-usb/fetch-skills.sh",
    "ops/live-usb/udev/99-rhm-no-automount.rules",
    "ops/live-usb/udev/99-rhm-network-tethering.rules",
]

BASH_SCRIPTS = [
    "ops/live-usb/build-iso.sh",
    "ops/live-usb/hooks/0100-build-apfs-and-drivers.hook.chroot",
    "ops/live-usb/hooks/0300-purge-build-deps.hook.chroot",
    "ops/live-usb/test-qemu.sh",
    "ops/live-usb/start-kiosk.sh",
    "ops/live-usb/start-lxde.sh",
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

    # Ephemeral LiveUSB runtime command and environment (Cloudflare Quick Tunnel default)
    required_runtime_tokens = [
        "python3 -m core.server --liveusb --tunnel cloudflare",
        "MESH_SCENARIO=live_usb",
        "MESH_LIVE_TUNNEL=",
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
        # Recovery / boot-repair additions (package audit 2026-11)
        "wimtools",
        "libbde-utils",
        "hfsprogs",
        "os-prober",
        "rsync",
        "wireless-regdb",
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

    # Binary hook that creates /live/vmlinuz and /live/initrd.img symlinks
    assert "0200-rhm-kernel-symlinks.hook.binary" in text
    assert "/live/vmlinuz" in text
    assert "/live/initrd.img" in text

    # Post-build cleanup hook that strips the compiler toolchain from the image
    assert "0300-purge-build-deps.hook.chroot" in text


def test_builder_creates_the_live_kernel_symlinks_the_grub_menu_needs() -> None:
    """GRUB points at unversioned /live paths; the hook must create them."""
    hook = LIVEUSB_DIR / "hooks" / "0200-rhm-kernel-symlinks.hook.binary"
    text = hook.read_text(encoding="utf-8")
    assert "vmlinuz-*" in text
    assert "initrd.img-*" in text
    assert "ln -sf" in text
    assert "exit 1" in text


def test_build_iso_ships_xterm_and_kiosk_server_inherits_display() -> None:
    """The kiosk terminal button needs an emulator package and a usable DISPLAY."""
    build_text = BUILD_ISO_SH.read_text(encoding="utf-8")
    start_text = START_KIOSK_SH.read_text(encoding="utf-8")

    # The generated package list (installed by live-build) must contain xterm.
    package_heredoc = build_text.split(
        "cat <<EOF > config/package-lists/rhm-rescue.list.chroot", 1
    )[1].split("\nEOF\n", 1)[0]
    packages = {line.strip() for line in package_heredoc.splitlines()}
    assert "xterm" in packages, "the image must install xterm for «Открыть терминал»"

    # …and --check must require it, so it cannot silently disappear again.
    check_block = build_text.split("local -a required_packages=(", 1)[1].split(")", 1)[0]
    assert "xterm" in check_block.split(), "build-iso.sh --check must verify package: xterm"

    # start-kiosk.sh must export DISPLAY (:0 fallback) before starting the server,
    # because the server spawns xterm for POST /api/terminal.
    export_line = 'export DISPLAY="${DISPLAY:-:0}"'
    assert export_line in start_text
    export_at = start_text.index(export_line)
    server_at = start_text.index('python3 "${SCRIPT_DIR}/kiosk_server.py"')
    assert export_at < server_at, "DISPLAY must be exported before kiosk_server.py starts"
    assert "kiosk X session is :0" in start_text


PACKAGE_LIST_HEREDOC_START = "\ncat <<EOF > config/package-lists/rhm-rescue.list.chroot\n"


def _package_list_heredoc() -> str:
    """Body of the live-build package-list heredoc in build-iso.sh.

    Anchored on the leading newline: `check_assets` also mentions the same path
    inside an awk pattern, so an unanchored split would start mid-script.
    """
    build_text = BUILD_ISO_SH.read_text(encoding="utf-8")
    assert PACKAGE_LIST_HEREDOC_START in build_text, (
        "package list heredoc not found in build-iso.sh"
    )
    return build_text.split(PACKAGE_LIST_HEREDOC_START, 1)[1].split("\nEOF\n", 1)[0]


def _generated_package_list() -> set:
    """Parse the live-build package list heredoc out of build-iso.sh."""
    return {
        line.strip()
        for line in _package_list_heredoc().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def _check_required_packages() -> List[str]:
    """Parse the `--check` required_packages array out of build-iso.sh."""
    build_text = BUILD_ISO_SH.read_text(encoding="utf-8")
    check_block = build_text.split("local -a required_packages=(", 1)[1].split(")", 1)[0]
    return check_block.split()


def test_build_iso_ships_file_manager_and_editor_for_the_app_launcher() -> None:
    """The «Приложения» launcher needs a file manager and a text editor in the image.

    xfe (with pcmanfm as a fallback) backs the `files` entry and mousepad backs
    the editor entry, so all three must be installed by live-build *and* pinned
    by `--check` so a later package-list edit cannot silently drop them.
    """
    packages = _generated_package_list()
    required = _check_required_packages()

    for package in ("xfe", "xdotool", "pcmanfm", "mousepad", "gparted"):
        assert package in packages, (
            f"the generated package list must install {package!r} for the app launcher"
        )
        assert package in required, (
            f"build-iso.sh --check must verify package: {package}"
        )


def test_build_iso_ships_service_toolkit_packages() -> None:
    """Stage 1 service toolkit packages verified against Debian 13 (Trixie)."""
    packages = _generated_package_list()
    required = _check_required_packages()

    service_packages = (
        "gparted",
        "gpart",
        "mtools",
        "ldmtool",
        "partclone",
        "restic",
        "clamav",
        "clamav-freshclam",
        "yara",
        "stress-ng",
        "fio",
        "s-tui",
        "python3-evtx",
        "mbr",
        "debootstrap",
    )
    for package in service_packages:
        assert package in packages, (
            f"the generated package list must install {package!r} for the service toolkit"
        )
        assert package in required, (
            f"build-iso.sh --check must verify package: {package}"
        )
    assert "ms-sys" not in packages, "ms-sys is not in Debian 13 apt repos"

    # The pre-existing kiosk GUI packages must survive the addition.
    for package in ("xterm", "chromium"):
        assert package in packages, (
            f"the kiosk GUI package block must keep {package!r}"
        )


def test_build_iso_installs_xdg_autostart_for_kiosk_browser() -> None:
    """LXDE/lightdm desktop must automatically open Chromium to http://127.0.0.1:8787."""
    build_text = (REPO_ROOT / "ops" / "live-usb" / "build-iso.sh").read_text(encoding="utf-8")
    autostart_sh = (REPO_ROOT / "ops" / "live-usb" / "open-kiosk-browser.sh").read_text(encoding="utf-8")
    start_kiosk = (REPO_ROOT / "ops" / "live-usb" / "start-kiosk.sh").read_text(encoding="utf-8")

    assert "/etc/xdg/autostart/rhm-kiosk-browser.desktop" in build_text
    assert "Exec=/usr/local/bin/rhm-open-kiosk-browser" in build_text
    assert "open-kiosk-browser.sh" in build_text

    assert "127.0.0.1" in autostart_sh
    assert "8787" in autostart_sh
    assert "xhost +SI:localuser:root" in autostart_sh
    assert "chromium" in autostart_sh
    assert "/tmp/rhm-kiosk-profile-$(id -u)" in autostart_sh

    assert "display_manager_active" in start_kiosk
    assert 'wait "${SERVER_PID}"' in start_kiosk


NETWORK_SETTINGS_PACKAGES = (
    "ppp",
    "wireguard-tools",
    "openresolv",
    "openvpn",
    "network-manager-openvpn",
)


def _kiosk_gui_package_block() -> set:
    """Packages declared after the `# Kiosk GUI + Runtime` heading (last block)."""
    heredoc = _package_list_heredoc()
    assert "# Kiosk GUI + Runtime" in heredoc
    tail = heredoc.split("# Kiosk GUI + Runtime", 1)[1]
    return {
        line.strip()
        for line in tail.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def test_build_iso_ships_network_settings_tooling() -> None:
    """«Настройки сети» needs PPPoE, WireGuard and OpenVPN backends in the image.

    The dialog only drives NetworkManager (`nmcli con mod`, `nmcli connection
    import type openvpn`), so every backend it offers must be installed by
    live-build *and* pinned by `--check`; otherwise the modal advertises actions
    that exit 503 on a real ISO. All four names are Debian 13 (trixie) `main`
    packages, verified with `apt-cache policy` (no backports pins needed).

    The plan allowed `network-manager-openvpn` *or* `network-manager-openvpn-gnome`;
    this pins the former, because the kiosk imports .ovpn files with nmcli and
    never opens nm-connection-editor (its GTK stack would be dead weight).
    """
    packages = _generated_package_list()
    required = _check_required_packages()

    for package in NETWORK_SETTINGS_PACKAGES:
        assert package in packages, (
            f"the generated package list must install {package!r} for «Настройки сети»"
        )
        assert package in required, (
            f"build-iso.sh --check must verify package: {package}"
        )

    # ppp / openvpn / network-manager-openvpn are declared in the Kiosk GUI block
    # (the dialog is a kiosk feature); wireguard-tools stays in the Network block.
    kiosk_block = _kiosk_gui_package_block()
    for package in ("ppp", "openvpn", "network-manager-openvpn"):
        assert package in kiosk_block, (
            f"{package!r} must be declared in the Kiosk GUI package block"
        )
    assert "wireguard-tools" in packages, (
        "wireguard-tools (Network block) must keep backing the WireGuard tab"
    )


AMNEZIAWG_HOOK_REL = "ops/live-usb/hooks/0100-build-apfs-and-drivers.hook.chroot"
AMNEZIAWG_HOOK = REPO_ROOT / AMNEZIAWG_HOOK_REL
AMNEZIAWG_TOOLS_REPO = "https://github.com/amnezia-vpn/amneziawg-tools.git"
AMNEZIAWG_MODULE_REPO = "https://github.com/amnezia-vpn/amneziawg-linux-kernel-module.git"


def _amneziawg_hook_text() -> str:
    assert AMNEZIAWG_HOOK.is_file(), f"chroot hook missing: {AMNEZIAWG_HOOK_REL}"
    return AMNEZIAWG_HOOK.read_text(encoding="utf-8")


def test_build_hook_clones_and_installs_amneziawg_tools() -> None:
    """Debian 13 has no amneziawg-tools package: the hook builds it from source.

    The plan requires a shallow clone of amneziawg-tools plus an `awg`/`awg-quick`
    install into /usr/local/bin, and it explicitly forbids an apt package named
    `amneziawg-tools`. A failed build must warn and continue, never abort the ISO.
    """
    hook = _amneziawg_hook_text()

    assert AMNEZIAWG_TOOLS_REPO in hook
    # Shallow clone: the chroot has no use for the full history.
    assert "git clone --depth 1" in hook
    # Upstream has no top-level Makefile: the build happens in src/, and the
    # install target only ships awg-quick when WITH_WGQUICK=yes (its own probe
    # looks for $(BINDIR)/bash, which is too fragile to rely on).
    assert 'make -C "${tmp_dir}/src"' in hook
    assert "WITH_WGQUICK=yes" in hook

    # Both binaries the kiosk drives must land in /usr/local/bin explicitly, so an
    # upstream prefix change cannot leave them outside the kiosk's PATH.
    assert "/usr/local/bin/awg" in hook
    assert "/usr/local/bin/awg-quick" in hook
    assert "install -m 0755" in hook
    # ...via a staged `make install DESTDIR=...` (or the built binaries directly).
    assert "DESTDIR=" in hook
    # The tools build names its binary "wg" and generates awg-quick from a script;
    # the direct-install fallback must follow that, not look for src/awg.
    assert '"${tmp_dir}/src/wg"' in hook
    assert "wg-quick/linux.bash" in hook
    # The kiosk writes configs into /etc/amnezia/amneziawg, so the hook creates it.
    assert "/etc/amnezia/amneziawg" in hook

    # Non-fatal by construction: the only way out of a failed clone is a warning.
    assert "warn" in hook
    assert "exit 1" not in hook


def test_build_hook_installs_the_amneziawg_kernel_module_through_dkms() -> None:
    """The awg kernel module comes from the AmneziaWG tree via DKMS, unsigned."""
    hook = _amneziawg_hook_text()

    assert AMNEZIAWG_MODULE_REPO in hook
    # The hook never trusts host uname -r; DKMS targets each chroot kernel.
    for step in ("dkms add -m", "dkms build -m", "dkms install -m"):
        assert step in hook, f"the hook must run {step!r} for the amneziawg module"
    assert '-k "${kver}"' in hook
    assert "/lib/modules/${kver}/build" in hook

    # The module tree has no top-level Makefile/dkms.conf; src/ carries both, and
    # upstream's own `make dkms-install` flattens them into /usr/src/<pkg>-<ver>.
    assert "dkms-install" in hook
    assert '"${tmp_dir}/src/dkms.conf"' in hook

    # A tree without any dkms.conf still gets a minimal one from the hook.
    assert "dkms.conf" in hook
    assert "dkms_conf_field" in hook
    assert 'BUILT_MODULE_NAME[0]="amneziawg"' in hook
    assert 'AUTOINSTALL="yes"' in hook
    assert 'BUILT_MODULE_LOCATION[0]=' in hook

    # Secure Boot note: the module is unsigned and must be documented as such.
    assert "UNSIGNED" in hook
    assert "Secure Boot" in hook


def test_package_list_adds_amneziawg_build_deps_without_a_bogus_package() -> None:
    """libmnl-dev/libelf-dev/pkg-config are the build deps; no amneziawg apt name."""
    packages = _generated_package_list()
    required = _check_required_packages()

    for package in ("libmnl-dev", "libelf-dev", "pkg-config"):
        assert package in packages, (
            f"the generated package list must install {package!r} to build AmneziaWG"
        )
        assert package in required, (
            f"build-iso.sh --check must verify package: {package}"
        )

    # Debian 13 (trixie) ships no amneziawg-tools/amneziawg-dkms package: an apt
    # entry would break `lb build` outright, so no such name may be declared.
    bogus = sorted(pkg for pkg in packages if pkg.startswith("amneziawg"))
    assert not bogus, f"no amneziawg apt package exists in trixie, found: {bogus}"


def test_build_iso_check_covers_the_amneziawg_hook(tmp_path: Path) -> None:
    """`--check` must pin both repos, the awg-quick install path and the DKMS calls."""
    build_text = BUILD_ISO_SH.read_text(encoding="utf-8")

    assert "local -a amneziawg_hook_tokens=(" in build_text
    token_block = build_text.split("local -a amneziawg_hook_tokens=(", 1)[1].split(")", 1)[0]
    for marker in (
        "amneziawg-tools",
        "amneziawg-linux-kernel-module",
        "/usr/local/bin/awg",
        "/usr/local/bin/awg-quick",
        "dkms add",
        "dkms build",
        "dkms install",
        "Secure Boot",
    ):
        assert marker in token_block, f"--check must pin {marker!r} in the 0100 hook"

    # The tokens are read back out of the hook that live-build actually ships.
    assert AMNEZIAWG_HOOK_REL in build_text

    try:
        bash_exe = _find_bash()
    except AssertionError as exc:  # pragma: no cover - environments without bash
        pytest.skip(str(exc))

    work_dir = tmp_path / "rhm-live-build-amneziawg-should-not-exist"
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
    for token in (
        "amneziawg-tools.git",
        "amneziawg-linux-kernel-module.git",
        "/usr/local/bin/awg",
        "/usr/local/bin/awg-quick",
        "dkms install",
        "Secure Boot",
    ):
        assert f"[PASS] 0100 hook contains: {token}" in proc.stdout, (
            f"--check did not report the AmneziaWG hook marker {token!r}"
        )
    assert "no amneziawg-tools apt package declared" in proc.stdout
    assert not work_dir.exists(), "build-iso.sh --check must not touch --work-dir"


def test_build_iso_installs_the_root_warning_wrapper() -> None:
    """Xfe shows a root warning; the ISO must install the auto-dismiss wrapper."""
    text = BUILD_ISO_SH.read_text(encoding="utf-8")
    wrapper = LIVEUSB_DIR / "open-files.sh"

    assert wrapper.is_file()
    assert "open-files.sh" in text
    assert "/usr/local/bin/rhm-open-files" in text
    assert "xdotool" in text
    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert "xfe" in wrapper_text
    assert "Warning" in wrapper_text


def test_build_iso_installs_lxde_desktop_launcher() -> None:
    """The app launcher must ship a full LXDE desktop (Start menu/settings)."""
    text = BUILD_ISO_SH.read_text(encoding="utf-8")
    packages = _generated_package_list()
    required = _check_required_packages()
    wrapper = LIVEUSB_DIR / "start-lxde.sh"

    assert wrapper.is_file()
    assert "start-lxde.sh" in text
    assert "/usr/local/bin/rhm-open-desktop" in text
    for package in ("lxde", "kbd"):
        assert package in packages
        assert package in required
    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert "startlxde" in wrapper_text
    assert "chvt 2" in wrapper_text
    assert ":1 vt2" in wrapper_text


def test_build_iso_installs_kiosk_service_and_masks_getty() -> None:
    """The kiosk must own tty1; a live-config 'user' getty left it at a prompt."""
    text = BUILD_ISO_SH.read_text(encoding="utf-8")
    assert "rhm-live-kiosk.service" in text
    assert "TTYPath=/dev/tty1" in text
    assert "ExecStart=/opt/remote-harness-mcp/ops/live-usb/start-kiosk.sh" in text
    assert "ln -sf /dev/null config/includes.chroot/etc/systemd/system/getty@tty1.service" in text
    assert "multi-user.target.wants/rhm-live-kiosk.service" in text


def test_start_kiosk_creates_mnt_rescue_before_the_kiosk_server_starts() -> None:
    """`files` opens /mnt/rescue, so the mount point must exist before the server."""
    start_text = START_KIOSK_SH.read_text(encoding="utf-8")

    mkdir_line = "mkdir -p /mnt/rescue"
    assert mkdir_line in start_text, (
        "start-kiosk.sh must create /mnt/rescue for the «Файловый менеджер» entry"
    )

    mkdir_at = start_text.index(mkdir_line)
    server_at = start_text.index('python3 "${SCRIPT_DIR}/kiosk_server.py"')
    assert mkdir_at < server_at, (
        "/mnt/rescue must be created before kiosk_server.py starts"
    )

    # The X11 detection branch must keep working on the pre-export DISPLAY value.
    assert 'X11_WAS_RUNNING=""' in start_text
    assert 'X11_WAS_RUNNING="1"' in start_text
    assert start_text.index('X11_WAS_RUNNING=""') < mkdir_at


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
                "https://racknerd-5a24bf9.merino-carob.ts.net",
                hostname="liveusb-qa-box",
                state_dir=str(state_dir),
                timeout_sec=10,
            )

        assert boot_res["ok"] is True
        assert captured_req["url"] == "https://racknerd-5a24bf9.merino-carob.ts.net/v1/liveusb/register"
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
