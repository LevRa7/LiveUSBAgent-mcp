---
name: windows-offline-boot-repair
description: "Diagnose and repair offline Windows installations from a Linux LiveUSB: identify ESP/NTFS partitions, safely handle Fast Startup/hibernation, unlock BitLocker, inspect/edit offline Registry hives, and verify UEFI boot entries."
category: liveusb-recovery
tags: [liveusb, windows, ntfs, bitlocker, uefi, esp, registry, boot-repair]
os: windows
risk_level: high
---

# Windows Offline Boot & System Repair (Linux LiveUSB)

Use this runbook to inspect, unlock, and repair an unbootable or offline Windows installation from a Linux LiveUSB environment. Default to **read-only** operations until the exact failure mode is confirmed and backups are taken.

## 1. Strict Safety Gates (Before Any Write)

- **Never** mount a hibernated NTFS volume read-write with `remove_hiberfile` without explicit operator confirmation; doing so permanently discards the saved Windows session state in `hiberfil.sys`.
- **Back up before editing:** Copy target Registry hives (`SYSTEM`, `SOFTWARE`, `SAM`, `SECURITY`) and the EFI BCD store (`/mnt/esp/EFI/Microsoft/Boot/BCD`) to external storage (never `/tmp` if RAM is tight) before running `chntpw`, `hivexsh`, `ntfsfix`, or `efibootmgr` mutations.
- **Verify LiveUSB boot mode:** Confirm `/sys/firmware/efi` exists before running `efibootmgr`; legacy BIOS boots cannot read or modify UEFI NVRAM variables.

## 2. Identify ESP and Windows NTFS Partitions

```bash
lsblk -o NAME,SIZE,FSTYPE,PARTTYPENAME,LABEL,MOUNTPOINTS
blkid
```

- **EFI System Partition (ESP):** FAT32 (`vfat`), typically 100–512 MB, GPT type `C12A7328-F81F-11D2-BA4B-00A0C93EC93B`.
- **Windows OS Partition:** `ntfs` (or `BitLocker` if encrypted), containing `Windows/System32/config/`.
- **Microsoft Reserved (MSR):** ~16 MB, no filesystem; do not mount or format.

## 3. Handle Hibernation & Fast Startup Safely

Always attempt a read-only mount first to inspect logs and confirm hibernation status without altering the filesystem:

```bash
mkdir -p /mnt/win
mount -t ntfs-3g -o ro /dev/sdXn /mnt/win
```

If `ntfs-3g` reports that Windows is hibernated or has an unclean journal (Fast Startup):
1. **Read-only inspection (preferred):** Keep `-o ro` to read `Windows/System32/LogFiles/Srt/SrtTrail.txt`, `Windows/Minidump/`, or extract user data.
2. **Clear dirty flag only (non-hibernated unclean shutdown):**
   ```bash
   umount /mnt/win
   ntfsfix -d /dev/sdXn
   mount -t ntfs-3g -o ro /dev/sdXn /mnt/win
   ```
   `ntfsfix -d` clears the dirty volume flag and resets the NTFS journal; it is **not** a full `chkdsk` replacement. Schedule a native Windows `chkdsk /f` on next boot.
3. **Read-write mount (only after operator approval and backup):**
   - If not hibernated: `mount -t ntfs-3g -o rw /dev/sdXn /mnt/win`
   - Only if the operator explicitly accepts losing hibernated state: `mount -t ntfs-3g -o remove_hiberfile,rw /dev/sdXn /mnt/win`

## 4. Unlock BitLocker Volumes

Identify the encrypted partition via `blkid` (`TYPE="BitLocker"`). Prefer native `cryptsetup` (kernel `dm-crypt`) when available, or fall back to `dislocker`.

### Option A: `cryptsetup` (Preferred — Native Kernel Block Device)
```bash
# Prompts for recovery key (48-digit) or passphrase
cryptsetup open --type bitlk /dev/sdXn win_bitlk
mount -t ntfs-3g -o ro /dev/mapper/win_bitlk /mnt/win
```
Teardown: `umount /mnt/win && cryptsetup close win_bitlk`

### Option B: `dislocker` (FUSE Fallback)
```bash
mkdir -p /mnt/bitlk /mnt/win
# Using 48-digit recovery password (-p) or user password (-u)
dislocker -V /dev/sdXn -p123456-123456-123456-123456-123456-123456-123456-123456 -- /mnt/bitlk
mount -t ntfs-3g -o loop,ro /mnt/bitlk/dislocker-file /mnt/win
```
Teardown: `umount /mnt/win && umount /mnt/bitlk`

## 5. Inspect & Edit Offline Registry Hives

Hives live in `/mnt/win/Windows/System32/config/` (`SYSTEM`, `SOFTWARE`, `SAM`, `SECURITY`, `DEFAULT`).

### Read-Only Inspection (`hivexget` / `hivexsh`)
```bash
# Check active ControlSet and Safe Mode / boot parameters
hivexget /mnt/win/Windows/System32/config/SYSTEM 'Select' 'Current'
hivexget /mnt/win/Windows/System32/config/SOFTWARE 'Microsoft\Windows NT\CurrentVersion' 'ProductName'

# Interactive read-only shell (omit -w to prevent writes)
hivexsh /mnt/win/Windows/System32/config/SYSTEM
```

### Editing Hives (Requires RW Mount + Hive Backup First)
```bash
cp /mnt/win/Windows/System32/config/SYSTEM /mnt/external/SYSTEM.bak
cp /mnt/win/Windows/System32/config/SAM /mnt/external/SAM.bak

# Local account unlock / password reset (interactive or batch)
chntpw -l /mnt/win/Windows/System32/config/SAM
chntpw -u Administrator /mnt/win/Windows/System32/config/SAM

# Registry key edit via hivexsh (explicit -w enables write mode; commit saves)
hivexsh -w /mnt/win/Windows/System32/config/SYSTEM
```

## 6. UEFI / ESP Boot Entry Checks & Repair

Mount the ESP read-only first to verify Windows Boot Manager files exist:

```bash
mkdir -p /mnt/esp
mount -o ro /dev/sdX1 /mnt/esp
ls -la /mnt/esp/EFI/Microsoft/Boot/
```

Verify key files: `bootmgfw.efi` and `BCD`.

Inspect UEFI NVRAM boot entries:
```bash
efibootmgr -v
```

Common non-destructive / targeted fixes (require operator confirmation):
- **Restore boot priority** if Windows Boot Manager (`BootXXXX`) exists in `efibootmgr -v`:
  ```bash
  efibootmgr -o XXXX,YYYY
  ```
- **Recreate missing UEFI entry** pointing to an existing `bootmgfw.efi`:
  ```bash
  efibootmgr --create --disk /dev/sdX --part 1 \
    --label "Windows Boot Manager" \
    --loader '\EFI\Microsoft\Boot\bootmgfw.efi'
  ```
- **Corrupted `BCD` store:** Linux cannot reliably rebuild a complex Windows BCD from scratch. Preserve `/mnt/esp/EFI/Microsoft/Boot/BCD` backup and recommend booting Windows Recovery Environment (WinRE) to run `bcdboot C:\Windows /s S: /f UEFI`.

## 7. Clean Unmount Checklist

Before rebooting, flush buffers and unmount in reverse order:
```bash
sync
umount /mnt/esp /mnt/win 2>/dev/null || true
[ -e /dev/mapper/win_bitlk ] && cryptsetup close win_bitlk
```
