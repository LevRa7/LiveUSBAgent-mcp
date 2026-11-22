---
name: macos-apfs-hfs-recovery
description: "Inspect and recover data from macOS HFS+ and APFS disks on a Linux LiveUSB: distinguish partition types, mount HFS+ safely, use dual-path APFS tools (linux-apfs-rw, apfs-fuse, libfsapfs/AgentDFIR), and extract files to external storage."
category: liveusb-recovery
tags: [liveusb, macos, apfs, hfsplus, filevault, data-recovery, forensics]
os: macos
risk_level: medium
---

# macOS APFS & HFS+ Recovery (Linux LiveUSB)

Use this runbook to identify, mount, and safely extract data from macOS disks on a Linux LiveUSB. Treat all macOS volumes as **read-only** by default.

## 1. Distinguish HFS+ vs. APFS Partitions

```bash
lsblk -o NAME,SIZE,FSTYPE,PARTTYPENAME,LABEL,MOUNTPOINTS
blkid
gdisk -l /dev/sdX
```

GPT Partition Type GUIDs:
- **APFS Container:** `7C3457EF-0000-11AA-AA11-00306543ECAC` (gdisk code `AF0A`)
- **HFS+ Volume:** `48465300-0000-11AA-AA11-00306543ECAC` (gdisk code `AF00`)
- **Apple CoreStorage (legacy FileVault 2 / Fusion Drive):** `53746F72-6167-11AA-AA11-00306543ECAC` (gdisk code `AF05`)

## 2. HFS+ Mounting (Read-Only & Forced Read-Write)

Requires `hfsprogs` (`fsck.hfsplus`, `mkfs.hfsplus`) and kernel `hfsplus` module.

### Read-Only Mount (Default & Safe)
```bash
mkdir -p /mnt/macos_hfs
mount -t hfsplus -o ro /dev/sdXn /mnt/macos_hfs
```

### Forced Read-Write Mount (Journaled HFS+ Caveat)
Linux refuses to mount journaled HFS+ read-write by default. Only use `force,rw` if write access is strictly required **and** you have backed up the volume first:
```bash
# Check filesystem integrity first (read-only check: -n)
fsck.hfsplus -n /dev/sdXn

# Forced read-write (operator confirmation required; risks journal inconsistency)
mount -t hfsplus -o force,rw /dev/sdXn /mnt/macos_hfs
```

## 3. APFS Dual-Path Strategy

Modern macOS splits system and user data across APFS volumes inside one container (e.g., `Macintosh HD` read-only system volume and `Macintosh HD - Data` user data volume). Always check which volume index holds `/Users/`.

### Path A: `linux-apfs-rw` (Unencrypted Native Kernel Mount)
Best performance for **unencrypted** APFS containers when the `apfs` kernel module is installed.
> **WARNING:** `linux-apfs-rw` write support is **experimental** and can corrupt APFS containers. Always mount with `-o ro` for recovery. Never mount read-write without an explicit operator-approved full disk backup first.

```bash
mkdir -p /mnt/apfs
# List or mount specific volume index (vol=0, vol=1, etc.; Data volume is often vol=1 or higher)
mount -t apfs -o ro,vol=1 /dev/sdXn /mnt/apfs
```

### Path B: `apfs-fuse` (FileVault-Encrypted Volumes — Read-Only)
Use `apfs-fuse` whenever the APFS volume is encrypted with FileVault 2 (passphrase or institutional/personal recovery key):
```bash
mkdir -p /mnt/apfs_fv
# Prompts for password/recovery key; add -v <index> to select the Data volume
apfs-fuse -o allow_other /dev/sdXn /mnt/apfs_fv
# Inspect volumes inside the container if unsure of index:
apfs-fuse -v 1 -o allow_other /dev/sdXn /mnt/apfs_fv
```
Teardown: `fusermount -u /mnt/apfs_fv`

### Path C: Damaged or Corrupted APFS Containers (`libfsapfs` / `AgentDFIR` Fallback)
When `linux-apfs-rw` or `apfs-fuse` fails to parse a damaged superblock or checkpoint tree:
1. **Image the failing disk first** (never run recovery parsers directly on a dying drive):
   ```bash
   ddrescue -f -n /dev/sdXn /mnt/external/apfs_container.img /mnt/external/apfs.mapfile
   ```
2. **Inspect & mount via `libfsapfs` (`fsapfsinfo` / `fsapfsmount`):**
   ```bash
   fsapfsinfo /mnt/external/apfs_container.img
   mkdir -p /mnt/apfs_lib
   # Add -p <passphrase> or -r <recovery_key> if encrypted
   fsapfsmount -f 1 /mnt/external/apfs_container.img /mnt/apfs_lib
   ```
3. **Forensic fallback (`AgentDFIR`):** If `fsapfsmount` cannot mount a severely corrupted container or deleted snapshots need carving, hand off the raw image (`/mnt/external/apfs_container.img`) to the `AgentDFIR` forensic pipeline for metadata carving and artifact extraction.

## 4. Safe Data Extraction to External Storage

- **Verify destination capacity first:** Confirm the external target drive (ext4/exFAT/NTFS) is mounted and has sufficient free space (`df -h /mnt/external`). **Never** extract macOS user data into LiveUSB RAM (`tmpfs`, `/tmp`, `/home/user`).
- **Preserve metadata and resume cleanly:**
  ```bash
  mkdir -p /mnt/external/macos_recovery
  rsync -aH --info=progress2 --no-specials --no-devices \
    /mnt/apfs_fv/root/Users/ /mnt/external/macos_recovery/Users/
  ```
- **Verify & Unmount:**
  ```bash
  sync
  umount /mnt/macos_hfs /mnt/apfs /mnt/apfs_lib 2>/dev/null || true
  fusermount -u /mnt/apfs_fv 2>/dev/null || true
  ```
