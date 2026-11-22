"""Unit tests for Live USB scenario detection, core/rescue.py, and mcp_tools wiring."""

from __future__ import annotations

import json
import os
import platform
from typing import Dict, List, Tuple

import pytest

from core import device, mcp_tools, rescue


@pytest.fixture
def linux_rescue_env(monkeypatch):
    """Force Linux mode in core.rescue so collectors run under mocked subprocesses."""
    monkeypatch.setattr(rescue, "_IS_WINDOWS", False)
    return monkeypatch


@pytest.fixture
def restore_mcp_tools(monkeypatch):
    """Ensure mcp_tools.TOOLS / TOOL_ANNOTATIONS are restored after scenario switching."""
    yield
    monkeypatch.delenv("MESH_SCENARIO", raising=False)
    mcp_tools.sync_scenario_tools()


# ---------------------------------------------------------------------------
# 1. scenario() == "live_usb" detection
# ---------------------------------------------------------------------------

def test_scenario_live_usb_via_env_var(monkeypatch):
    monkeypatch.setenv("MESH_SCENARIO", "live_usb")
    assert device.scenario() == "live_usb"
    assert device.is_live_usb() is True


def test_scenario_live_usb_via_run_live_medium(monkeypatch):
    monkeypatch.delenv("MESH_SCENARIO", raising=False)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(device, "_termux_data_dir", lambda: False)
    monkeypatch.setattr(os.path, "exists", lambda p: p == "/run/live/medium")
    monkeypatch.setattr(device, "_read_text", lambda p: "")
    assert device.scenario() == "live_usb"
    assert device.is_live_usb() is True


def test_scenario_live_usb_via_proc_cmdline(monkeypatch):
    monkeypatch.delenv("MESH_SCENARIO", raising=False)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(device, "_termux_data_dir", lambda: False)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.setattr(
        device,
        "_read_text",
        lambda p: "BOOT_IMAGE=/live/vmlinuz boot=live components quiet splash" if p == "/proc/cmdline" else "",
    )
    assert device.scenario() == "live_usb"
    assert device.is_live_usb() is True


def test_scenario_non_live_linux_stays_linux(monkeypatch):
    monkeypatch.delenv("MESH_SCENARIO", raising=False)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(device, "_termux_data_dir", lambda: False)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.setattr(device, "_read_text", lambda p: "root=/dev/nvme0n1p2 ro quiet")
    assert device.scenario() == "linux"
    assert device.is_live_usb() is False


# ---------------------------------------------------------------------------
# 2. collect_disks, collect_smart, collect_dmesg_io
# ---------------------------------------------------------------------------

def test_collect_disks_parses_lsblk_json(linux_rescue_env):
    sample = json.dumps(
        {
            "blockdevices": [
                {
                    "name": "nvme0n1",
                    "path": "/dev/nvme0n1",
                    "size": 512110190592,
                    "type": "disk",
                    "model": "Samsung SSD 980",
                    "tran": "nvme",
                }
            ]
        }
    )
    linux_rescue_env.setattr(rescue, "_run_cmd", lambda argv, timeout=8: (0, sample, ""))
    res = rescue.collect_disks()
    assert res["available"] is True
    assert res["source"] == "lsblk"
    assert len(res["blockdevices"]) == 1
    assert res["blockdevices"][0]["name"] == "nvme0n1"


def test_collect_dmesg_io_filters_device_and_counts_errors(linux_rescue_env):
    dmesg_out = "\n".join(
        [
            "[Sun Nov 22 10:00:01 2026] Linux version 6.8.0",
            "[Sun Nov 22 10:00:02 2026] blk_update_request: I/O error, dev sda, sector 1024",
            "[Sun Nov 22 10:00:03 2026] Buffer I/O error on dev sdb1, logical block 0",
        ]
    )
    linux_rescue_env.setattr(rescue, "_run_cmd", lambda argv, timeout=5: (0, dmesg_out, ""))

    all_io = rescue.collect_dmesg_io()
    assert all_io["available"] is True
    assert all_io["io_error_count"] == 2
    assert all_io["healthy_io"] is False

    sda_only = rescue.collect_dmesg_io(device="/dev/sda")
    assert sda_only["io_error_count"] == 1
    assert sda_only["device_filter"] == "sda"


def test_collect_smart_success_and_io_abort(linux_rescue_env):
    smart_json = json.dumps(
        {
            "model_name": "WD_BLACK SN850X",
            "serial_number": "WD12345",
            "smart_status": {"passed": True},
            "temperature": {"current": 38},
            "power_on_time": {"hours": 1420},
            "nvme_smart_health_information_log": {"percentage_used": 2},
        }
    )

    def fake_run(argv, timeout=10):
        if argv[0] == "dmesg":
            return 0, "[Sun] boot ok\n", ""
        if argv[0] == "smartctl":
            return 0, smart_json, ""
        return 1, "", "unexpected"

    linux_rescue_env.setattr(rescue, "_run_cmd", fake_run)
    res = rescue.collect_smart("/dev/nvme0n1")
    assert res["available"] is True
    assert res["model_name"] == "WD_BLACK SN850X"
    assert res["smart_passed"] is True
    assert res["temperature_c"] == 38

    # Now simulate >10 kernel I/O errors -> smartctl must be aborted before probing
    many_errors = "\n".join(
        f"[Sun] blk_update_request: I/O error, dev sda, sector {i}" for i in range(15)
    )
    linux_rescue_env.setattr(
        rescue,
        "_run_cmd",
        lambda argv, timeout=10: (0, many_errors, "") if argv[0] == "dmesg" else pytest.fail("smartctl should not run"),
    )
    aborted = rescue.collect_smart("/dev/sda")
    assert aborted["available"] is False
    assert aborted["reason"] == "aborted_due_to_active_kernel_io_errors"


# ---------------------------------------------------------------------------
# 3. collect_ram_info and collect_filesystems
# ---------------------------------------------------------------------------

def test_collect_ram_info_parses_all_four_sources(linux_rescue_env):
    meminfo_txt = (
        "MemTotal:       16384000 kB\n"
        "MemFree:         8192000 kB\n"
        "MemAvailable:   12288000 kB\n"
        "SwapTotal:       2097152 kB\n"
        "SwapFree:        2097152 kB\n"
        "HardwareCorrupted:     0 kB\n"
    )
    free_out = (
        "               total        used        free      shared  buff/cache   available\n"
        "Mem:     16777216000  4194304000  8388608000   104857600  4194304000 12582912000\n"
        "Swap:     2147483648           0  2147483648\n"
    )
    dmi_out = (
        "Physical Memory Array\n"
        "\tError Correction Type: Multi-bit ECC\n"
        "\tMaximum Capacity: 64 GB\n\n"
        "Memory Device\n"
        "\tSize: 16 GB\n"
        "\tType: DDR5\n"
        "\tSpeed: 4800 MT/s\n"
        "\tLocator: DIMM_A1\n"
        "\tManufacturer: Samsung\n\n"
        "Memory Device\n"
        "\tSize: No Module Installed\n"
        "\tLocator: DIMM_B1\n"
    )
    edac_out = "edac-util: No errors to report.\nmc0: 0 Uncorrected Errors with no DIMM info\n"

    linux_rescue_env.setattr(
        rescue, "_read_file_text", lambda p: meminfo_txt if p == "/proc/meminfo" else ""
    )

    def fake_run(argv, timeout=8):
        if argv[:2] == ["free", "-b"]:
            return 0, free_out, ""
        if argv[:3] == ["dmidecode", "-t", "memory"]:
            return 0, dmi_out, ""
        if argv[:2] == ["edac-util", "-s"]:
            return 0, edac_out, ""
        return 127, "", "not found"

    linux_rescue_env.setattr(rescue, "_run_cmd", fake_run)
    res = rescue.collect_ram_info()
    assert res["available"] is True
    assert res["meminfo"]["mem_total_bytes"] == 16384000 * 1024
    assert res["free"]["mem"]["total"] == 16777216000
    assert res["dmidecode"]["error_correction"] == "Multi-bit ECC"
    assert res["dmidecode"]["populated_slots"] == 1
    assert res["dmidecode"]["empty_slots"] == 1
    assert res["dmidecode"]["dimms"][0]["Type"] == "DDR5"
    assert res["edac"]["available"] is True
    assert len(res["edac"]["status_lines"]) == 2


def test_collect_filesystems_probes_kernel_and_binaries(linux_rescue_env):
    proc_fs = "nodev\ttmpfs\n\text4\n\tntfs3\n\texfat\n\tbtrfs\n\txfs\n\tf2fs\n\thfsplus\n"
    installed = {
        "ntfs-3g": "/usr/bin/ntfs-3g",
        "dislocker": "/usr/bin/dislocker",
        "e2fsck": "/sbin/e2fsck",
        "btrfs": "/sbin/btrfs",
        "xfs_repair": "/sbin/xfs_repair",
        "fsck.f2fs": "/sbin/fsck.f2fs",
        "zpool": "/sbin/zpool",
        "fsck.hfsplus": "/sbin/fsck.hfsplus",
        "apfs-fuse": "/usr/bin/apfs-fuse",
        "lvs": "/sbin/lvs",
        "mdadm": "/sbin/mdadm",
        "cryptsetup": "/sbin/cryptsetup",
    }
    linux_rescue_env.setattr(
        rescue, "_read_file_text", lambda p: proc_fs if p == "/proc/filesystems" else ""
    )
    linux_rescue_env.setattr(rescue, "_which", lambda name: installed.get(name))

    res = rescue.collect_filesystems()
    assert res["available"] is True
    expected_families = {
        "exfat", "ntfs", "dislocker", "e2fsprogs", "btrfs", "xfs", "f2fs",
        "zfs", "hfsplus", "apfs", "lvm2", "mdadm", "cryptsetup",
    }
    assert expected_families.issubset(set(res["filesystems"].keys()))
    for fam in expected_families:
        assert res["filesystems"][fam]["available"] is True, fam
    assert "ntfs3" in res["filesystems"]["ntfs"]["kernel_drivers"]
    assert "apfs-fuse" in res["filesystems"]["apfs"]["found_binaries"]


# ---------------------------------------------------------------------------
# 4. mount_auto across all supported filesystems
# ---------------------------------------------------------------------------

def test_mount_auto_ntfs_prefers_ntfs3_with_ntfs3g_fallback(linux_rescue_env):
    linux_rescue_env.setattr(rescue, "_read_file_text", lambda p: "\tntfs3\n" if p == "/proc/filesystems" else "")
    linux_rescue_env.setattr(rescue, "_which", lambda name: f"/usr/bin/{name}")

    plan = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/sda2", "fstype": "ntfs"}
    )
    assert plan["dry_run"] is True
    assert plan["writable"] is False
    assert plan["argv"] == ["mount", "-t", "ntfs3", "-o", "ro", "/dev/sda2", "/mnt/rescue/sda2"]
    assert plan["fallback_argv"] == ["ntfs-3g", "-o", "ro", "/dev/sda2", "/mnt/rescue/sda2"]

    # Live execution fallback when ntfs3 fails
    calls: List[List[str]] = []

    def fake_run(argv, timeout=20):
        calls.append(list(argv))
        if argv[0] == "mount":
            return 32, "", "ntfs3 mount failed"
        return 0, "mounted via ntfs-3g", ""

    linux_rescue_env.setattr(rescue, "_run_cmd", fake_run)
    linux_rescue_env.setattr(os, "makedirs", lambda *a, **kw: None)
    live = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/sda2", "fstype": "ntfs", "dry_run": False}
    )
    assert live["fallback_used"] is True
    assert live["exit_code"] == 0
    assert calls[0][:3] == ["mount", "-t", "ntfs3"]
    assert calls[1][0] == "ntfs-3g"


def test_mount_auto_exfat_and_linux_fs(linux_rescue_env):
    exfat_ro = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/sdb1", "fstype": "exfat"}
    )
    assert exfat_ro["dry_run"] is True
    assert exfat_ro["argv"] == ["mount", "-t", "exfat", "-o", "ro", "/dev/sdb1", "/mnt/rescue/sdb1"]

    exfat_rw = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/sdb1", "fstype": "exfat", "writable": True}
    )
    assert exfat_rw["argv"] == ["mount", "-t", "exfat", "-o", "rw,noatime", "/dev/sdb1", "/mnt/rescue/sdb1"]

    ext4_ro = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/nvme0n1p2", "fstype": "ext4"}
    )
    assert ext4_ro["argv"] == ["mount", "-t", "ext4", "-o", "ro,noload", "/dev/nvme0n1p2", "/mnt/rescue/nvme0n1p2"]

    btrfs_ro = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/nvme0n1p3", "fstype": "btrfs"}
    )
    assert btrfs_ro["argv"] == ["mount", "-t", "btrfs", "-o", "ro,norecovery", "/dev/nvme0n1p3", "/mnt/rescue/nvme0n1p3"]

    xfs_ro = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/nvme0n1p4", "fstype": "xfs"}
    )
    assert xfs_ro["argv"] == ["mount", "-t", "xfs", "-o", "ro,norecovery", "/dev/nvme0n1p4", "/mnt/rescue/nvme0n1p4"]

    f2fs_ro = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/nvme0n1p5", "fstype": "f2fs"}
    )
    assert f2fs_ro["argv"] == ["mount", "-t", "f2fs", "-o", "ro", "/dev/nvme0n1p5", "/mnt/rescue/nvme0n1p5"]


def test_mount_auto_hfsplus_ro_and_force_rw(linux_rescue_env):
    ro = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/sdc2", "fstype": "hfsplus"}
    )
    assert ro["argv"] == ["mount", "-t", "hfsplus", "-o", "ro", "/dev/sdc2", "/mnt/rescue/sdc2"]

    rw = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/sdc2", "fstype": "hfsplus", "writable": True}
    )
    assert rw["argv"] == ["mount", "-t", "hfsplus", "-o", "force,rw", "/dev/sdc2", "/mnt/rescue/sdc2"]


def test_mount_auto_apfs_unencrypted_encrypted_and_writable_warning(linux_rescue_env):
    # Unencrypted with kernel apfs driver available
    linux_rescue_env.setattr(rescue, "_read_file_text", lambda p: "\tapfs\n" if p == "/proc/filesystems" else "")
    unenc = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/nvme0n1p2", "fstype": "apfs"}
    )
    assert unenc["argv"] == ["mount", "-t", "apfs", "-o", "ro", "/dev/nvme0n1p2", "/mnt/rescue/nvme0n1p2"]
    assert unenc["fallback_argv"][0] == "apfs-fuse"

    # Encrypted -> apfs-fuse directly
    enc = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/nvme0n1p2", "fstype": "apfs", "encrypted": True}
    )
    assert enc["argv"][0] == "apfs-fuse"

    # writable=True -> explicit warning and forced read-only
    warn = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/nvme0n1p2", "fstype": "apfs", "writable": True}
    )
    assert warn["writable"] is False
    assert "warning" in warn
    assert "read-only" in warn["warning"].lower()


def test_mount_auto_bitlocker_luks_zfs(linux_rescue_env):
    bl = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/nvme0n1p3", "fstype": "bitlocker"}
    )
    assert bl["argv"][0] == "dislocker"
    assert "-r" in bl["argv"]
    assert len(bl["planned_steps"]) == 2
    assert bl["planned_steps"][1][0] == "mount"

    luks = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/sda3", "fstype": "luks", "mapper_name": "cryptroot"}
    )
    assert luks["argv"] == ["cryptsetup", "open", "--type", "luks", "--readonly", "/dev/sda3", "cryptroot"]
    assert luks["mapper_device"] == "/dev/mapper/cryptroot"

    zfs_ro = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "fstype": "zfs", "pool_name": "rpool", "mountpoint": "/mnt/rescue/rpool"}
    )
    assert zfs_ro["argv"] == ["zpool", "import", "-f", "-o", "readonly=on", "-R", "/mnt/rescue/rpool", "rpool"]

    zfs_rw = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "fstype": "zfs", "pool_name": "rpool", "mountpoint": "/mnt/rescue/rpool", "writable": True}
    )
    assert zfs_rw["argv"] == ["zpool", "import", "-f", "-R", "/mnt/rescue/rpool", "rpool"]


def test_mount_auto_detects_fstype_via_blkid(linux_rescue_env):
    linux_rescue_env.setattr(
        rescue,
        "_run_cmd",
        lambda argv, timeout=5: (0, "crypto_luks\n", "") if argv[0] == "blkid" else (1, "", ""),
    )
    res = rescue.plan_or_run_rescue_action({"action": "mount_auto", "device": "/dev/nvme0n1p5"})
    assert res["fstype"] == "luks"
    assert res["argv"][:2] == ["cryptsetup", "open"]


# ---------------------------------------------------------------------------
# 5. ddrescue_plan, ram_test_plan, ram_dump_plan & tmpfs guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_path", ["/tmp/disk.img", "/run/disk.img", "/var/tmp/disk.img"])
def test_ddrescue_plan_tmpfs_guard(bad_path):
    res = rescue.plan_or_run_rescue_action(
        {"action": "ddrescue_plan", "device": "/dev/sda", "target_image": bad_path}
    )
    assert "error" in res
    assert "volatile" in res["error"].lower()


def test_ddrescue_plan_valid_scratch_path():
    res = rescue.plan_or_run_rescue_action(
        {"action": "ddrescue_plan", "device": "/dev/sda", "target_image": "/mnt/scratch/sda.img"}
    )
    assert res["dry_run"] is True
    assert res["argv"] == ["ddrescue", "-f", "-n", "/dev/sda", "/mnt/scratch/sda.img", "/mnt/scratch/sda.img.mapfile"]


def test_ram_test_plan_builds_memtester_argv():
    res = rescue.plan_or_run_rescue_action(
        {"action": "ram_test_plan", "size_mb": 512, "loops": 2}
    )
    assert res["dry_run"] is True
    assert res["argv"] == ["memtester", "512M", "2"]


@pytest.mark.parametrize("bad_path", ["/tmp/ram.lime", "/run/ram.lime", "/var/tmp/ram.lime"])
def test_ram_dump_plan_tmpfs_guard(bad_path):
    res = rescue.plan_or_run_rescue_action({"action": "ram_dump_plan", "output_path": bad_path})
    assert "error" in res
    assert "volatile" in res["error"].lower()


def test_ram_dump_plan_avml_and_kcore(linux_rescue_env):
    linux_rescue_env.setattr(rescue, "_which", lambda name: "/usr/bin/avml" if name == "avml" else None)
    avml_res = rescue.plan_or_run_rescue_action(
        {"action": "ram_dump_plan", "output_path": "/mnt/scratch/mem.lime"}
    )
    assert avml_res["method"] == "avml"
    assert avml_res["argv"] == ["avml", "/mnt/scratch/mem.lime"]

    linux_rescue_env.setattr(rescue, "_which", lambda name: None)
    kcore_res = rescue.plan_or_run_rescue_action(
        {"action": "ram_dump_plan", "output_path": "/mnt/scratch/kcore.raw"}
    )
    assert kcore_res["method"] == "kcore"
    assert kcore_res["argv"][:3] == ["dd", "if=/proc/kcore", "of=/mnt/scratch/kcore.raw"]


# ---------------------------------------------------------------------------
# 6. mcp_tools registration, annotations, and dispatch
# ---------------------------------------------------------------------------

def test_mcp_tools_hides_rescue_tools_outside_live_usb(monkeypatch, restore_mcp_tools):
    monkeypatch.delenv("MESH_SCENARIO", raising=False)
    monkeypatch.setattr(device, "scenario", lambda: "linux")
    mcp_tools.sync_scenario_tools()

    tool_names = {t["name"] for t in mcp_tools.TOOLS}
    assert "rescue_info" not in tool_names
    assert "rescue_action" not in tool_names
    assert "rescue_info" not in mcp_tools.TOOL_ANNOTATIONS
    assert "rescue_action" not in mcp_tools.TOOL_ANNOTATIONS


def test_mcp_tools_registers_and_dispatches_rescue_tools_on_live_usb(
    monkeypatch, linux_rescue_env, restore_mcp_tools
):
    monkeypatch.setenv("MESH_SCENARIO", "live_usb")
    mcp_tools.sync_scenario_tools()

    by_name = {t["name"]: t for t in mcp_tools.TOOLS}
    assert "rescue_info" in by_name
    assert "rescue_action" in by_name

    # Verify MCP annotations
    info_hints = by_name["rescue_info"]["annotations"]
    assert info_hints["readOnlyHint"] is True
    assert info_hints["destructiveHint"] is False

    action_hints = by_name["rescue_action"]["annotations"]
    assert action_hints["readOnlyHint"] is False
    assert action_hints["destructiveHint"] is True

    # Dispatch rescue_info through mcp_tools.call_tool
    linux_rescue_env.setattr(rescue, "_read_file_text", lambda p: "\text4\n" if p == "/proc/filesystems" else "")
    linux_rescue_env.setattr(rescue, "_which", lambda name: "/sbin/e2fsck" if name == "e2fsck" else None)
    info_res = mcp_tools.call_tool("rescue_info", {"section": "filesystems"})
    assert info_res["available"] is True
    assert "e2fsprogs" in info_res["supported"]

    # Dispatch rescue_action through mcp_tools.call_tool
    action_res = mcp_tools.call_tool(
        "rescue_action",
        {"action": "ram_test_plan", "size_mb": 128, "loops": 1},
    )
    assert action_res["dry_run"] is True
    assert action_res["argv"] == ["memtester", "128M", "1"]


# ---------------------------------------------------------------------------
# 7. Volatile mount detection, mountpoint validation, BitLocker step 2, uplink lock
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("root_fstype", ["overlay", "tmpfs"])
def test_is_volatile_path_rejects_root_on_overlay_or_tmpfs(monkeypatch, root_fstype):
    proc_mounts = (
        f"{root_fstype} / {root_fstype} rw,relatime 0 0\n"
        "/dev/sdb1 /mnt/scratch ext4 rw,relatime 0 0\n"
    )
    monkeypatch.setattr(rescue, "_read_file_text", lambda p: proc_mounts if p == "/proc/mounts" else "")

    assert rescue._is_volatile_path("/root/dump.img") is True
    assert rescue._is_volatile_path("/mnt/scratch/dump.img") is False

    bad_ram = rescue.plan_or_run_rescue_action(
        {"action": "ram_dump_plan", "output_path": "/root/dump.img"}
    )
    assert "error" in bad_ram
    assert "volatile" in bad_ram["error"].lower()


def test_ddrescue_plan_rejects_volatile_log_file(monkeypatch):
    proc_mounts = (
        "overlay / overlay rw,relatime 0 0\n"
        "/dev/sdb1 /mnt/scratch ext4 rw,relatime 0 0\n"
    )
    monkeypatch.setattr(rescue, "_read_file_text", lambda p: proc_mounts if p == "/proc/mounts" else "")

    res_tmp = rescue.plan_or_run_rescue_action(
        {
            "action": "ddrescue_plan",
            "device": "/dev/sda",
            "target_image": "/mnt/scratch/sda.img",
            "log_file": "/tmp/sda.mapfile",
        }
    )
    assert "error" in res_tmp
    assert "volatile" in res_tmp["error"].lower()

    res_root = rescue.plan_or_run_rescue_action(
        {
            "action": "ddrescue_plan",
            "device": "/dev/sda",
            "target_image": "/mnt/scratch/sda.img",
            "log_file": "/root/sda.mapfile",
        }
    )
    assert "error" in res_root
    assert "volatile" in res_root["error"].lower()


@pytest.mark.parametrize(
    "bad_mp",
    ["/", "/etc", "/mnt/rescue", "/mnt/rescue/", "/mnt/rescue/../etc", "/mnt/scratch/../../etc", "relative/path"],
)
def test_valid_mountpoint_rejects_unsafe_paths(bad_mp):
    assert rescue._valid_mountpoint(bad_mp) is False

    res_safe = rescue.plan_or_run_rescue_action(
        {"action": "mount_safe", "device": "/dev/sda1", "mountpoint": bad_mp}
    )
    assert "error" in res_safe

    res_auto = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/sda1", "fstype": "ext4", "mountpoint": bad_mp}
    )
    assert "error" in res_auto

    res_grub = rescue.plan_or_run_rescue_action(
        {
            "action": "grub_repair",
            "device": "/dev/sda2",
            "efi_partition": "/dev/sda1",
            "mountpoint": bad_mp,
        }
    )
    assert "error" in res_grub


def test_valid_mountpoint_accepts_rescue_and_scratch_subdirs():
    assert rescue._valid_mountpoint("/mnt/rescue/sda2") is True
    assert rescue._valid_mountpoint("/mnt/scratch/recovery") is True


def test_mount_auto_bitlocker_executes_loop_mount_after_dislocker_succeeds(linux_rescue_env):
    calls: List[List[str]] = []

    def fake_run(argv, timeout=20):
        calls.append(list(argv))
        return 0, f"ok:{argv[0]}", ""

    linux_rescue_env.setattr(rescue, "_run_cmd", fake_run)
    linux_rescue_env.setattr(os, "makedirs", lambda *a, **kw: None)

    res = rescue.plan_or_run_rescue_action(
        {
            "action": "mount_auto",
            "device": "/dev/nvme0n1p3",
            "fstype": "bitlocker",
            "mountpoint": "/mnt/rescue/win_c",
            "dry_run": False,
        }
    )
    assert res["exit_code"] == 0
    assert res["unlock_exit_code"] == 0
    assert len(calls) == 2
    assert calls[0][0] == "dislocker"
    assert calls[1] == [
        "mount",
        "-o",
        "loop,ro",
        "/mnt/rescue/dislocker_nvme0n1p3/dislocker-file",
        "/mnt/rescue/win_c",
    ]


def test_active_uplink_lock_blocks_down_and_disconnect_in_live_usb(monkeypatch, restore_mcp_tools):
    monkeypatch.setenv("MESH_SCENARIO", "live_usb")
    monkeypatch.delenv("MESH_ALLOW_UPLINK_MUTATION", raising=False)
    mcp_tools.sync_scenario_tools()

    monkeypatch.setattr(mcp_tools._liveusb_bootstrap, "active_uplink_interface", lambda: "eth0")

    for cmd in (
        "ip link set eth0 down",
        "sudo ip link set dev eth0 down",
        "ifconfig eth0 down",
        "nmcli dev disconnect eth0",
    ):
        res_exec = mcp_tools.call_tool("bash_exec", {"command": cmd})
        assert "error" in res_exec
        assert "active_uplink_lock" in res_exec["error"]
        assert "eth0" in res_exec["error"]

        res_job = mcp_tools.call_tool("run_job", {"command": cmd})
        assert "error" in res_job
        assert "active_uplink_lock" in res_job["error"]
        assert "eth0" in res_job["error"]

    # Non-uplink interface is NOT blocked by active_uplink_lock
    assert mcp_tools._check_active_uplink_lock("ip link set eth1 down") is None

    # Explicit override via MESH_ALLOW_UPLINK_MUTATION=1 lifts the lock
    monkeypatch.setenv("MESH_ALLOW_UPLINK_MUTATION", "1")
    assert mcp_tools._check_active_uplink_lock("ip link set eth0 down") is None


# ---------------------------------------------------------------------------
# 8. Security hardening tests (F-07, F-08)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_dev",
    [
        "/dev/mem",
        "/dev/kmem",
        "/dev/port",
        "/dev/null",
        "/dev/zero",
        "/dev/shm",
        "/dev/shm/evil",
        "/dev/../etc/passwd",
        "/dev//sda1",
    ],
)
def test_valid_dev_path_rejects_dangerous_and_malformed_devices(bad_dev):
    assert rescue._valid_dev_path(bad_dev) is False
    res = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": bad_dev, "fstype": "ext4"}
    )
    assert "error" in res


def test_valid_mountpoint_rejects_symlink_escape(monkeypatch):
    monkeypatch.setattr(rescue, "_resolve_nearest_existing_realpath", lambda p: "/etc/evil")
    assert rescue._valid_mountpoint("/mnt/rescue/symlink_escape") is False


@pytest.mark.parametrize("bad_id", ["-rf", "../escape", "bad;cmd", "a" * 65, ".hidden"])
def test_mount_auto_rejects_invalid_mapper_and_pool_names(linux_rescue_env, bad_id):
    res_luks = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "device": "/dev/sda3", "fstype": "luks", "mapper_name": bad_id}
    )
    assert "error" in res_luks

    res_zfs = rescue.plan_or_run_rescue_action(
        {"action": "mount_auto", "fstype": "zfs", "pool_name": bad_id, "mountpoint": "/mnt/rescue/z"}
    )
    assert "error" in res_zfs


def test_mount_auto_bitlocker_redacts_recovery_key_in_plan(linux_rescue_env):
    secret = "111111-222222-333333-444444-555555-666666-777777-888888"
    plan = rescue.plan_or_run_rescue_action(
        {
            "action": "mount_auto",
            "device": "/dev/nvme0n1p3",
            "fstype": "bitlocker",
            "recovery_key": secret,
        }
    )
    assert "-p***REDACTED***" in plan["argv"]
    assert secret not in json.dumps(plan)
    assert "-p***REDACTED***" in plan["planned_steps"][0]


def test_recommended_execution_uses_shlex_join():
    res = rescue.plan_or_run_rescue_action(
        {"action": "ddrescue_plan", "device": "/dev/sda", "target_image": "/mnt/scratch/sda.img"}
    )
    assert "shlex.join(argv)" in res["recommended_execution"]


@pytest.mark.parametrize(
    "cmd",
    [
        "rfkill block all",
        "rfkill block wifi",
        "nmcli networking off",
        "nmcli radio wifi off",
        "nmcli con down uplink-profile",
        "ip l s eth0 down",
        "ip a f eth0",
        "ip r del default",
        "systemctl stop NetworkManager",
        "systemctl restart systemd-networkd",
        "systemctl stop wpa_supplicant",
        "systemctl restart rhm-live-agent",
        "iw dev eth0 disconnect",
        "dhclient -r eth0",
    ],
)
def test_active_uplink_lock_blocks_expanded_bypass_patterns(monkeypatch, restore_mcp_tools, cmd):
    monkeypatch.setenv("MESH_SCENARIO", "live_usb")
    monkeypatch.delenv("MESH_ALLOW_UPLINK_MUTATION", raising=False)
    mcp_tools.sync_scenario_tools()
    monkeypatch.setattr(mcp_tools._liveusb_bootstrap, "active_uplink_interface", lambda: "eth0")

    err = mcp_tools._check_active_uplink_lock(cmd)
    assert err is not None
    assert "active_uplink_lock" in err
