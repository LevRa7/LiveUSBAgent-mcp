# LiveUSBAgent-mcp

> **Bootable Debian 13 (Trixie) LiveUSB rescue OS that turns any broken or bare-metal PC/laptop into an AI-assisted sysadmin, OS repair, and data-recovery workstation — controlled via MCP directly from Gemini, ChatGPT, or Claude.**

Русская версия: [README_RU.md](README_RU.md)

**Repository:** [github.com/LevRa7/LiveUSBAgent-mcp](https://github.com/LevRa7/LiveUSBAgent-mcp) (evolved from [github.com/LevRa7/remote-harness-mcp](https://github.com/LevRa7/remote-harness-mcp))

**Version:** 0.1.0 — release notes: [docs/releases/v0.1.0.md](docs/releases/v0.1.0.md).

---

## What It Is & Purpose

When a PC refuses to boot, a drive starts failing, BitLocker or LUKS locks you out, or you need to rescue files from a Windows, Linux, or macOS disk, you usually have to memorize obscure rescue commands on a bare terminal.

**LiveUSBAgent-mcp** solves that for both **beginners** and **experienced sysadmins**:

- **For beginners:** Plug in the USB stick, boot the broken PC, connect to Wi-Fi or tether your phone on the graphical screen, scan the QR code, and paste one link into **Gemini**, **ChatGPT**, or **Claude**. You can then describe the problem in plain language while the AI inspects disks, reads logs, and guides or executes the recovery.
- **For sysadmins & engineers:** Get an instant, zero-config Debian 13 (Trixie) rescue environment loaded with cross-OS filesystem drivers (NTFS/BitLocker, ext4/Btrfs/XFS/F2FS/ZFS/LVM/mdadm/LUKS, HFS+/APFS), hardware/RAM diagnostics, curated `SKILL.md` playbooks, and full root MCP access over an outbound TLS WebSocket tunnel — no SSH setup, no port forwarding, and no manual agent installation.

```text
  Gemini (/sse) · ChatGPT (/mcp) · Claude (/mcp, CLI or JSON)
                          │  MCP over HTTPS
                          ▼
               Mesh Gateway (/connect/<session_code>)
                          │  Outbound WebSocket tunnel (no open ports)
                          ▼
   Booted LiveUSB (Debian 13 Trixie · python3 -m core.agent --ephemeral-liveusb)
   ├─ tty1: Graphical Kiosk UI (127.0.0.1:8787) — Wi-Fi, USB tethering, SVG QR
   ├─ tty2: Fallback console dashboard (firstboot_tui.py + nmtui + [S] Shell (bash))
   └─ Full root MCP tools + read-safe rescue_info / rescue_action + SKILL.md library
```

---

## Key Features

### 1. Broad Hardware, Wi-Fi & Phone USB Tethering Out of the Box
- **Firmware & drivers (`non-free-firmware`):** Ships with `firmware-linux`, `firmware-linux-nonfree`, `firmware-misc-nonfree`, `firmware-iwlwifi` (Intel Wi-Fi 6/6E/7), `firmware-realtek`, `firmware-atheros`, `firmware-brcm80211` (Broadcom), `firmware-mediatek`, `firmware-marvell-prestera`, `firmware-qcom-soc`, `firmware-ti-connectivity`, `firmware-zd1211`, and `firmware-libertas`, plus `network-manager`, `wpasupplicant`, `iw`, `rfkill`, `ethtool`, `usb-modeswitch`, `modemmanager`, `wireguard-tools`, and `openssh-client`. Out-of-tree DKMS support includes `broadcom-sta-dkms` for older Broadcom/MacBook Wi-Fi adapters.
- **iPhone USB Tethering (iOS):** Preinstalled `usbmuxd`, `ipheth` (`ipheth-utils`), `libimobiledevice-utils` (`idevicepair`, `ideviceinfo`), and `ifuse`. Plug in an iPhone with Personal Hotspot enabled, tap **Trust**, and NetworkManager brings up DHCP automatically.
- **Android USB Tethering:** Built-in `rndis_host`, `cdc_ether`, and `cdc_ncm` support with `99-rhm-network-tethering.rules` so newly attached USB-Ethernet interfaces automatically acquire DHCP.

### 2. Graphical Kiosk Startup Screen + Console Fallback
- **Kiosk UI (`tty1`):** Boots directly into a clean fullscreen Chromium kiosk over `openbox` / Xorg served locally by `ops/live-usb/kiosk_server.py` on `127.0.0.1:8787`. Features an interactive Wi-Fi scanner (`nmcli`) with **`Обновить список`** (refresh list) and **`Скрытая сеть`** (hidden SSID) controls, a password modal with an on-screen keyboard (handy for touchscreens or broken laptop keyboards), live Ethernet & USB tethering status (including an iPhone Trust prompt when unpaired), and a Ready view with a large SVG QR code, a detected-disks summary, **`Сменить Wi-Fi`** (switch Wi-Fi), and **`Новый токен`** (regenerate token).
- **Headless / GPU Fallback (`tty2`):** `ops/live-usb/firstboot_tui.py` runs on `tty2` (switch via `Ctrl+Alt+F2`; also takes over automatically if X11 cannot start), giving full status, QR code, `nmtui` network configuration, and a local `bash` shell via `Ctrl+Alt+F2` → **`[S] Shell (bash)`**.

### 3. Zero-Config Ephemeral Onboarding
- As soon as the LiveUSB gets internet access, `rhm-live-agent.service` (`python3 -m core.agent --ephemeral-liveusb`) automatically registers a temporary `live-*` node with the gateway (`POST /v1/liveusb/register`).
- Generates an ephemeral 24-hour `lk_...` link key stored only in RAM (`/run/rhm-live/`) and displays a QR code pointing to `https://<gateway>/connect/<session_code>`.
- Scanning the QR code on your phone or second computer opens a mobile-friendly pairing page with 1-click copy buttons for **Gemini (`/sse`)**, **ChatGPT (`/mcp`)**, and **Claude (`/mcp` URL, `claude mcp add --transport http ...` CLI command, and `mcpServers` JSON config)** plus live connection status.

### 4. Out-of-the-Box Cross-OS Filesystem Support

| OS Family | Filesystem / Volume | Included Packages & Modules | Capabilities & Notes |
| :--- | :--- | :--- | :--- |
| **Cross-platform** | **exFAT** | `exfatprogs`, `exfat-fuse`, kernel `exfat` | Full R/W, `fsck.exfat`, `mkfs.exfat`. |
| **Cross-platform** | **FAT32 / FAT16 (`vfat`)** | `dosfstools` | Full R/W, EFI System Partition (ESP) repair. |
| **Windows** | **NTFS** | Kernel `ntfs3` + `ntfs-3g` | Full R/W via `ntfs3`; safe fallback to `ntfs-3g` (`ro` or `ntfsfix`) for dirty/hibernated volumes. |
| **Windows** | **BitLocker** | `dislocker`, `cryptsetup` (`--type bitlk`) | Decrypt with password or 48-digit recovery key, then mount NTFS. |
| **Linux** | **ext2 / ext3 / ext4** | `e2fsprogs` | Full R/W, `e2fsck`, `resize2fs`, `debugfs`. |
| **Linux** | **Btrfs** | `btrfs-progs` | Full R/W, subvolumes, snapshots, `btrfs check`/`restore`. |
| **Linux** | **XFS** | `xfsprogs` | Full R/W, `xfs_repair`, `xfs_info`. |
| **Linux** | **F2FS** | `f2fs-tools` | Full R/W (`fsck.f2fs`, `dump.f2fs`). |
| **Linux** | **ZFS (OpenZFS)** | `zfsutils-linux`, `zfs-dkms` | Full R/W and read-only `zpool import -f -R /mnt/rescue <pool>`. |
| **Linux** | **LVM2 / mdadm / LUKS** | `lvm2`, `mdadm`, `cryptsetup`, `dmraid`, `kpartx` | Manual RAID assembly, LUKS1/LUKS2 unlock, and LVM activation. |
| **macOS** | **HFS / HFS+** | `hfsplus`, `libfshfs-utils`, kernel `hfsplus` | Safe R/O; R/W supported (`fsck.hfsplus`, `-o force,rw` for journaled volumes). |
| **macOS** | **APFS** | `apfs-dkms`, `apfsprogs`, `apfs-fuse` (compiled from source in the chroot hook to `/usr/local/bin/apfs-fuse`), `libfsapfs-utils` | Dual-mode: native `apfs-dkms` for unencrypted APFS (default R/O; experimental R/W with warning), `apfs-fuse` (R/O) for FileVault-encrypted volumes, and `libfsapfs-utils` (`fsapfsmount`/`fsapfsinfo`) for damaged containers. |

### 5. Built-In Progressive `SKILL.md` Library
Instead of flooding the AI's context window with dozens of long manuals, LiveUSBAgent-mcp exposes two progressive-disclosure MCP tools (`list_skills` and `load_skill`) backed by `/opt/remote-harness-mcp/skills/`:
- **4 custom LiveUSB recovery skills** in `skills/liveusb/`:
  1. `windows-offline-boot-repair` — UEFI/ESP & BCD repair, offline Windows Registry (`chntpw`, `libhivex-bin`), `ntfsfix`, and BitLocker decryption.
  2. `linux-chroot-grub-luks-rescue` — Safe `mdadm` -> `cryptsetup` -> `lvm2` assembly, `chroot`, GRUB/systemd-boot, and `initramfs` rebuilds.
  3. `macos-apfs-hfs-recovery` — Safe data extraction from APFS (`apfs-dkms` / `apfs-fuse` for FileVault / `libfsapfs-utils`) and HFS+.
  4. `ram-hardware-live-diagnostics` — Hardware inventory (`dmidecode`, `lshw`, `lspci`, `lsusb`, `lm-sensors`), live RAM stress testing (`memtester`), and memory capture (`/proc/kcore` works out of the box; `avml` is not preinstalled in the ISO and is optional if installed separately).
- **Curated external skills (`ops/live-usb/fetch-skills.sh`):** Downloads vetted GitHub sysadmin, DevOps, storage, and recovery skills listed in `ops/live-usb/skills-manifest.json`, records exact commit SHAs in `ops/live-usb/skills-lock.json`, and scans them offline with [NVIDIA/SkillSpector](https://github.com/NVIDIA/SkillSpector) (`--recursive --no-llm`) when installed.

---

## Step-by-Step: Build, Flash, Boot & Use

> **Note:** Prebuilt ISO releases are not published yet — ISO images are currently built locally on a Debian 13 (Trixie) host or VM.

### Step 1: Build the ISO (on Debian 13 Trixie)

Install the build prerequisites on a Debian 13 (Trixie) host or VM:

```bash
sudo apt-get update
sudo apt-get install -y \
  live-build debootstrap squashfs-tools xorriso \
  grub-efi-amd64-bin grub-pc-bin mtools dosfstools \
  curl jq git ca-certificates
```

Configure and build the hybrid ISO (including curated external skills):

```bash
git clone https://github.com/LevRa7/LiveUSBAgent-mcp.git
cd LiveUSBAgent-mcp
sudo ./ops/live-usb/build-iso.sh --with-external-skills --build
```

Useful `build-iso.sh` options:
- `--work-dir PATH` — Custom `live-build` workspace (default: `/tmp/rhm-live-build`; use e.g. `/var/tmp/rhm-live-build` if `/tmp` is small).
- `--gateway HOST` — Mesh gateway hostname (default: `smart-server.online`).
- `--distro NAME` / `--arch ARCH` — Debian suite and architecture (defaults: `trixie`, `amd64`).
- Omit `--build` if you want to inspect the generated `live-build` config before running `sudo lb build` manually.

The finished hybrid image (`live-image-amd64.hybrid.iso`) is written to your `WORK_DIR`.

#### Optional: Test in QEMU/KVM Before Flashing

```bash
# UEFI boot with auto-created virtual test disks (exfat, ntfs, ext4, btrfs, xfs, vfat)
./ops/live-usb/test-qemu.sh --mode uefi --create-fs-disks

# Legacy BIOS and Low-RAM (< 4 GB) check
./ops/live-usb/test-qemu.sh --mode bios --ram 2048

# Headless serial-console mode for CI or previewing the exact QEMU command
./ops/live-usb/test-qemu.sh --headless --create-fs-disks
./ops/live-usb/test-qemu.sh --dry-run --mode uefi --create-fs-disks
```

*(Requires `qemu-system-x86`, `ovmf`, and filesystem tools `exfatprogs`, `ntfs-3g`, `e2fsprogs`, `btrfs-progs`, `xfsprogs`, `dosfstools`.)*

---

### Step 2: Flash the ISO to a USB Drive

The ISO is hybrid (**UEFI + Legacy BIOS**). Choose whichever tool fits your OS:

- **Ventoy (recommended for multi-boot USBs):** Install [Ventoy](https://www.ventoy.net/) onto a USB stick and copy the `.iso` file straight onto the Ventoy partition. If normal mode has issues on a specific machine, choose **Grub2 mode** in the Ventoy menu.
- **Rufus (Windows):** Select your USB drive and the `.iso` file, then click **START**. When prompted, choose **Write in DD Image mode** to preserve the hybrid UEFI/BIOS partition layout.
- **balenaEtcher (Windows / Linux / macOS):** Click **Flash from file**, pick the `.iso`, select your target USB drive, and click **Flash!**.
- **`dd` (Linux / macOS):** Verify the target device with `lsblk` (Linux) or `diskutil list` (macOS), then write to the whole disk (not a partition):
  ```bash
  sudo dd if=/tmp/rhm-live-build/live-image-amd64.hybrid.iso of=/dev/sdX bs=4M status=progress oflag=sync
  sync
  ```

---

### Step 3: Boot from USB (UEFI / BIOS, Secure Boot & GRUB Menu)

1. Insert the USB drive, power on the target PC, and open its boot menu (`F12`, `F8`, `F11`, `Esc`, or `Option` on Intel Macs).
2. **Secure Boot note:** The image includes `shim-signed` and Debian's signed kernel, so basic boot works with Secure Boot enabled. However, **unsigned out-of-tree DKMS kernel modules** — specifically `apfs-dkms` (Apple APFS), `zfs` (OpenZFS), and `broadcom-sta-dkms` (some Broadcom Wi-Fi cards) — will be blocked by the kernel while Secure Boot is on. Disable Secure Boot in UEFI/BIOS (or enroll a MOK key) if you need APFS, ZFS, or Broadcom Wi-Fi.
3. Choose an entry in the **GRUB boot menu**:
   - **`Remote-Harness LiveUSB (Load to RAM / toram, >= 4 GB RAM)`** *(default)* — Copies `filesystem.squashfs` completely into RAM. Fast, responsive, and lets you unplug the USB stick after boot.
   - **`Remote-Harness LiveUSB (Low RAM < 4 GB, boot from USB)`** — Reads SquashFS directly from the USB drive to conserve RAM. **Keep the USB stick plugged in** until shutdown.
   - **`Memtest86+`** — Standalone pre-boot memory hardware tester (works on both UEFI and Legacy BIOS).

---

### Step 4: Connect to the Internet

Once the Kiosk screen appears on `tty1`:
- **Ethernet:** Plug in a cable — DHCP connects automatically.
- **iPhone USB Tethering:** Enable **Personal Hotspot** on your iPhone, connect via USB, unlock the phone, and tap **Trust This Computer** (enter passcode). The Kiosk screen prompts you if pairing is still pending.
- **Android USB Tethering:** Connect via USB and toggle **USB Tethering** in Android network settings — DHCP connects automatically.
- **Wi-Fi:** Pick your network on the Kiosk screen (click **`Обновить список`** to rescan or **`Скрытая сеть`** to connect to a hidden SSID), and enter the password using your keyboard or the built-in on-screen keyboard.
- **Console fallback / local shell:** Press `Ctrl+Alt+F2` for `firstboot_tui.py`, where you can launch `nmtui` or open a local `bash` shell via **`[S] Shell (bash)`**.

---

### Step 5: Pair with Gemini, ChatGPT, or Claude

As soon as the LiveUSB is online, the Kiosk Ready screen displays a large **SVG QR code**, short pairing URL (`https://<gateway>/connect/<session_code>`), detected-disks summary, **`Сменить Wi-Fi`**, and **`Новый токен`**:

1. **Scan the QR code** with your phone or open the URL on another device.
2. **Copy the MCP URL or config** for your AI client:
   - **Gemini (Web / Mobile):** Copy the **`/sse`** URL (`https://<gateway>/sse?user=<live_node_id>&token=<lk_...>`) and add it as an MCP connector.
   - **ChatGPT:** Copy the **`/mcp`** URL (`https://<gateway>/mcp?user=<live_node_id>&token=<lk_...>`) and add it under Custom MCP Servers.
   - **Claude (Claude.ai Custom Connectors / Claude Desktop / Claude Code):** Copy the **`/mcp`** URL, the `claude mcp add --transport http ...` CLI command, or the one-click `mcpServers` JSON snippet shown on `/connect/<session_code>`.
3. **Start with a first prompt:**
   - *"Check `mesh_status` and run `rescue_info` (`summary`) to show me all disks, partitions, and detected operating systems."*
   - *"My Windows PC won't boot. Load the `windows-offline-boot-repair` skill and inspect the EFI partition and NTFS health read-only first."*
   - *"Check SMART health and kernel I/O errors (`dmesg_io`) on `/dev/nvme0n1`, then prepare a safe `ddrescue` plan to my external drive."*
   - *"Run `rescue_info` with `ram_info` and show all DIMM slots, ECC status, and available RAM."*

---

## Available MCP Tools & Use Cases

### LiveUSB-Specific Tools

| Tool | Purpose | Key Parameters / Capabilities |
| :--- | :--- | :--- |
| `rescue_info` | **Read-only** hardware, disk, OS, RAM, and driver diagnostics (never modifies disks). | `section`: `summary`, `all`, `disks`, `smart`, `partitions`, `raid_lvm`, `installed_os`, `boot_efi`, `win_artifacts`, `dmesg_io`, `ram_info`, `filesystems`; optional `device`, `timeout_sec`. |
| `rescue_action` | Controlled recovery actions with mandatory `dry_run=true` default preview. | `action`: `mount_safe`, `mount_auto`, `umount`, `fsck_check`, `ntfs_fix`, `grub_repair`, `win_sam_reset`, `ddrescue_plan`, `chroot_exec`, `ram_test_plan`, `ram_dump_plan`. Supports `writable` (default `false`), `fstype`, `encrypted`, `mapper_name`, `pool_name`, `recovery_key`, `target_image`, `output_path`, `method` (`kcore` works out of the box; `avml` is optional if installed separately). |
| `list_skills` | Progressive discovery of installed `SKILL.md` playbooks (returns compact metadata only). | Optional filters: `category` (e.g. `recovery`, `sysadmin`), `query`, `os` (`linux`, `windows`, `macos`). |
| `load_skill` | Loads full instructions for a skill or a specific file from its `references/` directory. | `name` (required), optional `reference_file` (path-traversal protected). |

### Core System, File & Background Job Tools

| Tool | Purpose |
| :--- | :--- |
| `mesh_status` | Verify node connectivity, active session, and reachability. |
| `system_info` | Host summary: OS, kernel, CPU, memory, disks, load, and top processes. |
| `system_vitals` | Real-time CPU, RAM, and disk utilization metrics. |
| `bash_exec` | Run shell commands directly on the LiveUSB host as `root`. |
| `read_file` / `write_file` / `edit_file` | Read line-numbered files, write files atomically, or apply exact string replacements. |
| `list_dir` / `glob_find` / `grep_search` | Browse directories, find files by glob pattern, and recursively search file contents. |
| `run_job` / `job_output` / `job_list` / `job_kill` | Run long tasks (`ddrescue`, `memtester`, `fsck`, `rsync`) in the background, stream logs, list, or stop them. |
| `node_control` | Inspect or adjust node settings (access mode, working directory, pause/resume). |

### Concrete Rescue Use Cases

- **Windows Unbootable / BitLocker Repair:** Detect Windows partitions and EFI entries via `rescue_info(boot_efi, win_artifacts)`, unlock BitLocker volumes with `dislocker` or `cryptsetup --type bitlk`, clear dirty NTFS flags (`ntfs_fix`), or edit offline Registry hives (`chntpw`, `libhivex-bin`) guided by `windows-offline-boot-repair`.
- **Linux GRUB / LUKS / LVM / RAID Chroot Rescue:** Inspect arrays with `rescue_info(raid_lvm)`, assemble `mdadm`, unlock LUKS, activate LVM volume groups, chroot via `chroot_exec`, and repair GRUB or rebuild `initramfs` guided by `linux-chroot-grub-luks-rescue`.
- **macOS APFS / HFS+ Data Extraction:** Mount unencrypted APFS read-only with `apfs-dkms`, unlock FileVault APFS volumes read-only with `apfs-fuse`, inspect damaged containers with `libfsapfs-utils`, or extract data from HFS+ (`hfsplus` / `libfshfs-utils`) guided by `macos-apfs-hfs-recovery`.
- **Failing Disk `ddrescue` Imaging:** Check `dmesg_io` and `smart` before mounting anything, mount damaged filesystems `ro,noload` via `mount_safe`, or generate a resumable `gddrescue` plan (`ddrescue_plan`) with a mapfile writing strictly to non-volatile external storage.
- **RAM & Hardware Diagnostics:** Inventory DIMM modules, ECC error counters (`edac-utils`), PCI/USB devices, and temperatures (`lm-sensors`); run live memory stress tests (`ram_test_plan` -> `memtester`), capture physical memory (`ram_dump_plan` -> `/proc/kcore` works out of the box; `avml` is not preinstalled and is optional if installed separately), or reboot into `Memtest86+` from GRUB.

---

## Security Model & Safety Guardrails

LiveUSBAgent-mcp is designed to be transparent about both its power and its built-in protections:

### 1. Full Root Access & Trust Boundary
- On a booted LiveUSB, `rhm-live-agent.service` runs as `root` with `MESH_SCENARIO=live_usb`, `MESH_ACCESS_MODE=full`, and `MESH_WORKSPACE=/` so the AI can immediately diagnose hardware, mount partitions, and repair operating systems without blocking on local approval prompts.
- **Physical/screen possession of the QR code and `lk_...` token is the trust boundary.** Treat the pairing URL and token on screen like a temporary root password — do not share screenshots of the Kiosk QR code publicly.

### 2. Ephemeral Credentials & Session Lifecycle
- Credentials are generated per boot in RAM (`/run/rhm-live/`) with a **24-hour TTL**.
- Clicking **`Новый токен`** (token regeneration) in the Kiosk UI or a graceful agent exit (`atexit` on clean shutdown/reboot) calls `/v1/liveusb/revoke` on the gateway to invalidate the active session and `lk_...` key immediately; a hard power-off falls back to the 24-hour TTL.
- The gateway enforces a `LIVEUSB_MAX_ACTIVE` cap on concurrent ephemeral LiveUSB sessions.
- The local UI state file (`session.json`) is written with strict **`0600`** permissions and intentionally **excludes `device_token`** so the tunnel credential is never exposed to the Kiosk frontend.

### 3. Storage & OS Safety Guards
- **No silent automounting:** `/etc/udev/rules.d/99-rhm-no-automount.rules` disables automatic mounting at boot so failing or dirty disks are never touched before inspection.
- **No automatic LVM/RAID activation:** `/etc/lvm/lvm.conf` (`auto_activation_volume_list = []`) and `/etc/mdadm/mdadm.conf` (`AUTO -all`) prevent the kernel from auto-assembling degraded host arrays on boot.
- **Dry-run & read-only defaults:** `rescue_action` defaults to `dry_run=True` (previewing exact commands before execution) and `writable=False` (`ro,noload` for `mount_safe`).
- **Mountpoint restrictions:** `rescue_action` restricts target mount directories to `/mnt/rescue/*` and `/mnt/scratch/*`.
- **Volatile RAM-disk write refusal:** Before creating disk images (`ddrescue_plan`) or RAM dumps (`ram_dump_plan`), `core/rescue.py` inspects `/proc/mounts` and refuses destinations on volatile `tmpfs`, `ramfs`, `overlay`, `squashfs`, or `devtmpfs` filesystems (including `/tmp`, `/run`, and `/var/tmp`) to prevent crashing a `toram` session via OOM. Also caps `journald` to `50M` volatile RAM (`Storage=volatile`, `SystemMaxUse=50M`).
- **APFS read-only policy:** `mount_auto` always mounts APFS read-only; encrypted FileVault volumes use read-only `apfs-fuse`. The experimental `linux-apfs-rw` write path is not exposed by `rescue_action` (a `writable=true` request is downgraded to `ro` with a warning), so the tool never writes to an APFS volume.

### 4. Network, UI & Skill Safety
- **Active uplink protection (`active_uplink_lock`):** Protects the active default-route interface carrying the WebSocket tunnel from accidental disconnection during network troubleshooting.
- **Hardened loopback-only Kiosk server:** `ops/live-usb/kiosk_server.py` binds strictly to `127.0.0.1:8787` (never exposed on LAN/WAN) and enforces loopback `Host`, `Origin`, and CSRF checks on mutating endpoints.
- **Hardened `/connect/<session_code>` pairing page:** Served by the gateway with non-wildcard CORS, a per-response CSP nonce, and `X-Frame-Options: DENY` to prevent clickjacking and cross-origin token theft.
- **Strict transport hardening:** Outbound agent-to-gateway HTTP calls enforce **TLS 1.2+** with HTTP redirects disabled.
- **Path-traversal rejection:** `load_skill` validates every `reference_file` path and rejects `..` traversal or absolute paths outside the skill's `references/` directory.
- **Offline skill security scanning:** External skills fetched by `ops/live-usb/fetch-skills.sh` are commit-SHA pinned in `ops/live-usb/skills-lock.json` and scanned offline with `SkillSpector` (`--recursive --no-llm`) against prompt injection and tool poisoning.

---

## Additional Documentation

- **LiveUSB ISO build & QEMU testing guide:** [ops/live-usb/README.md](ops/live-usb/README.md)
- **LiveUSB build, write and boot guide:** [docs/liveusb/README.md](docs/liveusb/README.md)
- **LiveUSB troubleshooting (no Wi-Fi, no tunnel, I/O errors, RAM OOM):** [docs/liveusb/TROUBLESHOOTING.md](docs/liveusb/TROUBLESHOOTING.md)
- **Russian README:** [README_RU.md](README_RU.md)
- **Architecture & development roadmap:** [ProjectPlan.md](ProjectPlan.md)
