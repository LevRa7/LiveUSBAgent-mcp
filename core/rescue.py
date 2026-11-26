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

import hashlib
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

_IS_WINDOWS = os.name == "nt"

_VOLATILE_PREFIXES: Tuple[str, ...] = ("/tmp/", "/run/", "/var/tmp/")
_VOLATILE_EXACT: Tuple[str, ...] = ("/tmp", "/run", "/var/tmp")
_VOLATILE_FSTYPES = frozenset({"tmpfs", "ramfs", "overlay", "squashfs", "devtmpfs"})
_ALLOWED_MOUNTPOINT_BASES: Tuple[str, ...] = ("/mnt/rescue/", "/mnt/scratch/")

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
    "win_diag",
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
    "malware_scan",
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
                "mountpoint": {
                    "type": "string",
                    "description": "Optional mounted OS root under /mnt/rescue/ (used by win_diag).",
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
                "force": {
                    "type": "boolean",
                    "description": (
                        "umount: add -f (force detach). mount_safe/mount_auto: override the "
                        "kernel I/O-error guard with force=true (a mount is refused after more "
                        "than 10 active dmesg I/O errors for the target). Use only on "
                        "known-healthy media."
                    ),
                },
                "lazy": {
                    "type": "boolean",
                    "description": "umount: add -l (lazy detach) when the mountpoint is busy.",
                },
                "bind_mounts": {
                    "type": "boolean",
                    "description": (
                        "chroot_exec: bind /dev, /proc, /sys and /run into the guest before "
                        "running chroot_cmd (default true)."
                    ),
                },
                "scanner": {
                    "type": "string",
                    "enum": ["clamav", "yara"],
                    "description": "malware_scan: offline scanner engine (clamav or yara, default clamav).",
                },
                "yara_rules": {
                    "type": "string",
                    "description": "malware_scan (yara): path to a YARA rules file.",
                },
                "quarantine": {
                    "type": "boolean",
                    "description": "malware_scan: move matched files to quarantine_dir with SHA-256 manifest (default false).",
                },
                "quarantine_dir": {
                    "type": "string",
                    "description": "malware_scan: non-volatile quarantine directory under /mnt/scratch/ (default /mnt/scratch/quarantine).",
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


_FORBIDDEN_DEV_EXACT = frozenset({
    "/dev/mem",
    "/dev/kmem",
    "/dev/port",
    "/dev/null",
    "/dev/zero",
    "/dev/shm",
})
_FORBIDDEN_DEV_PREFIXES: Tuple[str, ...] = ("/dev/shm/",)
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _valid_dev_path(dev: str) -> bool:
    if not dev or not isinstance(dev, str):
        return False
    if ".." in dev or "//" in dev:
        return False
    if not re.match(r"^/dev/[a-zA-Z0-9_\-/]+$", dev):
        return False
    norm = dev.rstrip("/")
    if norm in _FORBIDDEN_DEV_EXACT:
        return False
    if any(norm.startswith(prefix) for prefix in _FORBIDDEN_DEV_PREFIXES):
        return False
    return True


def _valid_mountpoint(path: str) -> bool:
    """True only if path (and its resolved realpath) is a safe subdirectory under /mnt/rescue/ or /mnt/scratch/."""
    raw = (path or "").strip()
    if not raw or "\\" in raw or "\x00" in raw or not raw.startswith("/"):
        return False
    parts = [p for p in raw.split("/") if p]
    if any(p in (".", "..") for p in parts):
        return False
    norm = "/" + "/".join(parts)
    if not any(norm.startswith(base) and len(norm) > len(base) for base in _ALLOWED_MOUNTPOINT_BASES):
        return False
    resolved = _resolve_nearest_existing_realpath(norm)
    if not any(resolved.startswith(base) and len(resolved) > len(base) for base in _ALLOWED_MOUNTPOINT_BASES):
        return False
    return True


def _resolve_nearest_existing_realpath(path: str) -> str:
    """Resolve os.path.realpath() on the nearest existing parent and rejoin tail."""
    norm = os.path.normpath(path).replace("\\", "/")
    if not norm.startswith("/"):
        return norm
    cur = norm
    tail: List[str] = []
    while True:
        try:
            exists = os.path.exists(cur)
        except Exception:
            exists = False
        if exists:
            try:
                real_parent = os.path.realpath(cur).replace("\\", "/")
            except Exception:
                real_parent = cur
            # Guard against Windows host resolving POSIX '/' to 'D:/' during unit tests.
            if not real_parent.startswith("/"):
                return norm
            if not tail:
                return real_parent
            return os.path.normpath(
                real_parent.rstrip("/") + "/" + "/".join(reversed(tail))
            ).replace("\\", "/")
        parent, name = os.path.split(cur.rstrip("/"))
        if not name or parent == cur:
            return norm
        tail.append(name)
        cur = parent or "/"


def _mount_fstype_for_path(resolved_path: str) -> Optional[str]:
    """Return the filesystem type from /proc/mounts for the longest matching mountpoint."""
    mounts_text = _read_file_text("/proc/mounts")
    if not mounts_text or not resolved_path.startswith("/"):
        return None
    best_len = -1
    best_fstype: Optional[str] = None
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mp = parts[1].replace("\\040", " ").rstrip("/") or "/"
        fstype = parts[2].strip().lower()
        if mp == "/":
            matched = True
            mp_len = 1
        else:
            matched = resolved_path == mp or resolved_path.startswith(mp + "/")
            mp_len = len(mp)
        if matched and mp_len >= best_len:
            best_len = mp_len
            best_fstype = fstype
    return best_fstype


def _is_volatile_path(path: str) -> bool:
    """True if path points inside a volatile RAM filesystem (/tmp, /run, /var/tmp, or tmpfs/overlay mount)."""
    cleaned = (path or "").strip()
    if not cleaned:
        return False
    norm = os.path.normpath(cleaned).replace("\\", "/")
    if norm in _VOLATILE_EXACT:
        return True
    if any(norm.startswith(prefix) or cleaned.startswith(prefix) for prefix in _VOLATILE_PREFIXES):
        return True
    resolved = _resolve_nearest_existing_realpath(cleaned)
    if resolved in _VOLATILE_EXACT or any(resolved.startswith(prefix) for prefix in _VOLATILE_PREFIXES):
        return True
    fstype = _mount_fstype_for_path(resolved)
    if fstype in _VOLATILE_FSTYPES:
        return True
    return False


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
# Partition table / RAID+LVM / guest OS / UEFI / Windows artifact collectors
# ---------------------------------------------------------------------------

#: Filesystems that can host a Linux root and are probed via a read-only mount.
_LINUX_ROOT_FSTYPES = frozenset({"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs"})
#: Filesystems that can host a Windows root (probed with ntfsls, never mounted here).
_WIN_ROOT_FSTYPES = frozenset({"ntfs", "ntfs3", "fuseblk"})
#: Filesystems that are reported as probed but cannot be inspected without a mount.
_UNMOUNTABLE_FSTYPES = frozenset({"exfat", "vfat", "hfsplus", "apfs", "bitlocker"})

#: Absolute root for temporary read-only guest probes.
_RESCUE_PROBE_BASE = "/mnt/rescue/"

_LSBLK_BLOCK_COLUMNS = "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT"
_EFI_BOOT_RE = re.compile(r"^Boot([0-9A-Fa-f]{4})(\*)?\s*(.*)$")


def _remaining_timeout(deadline: float, cap: int = 15) -> int:
    """Seconds left before ``deadline``, clamped to ``1..cap`` for a subprocess call."""
    try:
        left = int(deadline - time.monotonic())
    except Exception:
        left = 1
    return max(1, min(int(cap), left))


def _flatten_lsblk(nodes: Any) -> List[Dict[str, Any]]:
    """Flatten nested ``lsblk -J`` blockdevice nodes (breadth-first, order preserved)."""
    flat: List[Dict[str, Any]] = []
    if not isinstance(nodes, list):
        return flat
    queue: List[Any] = list(nodes)
    while queue:
        node = queue.pop(0)
        if not isinstance(node, dict):
            continue
        children = node.get("children")
        flat.append({k: v for k, v in node.items() if k != "children"})
        if isinstance(children, list):
            queue.extend(children)
    return flat


def _lsblk_blockdevices(
    timeout: int,
    columns: str = _LSBLK_BLOCK_COLUMNS,
    device: Optional[str] = None,
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Run ``lsblk -J -b [-o cols] [device]`` and return (ok, reason, flattened_list)."""
    argv = ["lsblk", "-J", "-b", "-o", columns]
    if device:
        argv.append(device)
    rc, out, err = _run_cmd(argv, timeout=timeout)
    if rc != 0:
        return False, (err.strip() or f"lsblk exited with {rc}"), []
    if not out.strip():
        return False, "lsblk returned no output", []
    try:
        parsed = json.loads(out)
    except Exception as exc:
        return False, f"lsblk json parse error: {exc}", []
    if not isinstance(parsed, dict):
        return False, "lsblk json is not an object", []
    return True, "", _flatten_lsblk(parsed.get("blockdevices"))


def _path_belongs_to_device(path: str, device: str) -> bool:
    """True if ``path`` is ``device`` itself or a partition/child of it.

    Handles the ``/dev/sda`` -> ``/dev/sda1`` and ``/dev/nvme0n1`` ->
    ``/dev/nvme0n1p1`` naming schemes so an ``lsblk <device>`` fallback can
    never report another disk's partitions.
    """
    if not path or not device:
        return False
    if path == device:
        return True
    if not path.startswith(device):
        return False
    rest = path[len(device):]
    return rest.isdigit() or (rest[:1] == "p" and rest[1:].isdigit())


def _parse_blkid_export(text: str) -> Dict[str, str]:
    """Parse ``blkid -o export`` ``KEY=value`` lines into a dict."""
    values: Dict[str, str] = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def _parse_table_output(text: str, keys: Sequence[str]) -> List[Dict[str, Any]]:
    """Parse whitespace-separated LVM ``--noheadings -o`` rows into dictionaries."""
    rows: List[Dict[str, Any]] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < len(keys):
            parts.extend([None] * (len(keys) - len(parts)))  # type: ignore[list-item]
        rows.append({key: parts[idx] for idx, key in enumerate(keys)})
    return rows


def _parse_os_release(text: str) -> Dict[str, str]:
    """Parse ``/etc/os-release`` (or ``/usr/lib/os-release``) into a dict."""
    fields: Dict[str, str] = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        key = key.strip()
        if key:
            fields[key] = value
    return fields


def _parse_efibootmgr(text: str) -> Dict[str, Any]:
    """Parse ``efibootmgr -v`` output into boot_current/boot_order/entries."""
    boot_current: Optional[str] = None
    boot_order: List[str] = []
    entries: List[Dict[str, Any]] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith("bootcurrent:"):
            boot_current = stripped.split(":", 1)[1].strip() or None
            continue
        if lowered.startswith("bootorder:"):
            boot_order = [p.strip() for p in stripped.split(":", 1)[1].split(",") if p.strip()]
            continue
        match = _EFI_BOOT_RE.match(stripped)
        if not match:
            continue
        label, _sep, device_path = match.group(3).partition("\t")
        entries.append(
            {
                "index": match.group(1),
                "active": bool(match.group(2)),
                "label": label.strip(),
                "device_path": device_path.strip(),
            }
        )
    return {"boot_current": boot_current, "boot_order": boot_order, "entries": entries}


def _blkid_type(device: str, timeout: int) -> str:
    """Best-effort ``blkid -o value -s TYPE`` probe (returns '' on any failure)."""
    rc, out, _err = _run_cmd(["blkid", "-o", "value", "-s", "TYPE", device], timeout=timeout)
    if rc != 0 or not out.strip():
        return ""
    return out.strip().splitlines()[0].strip()


def _ntfsls_artifacts(device: str, timeout: int) -> Tuple[Dict[str, bool], List[str]]:
    """Probe ``Windows/System32/config`` with ntfsls without mounting the volume."""
    artifacts: Dict[str, bool] = {"SAM": False, "SYSTEM": False, "SOFTWARE": False}
    if not _which("ntfsls"):
        return artifacts, [f"ntfsls is not installed; cannot probe {device} without mounting"]
    rc, out, err = _run_cmd(
        ["ntfsls", "-f", "-l", "-p", "Windows/System32/config", device],
        timeout=timeout,
    )
    if rc != 0:
        return artifacts, [f"ntfsls failed on {device}: {err.strip() or f'exited with {rc}'}"]
    upper = out.upper()
    for name in ("SAM", "SYSTEM", "SOFTWARE"):
        artifacts[name] = bool(re.search(rf"(^|[^A-Z0-9_]){name}([^A-Z0-9_]|$)", upper))
    return artifacts, []


def collect_partitions(device: Optional[str] = None, timeout: int = 8) -> Dict[str, Any]:
    """Read the partition table of ``device`` via ``sfdisk -J`` with an ``lsblk`` fallback."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "partitions requires Linux"}
    device = str(device or "").strip().rstrip("/")
    if not device or not _valid_dev_path(device):
        return {
            "available": False,
            "reason": "partitions requires a valid device path, e.g. /dev/nvme0n1 or /dev/sda",
        }

    warnings: List[str] = []
    rc, out, err = _run_cmd(["sfdisk", "-J", device], timeout=timeout)
    table: Optional[Dict[str, Any]] = None
    if rc == 0 and out.strip():
        try:
            parsed = json.loads(out)
        except Exception as exc:
            warnings.append(f"sfdisk json parse error: {exc}")
            parsed = {}
        if isinstance(parsed, dict) and isinstance(parsed.get("partitiontable"), dict):
            table = parsed["partitiontable"]
    elif rc != 0:
        warnings.append(err.strip() or f"sfdisk exited with {rc}")

    if table is not None and (isinstance(table.get("partitions"), list)):
        partitions: List[Dict[str, Any]] = []
        for index, part in enumerate(table.get("partitions") or [], start=1):
            if not isinstance(part, dict):
                continue
            partitions.append(
                {
                    "index": index,
                    "node": part.get("node"),
                    "start": part.get("start"),
                    "size": part.get("size"),
                    "type": part.get("type"),
                    "uuid": part.get("uuid"),
                }
            )
        result: Dict[str, Any] = {
            "available": True,
            "source": "sfdisk",
            "device": table.get("device") or device,
            "label": table.get("label") or None,
            "partitions": partitions,
        }
        rc_blk, out_blk, _err_blk = _run_cmd(["blkid", "-o", "export", device], timeout=min(timeout, 5))
        if rc_blk == 0 and out_blk.strip():
            blkid = _parse_blkid_export(out_blk)
            if blkid:
                result["blkid"] = blkid
        if warnings:
            result["warnings"] = warnings
        return result

    if table is not None:
        warnings.append("sfdisk returned no partition entries")
    warnings.append("falling back to lsblk")
    ok, reason, nodes = _lsblk_blockdevices(timeout=timeout, device=device)
    if not ok:
        return {"available": False, "reason": reason or "lsblk failed", "source": "lsblk", "warnings": warnings}

    partitions = []
    for node in nodes:
        if str(node.get("type") or "").lower() == "disk":
            continue
        path = str(node.get("path") or "")
        if not path:
            name = str(node.get("name") or "")
            path = f"/dev/{name}" if name else ""
        if not path:
            continue
        if not _path_belongs_to_device(path, device):
            # lsblk was called with the device but never trust a foreign row.
            warnings.append(f"ignored partition outside {device}: {path}")
            continue
        partitions.append(
            {
                "index": len(partitions) + 1,
                "node": path,
                "start": None,
                "size": node.get("size"),
                "type": node.get("fstype") or node.get("type"),
                "uuid": node.get("uuid"),
                "label": node.get("label"),
                "mountpoint": node.get("mountpoint"),
            }
        )
    if not partitions:
        return {
            "available": False,
            "reason": f"no partitions could be read for {device}",
            "source": "lsblk",
            "warnings": warnings,
        }
    return {
        "available": True,
        "source": "lsblk",
        "device": device,
        "label": None,
        "partitions": partitions,
        "warnings": warnings,
    }


def collect_raid_lvm(timeout: int = 10) -> Dict[str, Any]:
    """Inspect RAID, LVM, and dm-crypt topology without mounting anything."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "raid_lvm requires Linux"}

    ok, reason, nodes = _lsblk_blockdevices(timeout=timeout)
    if not ok:
        return {"available": False, "reason": reason or "lsblk failed", "source": "lsblk"}

    devices: List[Dict[str, Any]] = []
    for node in nodes:
        kind = str(node.get("type") or "").strip().lower()
        if not (kind.startswith("raid") or kind in ("lvm", "crypt")):
            continue
        devices.append(
            {
                "name": node.get("name"),
                "path": node.get("path"),
                "size": node.get("size"),
                "type": kind,
                "fstype": node.get("fstype"),
                "mountpoint": node.get("mountpoint"),
            }
        )

    warnings: List[str] = []
    missing: List[str] = []

    def _lvm_rows(binary: str, keys: Sequence[str], timeout_hint: int) -> List[Dict[str, Any]]:
        if not _which(binary):
            missing.append(binary)
            return []
        rc, out, err = _run_cmd([binary, "--noheadings", "-o", ",".join(keys)], timeout=timeout_hint)
        if rc != 0:
            warnings.append(f"{binary} failed: {err.strip() or f'exited with {rc}'}")
            return []
        return _parse_table_output(out, keys)

    pvs = _lvm_rows("pvs", ("pv_name", "vg_name", "pv_size", "pv_free"), timeout)
    vgs = _lvm_rows("vgs", ("vg_name", "pv_count", "lv_count", "vg_size", "vg_free"), timeout)
    lvs = _lvm_rows("lvs", ("lv_name", "vg_name", "lv_size", "lv_attr"), timeout)

    crypt: Dict[str, Any] = {}
    for entry in devices:
        if entry["type"] != "crypt":
            continue
        name = str(entry.get("name") or "")
        if not name:
            continue
        if not _SAFE_IDENTIFIER_RE.match(name):
            warnings.append(f"skipped crypt device with unsafe mapper name: {name}")
            continue
        if not _which("cryptsetup"):
            missing.append("cryptsetup")
            break
        rc, out, err = _run_cmd(["cryptsetup", "status", name], timeout=min(timeout, 5))
        if rc != 0:
            # One inaccessible mapper must never fail the whole collector.
            crypt[name] = {"available": False, "reason": err.strip() or f"cryptsetup exited with {rc}"}
            continue
        status: Dict[str, str] = {}
        for line in out.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            status[key.strip().lower().replace(" ", "_")] = value.strip()
        crypt[name] = {"available": True, "status": status}

    result: Dict[str, Any] = {
        "available": True,
        "source": "lsblk",
        "devices": devices,
        "lvm": {"pvs": pvs, "vgs": vgs, "lvs": lvs},
        "crypt": crypt,
    }
    if missing:
        result["missing_binaries"] = sorted(set(missing))
    if warnings:
        result["warnings"] = warnings
    return result


def _probe_linux_root(device: str, fstype: str, deadline: float) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Mount ``device`` read-only under /mnt/rescue, read os-release/fstab, always umount."""
    warnings: List[str] = []
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(device.rstrip("/"))) or "unknown"
    mountpoint = f"{_RESCUE_PROBE_BASE}_osprobe_{safe_name}"
    if not mountpoint.startswith(_RESCUE_PROBE_BASE) or not mountpoint.strip("/"):
        return None, [f"refusing unsafe probe mountpoint: {mountpoint}"]
    try:
        os.makedirs(mountpoint, exist_ok=True)
    except Exception as exc:
        return None, [f"could not create {mountpoint}: {type(exc).__name__}: {exc}"]

    mounted = False
    last_error = ""
    for opts in ("ro,noload", "ro,norecovery", "ro"):
        rc, out, err = _run_cmd(
            ["mount", "-t", fstype, "-o", opts, device, mountpoint],
            timeout=_remaining_timeout(deadline),
        )
        if rc == 0:
            mounted = True
            break
        last_error = err.strip() or (out or "").strip() or f"mount exited with {rc}"
        if deadline - time.monotonic() <= 0:
            break
    if not mounted:
        return None, [f"mount failed for {device} ({fstype}): {last_error}"]

    entry: Dict[str, Any] = {"device": device, "type": fstype, "name": None, "version": None, "evidence": []}
    try:
        os_release_path = ""
        os_release: Dict[str, str] = {}
        for candidate in ("/etc/os-release", "/usr/lib/os-release"):
            os_release = _parse_os_release(_read_file_text(f"{mountpoint}{candidate}"))
            if os_release:
                os_release_path = candidate
                break
        if os_release:
            entry["name"] = os_release.get("PRETTY_NAME") or os_release.get("NAME") or os_release.get("ID")
            entry["version"] = (
                os_release.get("VERSION_ID")
                or os_release.get("VERSION")
                or os_release.get("VERSION_CODENAME")
            )
            entry["id"] = os_release.get("ID")
            entry["id_like"] = os_release.get("ID_LIKE")
            entry["evidence"].append(os_release_path)
        fstab = _read_file_text(f"{mountpoint}/etc/fstab")
        if fstab.strip():
            entry["fstab"] = [
                line.strip()
                for line in fstab.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            entry["evidence"].append("/etc/fstab")
    except Exception as exc:  # pragma: no cover - defensive contract
        warnings.append(f"reading guest files failed on {device}: {type(exc).__name__}: {exc}")
    finally:
        # A fixed 5s budget: an unmount must never be starved down to 1s,
        # because a leaked read-only mount is worse than a slightly late call.
        rc_umount, _out_umount, err_umount = _run_cmd(["umount", mountpoint], timeout=5)
        if rc_umount != 0:
            rc_lazy, _out_lazy, err_lazy = _run_cmd(["umount", "-l", mountpoint], timeout=5)
            if rc_lazy != 0:
                warnings.append(
                    f"umount failed for {mountpoint}: "
                    f"{err_umount.strip() or f'exited with {rc_umount}'}; "
                    f"lazy umount failed: {err_lazy.strip() or f'exited with {rc_lazy}'}"
                )
        try:
            still_mounted = bool(os.path.ismount(mountpoint))
        except Exception:
            still_mounted = False
        if still_mounted:
            warnings.append(f"left mounted: {mountpoint}")

    if not entry["evidence"] or not entry.get("name"):
        return None, warnings + [f"no os-release found on {device}"]
    return entry, warnings


def _probe_windows_root(device: str, deadline: float) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Probe a Windows root with ntfsls (no mount)."""
    artifacts, warnings = _ntfsls_artifacts(device, timeout=_remaining_timeout(deadline))
    if not any(artifacts.values()):
        return None, warnings + [f"no Windows registry hives found on {device}"]
    evidence = [f"ntfsls:{name}" for name, found in artifacts.items() if found]
    return (
        {
            "device": device,
            "type": "ntfs",
            "name": "Windows",
            "version": None,
            "evidence": evidence,
            "artifacts": artifacts,
        },
        warnings,
    )


def collect_installed_os(timeout: int = 12) -> Dict[str, Any]:
    """Discover installed Linux/Windows roots on attached partitions without leaking mounts."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "installed_os requires Linux"}

    ok, reason, nodes = _lsblk_blockdevices(timeout=timeout)
    if not ok:
        return {"available": False, "reason": reason or "lsblk failed", "source": "lsblk"}

    budget = max(1, int(timeout))
    deadline = time.monotonic() + budget
    operating_systems: List[Dict[str, Any]] = []
    warnings: List[str] = []
    probed = 0

    candidates = [
        node for node in nodes
        if str(node.get("fstype") or "").strip() and str(node.get("type") or "").lower() != "disk"
    ]
    for node in candidates:
        device = str(node.get("path") or "").strip()
        fstype = _normalize_fstype(str(node.get("fstype") or ""))
        if not device or not _valid_dev_path(device):
            warnings.append(f"skipped invalid candidate device path: {device or '<empty>'}")
            continue
        if deadline - time.monotonic() <= 0:
            warnings.append(
                f"probe deadline ({budget}s) reached; {len(candidates) - probed} candidate(s) not probed"
            )
            break
        probed += 1
        if fstype in _LINUX_ROOT_FSTYPES:
            entry, entry_warnings = _probe_linux_root(device, fstype, deadline)
        elif fstype in _WIN_ROOT_FSTYPES:
            entry, entry_warnings = _probe_windows_root(device, deadline)
        elif fstype in _UNMOUNTABLE_FSTYPES:
            entry, entry_warnings = None, [f"{device} ({fstype}): unsupported_without_mount"]
        else:
            entry, entry_warnings = None, [
                f"{device} ({fstype or 'unknown'}): no os-release (unsupported filesystem)"
            ]
        warnings.extend(entry_warnings)
        if entry:
            operating_systems.append(entry)

    result: Dict[str, Any] = {
        "available": True,
        "source": "lsblk",
        "operating_systems": operating_systems,
        "probed": probed,
        "warnings": warnings,
    }
    if not operating_systems:
        result["reason"] = "no installed operating system could be identified"
    return result


def collect_boot_efi(timeout: int = 8) -> Dict[str, Any]:
    """Parse UEFI boot entries via efibootmgr -v (BIOS systems report unavailable)."""
    if _IS_WINDOWS:
        # Windows is never an EFI-booted Live USB node: report the same honest
        # platform-agnostic reason as a Linux machine booted in BIOS/CSM mode.
        return {
            "available": False,
            "reason": "system is not booted in UEFI mode",
            "source": "efibootmgr",
        }
    try:
        uefi_present = os.path.exists("/sys/firmware/efi")
    except Exception:
        uefi_present = False
    if not uefi_present:
        return {
            "available": False,
            "reason": "system is not booted in UEFI mode",
            "source": "efibootmgr",
        }
    rc, out, err = _run_cmd(["efibootmgr", "-v"], timeout=timeout)
    if rc != 0 or not out.strip():
        return {
            "available": False,
            "reason": err.strip() or f"efibootmgr exited with {rc}",
            "source": "efibootmgr",
        }
    parsed = _parse_efibootmgr(out)
    return {
        "available": True,
        "source": "efibootmgr",
        "boot_current": parsed["boot_current"],
        "boot_order": parsed["boot_order"],
        "entries": parsed["entries"],
        "raw": out,
    }


def collect_win_artifacts(timeout: int = 8) -> Dict[str, Any]:
    """Locate Windows registry hives (SAM/SYSTEM/SOFTWARE) on NTFS partitions using ntfsls."""
    if _IS_WINDOWS:
        return {"available": False, "reason": "win_artifacts requires Linux"}

    ok, reason, nodes = _lsblk_blockdevices(timeout=timeout)
    if not ok:
        return {"available": False, "reason": reason or "lsblk failed", "source": "lsblk"}

    ntfsls_present = _which("ntfsls") is not None

    probe_timeout = max(1, min(int(timeout), 5))
    partitions: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for node in nodes:
        if str(node.get("type") or "").lower() == "disk":
            continue
        device = str(node.get("path") or "").strip()
        if not device or not _valid_dev_path(device):
            continue
        raw_fstype = str(node.get("fstype") or "").strip()
        fstype = _normalize_fstype(raw_fstype)
        blkid_type = _blkid_type(device, timeout=probe_timeout)
        combined = f"{raw_fstype} {fstype} {blkid_type}".lower()
        bitlocker = "bitlocker" in combined
        if not bitlocker and fstype not in _WIN_ROOT_FSTYPES and "ntfs" not in combined:
            continue
        artifacts: Dict[str, bool] = {"SAM": False, "SYSTEM": False, "SOFTWARE": False}
        if not bitlocker and ntfsls_present:
            artifacts, artifact_warnings = _ntfsls_artifacts(device, timeout=probe_timeout)
            warnings.extend(artifact_warnings)
        partitions.append(
            {
                "device": device,
                "fstype": blkid_type or raw_fstype or ("bitlocker" if bitlocker else ""),
                "bitlocker": bitlocker,
                "artifacts": artifacts,
            }
        )

    result: Dict[str, Any] = {
        "available": True,
        "source": "lsblk+ntfsls" if ntfsls_present else "lsblk",
        "partitions": partitions,
    }
    if warnings:
        result["warnings"] = warnings
    if not ntfsls_present:
        result["reason"] = (
            "ntfsls is not installed; NTFS/BitLocker partitions are listed but "
            "SAM/SYSTEM/SOFTWARE were not probed (nothing is ever mounted here)"
        )
    elif not partitions:
        result["reason"] = "no NTFS or BitLocker partition found"
    return result


def _resolve_ci_path(base: str, *parts: str) -> Optional[str]:
    """Resolve path segments case-insensitively under *base*."""
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
        target_low = part.lower()
        found = None
        for item in entries:
            if item.lower() == target_low:
                found = os.path.join(cur, item)
                break
        if found is None:
            return None
        cur = found
    return cur


def _parse_minidump_header(path: str) -> Dict[str, Any]:
    """Parse minimal Windows crash-dump header (PAGEDU64/PAGEDUMP/MDMP) using stdlib only."""
    info: Dict[str, Any] = {"name": os.path.basename(path)}
    try:
        st = os.stat(path)
        info["size_bytes"] = int(st.st_size)
        info["mtime"] = int(st.st_mtime)
        with open(path, "rb") as fh:
            header = fh.read(96)
    except OSError as exc:
        info["error"] = str(exc)
        return info

    if len(header) < 8:
        info["format"] = "truncated"
        return info

    sig8 = header[:8]
    if sig8 == b"PAGEDU64":
        info["format"] = "PAGEDU64"
        if len(header) >= 0x40:
            major, minor = struct.unpack_from("<II", header, 8)
            bugcheck = struct.unpack_from("<I", header, 0x38)[0]
            info["major_version"] = major
            info["minor_version"] = minor
            info["bugcheck_code"] = f"0x{bugcheck:08X}"
        return info
    if sig8 == b"PAGEDUMP":
        info["format"] = "PAGEDUMP"
        if len(header) >= 0x24:
            bugcheck = struct.unpack_from("<I", header, 0x20)[0]
            info["bugcheck_code"] = f"0x{bugcheck:08X}"
        return info
    if header[:4] == b"MDMP":
        info["format"] = "MDMP"
        if len(header) >= 12:
            version, streams = struct.unpack_from("<II", header, 4)
            info["version"] = version & 0xFFFF
            info["stream_count"] = streams
        return info

    info["format"] = "unknown"
    return info


def _parse_hivex_kv(text: str) -> Dict[str, str]:
    """Parse key=value pairs from hivexget stdout."""
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        m = re.match(r'^\s*"([^"]+)"\s*=\s*(.*)\s*$', line)
        if not m:
            continue
        k = m.group(1)
        v = m.group(2).strip()
        if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        out[k] = v
    return out


def _read_srt_trail(path: str, max_bytes: int = 8192) -> Dict[str, Any]:
    """Read Windows Startup Repair log tail (handles UTF-16LE and UTF-8)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            raw = fh.read(max_bytes)
    except OSError as exc:
        return {"present": False, "error": str(exc)}

    text = ""
    if raw.startswith(b"\xff\xfe") or b"\x00" in raw[:64]:
        text = raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    else:
        text = raw.decode("utf-8", errors="replace")

    root_causes = [
        line.strip()
        for line in text.splitlines()
        if "root cause" in line.lower() or "error code" in line.lower()
    ][:10]
    tail_lines = [ln for ln in text.splitlines() if ln.strip()][-15:]
    return {
        "present": True,
        "size_bytes": size,
        "root_causes": root_causes,
        "tail": tail_lines,
    }


def _find_mounted_windows_root() -> Optional[str]:
    """Return the first subdirectory under /mnt/rescue/ containing Windows/System32."""
    base = "/mnt/rescue"
    if not os.path.isdir(base):
        return None
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for name in names:
        cand = os.path.join(base, name)
        if not os.path.isdir(cand):
            continue
        sys32 = _resolve_ci_path(cand, "Windows", "System32")
        if sys32 and os.path.isdir(sys32):
            return cand
    return None


def collect_win_diag(mountpoint: Optional[str] = None, timeout: int = 12) -> Dict[str, Any]:
    """Read-only offline Windows diagnostics on a mounted volume under /mnt/rescue/.

    Inspects Windows version/build, Fast Startup / hiberfil.sys, SrtTrail.txt,
    BSOD Minidump headers, and System.evtx / Application.evtx presence.
    """
    if _IS_WINDOWS:
        return {"available": False, "reason": "win_diag requires Linux"}

    root = (mountpoint or "").strip()
    if root:
        if not _valid_mountpoint(root):
            return {
                "available": False,
                "reason": "mountpoint must be under /mnt/rescue/ or /mnt/scratch/",
            }
        if not os.path.isdir(root):
            return {"available": False, "reason": f"mountpoint does not exist: {root}"}
    else:
        found = _find_mounted_windows_root()
        if not found:
            return {
                "available": False,
                "reason": "no mounted Windows volume found under /mnt/rescue/; mount read-only first",
            }
        root = found

    sys32 = _resolve_ci_path(root, "Windows", "System32")
    if not sys32 or not os.path.isdir(sys32):
        return {
            "available": False,
            "mountpoint": root,
            "reason": "Windows/System32 not found under mountpoint",
        }

    warnings: List[str] = []
    probe_timeout = max(1, min(int(timeout), 6))

    # 1. Version & Fast Startup via hivexget (strictly read-only)
    version_info: Dict[str, str] = {}
    fast_startup: Dict[str, Any] = {}
    config_dir = _resolve_ci_path(sys32, "config")
    hivexget_bin = _which("hivexget")
    if config_dir and hivexget_bin:
        sw_hive = _resolve_ci_path(config_dir, "SOFTWARE")
        if sw_hive and os.path.isfile(sw_hive):
            rc, out, _ = _run_cmd(
                [hivexget_bin, sw_hive, r"Microsoft\Windows NT\CurrentVersion"],
                timeout=probe_timeout,
            )
            if rc == 0:
                kv = _parse_hivex_kv(out)
                for field in ("ProductName", "DisplayVersion", "CurrentBuild", "UBR", "EditionID"):
                    if field in kv:
                        version_info[field] = kv[field]
        sys_hive = _resolve_ci_path(config_dir, "SYSTEM")
        if sys_hive and os.path.isfile(sys_hive):
            rc, out, _ = _run_cmd(
                [hivexget_bin, sys_hive, r"ControlSet001\Control\Session Manager\Power"],
                timeout=probe_timeout,
            )
            if rc == 0:
                kv = _parse_hivex_kv(out)
                if "HiberbootEnabled" in kv:
                    fast_startup["hiberboot_enabled"] = kv["HiberbootEnabled"]
    elif not hivexget_bin:
        warnings.append("hivexget not installed; registry version and Fast Startup skipped")

    hiberfil = _resolve_ci_path(root, "hiberfil.sys")
    fast_startup["hiberfil_present"] = bool(hiberfil and os.path.isfile(hiberfil))
    if hiberfil and os.path.isfile(hiberfil):
        try:
            fast_startup["hiberfil_bytes"] = int(os.path.getsize(hiberfil))
        except OSError:
            pass

    # 2. Startup Repair log (SrtTrail.txt)
    srt_path = _resolve_ci_path(sys32, "LogFiles", "Srt", "SrtTrail.txt")
    srt_trail = _read_srt_trail(srt_path) if (srt_path and os.path.isfile(srt_path)) else {"present": False}

    # 3. BSOD Minidumps
    minidumps: List[Dict[str, Any]] = []
    minidump_dir = _resolve_ci_path(root, "Windows", "Minidump")
    if minidump_dir and os.path.isdir(minidump_dir):
        try:
            dmp_files = [
                os.path.join(minidump_dir, f)
                for f in os.listdir(minidump_dir)
                if f.lower().endswith(".dmp")
            ]
            dmp_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for dmp in dmp_files[:20]:
                minidumps.append(_parse_minidump_header(dmp))
        except OSError as exc:
            warnings.append(f"could not list Minidump directory: {exc}")

    # 4. Windows Event Logs (.evtx) presence
    evtx_logs: Dict[str, Any] = {}
    logs_dir = _resolve_ci_path(sys32, "winevt", "Logs")
    if logs_dir and os.path.isdir(logs_dir):
        for log_name in ("System.evtx", "Application.evtx"):
            lp = _resolve_ci_path(logs_dir, log_name)
            if lp and os.path.isfile(lp):
                try:
                    evtx_logs[log_name] = {"present": True, "size_bytes": int(os.path.getsize(lp))}
                except OSError:
                    evtx_logs[log_name] = {"present": True}
            else:
                evtx_logs[log_name] = {"present": False}

    result: Dict[str, Any] = {
        "available": True,
        "mountpoint": root,
        "version": version_info,
        "fast_startup": fast_startup,
        "srt_trail": srt_trail,
        "minidumps": minidumps,
        "evtx_logs": evtx_logs,
    }
    if warnings:
        result["warnings"] = warnings
    return result


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


def _mount_io_guard(
    device: str,
    args: Dict[str, Any],
    pool_name: str = "",
) -> Optional[Dict[str, Any]]:
    """Refuse a mount/import when the kernel is already logging I/O errors.

    ``collect_dmesg_io`` is best-effort: an unavailable collector (Windows host,
    no ``dmesg`` permission) never blocks a mount.  More than ten active errors
    is the threshold where the media should be cloned first; ``force=true`` is
    the explicit operator override.  A device-less ZFS import reports a
    pool-oriented message instead of an empty device name.
    """
    if _IS_WINDOWS:
        return None
    try:
        io_check = collect_dmesg_io(device, 4)
    except Exception:
        return None
    try:
        count = int(io_check.get("io_error_count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count > 10 and args.get("force") is not True:
        if device:
            target = f"mount {device}"
        elif pool_name:
            target = f"import pool {pool_name}"
        else:
            target = "import zpools"
        return {
            "error": (
                f"refusing to {target}: {count} active kernel I/O errors; "
                "pass force=true to override"
            )
        }
    return None


def _plan_or_run_mount_auto(args: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    device = str(args.get("device") or "").strip()
    writable = bool(args.get("writable", False))
    encrypted = bool(args.get("encrypted", False))
    pool_name = str(args.get("pool_name") or "").strip()
    mapper_name = str(args.get("mapper_name") or "").strip()
    recovery_key = str(args.get("recovery_key") or "").strip()

    if mapper_name and not _SAFE_IDENTIFIER_RE.match(mapper_name):
        return {"error": f"Invalid mapper_name '{mapper_name}': must match ^[A-Za-z0-9][A-Za-z0-9_.-]{{0,63}}$"}
    if pool_name and not _SAFE_IDENTIFIER_RE.match(pool_name):
        return {"error": f"Invalid pool_name '{pool_name}': must match ^[A-Za-z0-9][A-Za-z0-9_.-]{{0,63}}$"}

    fstype = _normalize_fstype(str(args.get("fstype") or ""))
    if not fstype and device and _valid_dev_path(device):
        fstype = _detect_fstype(device)

    if fstype != "zfs" and not _valid_dev_path(device):
        return {"error": "mount_auto requires a valid device path (e.g. /dev/nvme0n1p2)"}
    if not fstype:
        return {"error": "Could not detect filesystem type; pass 'fstype' explicitly."}

    dev_name = os.path.basename(device) if device else (pool_name or "target")
    mountpoint = str(args.get("mountpoint") or f"/mnt/rescue/{dev_name}").strip()
    if not _valid_mountpoint(mountpoint):
        return {
            "error": (
                f"Invalid mountpoint '{mountpoint}': must be a subdirectory under "
                "/mnt/rescue/ or /mnt/scratch/ without '..' traversal."
            )
        }

    warnings: List[str] = []
    fallback_argv: Optional[List[str]] = None
    planned_steps: Optional[List[List[str]]] = None
    extra: Dict[str, Any] = {}

    io_guard = _mount_io_guard(
        device if _valid_dev_path(device) else "",
        args,
        pool_name=pool_name,
    )
    if io_guard is not None:
        return io_guard

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
    elif fstype in ("vfat", "fat32", "fat16", "msdos"):
        opts = "rw,noatime" if writable else "ro"
        argv = ["mount", "-t", "vfat", "-o", opts, device, mountpoint]

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

    def _redact_argv(cmd: Sequence[str]) -> List[str]:
        if not recovery_key:
            return list(cmd)
        secret_arg = f"-p{recovery_key}"
        return ["-p***REDACTED***" if tok == secret_arg else tok for tok in cmd]

    plan: Dict[str, Any] = {
        "action": "mount_auto",
        "device": device or None,
        "fstype": fstype,
        "mountpoint": mountpoint,
        "writable": writable,
        "argv": _redact_argv(argv),
        "dry_run": dry_run,
    }
    if fallback_argv:
        plan["fallback_argv"] = _redact_argv(fallback_argv)
    if planned_steps:
        plan["planned_steps"] = [_redact_argv(step) for step in planned_steps]
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
    if fstype == "bitlocker" and rc == 0 and planned_steps and len(planned_steps) >= 2:
        loop_argv = planned_steps[1]
        rc_loop, out_loop, err_loop = _run_cmd(loop_argv, timeout=20)
        plan.update(
            {
                "unlock_exit_code": rc,
                "unlock_stdout": out,
                "unlock_stderr": err,
                "loop_argv": loop_argv,
                "exit_code": rc_loop,
                "stdout": out_loop,
                "stderr": err_loop,
            }
        )
        return plan
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
        if not _valid_mountpoint(mountpoint):
            return {
                "error": (
                    f"Invalid mountpoint '{mountpoint}': must be a subdirectory under "
                    "/mnt/rescue/ or /mnt/scratch/ without '..' traversal."
                )
            }
        opts = "rw,noatime" if writable else "ro,noload"
        io_guard = _mount_io_guard(device, args)
        if io_guard is not None:
            return io_guard
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
        if _is_volatile_path(target_image) or _is_volatile_path(log_file):
            return {
                "error": (
                    "Refusing to write recovery image or log_file to volatile RAM filesystem "
                    "(/tmp, /run, /var/tmp, or tmpfs/overlay mount). Mount an external scratch disk under /mnt/scratch first."
                )
            }
        argv = ["ddrescue", "-f", "-n", device, target_image, log_file]
        return {
            "action": action,
            "dry_run": True,
            "argv": argv,
            "recommended_execution": "Pass shlex.join(argv) to run_job() for non-blocking background cloning.",
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
            "recommended_execution": "Pass shlex.join(argv) to run_job() for non-blocking memory testing.",
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
            "recommended_execution": "Pass shlex.join(argv) to run_job() for non-blocking memory acquisition.",
        }

    if action == "ntfs_fix":
        if not _valid_dev_path(device):
            return {"error": "ntfs_fix requires a valid NTFS partition path"}
        argv = ["ntfsfix", "-d", device]
        if dry_run or _IS_WINDOWS:
            return {"action": action, "device": device, "argv": argv, "dry_run": True}
        rc, out, err = _run_cmd(argv, timeout=30)
        return {"action": action, "device": device, "argv": argv, "exit_code": rc, "stdout": out, "stderr": err}

    if action == "umount":
        mountpoint = str(args.get("mountpoint") or "").strip()
        if not mountpoint and _valid_dev_path(device):
            mountpoint = f"/mnt/rescue/{os.path.basename(device)}"
        if not mountpoint:
            return {
                "error": "umount requires 'mountpoint' or a valid 'device' to derive /mnt/rescue/<basename>"
            }
        if not _valid_mountpoint(mountpoint):
            return {
                "error": (
                    f"Invalid mountpoint '{mountpoint}': must be a subdirectory under "
                    "/mnt/rescue/ or /mnt/scratch/ without '..' traversal."
                )
            }
        lazy = args.get("lazy") is True
        force_umount = args.get("force") is True
        argv = ["umount"]
        if lazy:
            argv.append("-l")
        if force_umount:
            argv.append("-f")
        argv.append(mountpoint)
        plan = {
            "action": action,
            "mountpoint": mountpoint,
            "lazy": lazy,
            "force": force_umount,
            "argv": argv,
            "dry_run": dry_run,
        }
        if dry_run:
            return plan
        if _IS_WINDOWS:
            return {"error": "umount execution requires a Linux Live USB node"}
        try:
            mounted = bool(os.path.ismount(mountpoint))
        except Exception:
            mounted = False
        if not mounted:
            return {
                "action": action,
                "mountpoint": mountpoint,
                "argv": argv,
                "dry_run": False,
                "error": f"not mounted: {mountpoint}",
            }
        rc, out, err = _run_cmd(argv, timeout=15)
        plan.update({"exit_code": rc, "stdout": out, "stderr": err})
        return plan

    if action == "fsck_check":
        if not _valid_dev_path(device):
            return {"error": "fsck_check requires a valid device path (e.g. /dev/sda2)"}
        fstype = _normalize_fstype(str(args.get("fstype") or ""))
        if not fstype:
            fstype = _detect_fstype(device)
        if fstype.startswith("ext"):
            argv = ["e2fsck", "-n", device]
        elif fstype == "xfs":
            argv = ["xfs_repair", "-n", device]
        elif fstype == "btrfs":
            argv = ["btrfs", "check", "--readonly", device]
        elif fstype in ("vfat", "fat16", "fat32", "msdos"):
            argv = ["fsck.vfat", "-n", device]
        elif fstype == "exfat":
            argv = ["fsck.exfat", "-n", device]
        elif fstype == "ntfs":
            argv = ["ntfsfix", "-n", device]
        elif fstype == "hfsplus":
            argv = ["fsck.hfsplus", "-n", device]
        else:
            argv = ["fsck", "-n", device]
        plan = {
            "action": action,
            "device": device,
            "fstype": fstype or None,
            "argv": argv,
            "repair": False,
            "dry_run": dry_run,
        }
        if dry_run:
            return plan
        if _IS_WINDOWS:
            return {"error": "fsck_check execution requires a Linux Live USB node"}
        rc, out, err = _run_cmd(argv, timeout=30)
        plan.update({"exit_code": rc, "stdout": out, "stderr": err, "repair": False})
        return plan

    if action == "win_sam_reset":
        mountpoint = str(args.get("mountpoint") or "").strip()
        if not mountpoint and _valid_dev_path(device):
            mountpoint = f"/mnt/rescue/{os.path.basename(device)}"
        sam_user = str(args.get("sam_user") or "").strip()
        if not mountpoint:
            return {
                "error": "win_sam_reset requires 'mountpoint' (or a 'device' mounted under /mnt/rescue/)"
            }
        if not _valid_mountpoint(mountpoint):
            return {
                "error": (
                    f"Invalid mountpoint '{mountpoint}': must be a subdirectory under "
                    "/mnt/rescue/ or /mnt/scratch/ without '..' traversal."
                )
            }
        if not sam_user:
            return {"error": "win_sam_reset requires 'sam_user' (a Windows local account name)"}
        mount_root = mountpoint.rstrip("/")
        sam_path = f"{mount_root}/Windows/System32/config/SAM"
        argv = ["chntpw", "-u", sam_user, sam_path]
        plan = {
            "action": action,
            "mountpoint": mount_root,
            "sam_user": sam_user,
            "sam_path": sam_path,
            "argv": argv,
            "dry_run": dry_run,
        }
        if dry_run:
            return plan
        # chntpw is a full-screen interactive editor.  Running it through
        # subprocess pipes would hang or silently corrupt the hive, so the action
        # is honest about being a manual step instead of pretending to execute.
        plan.update(
            {
                "dry_run": False,
                "status": "requires_interactive",
                "executed": False,
                "message": "run chntpw interactively; no silent SAM write",
            }
        )
        return plan

    if action == "chroot_exec":
        mountpoint = str(args.get("mountpoint") or "").strip()
        chroot_cmd = str(args.get("chroot_cmd") or "").strip()
        bind_mounts = True if args.get("bind_mounts") is None else bool(args.get("bind_mounts"))
        if not _valid_mountpoint(mountpoint):
            return {
                "error": (
                    "chroot_exec requires a mountpoint under /mnt/rescue/ or /mnt/scratch/ "
                    f"(got '{mountpoint}')"
                )
            }
        if not chroot_cmd:
            return {"error": "chroot_exec requires a non-empty 'chroot_cmd'"}
        try:
            chroot_argv = shlex.split(chroot_cmd)
        except ValueError as exc:
            return {"error": f"chroot_exec could not parse 'chroot_cmd': {exc}"}
        # Whitespace-only or quoted-empty commands parse to no usable tokens.
        chroot_argv = [token for token in chroot_argv if token]
        if not chroot_argv:
            return {"error": "chroot_exec requires a non-empty 'chroot_cmd'"}

        root_mp = mountpoint.rstrip("/") or mountpoint
        bind_sources: List[str] = []
        if bind_mounts:
            for source in ("/dev", "/proc", "/sys", "/run"):
                try:
                    present = bool(os.path.exists(source))
                except Exception:
                    present = False
                if present:
                    bind_sources.append(source)
        bind_steps = [["mount", "--bind", source, f"{root_mp}{source}"] for source in bind_sources]
        chroot_step = ["chroot", root_mp] + chroot_argv
        unmount_steps = [["umount", f"{root_mp}{source}"] for source in reversed(bind_sources)]
        plan = {
            "action": action,
            "mountpoint": root_mp,
            "argv": chroot_step,
            "bind_mounts": bind_mounts,
            "binds": bind_sources,
            "planned_steps": bind_steps + [chroot_step] + unmount_steps,
            "dry_run": dry_run,
        }
        if dry_run:
            return plan
        if _IS_WINDOWS:
            return {"error": "chroot_exec execution requires a Linux Live USB node"}
        try:
            mounted = bool(os.path.ismount(root_mp))
        except Exception:
            mounted = False
        if not mounted:
            return {
                "action": action,
                "mountpoint": root_mp,
                "argv": chroot_step,
                "dry_run": False,
                "error": f"not mounted: {root_mp}",
            }

        step_results: List[Dict[str, Any]] = []
        exit_code = 0
        stdout = ""
        stderr = ""
        chroot_ran = False
        try:
            bind_failed = False
            for step in bind_steps:
                try:
                    os.makedirs(step[-1], exist_ok=True)
                except Exception as exc:
                    step_results.append(
                        {
                            "argv": list(step),
                            "exit_code": 1,
                            "stdout": "",
                            "stderr": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    exit_code = 1
                    bind_failed = True
                    break
                rc, out, err = _run_cmd(step, timeout=60)
                step_results.append(
                    {"argv": list(step), "exit_code": rc, "stdout": out, "stderr": err}
                )
                exit_code, stdout, stderr = rc, out, err
                if rc != 0:
                    bind_failed = True
                    break
            if not bind_failed:
                rc, out, err = _run_cmd(chroot_step, timeout=60)
                step_results.append(
                    {"argv": list(chroot_step), "exit_code": rc, "stdout": out, "stderr": err}
                )
                exit_code, stdout, stderr = rc, out, err
                chroot_ran = True
        finally:
            # Bind mounts must never leak, even when a bind or the chroot failed.
            for step in unmount_steps:
                rc_cleanup, out_cleanup, err_cleanup = _run_cmd(step, timeout=60)
                step_results.append(
                    {
                        "argv": list(step),
                        "exit_code": rc_cleanup,
                        "stdout": out_cleanup,
                        "stderr": err_cleanup,
                        "phase": "cleanup",
                    }
                )
        plan.update(
            {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "chroot_ran": chroot_ran,
                "step_results": step_results,
            }
        )
        return plan

    if action == "grub_repair":
        efi_part = str(args.get("efi_partition") or "").strip()
        mountpoint = str(args.get("mountpoint") or "/mnt/rescue/root").strip()
        if not _valid_dev_path(device) or not _valid_dev_path(efi_part):
            return {"error": "grub_repair requires 'device' (root FS) and 'efi_partition' (ESP)"}
        if not _valid_mountpoint(mountpoint):
            return {
                "error": (
                    f"Invalid mountpoint '{mountpoint}': must be a subdirectory under "
                    "/mnt/rescue/ or /mnt/scratch/ without '..' traversal."
                )
            }
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
        if dry_run:
            return {"action": action, "dry_run": True, "planned_steps": steps}
        if _IS_WINDOWS:
            return {"error": "grub_repair execution requires a Linux Live USB node"}

        mount_targets = [
            mountpoint,
            f"{mountpoint}/boot/efi",
            f"{mountpoint}/dev",
            f"{mountpoint}/proc",
            f"{mountpoint}/sys",
            f"{mountpoint}/run",
        ]
        try:
            for target in mount_targets:
                os.makedirs(target, exist_ok=True)
        except Exception as exc:
            return {
                "action": action,
                "dry_run": False,
                "executed": False,
                "stage": "preflight",
                "error": (
                    f"could not create mountpoints under {mountpoint}: "
                    f"{type(exc).__name__}: {exc}"
                ),
            }

        unmount_steps = [
            ["umount", f"{mountpoint}/run"],
            ["umount", f"{mountpoint}/sys"],
            ["umount", f"{mountpoint}/proc"],
            ["umount", f"{mountpoint}/dev"],
            ["umount", f"{mountpoint}/boot/efi"],
            ["umount", mountpoint],
        ]
        step_results: List[Dict[str, Any]] = []
        exit_code = 0
        try:
            for step in steps:
                rc, out, err = _run_cmd(step, timeout=60)
                step_results.append(
                    {"argv": list(step), "exit_code": rc, "stdout": out, "stderr": err}
                )
                exit_code = rc
                if rc != 0:
                    break
        finally:
            # Unmount failures are reported in step_results but must not mask the
            # exit code of the repair step that actually failed.
            for step in unmount_steps:
                rc_cleanup, out_cleanup, err_cleanup = _run_cmd(step, timeout=15)
                step_results.append(
                    {
                        "argv": list(step),
                        "exit_code": rc_cleanup,
                        "stdout": out_cleanup,
                        "stderr": err_cleanup,
                        "phase": "cleanup",
                    }
                )
        return {
            "action": action,
            "dry_run": False,
            "executed": True,
            "exit_code": exit_code,
            "planned_steps": steps,
            "step_results": step_results,
        }

    if action == "malware_scan":
        mp = str(args.get("mountpoint") or "").strip()
        if not mp or not _valid_mountpoint(mp):
            return {
                "error": "malware_scan requires a valid mountpoint under /mnt/rescue/ or /mnt/scratch/"
            }
        scanner = str(args.get("scanner") or "clamav").strip().lower()
        if scanner not in ("clamav", "yara"):
            return {"error": f"malware_scan scanner must be 'clamav' or 'yara', got: {scanner}"}

        quarantine = bool(args.get("quarantine", False))
        q_dir = str(args.get("quarantine_dir") or "/mnt/scratch/quarantine").strip()
        if quarantine:
            if not q_dir.startswith("/mnt/scratch/") or ".." in q_dir.split("/"):
                return {
                    "error": "malware_scan quarantine_dir must be under /mnt/scratch/"
                }
            if _is_volatile_path(q_dir):
                return {
                    "error": f"refusing to quarantine into volatile RAM path: {q_dir}"
                }

        if scanner == "clamav":
            argv = ["clamscan", "-r", "--infected", "--no-summary", mp]
        else:
            rules = str(args.get("yara_rules") or "").strip()
            if not rules:
                return {"error": "malware_scan with scanner='yara' requires 'yara_rules' path"}
            argv = ["yara", "-r", rules, mp]

        if dry_run:
            return {
                "action": action,
                "dry_run": True,
                "scanner": scanner,
                "mountpoint": mp,
                "quarantine": quarantine,
                "quarantine_dir": q_dir if quarantine else None,
                "argv": argv,
            }

        bin_name = "clamscan" if scanner == "clamav" else "yara"
        if not _which(bin_name):
            return {"error": f"{bin_name} is not installed"}
        if not os.path.isdir(mp):
            return {"error": f"mountpoint does not exist: {mp}"}
        if scanner == "yara" and not os.path.isfile(argv[2]):
            return {"error": f"yara_rules file does not exist: {argv[2]}"}

        rc, out, err = _run_cmd(argv, timeout=120)
        findings: List[Dict[str, str]] = []
        for line in out.splitlines():
            s = line.strip()
            if not s:
                continue
            if scanner == "clamav" and s.endswith(" FOUND"):
                # "<path>: <Signature> FOUND"
                path_part, _, rest = s.rpartition(": ")
                if path_part:
                    sig = rest[:-6].strip()
                    findings.append({"path": path_part, "signature": sig})
            elif scanner == "yara":
                parts = s.split(None, 1)
                if len(parts) == 2:
                    findings.append({"path": parts[1].strip(), "signature": parts[0].strip()})

        quarantined: List[Dict[str, Any]] = []
        quarantine_errors: List[str] = []
        if quarantine and findings:
            os.makedirs(q_dir, exist_ok=True)
            manifest_path = os.path.join(q_dir, "manifest.json")
            existing_manifest: List[Dict[str, Any]] = []
            if os.path.isfile(manifest_path):
                try:
                    loaded = json.loads(_read_file_text(manifest_path) or "[]")
                    if isinstance(loaded, list):
                        existing_manifest = loaded
                except Exception:
                    existing_manifest = []
            for item in findings:
                src = item["path"]
                if not os.path.isfile(src):
                    continue
                try:
                    h = hashlib.sha256()
                    with open(src, "rb") as fh:
                        for chunk in iter(lambda: fh.read(65536), b""):
                            h.update(chunk)
                    digest = h.hexdigest()
                    dst = os.path.join(q_dir, f"{digest[:12]}_{os.path.basename(src)}")
                    shutil.move(src, dst)
                    rec = {
                        "original_path": src,
                        "quarantine_path": dst,
                        "signature": item["signature"],
                        "sha256": digest,
                        "timestamp": int(time.time()),
                    }
                    quarantined.append(rec)
                    existing_manifest.append(rec)
                except OSError as exc:
                    quarantine_errors.append(f"{src}: {exc}")
            try:
                with open(manifest_path, "w", encoding="utf-8") as mf:
                    json.dump(existing_manifest, mf, indent=2)
            except OSError as exc:
                quarantine_errors.append(f"manifest write failed: {exc}")

        res: Dict[str, Any] = {
            "action": action,
            "dry_run": False,
            "executed": True,
            "scanner": scanner,
            "argv": argv,
            "exit_code": rc,
            "findings_count": len(findings),
            "findings": findings,
            "quarantined": quarantined,
            "stderr": err,
        }
        if quarantine_errors:
            res["quarantine_errors"] = quarantine_errors
        return res

    return {"error": f"rescue action is not implemented in this build: {action}"}


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
        if section == "partitions":
            return collect_partitions(device=device, timeout=timeout)
        if section == "raid_lvm":
            return collect_raid_lvm(timeout=timeout)
        if section == "installed_os":
            return collect_installed_os(timeout=timeout)
        if section == "boot_efi":
            return collect_boot_efi(timeout=timeout)
        if section == "win_artifacts":
            return collect_win_artifacts(timeout=timeout)
        if section == "win_diag":
            mountpoint = str(payload.get("mountpoint") or "").strip() or None
            return collect_win_diag(mountpoint=mountpoint, timeout=timeout)
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
                "partitions": collect_partitions(device=device, timeout=min(timeout, 5)),
                "installed_os": collect_installed_os(timeout=min(timeout, 5)),
                "boot_efi": collect_boot_efi(timeout=min(timeout, 5)),
                "dmesg_io": collect_dmesg_io(device=device, timeout=min(timeout, 5)),
                "ram_info": collect_ram_info(timeout=timeout),
                "filesystems": collect_filesystems(timeout=min(timeout, 5)),
            }
        if section == "all":
            out: Dict[str, Any] = {
                "section": "all",
                "disks": collect_disks(timeout=timeout),
                "partitions": collect_partitions(device=device, timeout=timeout),
                "raid_lvm": collect_raid_lvm(timeout=timeout),
                "installed_os": collect_installed_os(timeout=timeout),
                "boot_efi": collect_boot_efi(timeout=timeout),
                "win_artifacts": collect_win_artifacts(timeout=timeout),
                "dmesg_io": collect_dmesg_io(device=device, timeout=min(timeout, 5)),
                "ram_info": collect_ram_info(timeout=timeout),
                "filesystems": collect_filesystems(timeout=min(timeout, 5)),
            }
            if device:
                out["smart"] = collect_smart(device=device, timeout=timeout)
            return out

        # Unreachable while RESCUE_SECTIONS is in sync with the dispatch above; kept so an
        # unknown section still degrades cleanly instead of claiming a Linux requirement.
        return {
            "available": False,
            "section": section,
            "reason": f"section '{section}' has no collector in this build",
            "valid_sections": list(RESCUE_SECTIONS),
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
