#!/usr/bin/env bash
# ops/live-usb/build-iso.sh
# Production-ready Debian 13 "Trixie" live-build ISO builder for Remote-Harness LiveUSB.
#
# Usage:
#   ./ops/live-usb/build-iso.sh [OPTIONS]
#
# Options:
#   --work-dir PATH          live-build working directory (default: ${WORK_DIR:-/tmp/rhm-live-build})
#   --distro NAME            Debian suite (default: trixie for amd64/arm64, bookworm for i386)
#   --arch ARCH              Target architecture: amd64|x64, arm64|aarch64, i386|x86 (default: amd64)
#   --profile MODE           Image profile: full (~1.7GB), compact (~850MB GUI), lite (~500MB TUI)
#   --tunnel MODE            Tunnel mode: cloudflare (default) or gateway
#   --gateway HOST           Optional mesh gateway domain (used only when --tunnel gateway)
#   --with-external-skills   Run ops/live-usb/fetch-skills.sh before copying skills/
#   --build                  Immediately run 'lb build' after configuring (requires root)
#   --check                  Validate repository assets and exit; needs neither root nor live-build
#   -h, --help               Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

WORK_DIR="${WORK_DIR:-/tmp/rhm-live-build}"
DISTRO="${DISTRO:-}"
DISTRO_EXPLICIT="0"
ARCH="${ARCH:-amd64}"
PROFILE="${PROFILE:-}"
MESH_LIVE_TUNNEL="${MESH_LIVE_TUNNEL:-cloudflare}"
MESH_GATEWAY="${MESH_GATEWAY:-}"
WITH_EXTERNAL_SKILLS="0"
RUN_BUILD="0"
RUN_CHECK="0"

usage() {
    sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# --- --check: validate the repository assets without root or live-build ------
# The builder below embeds the package list, the systemd unit and the GRUB menu
# as heredocs. --check reads those heredocs back out of this very script, so the
# thing that is validated is the thing that will be written into the image.
CHECK_TOTAL=0
CHECK_FAILED=0

check_pass() {
    CHECK_TOTAL=$((CHECK_TOTAL + 1))
    printf '  [PASS] %s\n' "$1"
}

check_fail() {
    CHECK_TOTAL=$((CHECK_TOTAL + 1))
    CHECK_FAILED=$((CHECK_FAILED + 1))
    printf '  [FAIL] %s\n' "$1"
    if [[ -n "${2:-}" ]]; then
        printf '         %s\n' "$2"
    fi
}

# Print the body of the first "cat <<EOF > <file>" heredoc whose line matches $1.
# Patterns are plain ERE without backslashes: awk -v eats backslash escapes.
heredoc_body() {
    awk -v pattern="$1" '
        $0 ~ pattern { inside = 1; next }
        inside && /^EOF$/ { exit }
        inside { print }
    ' "${BASH_SOURCE[0]}"
}

# Print the first Python 3 interpreter that actually runs (on Windows the
# python3/python aliases can exist as Store stubs that fail every command).
python_bin() {
    local candidate
    for candidate in python3 python py; do
        command -v "${candidate}" >/dev/null 2>&1 || continue
        if "${candidate}" -c 'import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)' >/dev/null 2>&1; then
            printf '%s' "${candidate}"
            return 0
        fi
    done
    return 1
}

# py_compile a source file without leaving bytecode anywhere in the repository.
python_compile_check() {
    local python
    python="$(python_bin)" || return 1
    "${python}" - "$1" <<'PYEOF'
import os
import py_compile
import sys
import tempfile

source = sys.argv[1]
handle, target = tempfile.mkstemp(suffix=".pyc")
os.close(handle)
try:
    py_compile.compile(source, cfile=target, doraise=True)
except py_compile.PyCompileError as exc:
    sys.stderr.write("%s\n" % exc)
    sys.exit(1)
finally:
    os.unlink(target)
PYEOF
}

check_assets() {
    local rel_path package token output
    local package_text unit_text grub_text hook_text
    local hook_rel="ops/live-usb/hooks/0100-build-apfs-and-drivers.hook.chroot"
    local -a required_files=(
        "core/agent.py"
        "core/server.py"
        "core/liveusb_tunnel.py"
        "core/rescue.py"
        "core/skills_loader.py"
        "skills/orchestrator.md"
        "ops/live-usb/kiosk_server.py"
        "ops/live-usb/kiosk_ui/index.html"
        "ops/live-usb/start-kiosk.sh"
        "ops/live-usb/start-lxde.sh"
        "ops/live-usb/open-files.sh"
        "ops/live-usb/open-kiosk-browser.sh"
        "ops/live-usb/firstboot_tui.py"
        "ops/live-usb/udev/99-rhm-network-tethering.rules"
        "ops/live-usb/udev/99-rhm-no-automount.rules"
        "ops/live-usb/hooks/0100-build-apfs-and-drivers.hook.chroot"
        "ops/live-usb/hooks/0200-rhm-kernel-symlinks.hook.binary"
        "ops/live-usb/hooks/0300-purge-build-deps.hook.chroot"
    )
    local -a shell_scripts=(
        "ops/live-usb/build-iso.sh"
        "ops/live-usb/start-kiosk.sh"
        "ops/live-usb/start-lxde.sh"
        "ops/live-usb/open-files.sh"
        "ops/live-usb/open-kiosk-browser.sh"
        "ops/live-usb/fetch-skills.sh"
        "ops/live-usb/test-qemu.sh"
        "ops/live-usb/hooks/0100-build-apfs-and-drivers.hook.chroot"
        "ops/live-usb/hooks/0200-rhm-kernel-symlinks.hook.binary"
        "ops/live-usb/hooks/0300-purge-build-deps.hook.chroot"
        "install.sh"
    )
    local -a python_sources=(
        "ops/live-usb/firstboot_tui.py"
        "ops/live-usb/kiosk_server.py"
    )
    local -a required_packages=(
        network-manager
        ntfs-3g
        exfatprogs
        gddrescue
        apfs-dkms
        chromium
        xterm
        xfe
        xdotool
        lxde
        kbd
        pcmanfm
        mousepad
        gparted
        gpart
        mtools
        ldmtool
        partclone
        restic
        clamav
        clamav-freshclam
        yara
        stress-ng
        fio
        s-tui
        python3-evtx
        mbr
        debootstrap
        memtest86+
        wimtools
        libbde-utils
        hfsprogs
        os-prober
        rsync
        wireless-regdb
        # Kiosk GUI «Настройки сети»: PPPoE, WireGuard and OpenVPN tooling
        ppp
        wireguard-tools
        openresolv
        openvpn
        network-manager-openvpn
        # AmneziaWG build deps: awg/awg-quick need libmnl + pkg-config, and the
        # kernel module build needs libelf. Debian 13 ships no amneziawg-tools
        # package, so both halves are compiled in the 0100 chroot hook.
        libmnl-dev
        libelf-dev
        pkg-config
    )
    # Markers the 0100 chroot hook must keep so awg/awg-quick and the unsigned
    # DKMS kernel module really reach the image.
    local -a amneziawg_hook_tokens=(
        "amneziawg-tools.git"
        "amneziawg-linux-kernel-module.git"
        "/usr/local/bin/awg"
        "/usr/local/bin/awg-quick"
        "dkms.conf"
        "dkms add"
        "dkms build"
        "dkms install"
        "Secure Boot"
    )
    local -a unit_tokens=(
        "core.server --liveusb --tunnel cloudflare"
        "MESH_SCENARIO=live_usb"
        "MESH_LIVE_TUNNEL="
    )
    local -a grub_tokens=(
        "toram"
        "Low RAM"
        "/live/vmlinuz"
        "/live/initrd.img"
    )

    echo "[check] Repository assets"
    for rel_path in "${required_files[@]}"; do
        if [[ -s "${REPO_ROOT}/${rel_path}" ]]; then
            check_pass "present: ${rel_path}"
        else
            check_fail "missing or empty: ${rel_path}"
        fi
    done

    echo "[check] Shell and Python syntax"
    for rel_path in "${shell_scripts[@]}"; do
        if [[ ! -f "${REPO_ROOT}/${rel_path}" ]]; then
            check_fail "bash -n ${rel_path} (file missing)"
            continue
        fi
        if output="$("${BASH:-bash}" -n "${REPO_ROOT}/${rel_path}" 2>&1)"; then
            check_pass "bash -n ${rel_path}"
        else
            check_fail "bash -n ${rel_path}" "${output}"
        fi
    done
    for rel_path in "${python_sources[@]}"; do
        if [[ ! -f "${REPO_ROOT}/${rel_path}" ]]; then
            check_fail "py_compile ${rel_path} (file missing)"
            continue
        fi
        if output="$(python_compile_check "${REPO_ROOT}/${rel_path}" 2>&1)"; then
            check_pass "py_compile ${rel_path}"
        else
            check_fail "py_compile ${rel_path}" "${output}"
        fi
    done

    echo "[check] Generated package list"
    package_text="$(heredoc_body '^cat <<EOF > config/package-lists/rhm-rescue.list.chroot$')"
    if [[ -z "${package_text}" ]]; then
        check_fail "package list heredoc not found in build-iso.sh"
    else
        check_pass "package list heredoc found ($(printf '%s\n' "${package_text}" | grep -cv -e '^[[:space:]]*$' -e '^[[:space:]]*#') packages)"
        for package in "${required_packages[@]}"; do
            if grep -qxF -e "${package}" <<<"${package_text}"; then
                check_pass "package: ${package}"
            else
                check_fail "package missing from config/package-lists/rhm-rescue.list.chroot: ${package}"
            fi
        done
    fi

    echo "[check] systemd unit (rhm-live-agent.service)"
    unit_text="$(heredoc_body '^cat <<EOF > config/includes.chroot/etc/systemd/system/rhm-live-agent.service$')"
    if [[ -z "${unit_text}" ]]; then
        check_fail "rhm-live-agent.service heredoc not found in build-iso.sh"
    else
        for token in "${unit_tokens[@]}"; do
            if grep -qF -e "${token}" <<<"${unit_text}"; then
                check_pass "unit contains: ${token}"
            else
                check_fail "unit is missing: ${token}"
            fi
        done
    fi

    echo "[check] GRUB menu (config/includes.binary/boot/grub/grub.cfg)"
    grub_text="$(heredoc_body "^cat <<'EOF' > config/includes.binary/boot/grub/grub.cfg$")"
    if [[ -z "${grub_text}" ]]; then
        check_fail "grub.cfg heredoc not found in build-iso.sh"
    else
        for token in "${grub_tokens[@]}"; do
            if grep -qF -e "${token}" <<<"${grub_text}"; then
                check_pass "grub menu contains: ${token}"
            else
                check_fail "grub menu is missing: ${token}"
            fi
        done
    fi

    echo "[check] AmneziaWG chroot hook (source-built awg/awg-quick + DKMS module)"
    if [[ ! -s "${REPO_ROOT}/${hook_rel}" ]]; then
        check_fail "0100 hook missing; cannot verify the AmneziaWG build"
    else
        hook_text="$(<"${REPO_ROOT}/${hook_rel}")"
        for token in "${amneziawg_hook_tokens[@]}"; do
            if grep -qF -e "${token}" <<<"${hook_text}"; then
                check_pass "0100 hook contains: ${token}"
            else
                check_fail "0100 hook is missing: ${token}"
            fi
        done
    fi

    # Debian 13 (trixie) has no amneziawg-tools package: the tools are built from
    # source in the hook. An apt entry would fail the whole live-build.
    if [[ -n "${package_text:-}" ]] && grep -qxF -e "amneziawg-tools" <<<"${package_text}"; then
        check_fail "package list declares amneziawg-tools, which Debian 13 does not ship"
    else
        check_pass "no amneziawg-tools apt package declared (built from source in the 0100 hook)"
    fi

    echo

    if [[ "${CHECK_FAILED}" -eq 0 ]]; then
        printf 'PASS: %d asset checks passed (root and live-build not required)\n' "${CHECK_TOTAL}"
        return 0
    fi
    printf 'FAIL: %d of %d asset checks failed\n' "${CHECK_FAILED}" "${CHECK_TOTAL}"
    return 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --work-dir)
            WORK_DIR="$2"
            shift 2
            ;;
        --distro)
            DISTRO="$2"
            DISTRO_EXPLICIT="1"
            shift 2
            ;;
        --arch)
            ARCH="$2"
            shift 2
            ;;
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --tunnel)
            MESH_LIVE_TUNNEL="$2"
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
        --check)
            RUN_CHECK="1"
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

# Normalize architecture aliases (x64->amd64, x86->i386, aarch64->arm64)
case "${ARCH}" in
    amd64|x64|x86_64) ARCH="amd64" ;;
    arm64|aarch64)    ARCH="arm64" ;;
    i386|x86|i686|386) ARCH="i386" ;;
    *)
        echo "ERROR: Unsupported --arch '${ARCH}'. Supported: amd64 (x64), arm64, i386 (x86)." >&2
        exit 2
        ;;
esac

# Resolve default distro per arch: Debian 13 (trixie) dropped the official i386 kernel,
# so 32-bit x86 builds automatically use Debian 12 (bookworm) with 686-pae.
if [[ -z "${DISTRO}" ]]; then
    if [[ "${ARCH}" == "i386" ]]; then
        DISTRO="bookworm"
    else
        DISTRO="trixie"
    fi
fi
if [[ "${ARCH}" == "i386" && "${DISTRO}" == "trixie" ]]; then
    echo "ERROR: Debian 13 (trixie) has no official i386 kernel. Use --distro bookworm --arch i386." >&2
    exit 2
fi

# Resolve default profile: full for 64-bit, lite (TUI) for 32-bit i386.
if [[ -z "${PROFILE}" ]]; then
    if [[ "${ARCH}" == "i386" ]]; then
        PROFILE="lite"
    else
        PROFILE="full"
    fi
fi
case "${PROFILE}" in
    full|compact|lite) ;;
    *)
        echo "ERROR: Unsupported --profile '${PROFILE}'. Supported: full, compact, lite." >&2
        exit 2
        ;;
esac

if [[ "${RUN_CHECK}" == "1" ]]; then
    # Validate before touching WORK_DIR or calling lb: a broken checkout must
    # fail here, on a developer machine, and not halfway through a build.
    if check_assets; then
        exit 0
    fi
    exit 1
fi

echo "[1/7] Preparing Debian ${DISTRO} (${ARCH}, profile=${PROFILE}) live-build workspace at ${WORK_DIR}..."
mkdir -p "${WORK_DIR}"
cd "${WORK_DIR}"

LB_BOOTLOADERS="grub-pc,grub-efi"
LB_MEMTEST="memtest86+"
LB_EXTRA_ARGS=()
if [[ "${ARCH}" == "arm64" ]]; then
    LB_BOOTLOADERS="grub-efi"
    LB_MEMTEST="none"
elif [[ "${ARCH}" == "i386" ]]; then
    LB_BOOTLOADERS="grub-pc"
    LB_EXTRA_ARGS+=(--linux-flavours "686-pae")
fi

lb config \
    --distribution "${DISTRO}" \
    --architectures "${ARCH}" \
    --archive-areas "main contrib non-free non-free-firmware" \
    --binary-images iso-hybrid \
    --bootloaders "${LB_BOOTLOADERS}" \
    --bootappend-live "boot=live components quiet toram MESH_SCENARIO=live_usb" \
    --debian-installer false \
    --iso-application "Remote-Harness LiveUSB Rescue" \
    --iso-volume "RHM_RESCUE" \
    --memtest "${LB_MEMTEST}" \
    ${LB_EXTRA_ARGS[@]+"${LB_EXTRA_ARGS[@]}"}

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
# AmneziaWG build deps: libmnl-dev/libmnl, libelf-dev (Debian's out-of-tree
# module builds expect it) and pkg-config (the tools Makefile probes it for the
# systemd unit dir). Debian 13 (trixie) has no amneziawg-tools / amneziawg-dkms
# package, so both halves are compiled in the 0100 chroot hook instead.
libmnl-dev
libelf-dev
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
openresolv
openssh-client
curl
ca-certificates

# Filesystems (exFAT, NTFS, Linux, macOS, RAID/LVM/LUKS)
exfatprogs
exfat-fuse
dosfstools
ntfs-3g
dislocker
libbde-utils
e2fsprogs
btrfs-progs
xfsprogs
f2fs-tools
zfsutils-linux
zfs-dkms
hfsplus
hfsprogs
libfshfs-utils
apfs-dkms
apfsprogs
libfsapfs-utils
lvm2
mdadm
cryptsetup
kpartx
squashfs-tools
fuse3
udftools
thin-provisioning-tools
keyutils
dmg2img
libvhdi-utils
libvmdk-utils
libqcow-utils
ewf-tools
fsarchiver
partclone
restic
ldmtool
ext4magic
extundelete

# OS/Data Recovery & RAM/Hardware Diagnostics
smartmontools
nvme-cli
parted
gdisk
gpart
mtools
gddrescue
testdisk
sleuthkit
chntpw
libhivex-bin
python3-evtx
wimtools
mbr
debootstrap
clamav
clamav-freshclam
yara
stress-ng
fio
s-tui
efibootmgr
efivar
grub-efi-${ARCH}-bin
grub-pc-bin
shim-signed
os-prober
systemd-boot-efi
memtester
memtest86+
dmidecode
lshw
edac-utils
pciutils
usbutils
lm-sensors
sysstat
rasdaemon
hdparm
sdparm
sg3-utils
lsscsi
i2c-tools
acpica-tools
cpuid
msr-tools
f3
tpm2-tools

# CLI, archives, transfer & console tooling
rsync
pv
wget
unzip
zip
7zip
zstd
cabextract
nano
file
less
htop
lsof
strace
ncdu
dnsutils
traceroute
cifs-utils
nfs-common
wireless-regdb

# CPU microcode (early-loaded from initramfs; needed for stable RAM/CPU testing)
intel-microcode
amd64-microcode

# Kiosk GUI + Runtime
xserver-xorg-core
xserver-xorg-video-all
xserver-xorg-input-all
x11-xserver-utils
xinit
openbox
xterm
xfe
xdotool
chromium
lxde
kbd
pcmanfm
mousepad
gparted
fonts-dejavu-core
qrencode
python3
python3-venv
python3-websockets
tmux
jq
# Network settings dialog: PPPoE, WireGuard and OpenVPN are driven through
# NetworkManager (nmcli con mod / nmcli connection import), never a shell.
# wireguard-tools is already listed in the Network block above; ppp provides
# the PPPoE plugin and openvpn + network-manager-openvpn the OpenVPN one.
# network-manager-openvpn-gnome is deliberately skipped: the kiosk imports .ovpn
# files with nmcli and never opens nm-connection-editor.
ppp
openvpn
network-manager-openvpn
EOF

# Apply architecture and profile filtering to config/package-lists/rhm-rescue.list.chroot
PKG_LIST="config/package-lists/rhm-rescue.list.chroot"
drop_pkg() {
    local pkg="$1"
    sed -i -E "/^[[:space:]]*${pkg}[[:space:]]*$/d" "${PKG_LIST}"
}
if [[ "${ARCH}" == "arm64" ]]; then
    for p in grub-pc-bin memtest86+ mbr intel-microcode amd64-microcode cpuid msr-tools; do
        drop_pkg "${p}"
    done
elif [[ "${ARCH}" == "i386" ]]; then
    sed -i "s/^linux-headers-i386$/linux-headers-686-pae/" "${PKG_LIST}"
    for p in zfsutils-linux zfs-dkms grub-efi-i386-bin shim-signed systemd-boot-efi chromium; do
        drop_pkg "${p}"
    done
fi
if [[ "${PROFILE}" == "compact" ]]; then
    for p in chromium lxde xfe pcmanfm mousepad gparted xserver-xorg-video-all \
             firmware-linux-nonfree firmware-misc-nonfree firmware-qcom-soc \
             firmware-marvell-prestera firmware-ti-connectivity; do
        drop_pkg "${p}"
    done
    {
        echo "surf"
        echo "xserver-xorg-video-fbdev"
        if [[ "${ARCH}" != "arm64" ]]; then
            echo "xserver-xorg-video-vesa"
        fi
    } >> "${PKG_LIST}"
elif [[ "${PROFILE}" == "lite" ]]; then
    for p in xserver-xorg-core xserver-xorg-video-all xserver-xorg-input-all \
             x11-xserver-utils xinit openbox xterm xfe xdotool chromium lxde \
             pcmanfm mousepad gparted fonts-dejavu-core zfsutils-linux zfs-dkms \
             clamav clamav-freshclam \
             firmware-linux-nonfree firmware-misc-nonfree firmware-qcom-soc \
             firmware-marvell-prestera firmware-ti-connectivity; do
        drop_pkg "${p}"
    done
fi
mkdir -p config/includes.chroot/etc
printf '%s\n' "${PROFILE}" > config/includes.chroot/etc/rhm-live-profile

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
mkdir -p config/includes.chroot/usr/local/bin
cp "${REPO_ROOT}/ops/live-usb/open-files.sh" config/includes.chroot/usr/local/bin/rhm-open-files
cp "${REPO_ROOT}/ops/live-usb/start-lxde.sh" config/includes.chroot/usr/local/bin/rhm-open-desktop
cp "${REPO_ROOT}/ops/live-usb/open-kiosk-browser.sh" config/includes.chroot/usr/local/bin/rhm-open-kiosk-browser
chmod 0755 "${CHROOT_OPT}/ops/live-usb/start-kiosk.sh" "${CHROOT_OPT}/ops/live-usb/firstboot_tui.py" config/includes.chroot/usr/local/bin/rhm-open-files config/includes.chroot/usr/local/bin/rhm-open-desktop config/includes.chroot/usr/local/bin/rhm-open-kiosk-browser

# XDG autostart entry so LXDE / lightdm automatically opens http://127.0.0.1:8787
# in Chromium immediately after desktop startup for Wi-Fi setup and QR scanning.
mkdir -p config/includes.chroot/etc/xdg/autostart
cat <<'EOF' > config/includes.chroot/etc/xdg/autostart/rhm-kiosk-browser.desktop
[Desktop Entry]
Type=Application
Name=Remote-Harness LiveUSB Kiosk
Comment=Open LiveUSB Wi-Fi and QR onboarding UI
Exec=/usr/local/bin/rhm-open-kiosk-browser
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

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
cp "${REPO_ROOT}/ops/live-usb/hooks/0200-rhm-kernel-symlinks.hook.binary" \
    config/hooks/live/0200-rhm-kernel-symlinks.hook.binary
cp "${REPO_ROOT}/ops/live-usb/hooks/0300-purge-build-deps.hook.chroot" \
    config/hooks/live/0300-purge-build-deps.hook.chroot
chmod 0755 config/hooks/live/0100-build-apfs-and-drivers.hook.chroot \
    config/hooks/live/0200-rhm-kernel-symlinks.hook.binary \
    config/hooks/live/0300-purge-build-deps.hook.chroot

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
Environment="MESH_LIVE_TUNNEL=${MESH_LIVE_TUNNEL}"
Environment="MESH_GATEWAY=${MESH_GATEWAY}"
Environment="MESH_LIVE_STATE_DIR=/run/rhm-live"
RuntimeDirectory=rhm-live
RuntimeDirectoryMode=0755
ExecStart=/usr/bin/python3 -m core.server --liveusb --tunnel cloudflare --port 8096
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# tty1 kiosk as a systemd service: live-config may create a default "user" and
# take over getty@tty1, which left the kiosk on a plain login prompt. The service
# owns tty1 directly, starts start-kiosk.sh as root, and masks getty@tty1.
cat <<EOF > config/includes.chroot/etc/systemd/system/rhm-live-kiosk.service
[Unit]
Description=Remote-Harness MCP Live USB Kiosk
After=systemd-user-sessions.service network-online.target
Wants=network-online.target
Conflicts=getty@tty1.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=/opt/remote-harness-mcp
Environment="PYTHONPATH=/opt/remote-harness-mcp"
Environment="MESH_SCENARIO=live_usb"
Environment="MESH_GATEWAY=${MESH_GATEWAY}"
Environment="MESH_LIVE_STATE_DIR=/run/rhm-live"
Environment="TERM=linux"
StandardInput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
ExecStart=/opt/remote-harness-mcp/ops/live-usb/start-kiosk.sh
Restart=on-failure
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

# Mask ZFS auto-import/mount/share services so LiveUSB never touches host ZFS pools at boot,
# and mask zfs-load-module.service to avoid boot-time [FAILED] (zfs loads on demand via modprobe zfs).
mkdir -p config/includes.chroot/etc/systemd/system
for zfs_unit in \
    zfs-import-cache.service \
    zfs-import-scan.service \
    zfs-mount.service \
    zfs-share.service \
    zfs-load-module.service; do
    ln -sf /dev/null "config/includes.chroot/etc/systemd/system/${zfs_unit}"
done

# Enable rhm-live-agent.service via systemd symlink (hook also enables NetworkManager & usbmuxd)
mkdir -p config/includes.chroot/etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/rhm-live-agent.service \
    config/includes.chroot/etc/systemd/system/multi-user.target.wants/rhm-live-agent.service
ln -sf /etc/systemd/system/rhm-live-kiosk.service \
    config/includes.chroot/etc/systemd/system/multi-user.target.wants/rhm-live-kiosk.service

# The kiosk service owns tty1; mask the autologin getty that live-config creates.
mkdir -p config/includes.chroot/etc/systemd/system
ln -sf /dev/null config/includes.chroot/etc/systemd/system/getty@tty1.service

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
        if [ -f /live/memtest.efi ]; then
            chainloader /live/memtest.efi
        elif [ -f /live/memtest86+x64.efi ]; then
            chainloader /live/memtest86+x64.efi
        elif [ -f /boot/memtest86+x64.efi ]; then
            chainloader /boot/memtest86+x64.efi
        else
            echo "Memtest86+ EFI binary not found."
            sleep 3
        fi
    else
        if [ -f /live/memtest.bin ]; then
            linux16 /live/memtest.bin
        elif [ -f /live/memtest ]; then
            linux16 /live/memtest
        else
            echo "Memtest86+ BIOS binary not found."
            sleep 3
        fi
    fi
}
EOF

echo "[7/7] LiveUSB ISO workspace configured at ${WORK_DIR}."
echo "      Distro=${DISTRO} Arch=${ARCH} Profile=${PROFILE} Tunnel=${MESH_LIVE_TUNNEL} Gateway=${MESH_GATEWAY:-none} ExternalSkills=${WITH_EXTERNAL_SKILLS}"

if [[ "${RUN_BUILD}" == "1" ]]; then
    echo "Starting 'lb build' in ${WORK_DIR}..."
    lb build
else
    echo "Run 'sudo lb build' inside ${WORK_DIR} on a Debian host to produce the ISO."
fi
