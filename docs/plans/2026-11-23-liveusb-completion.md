# LiveUSB completion plan (2026-11-23)

## Goal

Close the P0 gaps from `PROJECT_STATUS_2026-11-23.md` without regressing the
1,756 currently passing tests:

1. every section advertised by `rescue_info` returns real data (or an honest
   `available:false` with a platform reason);
2. every action advertised by `rescue_action` either executes or explicitly
   says it is a plan/interactive step — no more silent `{"status":"schema_ready"}`;
3. the full test suite is green;
4. release identity and the LiveUSB CI/asset checks are in place.

## Global constraints

- `core/` is standard-library only (Python 3.8+).
- Collectors never raise: on any failure return `{"available": False, "reason": ...}`.
- Actions never raise: return `{"error": ...}` or a JSON-serialisable result.
- `dry_run=True` is the default for every action that touches a disk or a guest OS.
- Never mount read-write by default. `ro,noload` / `ro,norecovery` first.
- Never write outside `/mnt/rescue/*` or `/mnt/scratch/*`.
- Every new collector/action needs unit tests that run on Windows with mocked
  subprocesses; Linux-only behaviour is marked `skipif`.
- Do not touch unrelated upstream tests just to make them pass; the two README
  tests are updated deliberately for the fork (Task 1).
- Commit after each task with a focused message. Do not push.

## Task 1 — Green the inherited suite

Files: `install.sh`, `install.ps1`, `core/policy.py`,
`tests/test_rename_compat.py`, `tests/test_version_single_source.py`.

1. Add `core/autoruns.py`, `core/liveusb_bootstrap.py`,
   `core/liveusb_connect_page.py`, `core/skills_loader.py` to the bootstrap
   lists in both installers (keep alphabetical/grouped order).
2. Add `list_skills` and `load_skill` to `core/policy.py::READ_ONLY_TOOLS`.
3. Update `test_readme_names_the_project` to accept `LiveUSBAgent-mcp`
   (the fork's first README line) while still rejecting the legacy names.
4. Update `test_the_docs_name_the_current_version`: a LiveUSB fork no longer
   ships `RemoteHarnessMCP-Setup-*.exe`; assert the README names the current
   version in a LiveUSB-relevant place instead (or skip the exe assertion with
   a comment explaining the fork). Do not weaken the version-single-source
   invariant.
5. Run `py -m pytest tests -q -p no:cacheprovider` and record the exact count.
   Expected: 0 failed (8 skipped allowed).

Acceptance: full suite green; both installers list every `core/**.py`;
`policy.READ_ONLY_TOOLS` matches the node guard.

## Task 2 — Read-only collectors

Files: `core/rescue.py`, `tests/test_rescue.py`.

Add, in the collector section, and wire into `handle_rescue_info`:

### `collect_partitions(device=None, timeout=8)`

- Windows: `{"available": False, "reason": "partitions requires Linux"}`.
- Invalid `device`: `available:false` with reason.
- Primary source: `sfdisk -J <device>`; parse
  `partitiontable.label`, `partitiontable.device`, `partitiontable.partitions`
  (`node`, `start`, `size`, `type`, `uuid`).
- Fallback: `lsblk -J -b -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT <device>`.
- Optional: `blkid -o export <device>` key=value map (best-effort).
- Return `{"available": true, "source": "sfdisk"|"lsblk", "device": ..., "label": ..., "partitions": [...]}`.

### `collect_raid_lvm(timeout=10)`

- Windows: unavailable.
- `lsblk -J -b -o NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT`; keep entries whose
  `TYPE` is `raid*`, `lvm`, or `crypt`.
- Best-effort `pvs`, `vgs`, `lvs` (`--noheadings -o ...`); skip missing binaries.
- Best-effort `cryptsetup status <name>` for crypt entries (never fail the whole
  collector if one device is inaccessible).
- Return `{"available": true, "source": "lsblk", "devices": [...], "lvm": {"pvs": [...], "vgs": [...], "lvs": [...]}, "crypt": {...}}`.

### `collect_installed_os(timeout=12)`

- Windows: unavailable.
- Build candidates from `lsblk -J` partitions with a filesystem type.
- Linux roots (`ext2/3/4`, `btrfs`, `xfs`, `f2fs`): mount each candidate
  `ro,noload` (fallback `ro,norecovery`, then `ro`) into a temporary directory
  under `/mnt/rescue/`, read `/etc/os-release` and `/etc/fstab` via
  `_read_file_text`, then `umount` in a `finally` block. Never leave a mount.
- Windows roots (`ntfs`): if `ntfsls` exists, probe
  `/Windows/System32/config/SOFTWARE` without mounting.
- `exfat`, `vfat`, `hfsplus`, `apfs`: report `probed` but mark
  `reason: "no os-release"` / `unsupported_without_mount` rather than failing.
- Return `{"available": true, "operating_systems": [{"device", "type", "name", "version", "evidence"}], "probed": N, "warnings": [...]}`.
- The collector must stop probing after the configured `timeout` budget and must
  never raise.

### `collect_boot_efi(timeout=8)`

- Windows or missing `/sys/firmware/efi`: `available:false`, reason
  `"system is not booted in UEFI mode"`.
- Parse `efibootmgr -v`: `BootCurrent`, `BootOrder`, `BootNNNN* Label\tDevicePath`.
- Return `{"available": true, "source": "efibootmgr", "boot_current", "boot_order", "entries": [...], "raw": "..."}`.

### `collect_win_artifacts(timeout=8)`

- Windows: unavailable.
- `lsblk -J`; for each NTFS/BitLocker partition, `ntfsls` (if present) probes
  `SAM`, `SYSTEM`, `SOFTWARE` under `Windows/System32/config`.
- BitLocker flag from `blkid -o value -s TYPE <device>` (`BitLocker`).
- No mounting. Return `{"available": true, "partitions": [{"device", "fstype", "bitlocker", "artifacts": {"SAM": bool, "SYSTEM": bool, "SOFTWARE": bool}}]}`.

### Dispatch

- `handle_rescue_info` maps each new section to its collector.
- `summary` adds `partitions`, `installed_os`, `boot_efi` (keep total bounded;
  pass `min(timeout, 5)`).
- `all` adds all five.
- Remove the false fallback text "requires a Linux Live USB environment" for
  implemented sections; the only remaining fallback must be for a genuinely
  unknown section.

### Tests

Use `linux_rescue_env` plus monkeypatched `_run_cmd` / `_read_file_text` /
`_which`; no real mounts. Cover:
- sfdisk JSON parse and lsblk fallback;
- raid/lvm parse;
- installed_os reads os-release and always calls umount;
- boot_efi absent and parsed;
- win_artifacts with/without ntfsls;
- dispatch for each new section returns the collector's shape;
- Windows returns `available:false` for every new collector.

## Task 3 — Actions

Files: `core/rescue.py`, `tests/test_rescue.py`.

### `umount`

- Params: `mountpoint` (or derive from `device`), `lazy`, `force`.
- Validate mountpoint (`/mnt/rescue/*` or `/mnt/scratch/*`).
- `dry_run`: return argv.
- Execute: refuse if `not os.path.ismount(mountpoint)`; run `umount [-l] [-f] <mp>`
  with timeout 15; return exit code/stdout/stderr.

### `fsck_check`

- Params: `device`, optional `fstype`.
- Validate device; detect fstype via `_detect_fstype`.
- Read-only checks only:
  - ext*: `e2fsck -n`
  - xfs: `xfs_repair -n`
  - btrfs: `btrfs check --readonly`
  - vfat: `fsck.vfat -n`
  - exfat: `fsck.exfat -n`
  - ntfs: `ntfsfix -n`
  - hfsplus: `fsck.hfsplus -n`
  - unknown: `fsck -n <device>`
- `dry_run`: return argv + `repair: false`.
- Execute with timeout 30; return `repair:false` and output.

### `win_sam_reset`

- Params: `device` or `mountpoint`, `sam_user`.
- This is inherently interactive. Implement:
  - validate a mounted Windows volume (or a `mountpoint` under `/mnt/rescue/`);
  - `dry_run` (default): return the exact `chntpw` argv and target user;
  - `dry_run=false`: return `{"status": "requires_interactive", "executed": false, "argv": [...], "message": "run chntpw interactively; no silent SAM write"}`.
- Do not claim success. Tests pin the honest `executed:false`.

### `chroot_exec`

- Params: `mountpoint`, `chroot_cmd` (string), optional `bind_mounts` (default true).
- Validate mountpoint; `shlex.split(chroot_cmd)`; reject empty.
- `dry_run`: return `planned_steps` (bind `/dev`, `/proc`, `/sys`, `/run`, then
  `chroot <mp> <argv>`, then unmount binds).
- Execute: require `os.path.ismount(mountpoint)`; bind-mount only paths that
  exist; run with timeout 60; always unmount binds in `finally`; return
  `step_results`, `exit_code`, stdout/stderr.

### `grub_repair`

- Keep the existing `planned_steps` for `dry_run`.
- For `dry_run=false`: create mountpoints, execute steps in order, stop on the
  first non-zero, always unmount bind mounts/EFI/root in `finally`, return
  `{"executed": true, "step_results": [...], "exit_code": ...}`.
- Never return `dry_run:false` without running.

### Mount I/O guard

- Add `_mount_io_guard(device, args)` using `collect_dmesg_io(device, 4)`.
- In `mount_safe` and `mount_auto`, before planning/executing, if
  `io_error_count > 10` and `args.get("force")` is not true, return
  `{"error": "refusing to mount ... active kernel I/O errors ... pass force=true"}`.
- Add `force: boolean` to the `rescue_action` schema description.

### Tests

- `umount` dry-run and execute via mock; invalid/not-mounted refusal.
- `fsck_check` argv per fstype and `repair:false`; invalid device.
- `win_sam_reset` plan + `executed:false`.
- `chroot_exec` dry-run steps; execute mock and unmount cleanup.
- `grub_repair` executes steps; stops on first failure; dry-run unchanged.
- dmesg guard refuses and `force=true` bypasses.
- All actions still refused under `MESH_READ_ONLY=1`.

## Task 4 — Release and LiveUSB checks

Files: `VERSION`, `package.json`, `core/version.py`,
`npm/gemini-computer-use/package.json`, `docs/releases/v0.7.0.md`,
`ops/live-usb/build-iso.sh`, `docs/liveusb/`,
`.github/workflows/liveusb-iso.yml`, `README.md`/`README_RU.md` only if needed.

1. Bump `0.6.0` → `0.7.0` in the four version sources and add
   `docs/releases/v0.7.0.md`. Keep `REPO_SLUG` unchanged unless every test and
   script that pins it is updated in the same commit.
2. Add `--check` to `build-iso.sh`: validate required repo files, hook, udev,
   systemd unit text, package list non-empty, and `bash -n` the hook; exit 0
   without root or `lb`.
3. Add `docs/liveusb/README.md` and `docs/liveusb/TROUBLESHOOTING.md` (short,
   practical: build, write, boot, no Wi-Fi, no tunnel, dmesg/D-state, RAM OOM).
4. Add `.github/workflows/liveusb-iso.yml`: on PR — pytest for the LiveUSB
   modules, `build-iso.sh --check`, `shellcheck` if available; manual/tag —
   optional ISO build job that is allowed to fail if no Debian runner is set up.
5. If cheap, add the missing «Открыть терминал» control to the Kiosk UI (a
   local shell escape is acceptable only if documented).

## Task 5 — Verification

- `py -m pytest tests -q -p no:cacheprovider` → 0 failed.
- `py -m pytest tests/test_rescue.py tests/test_liveusb_iso_pipeline.py -q`.
- `py -m compileall core gateway.py ops/live-usb`.
- `bash -n` on all `ops/live-usb/*.sh` and the hook.
- `bash ops/live-usb/build-iso.sh --check` under Git Bash (if it is portable)
  or document the Debian-only limitation.
- Update `PROJECT_STATUS_2026-11-23.md` with the new counts and remaining
  environment-only gaps (ISO/QEMU/hardware).
