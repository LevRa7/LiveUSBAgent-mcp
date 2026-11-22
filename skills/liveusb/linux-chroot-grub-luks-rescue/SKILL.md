---
name: linux-chroot-grub-luks-rescue
description: "Recover unbootable Linux systems from a LiveUSB: read-only preflight, assemble mdadm RAID, unlock LUKS, activate LVM, mount root/Btrfs/ESP, bind-mount pseudo-filesystems, chroot, rebuild initramfs, and repair GRUB or systemd-boot."
category: liveusb-recovery
tags: [liveusb, linux, chroot, grub, systemd-boot, luks, lvm, mdadm, btrfs, initramfs]
os: linux
risk_level: high
---

# Linux Offline Chroot, LUKS & Bootloader Rescue (LiveUSB)

Use this runbook to mount an offline Linux installation (including mdadm RAID, LUKS encryption, LVM, and Btrfs subvolumes), enter a clean `chroot`, regenerate `initramfs`, and repair GRUB or `systemd-boot`.

## 1. Read-Only Preflight

Check hardware and disk health before assembling arrays or mounting filesystems:

```bash
dmesg -T --level=err,crit,alert,emerg | tail -n 50
lsblk -o NAME,SIZE,FSTYPE,UUID,PARTTYPENAME,MOUNTPOINTS
smartctl -H /dev/sdX          # or: smartctl -H /dev/nvme0n1
```

- **Stop if the drive shows I/O errors or failing SMART status:** Image the drive with `ddrescue` to external storage before attempting filesystem or bootloader writes.
- Confirm firmware mode: `[ -d /sys/firmware/efi ] && echo UEFI || echo BIOS`. Boot the LiveUSB in the **same mode** as the installed OS when repairing UEFI bootloaders.

## 2. Assemble Storage Stack (RAID -> LUKS -> LVM)

Run only the layers present in `lsblk`:

```bash
# 1. Assemble mdadm software RAID (if present)
mdadm --assemble --scan
cat /proc/mdstat

# 2. Unlock LUKS encrypted volume (if present)
cryptsetup open /dev/sdXn cryptroot

# 3. Activate LVM volume groups (if present)
vgscan
vgchange -ay
lvs
```

## 3. Mount Root, Subvolumes, `/boot`, and ESP

Target mountpoint: `/mnt/sysroot`.

### Standard ext4 / xfs Root
```bash
mkdir -p /mnt/sysroot
mount /dev/mapper/vg0-root /mnt/sysroot
```

### Btrfs Root with Subvolumes
Inspect subvolumes first so you mount the exact layout expected by `/etc/fstab`:
```bash
mount -o ro /dev/mapper/cryptroot /mnt/sysroot
btrfs subvolume list /mnt/sysroot
umount /mnt/sysroot

# Common layout (@, @home, @var, @snapshots) — adjust to match fstab
mount -o subvol=@ /dev/mapper/cryptroot /mnt/sysroot
mount -o subvol=@home /dev/mapper/cryptroot /mnt/sysroot/home
```

### Inspect `/etc/fstab` and Mount `/boot` & ESP
```bash
cat /mnt/sysroot/etc/fstab
# Mount separate /boot if listed in fstab
mount /dev/sdX2 /mnt/sysroot/boot
# Mount ESP at the path specified in fstab (commonly /boot/efi or /efi)
mount /dev/sdX1 /mnt/sysroot/boot/efi
```

## 4. Bind-Mount Pseudo-Filesystems & Enter `chroot`

```bash
for dir in /dev /dev/pts /proc /sys /run; do
    mount --bind "$dir" "/mnt/sysroot$dir"
done

# Required for UEFI NVRAM updates (efibootmgr / grub-install / bootctl)
if [ -d /sys/firmware/efi/efivars ]; then
    mount --bind /sys/firmware/efi/efivars /mnt/sysroot/sys/firmware/efi/efivars
fi

# Optional: enable DNS inside chroot if reinstalling packages
cp -L /etc/resolv.conf /mnt/sysroot/etc/resolv.conf

chroot /mnt/sysroot /bin/bash
```

Inside `chroot`, verify `uname -r`, `/etc/fstab`, and `/etc/crypttab` (ensure LUKS UUIDs match `blkid`).

## 5. Regenerate `initramfs`

Use the tool matching the target distribution:

| Distribution | Command |
|---|---|
| Debian / Ubuntu | `update-initramfs -u -k all` |
| Fedora / RHEL / Rocky / openSUSE | `dracut --regenerate-all --force` |
| Arch Linux / Manjaro | `mkinitcpio -P` |

## 6. Repair Bootloader

### Option A: GRUB (UEFI)
```bash
# Verify ESP mountpoint (/boot/efi or /efi)
grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=linux --recheck

# Regenerate grub.cfg (Debian/Ubuntu vs RHEL/Fedora/Arch)
if command -v update-grub >/dev/null; then
    update-grub
elif [ -d /boot/grub2 ]; then
    grub2-mkconfig -o /boot/grub2/grub.cfg
else
    grub-mkconfig -o /boot/grub/grub.cfg
fi
```

### Option B: GRUB (Legacy BIOS)
```bash
grub-install --target=i386-pc /dev/sdX   # whole disk, NOT a partition
update-grub || grub-mkconfig -o /boot/grub/grub.cfg
```

### Option C: `systemd-boot` (UEFI Only)
```bash
bootctl status
bootctl update || bootctl install
# Verify loader entries point to valid kernels/initrds in /boot or /efi
ls -la /boot/loader/entries/ /efi/loader/entries/ 2>/dev/null
```

Verify UEFI boot order after repair: `efibootmgr -v`.

## 7. Clean Teardown & Unmount

```bash
exit   # leave chroot
sync
umount -R /mnt/sysroot
vgchange -an 2>/dev/null || true
[ -e /dev/mapper/cryptroot ] && cryptsetup close cryptroot
```

Confirm nothing remains mounted under `/mnt/sysroot` (`findmnt /mnt/sysroot`) before rebooting.
