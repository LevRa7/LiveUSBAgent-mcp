#!/usr/bin/env bash
# ops/live-usb/build-iso.sh
# Production-ready Debian 13 "Trixie" live-build ISO builder for Remote-Harness LiveUSB.
#
# Usage:
#   ./ops/live-usb/build-iso.sh [OPTIONS]
#
# Options:
#   --work-dir PATH          live-build working directory (default: ${WORK_DIR:-/tmp/rhm-live-build})
#   --distro NAME            Debian suite (default: ${DISTRO:-trixie})
#   --arch ARCH              Target architecture (default: ${ARCH:-amd64})
#   --gateway HOST           Mesh gateway domain (default: ${MESH_GATEWAY:-smart-server.online})
#   --with-external-skills   Run ops/live-usb/fetch-skills.sh before copying skills/
#   --build                  Immediately run 'lb build' after configuring (requires root)
#   -h, --help               Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

WORK_DIR="${WORK_DIR:-/tmp/rhm-live-build}"
DISTRO="${DISTRO:-trixie}"
ARCH="${ARCH:-amd64}"
MESH_GATEWAY="${MESH_GATEWAY:-smart-server.online}"
WITH_EXTERNAL_SKILLS="0"
RUN_BUILD="0"

usage() {
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --work-dir)
            WORK_DIR="$2"
            shift 2
            ;;
        --distro)
            DISTRO="$2"
            shift 2
            ;;
        --arch)
            ARCH="$2"
            shift 2
            ;;
        --gateway)
            MESH_GATEWAY="$2"
            shift 2
            ;;
        --with-external-skills)
            WITH_EXTERNAL_SKILLS="1"
            shift
            ;;
        --build)
            RUN_BUILD="1"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

echo "[1/7] Preparing Debian ${DISTRO} (${ARCH}) live-build workspace at ${WORK_DIR}..."
mkdir -p "${WORK_DIR}"
cd "${WORK_DIR}"

lb config \
    --distribution "${DISTRO}" \
    --architectures "${ARCH}" \
    --archive-areas "main contrib non-free non-free-firmware" \
    --binary-images iso-hybrid \
    --bootloaders "grub-pc,grub-efi" \
    --bootappend-live "boot=live components quiet toram MESH_SCENARIO=live_usb" \
    --debian-installer false \
    --iso-application "Remote-Harness LiveUSB Rescue" \
    --iso-volume "RHM_RESCUE" \
    --memtest memtest86+

echo "[2/7] Writing complete package list (config/package-lists/rhm-rescue.list.chroot)..."
mkdir -p config/package-lists
cat <<EOF > config/package-lists/rhm-rescue.list.chroot
# Firmware & Kernel Headers / DKMS Build Toolchain
firmware-linux
firmware-linux-nonfree
firmware-misc-nonfree
firmware-iwlwifi
firmware-realtek
firmware-atheros
firmware-brcm80211
firmware-mediatek
firmware-marvell-prestera
firmware-qcom-soc
firmware-ti-connectivity
firmware-zd1211
firmware-libertas
linux-headers-${ARCH}
dkms
build-essential
cmake
git
pkg-config
libfuse3-dev
libbz2-dev
libattr1-dev
zlib1g-dev

# Network & iOS/Android USB Tethering
network-manager
wpasupplicant
iw
rfkill
ethtool
dnsmasq-base
usb-modeswitch
modemmanager
usbmuxd
libimobiledevice-utils
ipheth-utils
ifuse
wireguard-tools
openssh-client
curl
ca-certificates

# Filesystems (exFAT, NTFS, Linux, macOS, RAID/LVM/LUKS)
exfatprogs
exfat-fuse
dosfstools
ntfs-3g
dislocker
e2fsprogs
btrfs-progs
xfsprogs
f2fs-tools
zfsutils-linux
zfs-dkms
hfsplus
libfshfs-utils
apfs-dkms
apfsprogs
libfsapfs-utils
lvm2
mdadm
cryptsetup
dmraid
kpartx
squashfs-tools
fuse3
udftools

# OS/Data Recovery & RAM/Hardware Diagnostics
smartmontools
nvme-cli
parted
gdisk
gddrescue
testdisk
sleuthkit
chntpw
libhivex-bin
efibootmgr
grub-efi-${ARCH}-bin
grub-pc-bin
shim-signed
memtester
memtest86+
dmidecode
lshw
edac-utils
pciutils
usbutils
lm-sensors
sysstat

# Kiosk GUI + Runtime
xserver-xorg-core
xserver-xorg-video-all
xserver-xorg-input-all
x11-xserver-utils
xinit
openbox
chromium
fonts-dejavu-core
qrencode
python3
python3-venv
python3-websockets
tmux
jq
EOF

if [[ "${WITH_EXTERNAL_SKILLS}" == "1" ]]; then
    echo "[2b/7] Fetching external skills via ops/live-usb/fetch-skills.sh..."
    "${REPO_ROOT}/ops/live-usb/fetch-skills.sh"
fi

echo "[3/7] Injecting Remote-Harness MCP runtime, skills, and kiosk UI into chroot overlay..."
CHROOT_OPT="config/includes.chroot/opt/remote-harness-mcp"
mkdir -p "${CHROOT_OPT}/ops/live-usb"

rm -rf "${CHROOT_OPT}/core" "${CHROOT_OPT}/skills" "${CHROOT_OPT}/ops/live-usb/kiosk_ui"
cp -r "${REPO_ROOT}/core" "${CHROOT_OPT}/"
if [[ -d "${REPO_ROOT}/skills" ]]; then
    cp -r "${REPO_ROOT}/skills" "${CHROOT_OPT}/"
fi
cp "${REPO_ROOT}/ops/live-usb/kiosk_server.py" "${CHROOT_OPT}/ops/live-usb/"
cp -r "${REPO_ROOT}/ops/live-usb/kiosk_ui" "${CHROOT_OPT}/ops/live-usb/"
cp "${REPO_ROOT}/ops/live-usb/start-kiosk.sh" "${CHROOT_OPT}/ops/live-usb/"
cp "${REPO_ROOT}/ops/live-usb/firstboot_tui.py" "${CHROOT_OPT}/ops/live-usb/"
chmod 0755 "${CHROOT_OPT}/ops/live-usb/start-kiosk.sh" "${CHROOT_OPT}/ops/live-usb/firstboot_tui.py"

echo "[4/7] Installing udev rules and chroot build hooks..."
mkdir -p config/includes.chroot/etc/udev/rules.d
cp "${REPO_ROOT}/ops/live-usb/udev/99-rhm-network-tethering.rules" \
    config/includes.chroot/etc/udev/rules.d/99-rhm-network-tethering.rules
cp "${REPO_ROOT}/ops/live-usb/udev/99-rhm-no-automount.rules" \
    config/includes.chroot/etc/udev/rules.d/99-rhm-no-automount.rules
chmod 0644 \
    config/includes.chroot/etc/udev/rules.d/99-rhm-network-tethering.rules \
    config/includes.chroot/etc/udev/rules.d/99-rhm-no-automount.rules

mkdir -p config/hooks/live
cp "${REPO_ROOT}/ops/live-usb/hooks/0100-build-apfs-and-drivers.hook.chroot" \
    config/hooks/live/0100-build-apfs-and-drivers.hook.chroot
chmod 0755 config/hooks/live/0100-build-apfs-and-drivers.hook.chroot

echo "[5/7] Configuring systemd services, tty1 kiosk / tty2 TUI autologin, LVM/RAID guards, and journald..."
mkdir -p config/includes.chroot/etc/systemd/system
cat <<EOF > config/includes.chroot/etc/systemd/system/rhm-live-agent.service
[Unit]
Description=Remote-Harness MCP Live USB Rescue Agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=/opt/remote-harness-mcp
Environment="PYTHONPATH=/opt/remote-harness-mcp"
Environment="MESH_SCENARIO=live_usb"
Environment="MESH_ACCESS_MODE=full"
Environment="MESH_WORKSPACE=/"
Environment="MESH_GATEWAY=${MESH_GATEWAY}"
Environment="MESH_LIVE_STATE_DIR=/run/rhm-live"
RuntimeDirectory=rhm-live
RuntimeDirectoryMode=0755
ExecStart=/usr/bin/python3 -m core.agent --ephemeral-liveusb
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# Prevent auto-activation of dying host LVM volume groups and mdadm RAID arrays at boot
mkdir -p config/includes.chroot/etc/lvm config/includes.chroot/etc/mdadm
cat <<'EOF' > config/includes.chroot/etc/lvm/lvm.conf
# Remote-Harness LiveUSB: disable automatic LVM activation on host disks
activation {
    auto_activation_volume_list = []
}
EOF

cat <<'EOF' > config/includes.chroot/etc/mdadm/mdadm.conf
# Remote-Harness LiveUSB: disable automatic mdadm RAID assembly on host disks
AUTO -all
EOF

# Enable rhm-live-agent.service via systemd symlink (hook also enables NetworkManager & usbmuxd)
mkdir -p config/includes.chroot/etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/rhm-live-agent.service \
    config/includes.chroot/etc/systemd/system/multi-user.target.wants/rhm-live-agent.service

# tty1 autologin -> start-kiosk.sh (falls back to firstboot_tui.py if X11 unavailable)
mkdir -p config/includes.chroot/etc/systemd/system/getty@tty1.service.d
cat <<'EOF' > config/includes.chroot/etc/systemd/system/getty@tty1.service.d/autologin.conf
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I $TERM
EOF

# tty2 autologin -> firstboot_tui.py console dashboard
mkdir -p config/includes.chroot/etc/systemd/system/getty@tty2.service.d
cat <<EOF > config/includes.chroot/etc/systemd/system/getty@tty2.service.d/autologin.conf
[Service]
WorkingDirectory=/opt/remote-harness-mcp
Environment="PYTHONPATH=/opt/remote-harness-mcp"
Environment="MESH_SCENARIO=live_usb"
Environment="MESH_GATEWAY=${MESH_GATEWAY}"
Environment="MESH_LIVE_STATE_DIR=/run/rhm-live"
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I \$TERM
EOF

# Root login profile dispatching tty1 to Kiosk GUI and tty2 to Firstboot TUI
mkdir -p config/includes.chroot/root
cat <<EOF > config/includes.chroot/root/.bash_profile
# Remote-Harness LiveUSB tty dispatcher
export PYTHONPATH="/opt/remote-harness-mcp"
export MESH_SCENARIO="live_usb"
export MESH_GATEWAY="${MESH_GATEWAY}"
export MESH_LIVE_STATE_DIR="/run/rhm-live"

CURRENT_TTY="\$(tty 2>/dev/null || true)"
if [[ "\${CURRENT_TTY}" == "/dev/tty1" ]] && [[ -z "\${RHM_KIOSK_LAUNCHED:-}" ]]; then
    export RHM_KIOSK_LAUNCHED=1
    exec /opt/remote-harness-mcp/ops/live-usb/start-kiosk.sh
elif [[ "\${CURRENT_TTY}" == "/dev/tty2" ]] && [[ -z "\${RHM_TUI_LAUNCHED:-}" ]]; then
    export RHM_TUI_LAUNCHED=1
    /usr/bin/python3 /opt/remote-harness-mcp/ops/live-usb/firstboot_tui.py || true
fi

if [[ -f ~/.bashrc ]]; then
    # shellcheck disable=SC1090
    source ~/.bashrc
fi
EOF

# Volatile journald cap (50M in RAM)
mkdir -p config/includes.chroot/etc/systemd/journald.conf.d
cat <<'EOF' > config/includes.chroot/etc/systemd/journald.conf.d/volatile-limit.conf
[Journal]
Storage=volatile
SystemMaxUse=50M
EOF

echo "[6/7] Configuring dual GRUB boot menu (toram / Low RAM / Memtest86+)..."
mkdir -p config/includes.binary/boot/grub
cat <<'EOF' > config/includes.binary/boot/grub/grub.cfg
search --no-floppy --set=root --file /live/vmlinuz
set default=0
set timeout=5

insmod all_video
insmod gfxterm
terminal_output gfxterm

menuentry "Remote-Harness LiveUSB (Load to RAM / toram, >= 4 GB RAM)" {
    linux /live/vmlinuz boot=live components quiet toram MESH_SCENARIO=live_usb
    initrd /live/initrd.img
}

menuentry "Remote-Harness LiveUSB (Low RAM < 4 GB, boot from USB)" {
    linux /live/vmlinuz boot=live components quiet MESH_SCENARIO=live_usb
    initrd /live/initrd.img
}

menuentry "Memtest86+" {
    if [ "${grub_platform}" = "efi" ]; then
        if [ -f /live/memtest86+x64.efi ]; then
            chainloader /live/memtest86+x64.efi
        elif [ -f /boot/memtest86+x64.efi ]; then
            chainloader /boot/memtest86+x64.efi
        else
            echo "Memtest86+ EFI binary not found."
            sleep 3
        fi
    else
        linux16 /live/memtest
    fi
}
EOF

echo "[7/7] LiveUSB ISO workspace configured at ${WORK_DIR}."
echo "      Distro=${DISTRO} Arch=${ARCH} Gateway=${MESH_GATEWAY} ExternalSkills=${WITH_EXTERNAL_SKILLS}"

if [[ "${RUN_BUILD}" == "1" ]]; then
    echo "Starting 'lb build' in ${WORK_DIR}..."
    lb build
else
    echo "Run 'sudo lb build' inside ${WORK_DIR} on a Debian host to produce the ISO."
fi
