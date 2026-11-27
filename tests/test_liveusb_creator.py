"""Tests for the cross-platform LiveUSBAgent Creator installers (Windows, Linux .deb, macOS .dmg)."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_linux_creator_parses_only_usb_and_removable_whole_disks() -> None:
    gui = _load_module("liveusb_creator_gui", REPO_ROOT / "tools" / "liveusb-creator-gui.py")
    sample = json.dumps(
        {
            "blockdevices": [
                {"name": "nvme0n1", "path": "/dev/nvme0n1", "size": 512000000000, "model": "Samsung NVMe", "tran": "nvme", "rm": False, "type": "disk"},
                {"name": "sda", "path": "/dev/sda", "size": 1000000000000, "model": "Internal SATA", "tran": "sata", "rm": False, "type": "disk"},
                {"name": "sdb", "path": "/dev/sdb", "size": 7813988352, "model": "JetFlash Transcend 8GB", "tran": "usb", "rm": True, "type": "disk"},
                {"name": "sdb1", "path": "/dev/sdb1", "size": 2000000000, "model": "", "tran": "usb", "rm": True, "type": "part"},
                {"name": "loop0", "path": "/dev/loop0", "size": 104857600, "model": "", "tran": None, "rm": False, "type": "loop"},
            ]
        }
    )
    disks = gui.parse_lsblk_usb_disks(sample)
    assert len(disks) == 1
    assert disks[0]["path"] == "/dev/sdb"
    assert "JetFlash Transcend 8GB" in disks[0]["label"]
    assert "7.28 GB" in disks[0]["label"]


def test_linux_creator_selects_matching_release_iso_asset() -> None:
    gui = _load_module("liveusb_creator_gui", REPO_ROOT / "tools" / "liveusb-creator-gui.py")
    release = {
        "tag_name": "v0.1.2",
        "assets": [
            {"name": "LiveUSBAgent-0.1.2-amd64-full.iso.sha256", "size": 99, "browser_download_url": "https://example.invalid/a.sha256"},
            {"name": "LiveUSBAgent-0.1.2-amd64-full.iso", "size": 2000000000, "browser_download_url": "https://example.invalid/amd64.iso"},
            {"name": "LiveUSBAgent-0.1.2-arm64-full.iso", "size": 1900000000, "browser_download_url": "https://example.invalid/arm64.iso"},
            {"name": "LiveUSBAgent-0.1.2-i386-lite.iso", "size": 500000000, "browser_download_url": "https://example.invalid/i386.iso"},
        ],
    }
    assert gui.select_release_iso_asset(release, "amd64")["name"] == "LiveUSBAgent-0.1.2-amd64-full.iso"
    assert gui.select_release_iso_asset(release, "arm64")["name"] == "LiveUSBAgent-0.1.2-arm64-full.iso"
    assert gui.select_release_iso_asset(release, "i386")["name"] == "LiveUSBAgent-0.1.2-i386-lite.iso"
    assert gui.select_release_iso_asset(release, "riscv64") is None


def test_linux_creator_self_test_exits_zero() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "liveusb-creator-gui.py"), "--self-test"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_build_creator_deb_produces_valid_debian_package(tmp_path: Path) -> None:
    builder = _load_module("build_creator_deb", REPO_ROOT / "tools" / "build-creator-deb.py")
    deb_path, sha_path = builder.build_deb(tmp_path)
    assert deb_path.is_file()
    assert sha_path.is_file()

    raw = deb_path.read_bytes()
    assert raw.startswith(b"!<arch>\n")
    expected_sha = hashlib.sha256(raw).hexdigest()
    assert sha_path.read_text(encoding="ascii").startswith(expected_sha)

    pos = 8
    members = {}
    while pos < len(raw):
        hdr = raw[pos : pos + 60].decode("ascii")
        name = hdr[:16].strip()
        size = int(hdr[48:58].strip())
        pos += 60
        members[name] = raw[pos : pos + size]
        pos += size + (size % 2)

    assert members["debian-binary"] == b"2.0\n"
    assert "control.tar.gz" in members
    assert "data.tar.gz" in members

    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"]), mode="r:gz") as tf:
        control = tf.extractfile("./control").read().decode("utf-8")
    assert "Package: liveusb-agent-creator" in control
    assert "Architecture: all" in control
    assert "python3-tk" in control

    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"]), mode="r:gz") as tf:
        names = tf.getnames()
    assert "./usr/bin/liveusb-agent-creator" in names
    assert "./usr/share/applications/liveusb-agent-creator.desktop" in names


def test_macos_creator_sources_and_workflows_wired() -> None:
    swift_src = (REPO_ROOT / "tools" / "macos-creator" / "LiveUSBAgentCreator.swift").read_text(encoding="utf-8")
    assert "diskutil" in swift_src
    assert "external" in swift_src and "physical" in swift_src
    assert "with administrator privileges" in swift_src
    assert "releases/latest" in swift_src

    macos_sh = (REPO_ROOT / "tools" / "build-creator-macos.sh").read_text(encoding="utf-8")
    assert "arm64-apple-macos12.0" in macos_sh
    assert "x86_64-apple-macos12.0" in macos_sh
    assert "lipo -create" in macos_sh
    assert "hdiutil create" in macos_sh

    rel_wf = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "linux-deb:" in rel_wf
    assert "macos-dmg:" in rel_wf
    assert "tools/build-creator-deb.py" in rel_wf
    assert "tools/build-creator-macos.sh" in rel_wf

    ci_wf = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "creator-linux-deb:" in ci_wf
    assert "creator-macos-dmg:" in ci_wf
