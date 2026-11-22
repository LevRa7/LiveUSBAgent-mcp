#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/rescue.py - Live USB hardware diagnostics & OS recovery schemas and collectors.

Designed to follow the exact contract of :mod:`core.device` and :mod:`core.mcp_tools`:
* Standard library only (Python 3.8+);
* Never raises: returns ``{"available": False, "reason": ...}`` or ``{"error": ...}``;
* Strict separation between Read-Only inspection (``rescue_info``) and mutating
  recovery operations (``rescue_action``) with mandatory ``dry_run`` support;
* Protects dying storage media by enforcing ``ro,noload`` mounts and checking kernel
  ring buffers for I/O errors before probing block devices.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

_IS_WINDOWS = os.name == "nt"

_VOLATILE_PREFIXES: Tuple[str, ...] = ("/tmp/", "/run/", "/var/tmp/")
_VOLATILE_EXACT: Tuple[str, ...] = ("/tmp", "/run", "/var/tmp")

RESCUE_SECTIONS: Tuple[str, ...] = (
    "summary",
    "all",
    "disks",
    "smart",
    "partitions",
    "raid_lvm",
    "installed_os",
    "boot_efi",
    "win_artifacts",
    "dmesg_io",
    "ram_info",
    "filesystems",
)

RESCUE_ACTIONS: Tuple[str, ...] = (
    "mount_safe",
    "mount_auto",
    "umount",
    "fsck_check",
    "ntfs_fix",
    "grub_repair",
    "win_sam_reset",
    "ddrescue_plan",
    "chroot_exec",
    "ram_test_plan",
    "ram_dump_plan",
)

#: Filesystem driver and binary probe matrix for ``collect_filesystems()``.
_FS_PROBES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "exfat": {
        "kernel": ("exfat",),
        "binaries": ("mount.exfat", "mount.exfat-fuse", "fsck.exfat", "mkfs.exfat"),
    },
    "ntfs": {
        "kernel": ("ntfs3", "ntfs"),
        "binaries": ("ntfs-3g", "mount.ntfs-3g", "ntfsfix"),
    },
    "dislocker": {
        "kernel": (),
        "binaries": ("dislocker", "dislocker-fuse"),
    },
    "e2fsprogs": {
        "kernel": ("ext4", "ext3", "ext2"),
        "binaries": ("e2fsck", "fsck.ext4", "dumpe2fs", "tune2fs"),
    },
    "btrfs": {
        "kernel": ("btrfs",),
        "binaries": ("btrfs", "btrfsck"),
    },
    "xfs": {
        "kernel": ("xfs",),
        "binaries": ("xfs_repair", "xfs_info", "xfs_db"),
    },
    "f2fs": {
        "kernel": ("f2fs",),
        "binaries": ("fsck.f2fs", "dump.f2fs"),
    },
    "zfs": {
        "kernel": ("zfs",),
        "binaries": ("zpool", "zfs"),
    },
    "hfsplus": {
        "kernel": ("hfsplus",),
        "binaries": ("fsck.hfsplus", "mount.hfsplus"),
    },
    "apfs": {
        "kernel": ("apfs",),
        "binaries": ("apfs-fuse", "fsapfsmount", "fsapfsinfo"),
    },
    "lvm2": {
        "kernel": (),
        "binaries": ("lvs", "vgs", "pvs", "vgchange", "lvscan"),
    },
    "mdadm": {
        "kernel": (),
        "binaries": ("mdadm",),
    },
    "cryptsetup": {
        "kernel": (),
        "binaries": ("cryptsetup",),
    },
}


def _schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        out["required"] = list(required)
    return out


RESCUE_TOOLS_SPEC: List[Dict[str, Any]] = [
    {
        "name": "rescue_info",
        "title": "Live USB Rescue & Storage Diagnostics",
        "description": (
            "Read-only hardware and OS recovery inspection for Live USB nodes: "
            "block devices, SMART health attributes, partition tables, RAID/LVM/LUKS topology, "
            "installed Linux/Windows roots on unmounted drives, UEFI boot entries, kernel "
            "I/O error logs, RAM/ECC diagnostics, and installed filesystem drivers/tools. "
            "Never modifies disks."
        ),
        "inputSchema": _schema(
            {
                "section": {
                    "type": "string",
                    "enum": list(RESCUE_SECTIONS),
                    "description": "Diagnostic section to collect (default: summary).",
                },
                "device": {
                    "type": "string",
                    "description": "Optional target block device, e.g. /dev/nvme0n1 or /dev/sda.",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Probe deadline in seconds (1-30, default 10).",
                },
            }
        ),
    },
    {
        "name": "rescue_action",
        "title": "Live USB OS & Filesystem Recovery Action",
        "description": (
            "Perform a controlled recovery action on an unmounted target disk, guest OS, or RAM. "
            "Supports dry_run=true (default when omitted) to preview the exact command argv and "
            "safety checks before touching the disk. Refuses to write disk images or RAM dumps "
            "to volatile tmpfs/overlay RAM disks."
        ),
        "inputSchema": _schema(
            {
                "action": {
                    "type": "string",
                    "enum": list(RESCUE_ACTIONS),
                    "description": "Recovery operation to perform.",
                },
                "device": {
                    "type": "string",
                    "description": "Target block device or partition (e.g. /dev/nvme0n1p2).",
                },
                "mountpoint": {
                    "type": "string",
                    "description": "Mount directory (default: /mnt/rescue/<dev>).",
                },
                "fstype": {
                    "type": "string",
                    "description": (
                        "mount_auto: optional explicit filesystem type "
                        "(exfat, ntfs, ext2/3/4, btrfs, xfs, f2fs, hfsplus, apfs, bitlocker, luks, zfs)."
                    ),
                },
                "writable": {
                    "type": "boolean",
                    "description": "mount_safe/mount_auto: allow read-write mount (default false).",
                },
                "encrypted": {
                    "type": "boolean",
                    "description": "mount_auto: hint that an APFS/LUKS/BitLocker volume is encrypted.",
                },
                "mapper_name": {
                    "type": "string",
                    "description": "mount_auto (luks): device-mapper target name under /dev/mapper/.",
                },
                "pool_name": {
                    "type": "string",
                    "description": "mount_auto (zfs): ZFS pool name to import.",
                },
                "recovery_key": {
                    "type": "string",
                    "description": "mount_auto (bitlocker): optional BitLocker recovery password.",
                },
                "target_image": {
                    "type": "string",
                    "description": "ddrescue_plan: destination image file on a non-volatile disk.",
                },
                "output_path": {
                    "type": "string",
                    "description": "ram_dump_plan: destination path on a non-volatile disk.",
                },
                "method": {
                    "type": "string",
                    "enum": ["avml", "kcore"],
                    "description": "ram_dump_plan: preferred capture method (avml or kcore).",
                },
                "size_mb": {
                    "type": "integer",
                    "description": "ram_test_plan: megabytes of RAM to test with memtester (default 256).",
                },
                "loops": {
                    "type": "integer",
                    "description": "ram_test_plan: number of memtester iterations (default 1).",
                },
                "log_file": {
                    "type": "string",
                    "description": "ddrescue_plan: mapfile/logfile path for resumable cloning.",
                },
                "efi_partition": {
                    "type": "string",
                    "description": "grub_repair: EFI System Partition (e.g. /dev/nvme0n1p1).",
                },
                "sam_user": {
                    "type": "string",
                    "description": "win_sam_reset: Windows local username to unlock/clear password.",
                },
                "chroot_cmd": {
                    "type": "string",
                    "description": "chroot_exec: command to execute inside the mounted guest OS.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Validate safety guards and return exact argv without executing (default true).",
                },
            },
            ["action"],
        ),
    },
]


# ---------------------------------------------------------------------------
# Low-level helpers (never raise)
# ---------------------------------------------------------------------------

def _which(name: str) -> Optional[str]:
    try:
        return shutil.which(name)
    except Exception:
        return None


def _read_file_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except Exception:
        return ""


def _run_cmd(argv: Sequence[str], timeout: int = 10) -> Tuple[int, str, str]:
    """Run an external binary in a new session with a hard timeout."""
    cmd = list(argv) if argv else []
    if not cmd or _which(cmd[0]) is None:
        return 127, "", f"binary not found: {cmd[0] if cmd else ''}"
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if not _IS_WINDOWS:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.run(cmd, timeout=max(1, int(timeout)), **kwargs)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s (potential D-state I/O hang)"
    except Exception as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"


def _valid_dev_path(dev: str) -> bool:
    return bool(re.match(r"^/dev/[a-zA-Z0-9_\-/]+$", dev or ""))


def _is_volatile_path(path: str) -> bool:
    """True if path points inside a volatile RAM filesystem (/tmp, /run, /var/tmp)."""
    cleaned = (path or "").strip()
    if not cleaned:
        return False
    norm = os.path.normpath(cleaned).replace("\\", "/")
    if norm in _VOLATILE_EXACT:
        return True
    return any(norm.startswith(prefix) or cleaned.startswith(prefix) for prefix in _VOLATILE_PREFIXES)


def _kernel_filesystems() -> List[str]:
    """Return sorted filesystem names listed in /proc/filesystems."""
    text = _read_file_text("/proc/filesystems")
    if not text:
        return []
    found: List[str] = []
    for line in text.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        fs_name = parts[-1].strip().lower()
        if fs_name and fs_name not in found:
            found.append(fs_name)
    return sorted(found)


# ---------------------------------------------------------------------------
# Read-only diagnostic collectors
# ---------------------------------------------------------------------------

def collect_dmesg_io(device: Optional[str] = None, timeout: int = 5) -> Dict[str, Any]:
    """Scan kernel buffer for block I/O errors before touching potentially dying media."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "dmesg_io is only available on Linux Live USB nodes"}
    rc, out, err = _run_cmd(["dmesg", "--color=never", "-T"], timeout=timeout)
    if rc != 0:
        return {"available": False, "reason": err.strip() or "dmesg failed", "source": "dmesg"}
    pattern = re.compile(
        r"(I/O error|blk_update_request|Buffer I/O error|critical medium error|nvme.*I/O Cmd|FPDMA QUEUED)",
        re.IGNORECASE,
    )
    dev_short = os.path.basename(device) if device else ""
    matches: List[str] = []
    for line in out.splitlines()[-2000:]:
        if pattern.search(line):
            if not dev_short or dev_short in line:
                matches.append(line.strip())
    return {
        "available": True,
        "source": "dmesg",
        "device_filter": dev_short or None,
        "io_error_count": len(matches),
        "healthy_io": len(matches) == 0,
        "recent_errors": matches[-25:],
    }


def collect_disks(timeout: int = 8) -> Dict[str, Any]:
    """Return structured block device topology from lsblk -J."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "lsblk requires Linux"}
    argv = [
        "lsblk", "-J", "-b", "-o",
        "NAME,KNAME,PATH,SIZE,FSTYPE,FSVER,LABEL,UUID,TYPE,MOUNTPOINT,MODEL,SERIAL,RO,RM,TRAN",
    ]
    rc, out, err = _run_cmd(argv, timeout=timeout)
    if rc != 0:
        return {"available": False, "reason": err.strip() or "lsblk failed", "source": "lsblk"}
    try:
        parsed = json.loads(out)
    except Exception as exc:
        return {"available": False, "reason": f"lsblk json parse error: {exc}", "source": "lsblk"}
    return {
        "available": True,
        "source": "lsblk",
        "blockdevices": parsed.get("blockdevices", []),
    }


def collect_smart(device: Optional[str] = None, timeout: int = 10) -> Dict[str, Any]:
    """Collect SMART health via smartctl -a -j."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "Linux smartctl collector only"}
    if not device or not _valid_dev_path(device):
        return {"available": False, "reason": "Provide a valid device path, e.g. /dev/nvme0n1 or /dev/sda"}
    io_check = collect_dmesg_io(device=device, timeout=4)
    if io_check.get("io_error_count", 0) > 10:
        return {
            "available": False,
            "reason": "aborted_due_to_active_kernel_io_errors",
            "dmesg_io": io_check,
        }
    rc, out, err = _run_cmd(["smartctl", "-a", "-j", device], timeout=timeout)
    try:
        data = json.loads(out) if out.strip() else {}
    except Exception:
        data = {}
    if not data:
        return {"available": False, "reason": err.strip() or f"smartctl exited with {rc}", "source": "smartctl"}
    return {
        "available": True,
        "source": "smartctl",
        "device": device,
        "model_name": data.get("model_name"),
        "serial_number": data.get("serial_number"),
        "smart_passed": (data.get("smart_status") or {}).get("passed"),
        "temperature_c": (data.get("temperature") or {}).get("current"),
        "power_on_hours": (data.get("power_on_time") or {}).get("hours"),
        "nvme_health": data.get("nvme_smart_health_information_log"),
    }


def _parse_proc_meminfo(text: str) -> Dict[str, Any]:
    """Parse /proc/meminfo key/value kB entries."""
    if not text:
        return {"available": False, "reason": "/proc/meminfo not readable"}
    values_kb: Dict[str, int] = {}
    wanted = {
        "MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached",
        "SwapTotal", "SwapFree", "HardwareCorrupted",
    }
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        if key not in wanted:
            continue
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            values_kb[key] = int(parts[0])
        except ValueError:
            continue
    if not values_kb:
        return {"available": False, "reason": "no recognized fields in /proc/meminfo"}
    return {
        "available": True,
        "source": "/proc/meminfo",
        "kb": values_kb,
        "mem_total_bytes": values_kb.get("MemTotal", 0) * 1024,
        "mem_available_bytes": values_kb.get("MemAvailable", 0) * 1024,
        "swap_total_bytes": values_kb.get("SwapTotal", 0) * 1024,
        "hardware_corrupted_kb": values_kb.get("HardwareCorrupted", 0),
    }


def _parse_free_b(text: str) -> Dict[str, Any]:
    """Parse `free -b` output into Mem and Swap byte dictionaries."""
    if not text.strip():
        return {"available": False, "reason": "empty output from free -b"}
    mem: Dict[str, int] = {}
    swap: Dict[str, int] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Mem:"):
            cols = stripped.split()[1:]
            keys = ("total", "used", "free", "shared", "buff_cache", "available")
            for k, v in zip(keys, cols):
                try:
                    mem[k] = int(v)
                except ValueError:
                    pass
        elif stripped.startswith("Swap:"):
            cols = stripped.split()[1:]
            keys = ("total", "used", "free")
            for k, v in zip(keys, cols):
                try:
                    swap[k] = int(v)
                except ValueError:
                    pass
    if not mem and not swap:
        return {"available": False, "reason": "could not parse free -b output"}
    return {"available": True, "source": "free -b", "mem": mem, "swap": swap}


def _parse_dmidecode_memory(text: str) -> Dict[str, Any]:
    """Parse `dmidecode -t memory` output for array ECC and populated DIMMs."""
    if not text.strip():
        return {"available": False, "reason": "empty dmidecode output"}
    ecc_type: Optional[str] = None
    max_capacity: Optional[str] = None
    dimms: List[Dict[str, str]] = []
    empty_slots = 0

    current_section = ""
    current_dimm: Dict[str, str] = {}

    def _flush_dimm() -> None:
        nonlocal empty_slots
        if not current_dimm:
            return
        size = current_dimm.get("Size", "")
        if not size or "no module installed" in size.lower():
            empty_slots += 1
        else:
            dimms.append(dict(current_dimm))

    dimm_keys = {
        "Size", "Type", "Speed", "Configured Memory Speed",
        "Manufacturer", "Part Number", "Serial Number", "Locator",
        "Form Factor", "Data Width", "Total Width",
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current_section == "Memory Device":
                _flush_dimm()
                current_dimm = {}
                current_section = ""
            continue
        if line == "Physical Memory Array":
            current_section = "Physical Memory Array"
            continue
        if line == "Memory Device":
            _flush_dimm()
            current_dimm = {}
            current_section = "Memory Device"
            continue
        if ":" not in line:
            continue
        k, v = [p.strip() for p in line.split(":", 1)]
        if current_section == "Physical Memory Array":
            if k == "Error Correction Type":
                ecc_type = v
            elif k == "Maximum Capacity":
                max_capacity = v
        elif current_section == "Memory Device" and k in dimm_keys:
            current_dimm[k] = v

    if current_section == "Memory Device":
        _flush_dimm()

    return {
        "available": True,
        "source": "dmidecode -t memory",
        "error_correction": ecc_type,
        "max_capacity": max_capacity,
        "populated_slots": len(dimms),
        "empty_slots": empty_slots,
        "dimms": dimms,
    }


def collect_ram_info(timeout: int = 8) -> Dict[str, Any]:
    """Collect RAM capacity, DIMM inventory, and ECC status on Linux Live USB nodes."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "ram_info is only available on Linux Live USB nodes"}

    meminfo = _parse_proc_meminfo(_read_file_text("/proc/meminfo"))

    rc_free, out_free, err_free = _run_cmd(["free", "-b"], timeout=timeout)
    if rc_free == 0:
        free_block = _parse_free_b(out_free)
    else:
        free_block = {"available": False, "reason": err_free.strip() or f"free exited with {rc_free}"}

    rc_dmi, out_dmi, err_dmi = _run_cmd(["dmidecode", "-t", "memory"], timeout=timeout)
    if rc_dmi == 0 and out_dmi.strip():
        dmi_block = _parse_dmidecode_memory(out_dmi)
    else:
        dmi_block = {"available": False, "reason": err_dmi.strip() or f"dmidecode exited with {rc_dmi}"}

    rc_edac, out_edac, err_edac = _run_cmd(["edac-util", "-s"], timeout=timeout)
    if rc_edac == 0 and out_edac.strip():
        edac_lines = [ln.strip() for ln in out_edac.splitlines() if ln.strip()]
        edac_block: Dict[str, Any] = {
            "available": True,
            "source": "edac-util -s",
            "status_lines": edac_lines,
        }
    else:
        edac_block = {
            "available": False,
            "reason": err_edac.strip() or f"edac-util exited with {rc_edac}",
        }

    any_ok = any(
        b.get("available") for b in (meminfo, free_block, dmi_block, edac_block)
    )
    if not any_ok:
        return {
            "available": False,
            "reason": "no RAM diagnostic source answered",
            "meminfo": meminfo,
            "free": free_block,
            "dmidecode": dmi_block,
            "edac": edac_block,
        }
    return {
        "available": True,
        "source": "/proc/meminfo + free + dmidecode + edac-util",
        "meminfo": meminfo,
        "free": free_block,
        "dmidecode": dmi_block,
        "edac": edac_block,
    }


def collect_filesystems(timeout: int = 5) -> Dict[str, Any]:
    """Probe installed filesystem kernel drivers and user-space recovery binaries."""
    _ = timeout  # kept for uniform collector signature
    if _IS_WINDOWS:
        return {"available": False, "reason": "filesystems probe is only available on Linux Live USB nodes"}

    kernel_fs = _kernel_filesystems()
    kernel_set = set(kernel_fs)
    results: Dict[str, Dict[str, Any]] = {}
    for family, spec in _FS_PROBES.items():
        matched_kernel = [k for k in spec["kernel"] if k in kernel_set]
        bin_map: Dict[str, Optional[str]] = {}
        found_bins: List[str] = []
        for binary in spec["binaries"]:
            path = _which(binary)
            bin_map[binary] = path
            if path:
                found_bins.append(binary)
        results[family] = {
            "available": bool(matched_kernel or found_bins),
            "kernel_drivers": matched_kernel,
            "binaries": bin_map,
            "found_binaries": found_bins,
        }

    supported = [name for name, info in results.items() if info["available"]]
    return {
        "available": True,
        "source": "/proc/filesystems + PATH",
        "kernel_filesystems": kernel_fs,
        "filesystems": results,
        "supported": supported,
    }


# ---------------------------------------------------------------------------
# Recovery action planner & executor
# ---------------------------------------------------------------------------

def _normalize_fstype(raw: str) -> str:
    fs = (raw or "").strip().lower()
    aliases = {
        "ntfs3": "ntfs",
        "ntfs-3g": "ntfs",
        "fuseblk": "ntfs",
        "hfs+": "hfsplus",
        "crypto_luks": "luks",
        "zfs_member": "zfs",
        "bitlocker": "bitlocker",
    }
    return aliases.get(fs, fs)


def _detect_fstype(device: str, timeout: int = 5) -> str:
    rc, out, _err = _run_cmd(["blkid", "-o", "value", "-s", "TYPE", device], timeout=timeout)
    if rc == 0 and out.strip():
        return _normalize_fstype(out.splitlines()[0].strip())
    return ""


def _plan_or_run_mount_auto(args: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    device = str(args.get("device") or "").strip()
    writable = bool(args.get("writable", False))
    encrypted = bool(args.get("encrypted", False))
    pool_name = str(args.get("pool_name") or "").strip()
    mapper_name = str(args.get("mapper_name") or "").strip()
    recovery_key = str(args.get("recovery_key") or "").strip()

    fstype = _normalize_fstype(str(args.get("fstype") or ""))
    if not fstype and device and _valid_dev_path(device):
        fstype = _detect_fstype(device)

    if fstype != "zfs" and not _valid_dev_path(device):
        return {"error": "mount_auto requires a valid device path (e.g. /dev/nvme0n1p2)"}
    if not fstype:
        return {"error": "Could not detect filesystem type; pass 'fstype' explicitly."}

    dev_name = os.path.basename(device) if device else (pool_name or "target")
    mountpoint = str(args.get("mountpoint") or f"/mnt/rescue/{dev_name}").strip()

    warnings: List[str] = []
    fallback_argv: Optional[List[str]] = None
    planned_steps: Optional[List[List[str]]] = None
    extra: Dict[str, Any] = {}

    if fstype == "exfat":
        opts = "rw,noatime" if writable else "ro"
        argv = ["mount", "-t", "exfat", "-o", opts, device, mountpoint]

    elif fstype == "ntfs":
        opts = "rw,noatime" if writable else "ro"
        kernel_fs = set(_kernel_filesystems())
        ntfs3_argv = ["mount", "-t", "ntfs3", "-o", opts, device, mountpoint]
        ntfs3g_argv = ["ntfs-3g", "-o", opts, device, mountpoint]
        if "ntfs3" in kernel_fs or not _which("ntfs-3g"):
            argv = ntfs3_argv
            fallback_argv = ntfs3g_argv
        else:
            argv = ntfs3g_argv
            fallback_argv = ntfs3_argv

    elif fstype in ("ext2", "ext3", "ext4"):
        opts = "rw,noatime" if writable else "ro,noload"
        argv = ["mount", "-t", fstype, "-o", opts, device, mountpoint]

    elif fstype in ("btrfs", "xfs"):
        opts = "rw,noatime" if writable else "ro,norecovery"
        argv = ["mount", "-t", fstype, "-o", opts, device, mountpoint]

    elif fstype == "f2fs":
        opts = "rw,noatime" if writable else "ro"
        argv = ["mount", "-t", "f2fs", "-o", opts, device, mountpoint]

    elif fstype == "hfsplus":
        opts = "force,rw" if writable else "ro"
        argv = ["mount", "-t", "hfsplus", "-o", opts, device, mountpoint]

    elif fstype == "apfs":
        if writable:
            warnings.append(
                "APFS write support on Linux is experimental/unsupported; forcing read-only mount."
            )
            writable = False
        kernel_fs = set(_kernel_filesystems())
        fuse_argv = ["apfs-fuse", "-o", "ro,allow_other", device, mountpoint]
        kernel_argv = ["mount", "-t", "apfs", "-o", "ro", device, mountpoint]
        if encrypted or "apfs" not in kernel_fs:
            argv = fuse_argv
            if not encrypted:
                fallback_argv = kernel_argv
        else:
            argv = kernel_argv
            fallback_argv = fuse_argv

    elif fstype == "bitlocker":
        dislocker_dir = f"/mnt/rescue/dislocker_{dev_name}"
        unlock_argv = ["dislocker", "-V", device]
        if recovery_key:
            unlock_argv.append(f"-p{recovery_key}")
        if not writable:
            unlock_argv.append("-r")
        unlock_argv.extend(["--", dislocker_dir])
        loop_opts = "loop,rw" if writable else "loop,ro"
        loop_argv = ["mount", "-o", loop_opts, f"{dislocker_dir}/dislocker-file", mountpoint]
        argv = unlock_argv
        planned_steps = [unlock_argv, loop_argv]
        extra["dislocker_dir"] = dislocker_dir

    elif fstype == "luks":
        target_name = mapper_name or f"rescue_{dev_name}"
        argv = ["cryptsetup", "open", "--type", "luks"]
        if not writable:
            argv.append("--readonly")
        argv.extend([device, target_name])
        extra["mapper_name"] = target_name
        extra["mapper_device"] = f"/dev/mapper/{target_name}"
        extra["next_step"] = f"Run mount_auto on /dev/mapper/{target_name} after unlocking."

    elif fstype == "zfs":
        argv = ["zpool", "import", "-f"]
        if not writable:
            argv.extend(["-o", "readonly=on"])
        argv.extend(["-R", mountpoint])
        if pool_name:
            argv.append(pool_name)
        else:
            argv.append("-a")
        extra["pool_name"] = pool_name or None

    else:
        return {"error": f"Unsupported filesystem type for mount_auto: {fstype}"}

    plan: Dict[str, Any] = {
        "action": "mount_auto",
        "device": device or None,
        "fstype": fstype,
        "mountpoint": mountpoint,
        "writable": writable,
        "argv": argv,
        "dry_run": dry_run,
    }
    if fallback_argv:
        plan["fallback_argv"] = fallback_argv
    if planned_steps:
        plan["planned_steps"] = planned_steps
    if warnings:
        plan["warning"] = " ".join(warnings)
        plan["warnings"] = warnings
    plan.update(extra)

    if dry_run or _IS_WINDOWS:
        return plan

    if fstype not in ("luks",):
        os.makedirs(mountpoint, exist_ok=True)
    if fstype == "bitlocker" and extra.get("dislocker_dir"):
        os.makedirs(str(extra["dislocker_dir"]), exist_ok=True)

    rc, out, err = _run_cmd(argv, timeout=20)
    if rc != 0 and fallback_argv:
        rc_fb, out_fb, err_fb = _run_cmd(fallback_argv, timeout=20)
        plan.update(
            {
                "primary_exit_code": rc,
                "primary_stderr": err,
                "fallback_used": True,
                "executed_argv": fallback_argv,
                "exit_code": rc_fb,
                "stdout": out_fb,
                "stderr": err_fb,
            }
        )
        return plan

    plan.update({"exit_code": rc, "stdout": out, "stderr": err})
    return plan


def plan_or_run_rescue_action(args: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and build (or execute) a recovery action with strict safety guards."""
    if not isinstance(args, dict):
        return {"error": "rescue_action arguments must be a JSON object"}

    action = str(args.get("action") or "").strip()
    dry_run = True if args.get("dry_run") is None else bool(args.get("dry_run"))
    device = str(args.get("device") or "").strip()

    if action not in RESCUE_ACTIONS:
        return {"error": f"Unknown rescue action: {action}"}

    if action == "mount_safe":
        if not _valid_dev_path(device):
            return {"error": "mount_safe requires a valid device path (e.g. /dev/nvme0n1p2)"}
        writable = bool(args.get("writable", False))
        dev_name = os.path.basename(device)
        mountpoint = str(args.get("mountpoint") or f"/mnt/rescue/{dev_name}").strip()
        opts = "rw,noatime" if writable else "ro,noload"
        argv = ["mount", "-o", opts, device, mountpoint]
        plan = {
            "action": action,
            "device": device,
            "mountpoint": mountpoint,
            "writable": writable,
            "options": opts,
            "argv": argv,
            "dry_run": dry_run,
        }
        if dry_run or _IS_WINDOWS:
            return plan
        os.makedirs(mountpoint, exist_ok=True)
        rc, out, err = _run_cmd(argv, timeout=15)
        plan.update({"exit_code": rc, "stdout": out, "stderr": err})
        return plan

    if action == "mount_auto":
        return _plan_or_run_mount_auto(args, dry_run=dry_run)

    if action == "ddrescue_plan":
        target_image = str(args.get("target_image") or "").strip()
        log_file = str(args.get("log_file") or f"{target_image}.mapfile").strip()
        if not _valid_dev_path(device) or not target_image:
            return {"error": "ddrescue_plan requires both 'device' and 'target_image'"}
        if _is_volatile_path(target_image):
            return {
                "error": (
                    "Refusing to write recovery image to volatile RAM filesystem "
                    "(/tmp, /run, or /var/tmp). Mount an external scratch disk under /mnt/scratch first."
                )
            }
        argv = ["ddrescue", "-f", "-n", device, target_image, log_file]
        return {
            "action": action,
            "dry_run": True,
            "argv": argv,
            "recommended_execution": "Pass ' '.join(argv) to run_job() for non-blocking background cloning.",
        }

    if action == "ram_test_plan":
        try:
            size_mb = int(args.get("size_mb") or 256)
            loops = int(args.get("loops") or 1)
        except (TypeError, ValueError):
            return {"error": "ram_test_plan 'size_mb' and 'loops' must be positive integers"}
        if size_mb < 1 or loops < 1:
            return {"error": "ram_test_plan 'size_mb' and 'loops' must be >= 1"}
        argv = ["memtester", f"{size_mb}M", str(loops)]
        plan = {
            "action": action,
            "size_mb": size_mb,
            "loops": loops,
            "dry_run": dry_run,
            "argv": argv,
            "recommended_execution": "Pass ' '.join(argv) to run_job() for non-blocking memory testing.",
        }
        if dry_run or _IS_WINDOWS:
            return plan
        rc, out, err = _run_cmd(argv, timeout=60)
        plan.update({"exit_code": rc, "stdout": out, "stderr": err})
        return plan

    if action == "ram_dump_plan":
        output_path = str(args.get("output_path") or args.get("target_image") or "").strip()
        if not output_path:
            return {"error": "ram_dump_plan requires 'output_path' on a non-volatile external disk"}
        if _is_volatile_path(output_path):
            return {
                "error": (
                    "Refusing to write RAM dump to volatile RAM filesystem "
                    "(/tmp, /run, or /var/tmp). Mount an external scratch disk under /mnt/scratch first."
                )
            }
        requested_method = str(args.get("method") or "").strip().lower()
        if requested_method not in ("", "avml", "kcore"):
            return {"error": f"Unknown ram_dump_plan method: {requested_method} (expected 'avml' or 'kcore')"}
        if requested_method == "kcore" or (not requested_method and not _which("avml")):
            method = "kcore"
            argv = ["dd", "if=/proc/kcore", f"of={output_path}", "bs=4M", "status=progress"]
        else:
            method = "avml"
            argv = ["avml", output_path]
        return {
            "action": action,
            "method": method,
            "output_path": output_path,
            "dry_run": True,
            "argv": argv,
            "recommended_execution": "Pass ' '.join(argv) to run_job() for non-blocking memory acquisition.",
        }

    if action == "ntfs_fix":
        if not _valid_dev_path(device):
            return {"error": "ntfs_fix requires a valid NTFS partition path"}
        argv = ["ntfsfix", "-d", device]
        if dry_run or _IS_WINDOWS:
            return {"action": action, "device": device, "argv": argv, "dry_run": True}
        rc, out, err = _run_cmd(argv, timeout=30)
        return {"action": action, "device": device, "argv": argv, "exit_code": rc, "stdout": out, "stderr": err}

    if action == "grub_repair":
        efi_part = str(args.get("efi_partition") or "").strip()
        mountpoint = str(args.get("mountpoint") or "/mnt/rescue/root").strip()
        if not _valid_dev_path(device) or not _valid_dev_path(efi_part):
            return {"error": "grub_repair requires 'device' (root FS) and 'efi_partition' (ESP)"}
        steps = [
            ["mount", device, mountpoint],
            ["mount", efi_part, f"{mountpoint}/boot/efi"],
            ["mount", "--bind", "/dev", f"{mountpoint}/dev"],
            ["mount", "--bind", "/proc", f"{mountpoint}/proc"],
            ["mount", "--bind", "/sys", f"{mountpoint}/sys"],
            ["mount", "--bind", "/run", f"{mountpoint}/run"],
            ["chroot", mountpoint, "grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", "--bootloader-id=GRUB"],
            ["chroot", mountpoint, "update-grub"],
        ]
        return {"action": action, "dry_run": dry_run, "planned_steps": steps}

    return {"action": action, "dry_run": dry_run, "status": "schema_ready"}


# ---------------------------------------------------------------------------
# Clean MCP tool dispatchers (never raise, always return JSON-serializable dict)
# ---------------------------------------------------------------------------

def handle_rescue_info(args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dispatch ``rescue_info`` tool calls cleanly without raising."""
    try:
        payload = args if isinstance(args, dict) else {}
        section = str(payload.get("section") or "summary").strip().lower()
        device = str(payload.get("device") or "").strip() or None
        raw_timeout = payload.get("timeout_sec")
        try:
            timeout = max(1, min(30, int(raw_timeout if raw_timeout is not None else 10)))
        except (TypeError, ValueError):
            timeout = 10

        if section not in RESCUE_SECTIONS:
            return {
                "error": f"Unknown rescue_info section: {section}",
                "valid_sections": list(RESCUE_SECTIONS),
            }

        if section == "disks":
            return collect_disks(timeout=timeout)
        if section == "smart":
            return collect_smart(device=device, timeout=timeout)
        if section == "dmesg_io":
            return collect_dmesg_io(device=device, timeout=min(timeout, 5))
        if section == "ram_info":
            return collect_ram_info(timeout=timeout)
        if section == "filesystems":
            return collect_filesystems(timeout=min(timeout, 5))
        if section == "summary":
            return {
                "section": "summary",
                "disks": collect_disks(timeout=timeout),
                "dmesg_io": collect_dmesg_io(device=device, timeout=min(timeout, 5)),
                "ram_info": collect_ram_info(timeout=timeout),
                "filesystems": collect_filesystems(timeout=min(timeout, 5)),
            }
        if section == "all":
            out: Dict[str, Any] = {
                "section": "all",
                "disks": collect_disks(timeout=timeout),
                "dmesg_io": collect_dmesg_io(device=device, timeout=min(timeout, 5)),
                "ram_info": collect_ram_info(timeout=timeout),
                "filesystems": collect_filesystems(timeout=min(timeout, 5)),
            }
            if device:
                out["smart"] = collect_smart(device=device, timeout=timeout)
            return out

        return {
            "available": False,
            "section": section,
            "reason": f"section '{section}' collector requires a Linux Live USB environment",
        }
    except Exception as exc:  # pragma: no cover - defensive contract
        return {"error": f"{type(exc).__name__}: {exc}"}


def handle_rescue_action(args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dispatch ``rescue_action`` tool calls cleanly without raising."""
    try:
        if args is None:
            return {"error": "rescue_action requires an 'action' parameter"}
        if not isinstance(args, dict):
            return {"error": "rescue_action arguments must be an object"}
        return plan_or_run_rescue_action(args)
    except Exception as exc:  # pragma: no cover - defensive contract
        return {"error": f"{type(exc).__name__}: {exc}"}
