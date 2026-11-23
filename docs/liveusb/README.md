# LiveUSB build, write and boot

The short path from a checkout to a booting LiveUSB: build the Debian 13 image,
write it to a stick, boot it, and smoke-test it in QEMU before touching real
hardware. The long, step-by-step Russian guide with every flag and package is
[`ops/live-usb/README.md`](../../ops/live-usb/README.md); symptoms and fixes are
in [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

---

## 1. Build prerequisites

The image is built **on Debian 13 (Trixie)**, on a host or in a VM. `live-build`
debootstrap the target suite, so another distribution (or an older Debian) is
not supported.

- root (or `sudo`), about **15 GB free** in the working directory
  (`/tmp/rhm-live-build` by default; override with `WORK_DIR` or `--work-dir`),
- working network access — debootstrap fetches the whole base system,
- optionally `qemu-system-x86` + `ovmf` for the smoke test in section 5.

```bash
sudo apt-get update
sudo apt-get install -y \
  live-build debootstrap squashfs-tools xorriso \
  grub-efi-amd64-bin grub-pc-bin mtools dosfstools \
  curl jq git ca-certificates
```

Check the checkout **before** spending 15–40 minutes on a build. This needs
neither root nor `live-build` — but it is not tool-free either: the
`py_compile` checks need a working Python 3 on `PATH` (`python3`, `python` or
`py`):

```bash
bash ops/live-usb/build-iso.sh --check
```

It verifies that every file the image needs exists (`core/agent.py`,
`core/rescue.py`, `core/skills_loader.py`, the kiosk server and UI,
`start-kiosk.sh`, `firstboot_tui.py`, both udev rules, the APFS/driver chroot
hook, `skills/orchestrator.md`), that the shell scripts and Python sources
parse, and that the generated package list, systemd unit and GRUB menu still
carry the required entries. It prints one `PASS`/`FAIL` line per check and
exits `0` only when all of them pass — a broken checkout fails here instead of
halfway through a build.

## 2. Build the image

```bash
# 1. Prepare the live-build workspace only (inspect it, then build by hand)
sudo ops/live-usb/build-iso.sh

# 2. Prepare and build in one go
sudo ops/live-usb/build-iso.sh --build

# 3. Include the pinned external skills from ops/live-usb/skills-manifest.json
sudo ops/live-usb/build-iso.sh --with-external-skills --build
```

Useful flags: `--work-dir PATH`, `--distro NAME`, `--arch ARCH` and
`--gateway HOST`. `--gateway` takes a bare host (`racknerd-5a24bf9.merino-carob.ts.net`) or an
`https://` URL; plaintext `http://` is refused for anything but loopback
because the tunnel is TLS-only.

The hybrid image lands in the working directory:

```text
${WORK_DIR:-/tmp/rhm-live-build}/live-image-amd64.hybrid.iso
```

## 3. Write the USB stick

The image is hybrid: it boots on UEFI and on Legacy BIOS. Any of the usual
tools works — verify the device name first, and never point it at a disk that
holds data.

```bash
lsblk                       # find the stick, e.g. /dev/sdb (not /dev/sdb1)
sudo dd if=/tmp/rhm-live-build/live-image-amd64.hybrid.iso of=/dev/sdb \
  bs=4M status=progress oflag=sync
sync
```

- **Rufus (Windows):** pick the ISO, and when asked choose **DD Image**, not
  ISO Image — that keeps the hybrid partition table intact.
- **Ventoy:** copy the `.iso` to the Ventoy partition and select it at boot
  (use *Grub2 mode* if the normal mode stalls).
- **balenaEtcher:** *Flash from file* → the ISO → *Select target* → *Flash*.

## 4. Secure Boot

The image ships `shim-signed` and a signed Debian kernel, so it boots with
Secure Boot enabled. The **out-of-tree DKMS modules are not signed** and are
refused by the kernel while Secure Boot is on:

| Module | What it enables |
| :--- | :--- |
| `linux-apfs-rw` (`apfs-dkms`) | reading (and experimental writing) Apple APFS |
| `zfs` (`zfs-dkms`) | importing OpenZFS pools |
| `broadcom-sta-dkms` | some older Broadcom / MacBook Wi-Fi adapters |

If the machine needs APFS, ZFS or those Broadcom adapters, disable Secure Boot
in the firmware settings or enrol the modules with your own MOK key. Everything
else (NTFS, BitLocker via `dislocker`, ext4/Btrfs/XFS/F2FS, HFS+, LVM, RAID,
LUKS) works with Secure Boot on. See also
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md#secure-boot-blocks-apfs-and-zfs).

## 5. QEMU smoke test

Test the ISO in a VM before writing it, and test the EFI and BIOS paths
separately:

```bash
# Print the exact qemu command line without running it (works without QEMU)
ops/live-usb/test-qemu.sh --dry-run --mode uefi

# UEFI boot with virtual exFAT/NTFS/ext4/Btrfs/XFS/vFAT test disks attached
ops/live-usb/test-qemu.sh \
  --iso /tmp/rhm-live-build/live-image-amd64.hybrid.iso \
  --mode uefi --create-fs-disks

# Legacy BIOS and the low-RAM path
ops/live-usb/test-qemu.sh --mode bios --ram 2048

# Headless: serial console only, suitable for CI logs
ops/live-usb/test-qemu.sh --headless --create-fs-disks
```

What a good boot looks like: GRUB shows the `toram`, `Low RAM` and
`Memtest86+` entries; `tty1` reaches the kiosk screen; `Ctrl+Alt+F2` gives the
console dashboard; a `rescue_info` call returns real disks. On the kiosk ready
view, «Открыть терминал» launches `xterm` on the local X display (`:0`, shipped
with the image); if it reports unavailable, the console fallback is
`Ctrl+Alt+F2` → `[S] Shell`. The QEMU stdout is logged to `qemu-boot-*.log` in
the current directory.

## 6. CI

[`.github/workflows/liveusb-iso.yml`](../../.github/workflows/liveusb-iso.yml)
runs the LiveUSB pytest modules and `build-iso.sh --check` on every pull
request (plus `shellcheck` when it is installed). The full ISO build runs only
on manual dispatch or a tag, in a `debian:trixie` container, and is allowed to
fail without failing the workflow: it is an artifact producer, not a gate.

---

## See also

- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — no Wi-Fi, iPhone trust, no
  tunnel, I/O errors, RAM OOM, Secure Boot, wrong gateway scheme.
- [`STARTUP_UI.md`](STARTUP_UI.md) — the network screen (no internet) and the
  QR/token screen (registered session), with rendered screenshots.
- [`ops/live-usb/README.md`](../../ops/live-usb/README.md) — the detailed build,
  test and hardware guide.
- [`docs/releases/v0.1.0.md`](../releases/v0.1.0.md) — what changed in this
  release.
- [`README.md`](../../README.md) — what the product is and how onboarding works.
