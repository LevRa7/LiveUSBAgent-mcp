# LiveUSB Service Toolkit Roadmap (2026-11-27)

## Status (2026-11-27)

- [x] **Stage 1** — ISO Package Base & Kiosk Disk Manager (GParted)
- [x] **Stage 2** — Deep Offline Windows Diagnostics (`win_diag`), Extended `autoruns.py` & `malware_scan`
- [x] **Stage 3** — Bootloader Repair (`win_boot_repair`, `systemd_boot_repair`) & `win_sam_reset` Backup
- [x] **Stage 4** — Disk Operations (`partition_ops`), Backups (`partition_backup`/`partition_restore`, `backup_user_data`) & `win_deploy`
- [x] **Stage 5** — Hybrid Multiboot Guide (`docs/liveusb/MULTIBOOT_VENTOY.md`) & Full Verification

## Goal

Expand `remote-harness-liveusb` (`LiveUSBAgent-mcp`) into a full-featured service and recovery LiveUSB covering offline Windows diagnostics, local password recovery, Acronis-style disk management, OS deployment, hardware/RAM stress testing, offline malware scanning, image/file backups, and Windows/Linux bootloader repair — while preserving the repository invariants:

- `core/` remains Python 3.8+ standard-library only.
- `dry_run=True` is the default for every mutating `rescue_action`.
- Read-only mounts first (`ro,noload` / `ro,norecovery`), no writes to volatile `tmpfs`/`overlay` paths.
- Every apt package in `ops/live-usb/build-iso.sh` is verified against Debian 13 (Trixie) via `apt-cache policy`.
- All new collectors/actions have unit tests that run on Windows with mocked subprocesses.

---

## Stage 1 — ISO Package Base & Kiosk Disk Manager (GParted)

- **`ops/live-usb/build-iso.sh`:**
  - Disks & dynamic volumes: `gparted`, `gpart`, `mtools`, `ldmtool` (Windows LDM dynamic disks).
  - Backups & cloning: `partclone`, `restic`.
  - Offline antivirus & malware triage: `clamav`, `clamav-freshclam`, `yara`.
  - Hardware & stress testing: `stress-ng`, `fio`, `s-tui`.
  - Windows offline logs & bootloader/OS deploy: `python3-evtx`, `mbr`, `debootstrap` (note: `ms-sys` is not in Debian 13 repos; do not add as an apt package).
  - Profile filtering: drop `gparted` in `compact` and `lite`; drop `clamav`/`clamav-freshclam` in `lite`; drop `mbr` on `arm64`.
  - Validate all new packages in `build-iso.sh --check`.
- **`ops/live-usb/kiosk_server.py`:**
  - Add `"partitions"` (`Редактор разделов (GParted)`) to `APP_LAUNCH_ORDER` and `APP_LAUNCHERS`.
- **Tests:**
  - Update `tests/test_kiosk_server.py` for the new allowlisted app entry.
  - Add contract tests in `tests/test_liveusb_iso_pipeline.py` for the new service-toolkit packages.

---

## Stage 2 — Deep Offline Windows Diagnostics & Malware Triage

- **`core/rescue.py` (`win_diag` section in `rescue_info`):**
  - Read Windows version/build (`SOFTWARE\Microsoft\Windows NT\CurrentVersion`), Fast Startup/hibernation flags, `Windows/System32/LogFiles/Srt/SrtTrail.txt`, and `Windows/Minidump/*.dmp` metadata (stdlib minidump header parsing for BugCheck code/architecture).
  - Optional `.evtx` critical event extraction (`EventID` 41, 1001, 7, 51, 55) via `python3-evtx` when installed.
- **`core/autoruns.py`:**
  - Extend Windows persistence audit: `Winlogon` (`Shell`, `Userinit`), `Image File Execution Options` (`Debugger`), `AppInit_DLLs`, and non-standard service paths.
- **`core/rescue.py` (`malware_scan` action in `rescue_action`):**
  - Wrapper for `clamscan` / `yara` on mounted `/mnt/rescue/*` paths.
  - Read-only report by default; optional quarantine to `/mnt/scratch/quarantine/` with JSON manifest and SHA-256 checksums for rollback.

---

## Stage 3 — Bootloader Repair (Windows & Linux) & Local SAM Reset

- **`core/rescue.py` (`win_boot_repair` action):**
  - UEFI (GPT): verify/copy `bootmgfw.efi` from `Windows/Boot/EFI/` to ESP, restore/inspect `BCD` via `hivexsh`, create/prioritize `Windows Boot Manager` via `efibootmgr`.
  - Legacy BIOS (MBR): install standard MBR (`install-mbr` from `mbr` package) and verify active partition flag.
  - Mandatory backup of current `BCD` and first 512 bytes (MBR) to `/mnt/scratch/` before any write.
- **`core/rescue.py` (`grub_repair` / `systemd_boot_repair`):**
  - Add `systemd-boot` (`bootctl`) repair path and optional LUKS/LVM pre-assembly.
- **`core/rescue.py` (`win_sam_reset`):**
  - Add mandatory `SAM` backup to `/mnt/scratch/sam-backups/` and controlled non-interactive local account unlock/clear (`chntpw`) when `dry_run=false`, with explicit BitLocker and Microsoft Account notices.

---

## Stage 4 — Disk Operations, Backups & OS Deployment

- **`partition_backup` / `partition_restore`:** `partclone.<fstype>` + `zstd` + partition table dump (`sfdisk -d` / `sgdisk --backup`) + SHA-256, enforcing `_is_volatile_path()`.
- **`backup_user_data`:** incremental backup of `Users/` or `/home/` via `restic` or `rsync` with destination free-space checks.
- **`partition_ops`:** `dry_run`-guarded wrappers for `ntfsresize`/`resize2fs` + `parted` and MBR→GPT conversion (`sgdisk -g`) with mandatory partition-table backup.
- **`win_deploy`:** apply `install.wim`/`install.esd` from a local Windows ISO via `wimlib-imagex apply` (`wimtools`) onto GPT (ESP + MSR + NTFS) and stage EFI boot files.

---

## Stage 5 — Hybrid Multiboot USB (Ventoy + WinPE) & Verification

- **`docs/liveusb/MULTIBOOT_VENTOY.md`:** layout guide for combining `LiveUSBAgent.iso` + compact WinPE/WinRE (for native `chkdsk /f`, `bcdboot`, `sfc`, `dism`) + `ISO/` directory for Windows/Linux installers + optional user-supplied UEFI utilities (with Secure Boot notes).
- **Verification:** full `pytest`, `build-iso.sh --check` across `amd64`/`arm64`/`i386` and `full`/`compact`/`lite`, WSL Debian 13 ISO build, and QEMU boot validation.
