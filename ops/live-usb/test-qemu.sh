#!/usr/bin/env bash
# ops/live-usb/test-qemu.sh
# Automated QEMU/KVM smoke-test harness for verifying a built
# remote-harness-liveusb ISO before flashing to USB.

set -euo pipefail

WORK_DIR="${WORK_DIR:-/tmp/rhm-live-build}"
ISO_PATH=""
MODE="uefi"
RAM_MB="4096"
CPUS="2"
CREATE_FS_DISKS=0
HEADLESS=0
DRY_RUN=0
LOG_DIR="${LOG_DIR:-}"
TMP_DIR=""

usage() {
  cat << 'EOF'
Usage: ops/live-usb/test-qemu.sh [OPTIONS]

Automated QEMU/KVM smoke-test harness for remote-harness-liveusb ISOs.

Options:
  --iso <path>         Path to ISO image (auto-detects newest *.iso in WORK_DIR if omitted)
  --mode <uefi|bios>   Firmware boot mode (default: uefi)
  --ram <MB>           Guest memory in MiB (default: 4096)
  --cpus <N>           Number of vCPUs (default: 2)
  --create-fs-disks    Create small sparse virtual disks formatted with exfat, ntfs,
                       ext4, btrfs, xfs, and vfat (using available mkfs.* tools)
                       and attach them as virtio-blk drives
  --headless           Run without display and capture serial console output for CI
  --log-dir <dir>      Directory for timestamped boot logs (default: $WORK_DIR/qemu-logs)
  --dry-run            Print the exact QEMU argv and exit 0 (safe without QEMU)
  -h, --help           Show this help message

Environment:
  WORK_DIR             Build directory to search for *.iso (default: /tmp/rhm-live-build)
  LOG_DIR              Override default log directory
EOF
}

die() {
  echo "[test-qemu] ERROR: $*" >&2
  exit 1
}

warn() {
  echo "[test-qemu] WARN: $*" >&2
}

info() {
  echo "[test-qemu] $*" >&2
}

cleanup() {
  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    rm -rf "${TMP_DIR}"
  fi
}
trap cleanup EXIT

print_argv() {
  local first=1 arg
  for arg in "$@"; do
    if (( ! first )); then
      printf ' '
    fi
    first=0
    if [[ "${arg}" =~ ^[A-Za-z0-9_./:=,-]+$ ]]; then
      printf '%s' "${arg}"
    else
      printf '%q' "${arg}"
    fi
  done
  printf '\n'
}

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --iso)
      [[ $# -ge 2 ]] || die "--iso requires a path argument"
      ISO_PATH="$2"
      shift 2
      ;;
    --iso=*)
      ISO_PATH="${1#*=}"
      shift
      ;;
    --mode)
      [[ $# -ge 2 ]] || die "--mode requires 'uefi' or 'bios'"
      MODE="$2"
      shift 2
      ;;
    --mode=*)
      MODE="${1#*=}"
      shift
      ;;
    --ram)
      [[ $# -ge 2 ]] || die "--ram requires a value in MB"
      RAM_MB="$2"
      shift 2
      ;;
    --ram=*)
      RAM_MB="${1#*=}"
      shift
      ;;
    --cpus)
      [[ $# -ge 2 ]] || die "--cpus requires an integer value"
      CPUS="$2"
      shift 2
      ;;
    --cpus=*)
      CPUS="${1#*=}"
      shift
      ;;
    --log-dir)
      [[ $# -ge 2 ]] || die "--log-dir requires a directory path"
      LOG_DIR="$2"
      shift 2
      ;;
    --log-dir=*)
      LOG_DIR="${1#*=}"
      shift
      ;;
    --create-fs-disks)
      CREATE_FS_DISKS=1
      shift
      ;;
    --headless)
      HEADLESS=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1 (use --help for usage)"
      ;;
  esac
done

# Validate arguments
case "${MODE}" in
  uefi|bios) ;;
  *) die "Invalid --mode '${MODE}': expected 'uefi' or 'bios'" ;;
esac

if ! [[ "${RAM_MB}" =~ ^[0-9]+$ ]] || (( RAM_MB < 256 )); then
  die "Invalid --ram '${RAM_MB}': expected integer >= 256 (MB)"
fi

if ! [[ "${CPUS}" =~ ^[0-9]+$ ]] || (( CPUS < 1 )); then
  die "Invalid --cpus '${CPUS}': expected integer >= 1"
fi

# Auto-detect ISO if not explicitly provided
if [[ -z "${ISO_PATH}" ]]; then
  if [[ -d "${WORK_DIR}" ]]; then
    shopt -s nullglob
    iso_candidates=( "${WORK_DIR}"/*.iso )
    shopt -u nullglob
    if (( ${#iso_candidates[@]} > 0 )); then
      # Pick the most recently modified ISO
      ISO_PATH="$(ls -t -- "${iso_candidates[@]}" | head -n 1)"
      info "Auto-detected ISO: ${ISO_PATH}"
    fi
  fi

  if [[ -z "${ISO_PATH}" ]]; then
    if (( DRY_RUN )); then
      ISO_PATH="${WORK_DIR}/live-image-amd64.hybrid.iso"
      warn "No *.iso found in ${WORK_DIR}; using placeholder path for --dry-run: ${ISO_PATH}"
    else
      die "No *.iso found in WORK_DIR (${WORK_DIR}). Pass --iso <path> or build the ISO first."
    fi
  fi
elif (( ! DRY_RUN )) && [[ ! -f "${ISO_PATH}" ]]; then
  die "ISO file not found: ${ISO_PATH}"
fi

QEMU_BIN="qemu-system-x86_64"
if (( ! DRY_RUN )) && ! command -v "${QEMU_BIN}" >/dev/null 2>&1; then
  die "${QEMU_BIN} not found. Install QEMU (e.g. 'sudo apt install qemu-system-x86 ovmf')."
fi

# Prepare log file path
LOG_DIR="${LOG_DIR:-${WORK_DIR}/qemu-logs}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_DIR}/qemu-boot-${MODE}-${TIMESTAMP}.log"
if (( ! DRY_RUN )); then
  mkdir -p "${LOG_DIR}"
fi

# Create temporary directory when needed for OVMF_VARS copy or virtual FS disks
if (( ! DRY_RUN )) && { [[ "${MODE}" == "uefi" ]] || (( CREATE_FS_DISKS )); }; then
  TMP_DIR="$(mktemp -d -t rhm-qemu-XXXXXX)"
fi

# Locate OVMF firmware for UEFI mode across Debian, Ubuntu, and Arch Linux
OVMF_CODE=""
OVMF_VARS=""
OVMF_SINGLE=""

find_ovmf() {
  local -a pairs=(
    "/usr/share/OVMF/OVMF_CODE_4M.fd:/usr/share/OVMF/OVMF_VARS_4M.fd"
    "/usr/share/OVMF/OVMF_CODE.fd:/usr/share/OVMF/OVMF_VARS.fd"
    "/usr/share/edk2/x64/OVMF_CODE.4m.fd:/usr/share/edk2/x64/OVMF_VARS.4m.fd"
    "/usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd:/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd"
    "/usr/share/edk2-ovmf/x64/OVMF_CODE.fd:/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"
  )
  local entry code vars
  for entry in "${pairs[@]}"; do
    code="${entry%%:*}"
    vars="${entry#*:}"
    if [[ -f "${code}" && -f "${vars}" ]]; then
      OVMF_CODE="${code}"
      OVMF_VARS="${vars}"
      return 0
    fi
  done

  local -a singles=(
    "/usr/share/ovmf/OVMF.fd"
    "/usr/share/OVMF/OVMF.fd"
    "/usr/share/edk2/x64/OVMF.fd"
    "/usr/share/edk2-ovmf/x64/OVMF.fd"
  )
  local fw
  for fw in "${singles[@]}"; do
    if [[ -f "${fw}" ]]; then
      OVMF_SINGLE="${fw}"
      return 0
    fi
  done

  return 1
}

# Build QEMU command line
QEMU_ARGS=(
  "${QEMU_BIN}"
  -name "rhm-liveusb-${MODE}"
  -machine q35
  -smp "${CPUS}"
  -m "${RAM_MB}"
)

# Enable KVM if available on host
if [[ -r /dev/kvm && -w /dev/kvm ]]; then
  QEMU_ARGS+=( -enable-kvm -cpu host )
else
  if (( ! DRY_RUN )); then
    warn "/dev/kvm not accessible; falling back to TCG software emulation (slower)."
  fi
  QEMU_ARGS+=( -cpu max )
fi

# Configure firmware (UEFI vs BIOS)
if [[ "${MODE}" == "uefi" ]]; then
  if find_ovmf; then
    if [[ -n "${OVMF_CODE}" ]]; then
      if (( DRY_RUN )); then
        ovmf_vars_copy="/tmp/rhm-qemu-dryrun/OVMF_VARS.fd"
      else
        ovmf_vars_copy="${TMP_DIR}/OVMF_VARS.fd"
        cp "${OVMF_VARS}" "${ovmf_vars_copy}"
      fi
      QEMU_ARGS+=(
        -drive "if=pflash,format=raw,readonly=on,file=${OVMF_CODE}"
        -drive "if=pflash,format=raw,file=${ovmf_vars_copy}"
      )
    else
      QEMU_ARGS+=( -bios "${OVMF_SINGLE}" )
    fi
  else
    if (( DRY_RUN )); then
      warn "OVMF firmware not found on host; using Debian default paths for --dry-run."
      QEMU_ARGS+=(
        -drive "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd"
        -drive "if=pflash,format=raw,file=/tmp/rhm-qemu-dryrun/OVMF_VARS_4M.fd"
      )
    else
      die "OVMF firmware not found. Install 'ovmf' (Debian/Ubuntu) or 'edk2-ovmf' (Arch), or use --mode bios."
    fi
  fi
fi

# Attach ISO image
QEMU_ARGS+=(
  -cdrom "${ISO_PATH}"
  -boot order=d
)

# Attach user-mode networking
QEMU_ARGS+=(
  -netdev user,id=net0
  -device virtio-net-pci,netdev=net0
)

# Create and attach multi-FS sparse virtual disks if requested
format_fs_disk() {
  local fs="$1"
  local size="$2"
  local img="$3"

  truncate -s "${size}" "${img}"

  case "${fs}" in
    exfat)
      if command -v mkfs.exfat >/dev/null 2>&1; then
        mkfs.exfat -L RHM_EXFAT "${img}" >/dev/null 2>&1 || mkfs.exfat -n RHM_EXFAT "${img}" >/dev/null
      else
        return 127
      fi
      ;;
    ntfs)
      if command -v mkfs.ntfs >/dev/null 2>&1; then
        mkfs.ntfs -F -f -q -L RHM_NTFS "${img}" >/dev/null
      elif command -v mkntfs >/dev/null 2>&1; then
        mkntfs -F -f -q -L RHM_NTFS "${img}" >/dev/null
      else
        return 127
      fi
      ;;
    ext4)
      if command -v mkfs.ext4 >/dev/null 2>&1; then
        mkfs.ext4 -F -q -L RHM_EXT4 "${img}" >/dev/null
      else
        return 127
      fi
      ;;
    btrfs)
      if command -v mkfs.btrfs >/dev/null 2>&1; then
        mkfs.btrfs -f -q -L RHM_BTRFS "${img}" >/dev/null
      else
        return 127
      fi
      ;;
    xfs)
      if command -v mkfs.xfs >/dev/null 2>&1; then
        mkfs.xfs -f -q -L RHM_XFS "${img}" >/dev/null
      else
        return 127
      fi
      ;;
    vfat)
      if command -v mkfs.vfat >/dev/null 2>&1; then
        mkfs.vfat -F 32 -n RHM_VFAT "${img}" >/dev/null
      else
        return 127
      fi
      ;;
    *)
      return 1
      ;;
  esac
}

if (( CREATE_FS_DISKS )); then
  fs_specs=(
    "exfat:64M"
    "ntfs:64M"
    "ext4:64M"
    "btrfs:128M"
    "xfs:300M"
    "vfat:64M"
  )
  disk_dir="${TMP_DIR:-/tmp/rhm-qemu-dryrun}/fs-disks"
  if (( ! DRY_RUN )); then
    mkdir -p "${disk_dir}"
  fi

  attached_fs=0
  for spec in "${fs_specs[@]}"; do
    fs="${spec%%:*}"
    size="${spec#*:}"
    img="${disk_dir}/test-${fs}.img"
    fs_upper="$(printf '%s' "${fs}" | tr '[:lower:]' '[:upper:]')"

    if (( DRY_RUN )); then
      QEMU_ARGS+=(
        -drive "file=${img},format=raw,if=none,id=fs_${fs}"
        -device "virtio-blk-pci,drive=fs_${fs},serial=RHM_${fs_upper}"
      )
      attached_fs=$(( attached_fs + 1 ))
    else
      if format_fs_disk "${fs}" "${size}" "${img}"; then
        info "Created ${fs} test disk (${size}): ${img}"
        QEMU_ARGS+=(
          -drive "file=${img},format=raw,if=none,id=fs_${fs}"
          -device "virtio-blk-pci,drive=fs_${fs},serial=RHM_${fs_upper}"
        )
        attached_fs=$(( attached_fs + 1 ))
      else
        rc=$?
        rm -f "${img}"
        if (( rc == 127 )); then
          warn "Skipping ${fs} test disk: mkfs.${fs} not installed on host."
        else
          warn "Skipping ${fs} test disk: formatting failed (exit ${rc})."
        fi
      fi
    fi
  done

  if (( ! DRY_RUN && attached_fs == 0 )); then
    warn "No virtual FS test disks were created (no supported mkfs.* tools found)."
  fi
fi

# Configure display and serial console logging
if (( HEADLESS )); then
  QEMU_ARGS+=(
    -display none
    -monitor none
    -serial stdio
  )
else
  QEMU_ARGS+=(
    -vga std
    -serial "file:${LOG_FILE}"
  )
fi

# Dry-run: print exact QEMU argv and exit 0
if (( DRY_RUN )); then
  print_argv "${QEMU_ARGS[@]}"
  exit 0
fi

info "Starting QEMU (${MODE^^}, ${RAM_MB} MB RAM, ${CPUS} vCPU)"
info "ISO: ${ISO_PATH}"
info "Boot log: ${LOG_FILE}"

if (( HEADLESS )); then
  info "Headless mode active: streaming serial console to stdout and ${LOG_FILE} (Ctrl+C to stop)."
  "${QEMU_ARGS[@]}" 2>&1 | tee -a "${LOG_FILE}"
else
  "${QEMU_ARGS[@]}"
fi
