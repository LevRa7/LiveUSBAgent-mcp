---
name: cross-os-autoruns-and-password-recovery
description: "Defensive read-only autorun and persistence inspection across offline Windows, Linux, and macOS volumes, plus standard legitimate forgotten-password recovery for local accounts with mandatory pre-change backups."
category: liveusb-recovery
tags: [liveusb, autoruns, persistence, forensics, password-recovery, windows, linux, macos, chntpw, chroot]
os: cross-platform
risk_level: high
---

# Cross-OS Offline Autoruns Inspection & Local Password Recovery

Use this runbook from a Linux LiveUSB environment to (1) perform **strictly read-only** startup and persistence inspection across mounted Windows, Linux, and macOS filesystems, and (2) perform **legitimate, owner-authorized** local-account password recovery with mandatory backups.

## 1. Strict Safety Boundaries & Pre-Flight Gates

- **Read-only first:** Always mount target OS partitions read-only (`-o ro`, or `ro,noload` for ext4/XFS/Btrfs on suspect disks) while inspecting startup entries and verifying account state.
- **No binary patching or security circumvention:** Never patch OS binaries or DLLs, disable system integrity protections, or bypass licensing/authentication services. Restrict changes strictly to standard local-account recovery tools (`chntpw`, `passwd` inside `chroot`, or official Apple Recovery).
- **Mandatory persistent backups before any write:** Back up target authentication databases (`SAM`, `/etc/shadow`) to persistent scratch storage (`/mnt/scratch/` or an external USB drive) **before** remounting read-write. Never store backups only in volatile LiveUSB RAM (`/tmp`).
- **Verify authorization & disk encryption first:** Confirm the machine owner authorized the recovery, and unlock BitLocker (`cryptsetup open --type bitlk` / `dislocker`) or LUKS (`cryptsetup open`) before accessing OS files.

---

## 2. Defensive Autorun & Persistence Inspection (Read-Only)

### Automated Read-Only Scan (`core.autoruns`)

Once the target OS root is mounted read-only (for example at `/mnt/rescue/os`), run the stdlib-only scanner:

```bash
python3 -c 'import json; from core.autoruns import scan_autoruns; print(json.dumps(scan_autoruns("/mnt/rescue/os"), indent=2))'
```

`scan_autoruns(root_path, os_hint="auto")` auto-detects Windows, Linux, or macOS and returns structured JSON without modifying any file on the mounted volume.

### Windows Persistence Locations

1. **Startup Folders:**
   - Machine-wide: `ProgramData/Microsoft/Windows/Start Menu/Programs/Startup`
   - Per-user: `Users/*/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup`
2. **Scheduled Tasks XML:**
   - `Windows/System32/Tasks/**` (inspect `<Exec><Command>` and `<Arguments>`)
3. **Offline Registry Run Keys (Read-Only via `hivexget`):**
   ```bash
   hivexget /mnt/rescue/win/Windows/System32/config/SOFTWARE 'Microsoft\Windows\CurrentVersion\Run'
   hivexget /mnt/rescue/win/Windows/System32/config/SOFTWARE 'Microsoft\Windows\CurrentVersion\RunOnce'
   ```
   *(If `hivexget` is not installed on the LiveUSB, verify hive presence under `Windows/System32/config/` and inspect with `chntpw -e` in read-only mode without saving.)*

### Linux Persistence Locations

1. **Systemd Units:** `/etc/systemd/system/*.service` (inspect `ExecStart=`, `User=`, `Description=`)
2. **Cron Jobs:** `/etc/cron.d/*` and `/etc/crontab`
3. **Legacy Boot Script:** `/etc/rc.local`
4. **Desktop Autostart Entries:**
   - System-wide: `/etc/xdg/autostart/*.desktop` (inspect `Exec=`)
   - Per-user: `home/*/.config/autostart/*.desktop` (inspect `Exec=`)

### macOS Persistence Locations

Mount the macOS Data volume read-only (`mount -t apfs -o ro` or `apfs-fuse` for FileVault):

1. **System LaunchDaemons:** `Library/LaunchDaemons/*.plist`
2. **Global LaunchAgents:** `Library/LaunchAgents/*.plist`
3. **Per-User LaunchAgents:** `Users/*/Library/LaunchAgents/*.plist`

Inspect `Label`, `Program`, `ProgramArguments`, `RunAtLoad`, and `KeepAlive` via `scan_autoruns` (which uses Python's `plistlib` for both XML and binary plists).

### Suspicious Persistence Patterns to Flag

Review collected entries for common red flags before deciding whether to quarantine or investigate further:
- **Obfuscated or staged commands:** Encoded PowerShell (`-enc`, `-EncodedCommand`, `FromBase64String`), piped download-and-execute chains (`curl ... | sh`, `wget -O- ... | bash`), or `mshta` / `rundll32` / `wscript` launching scripts from user-writable folders.
- **Execution from temporary or world-writable directories:** Binaries or scripts running out of `AppData\Local\Temp`, `Users\Public`, `/tmp`, `/var/tmp`, or `/dev/shm`.
- **Resilient unknown agents:** Unrecognized macOS plists with both `RunAtLoad: true` and `KeepAlive: true`, or Linux systemd services with `Restart=always` pointing to unsigned/hidden binaries.
- **Masqueraded names:** Services or tasks mimicking OS binaries (`svch0st`, `systemd-helperd`) located outside standard system directories.

---

## 3. Legitimate Offline Forgotten-Password Recovery (Local Accounts)

Only proceed after read-only verification and explicit operator confirmation.

### A. Windows Local Accounts (`chntpw`)

**Prerequisites & Limitations:**
- **Local accounts only:** `chntpw` works only on local SAM accounts. It **cannot** reset Microsoft online accounts (MSA), Active Directory domain accounts, or Microsoft Entra ID accounts.
- **BitLocker:** If the Windows volume is BitLocker-encrypted, unlock it first with the 48-digit recovery key (`cryptsetup open --type bitlk /dev/sdXn win_bitlk` or `dislocker`).
- **Hibernation / Fast Startup:** Never force-mount a hibernated NTFS volume read-write (`remove_hiberfile`) without explicit operator approval, as doing so discards saved session state in `hiberfil.sys`.

**Procedure:**
1. Remount the unlocked, non-hibernated Windows partition read-write:
   ```bash
   umount /mnt/rescue/win
   mount -t ntfs-3g -o rw /dev/sdXn /mnt/rescue/win
   ```
2. **Mandatory pre-change backup of `SAM` to `/mnt/scratch/`:**
   ```bash
   mkdir -p /mnt/scratch/sam-backups
   cp -a /mnt/rescue/win/Windows/System32/config/SAM \
     "/mnt/scratch/sam-backups/SAM.bak.$(date +%Y%m%d_%H%M%S)"
   sync
   ```
3. List local accounts and verify the target username:
   ```bash
   chntpw -l /mnt/rescue/win/Windows/System32/config/SAM
   ```
4. Unlock the account and clear its local password:
   ```bash
   chntpw -u <user> /mnt/rescue/win/Windows/System32/config/SAM
   ```
   Select `1` (Clear / blank user password) and `2` (Unlock and enable user account) if locked, then `q` and `y` to write changes.
5. Sync and unmount cleanly:
   ```bash
   sync
   umount /mnt/rescue/win
   ```
6. **Post-boot action:** Boot into Windows, sign in to the local account, and immediately set a strong new password.

### B. Linux Local Accounts (`chroot` + `passwd`)

**Prerequisites:**
- Unlock LUKS first if the root filesystem is encrypted (`cryptsetup open /dev/sdXn cryptroot`).
- If the system uses a separate `/usr` partition, mount it inside the target root before `chroot`.

**Procedure:**
1. Remount the Linux root filesystem read-write:
   ```bash
   umount /mnt/rescue/linux
   mount -o rw /dev/sdXn /mnt/rescue/linux
   ```
2. **Mandatory pre-change backup of `/etc/shadow` to `/mnt/scratch/`:**
   ```bash
   mkdir -p /mnt/scratch/shadow-backups
   cp -a /mnt/rescue/linux/etc/shadow \
     "/mnt/scratch/shadow-backups/shadow.bak.$(date +%Y%m%d_%H%M%S)"
   sync
   ```
3. Bind-mount virtual filesystems and reset the user password inside `chroot`:
   ```bash
   mount --bind /dev /mnt/rescue/linux/dev
   mount --bind /proc /mnt/rescue/linux/proc
   mount --bind /sys /mnt/rescue/linux/sys
   chroot /mnt/rescue/linux passwd <user>
   ```
   *(If SELinux is enforcing on RHEL/Fedora systems, touch `/mnt/rescue/linux/.autorelabel` so `/etc/shadow` retains a valid security context on boot.)*
4. Unmount cleanly:
   ```bash
   sync
   umount -R /mnt/rescue/linux
   ```

### C. macOS Local Accounts (Modern APFS / FileVault / Secure Enclave)

- **Cannot be reset offline from Linux:** Modern macOS ties user login credentials to APFS volume encryption, FileVault 2, Secure Token, and the Apple Secure Enclave / T2 chip. Editing plist or shadow files from a Linux LiveUSB will **not** unlock FileVault or reset a modern macOS password and can corrupt user keychains.
- **Official Recovery Procedure:**
  1. Reboot the Mac into **macOS Recovery** (hold the **Power** button on Apple Silicon until "Loading startup options" appears, or hold **Command-R** on Intel Macs).
  2. Choose **Options** → open **Utilities → Terminal**.
  3. Run the official Apple utility:
     ```bash
     resetpassword
     ```
  4. Follow the Recovery Assistant prompts using the owner's **Apple Account** credentials or **FileVault Recovery Key**.
