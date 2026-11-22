---
name: ram-hardware-live-diagnostics
description: "Run full hardware inventory, storage SMART/NVMe health checks, safe ECC/RAM stress testing without OOMing LiveUSB tmpfs, and live RAM acquisition (avml, /proc/kcore, LiME) to external storage."
category: liveusb-diagnostics
tags: [liveusb, hardware, ram, diagnostics, smartctl, nvme, memtester, edac, memory-forensics, avml, lime]
os: any
risk_level: medium
---

# RAM, Hardware Diagnostics & Live Memory Acquisition (LiveUSB)

Use this runbook to audit host hardware, verify storage and ECC/RAM health, and perform forensically sound live RAM acquisition from a Linux LiveUSB.

## 1. Hardware & Thermal Inventory

Collect a baseline hardware snapshot before stress testing:

```bash
# Memory DIMMs, speed, ECC capability, slot population
dmidecode -t memory

# Full hardware tree (concise summary + full bus details)
lshw -short
lspci -vvv
lsusb -v

# CPU/board/NVMe temperatures and fan speeds
sensors
```

Check kernel hardware errors (MCE, PCIe AER, disk resets):
```bash
dmesg -T --level=err,crit,alert,emerg | tail -n 80
```

## 2. Storage Health (SATA/SAS & NVMe)

```bash
lsblk -d -o NAME,MODEL,SERIAL,SIZE,ROTA,TRAN

# SATA / SAS drives
smartctl -a /dev/sdX
# Check: Reallocated_Sector_Ct, Current_Pending_Sector, Offline_Uncorrectable, UDMA_CRC_Error_Count

# NVMe drives
nvme list
nvme smart-log /dev/nvme0
smartctl -a /dev/nvme0n1
# Check: critical_warning, media_errors, percentage_used, temperature
```

For non-destructive self-tests: `smartctl -t short /dev/sdX` (2 min) or `smartctl -t long /dev/sdX` (hours).

## 3. ECC & RAM Diagnostics (Without OOMing LiveUSB `tmpfs`)

### Check Hardware ECC Error Counters
```bash
edac-util -v
# If edac-util is absent, inspect sysfs directly:
grep -H . /sys/devices/system/edac/mc/mc*/{ce_count,ue_count} 2>/dev/null
```
Any non-zero `ue_count` (uncorrectable errors) or rapidly climbing `ce_count` indicates failing DIMMs.

### Safe `memtester` Sizing on LiveUSB
LiveUSB root filesystems run in RAM (`tmpfs`/`overlayfs`). Allocating all free RAM to `memtester` triggers the OOM killer and crashes the LiveUSB session.

1. Check available memory and `tmpfs` usage first:
   ```bash
   free -m
   df -h -t tmpfs
   ```
2. **Safe calculation:** Leave at least **25% of `MemAvailable` (minimum 1024 MB)** untouched for the kernel, LiveUSB `tmpfs` overlay, and active processes.
   ```bash
   AVAIL_MB=$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
   TEST_MB=$(( AVAIL_MB - (AVAIL_MB / 4) ))
   [ "$TEST_MB" -gt $(( AVAIL_MB - 1024 )) ] && TEST_MB=$(( AVAIL_MB - 1024 ))
   echo "Safe memtester allocation: ${TEST_MB}M (from ${AVAIL_MB}M available)"
   memtester "${TEST_MB}M" 1
   ```
3. For exhaustive full-RAM testing (including kernel-occupied pages), reboot into dedicated `memtest86+` from the LiveUSB bootloader menu.

## 4. Live RAM Acquisition (Forensic Memory Dump)

### STRICT REFUSAL RULE: Never Write Dumps to `/tmp` or `/run`
- **Hard gate:** `/tmp`, `/run`, and `/dev/shm` on a LiveUSB are RAM-backed (`tmpfs`). Writing a memory dump there causes an immediate out-of-memory crash **and** contaminates the memory image being acquired.
- **Required pre-check:** Verify an external physical block device (or remote network share/pipe) is mounted with free space strictly greater than total physical RAM (`MemTotal` + 10% overhead):
  ```bash
  free -h
  df -h /mnt/external
  # Verify target is NOT tmpfs/overlay/ramfs
  findmnt -no FSTYPE -T /mnt/external
  ```
  Refuse to run acquisition if `FSTYPE` is `tmpfs`, `ramfs`, or `overlay`, or if free space is insufficient.

### Acquisition Methods (in Order of Preference)

1. **`avml` (Microsoft Acquire Volatile Memory for Linux — Preferred, No Kernel Module Required):**
   ```bash
   avml --compress /mnt/external/memdump_$(hostname)_$(date -u +%Y%m%dT%H%M%SZ).lime
   ```
2. **LiME (Linux Memory Extractor — Kernel Module, When Loaded/Available):**
   ```bash
   insmod /path/to/lime-$(uname -r).ko \
     "path=/mnt/external/memdump_$(hostname)_$(date -u +%Y%m%dT%H%M%SZ).lime format=lime"
   ```
   (For zero-local-storage network capture: `path=tcp:4444 format=lime` and receive via `nc` on a forensic workstation.)
3. **`/proc/kcore` (Fallback — ELF Core Image of Kernel/Physical Memory):**
   ```bash
   # Verify /proc/kcore is readable (may be restricted by kernel lockdown)
   ls -lh /proc/kcore
   # Stream with dd or gdb/crash tooling to external storage only
   dd if=/proc/kcore of=/mnt/external/kcore_$(date -u +%Y%m%dT%H%M%SZ).elf bs=4M status=progress
   ```

### Post-Acquisition Verification
```bash
sha256sum /mnt/external/memdump_*.lime > /mnt/external/memdump.sha256
sync
```
