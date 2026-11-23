# LiveUSB troubleshooting

Symptoms first. Every command here runs **on the booted LiveUSB** as `root`
unless it says otherwise. Build-side problems are covered in
[`README.md`](README.md); the long hardware guide is
[`ops/live-usb/README.md`](../../ops/live-usb/README.md).

Useful entry points:

| What | How |
| :--- | :--- |
| Kiosk UI | `tty1`, served on `http://127.0.0.1:8787` |
| Console dashboard | `Ctrl+Alt+F2` (`firstboot_tui.py`), with `nmtui` and `[S] Shell (bash)` |
| Agent service | `systemctl status rhm-live-agent`, `journalctl -u rhm-live-agent -b` |
| Runtime state (RAM only) | `/run/rhm-live/session.json`, `/run/rhm-live/agent.env` |

> `agent.env` and `session.json` are credentials: the token in `agent.env` is a
> 24-hour root-equivalent key. Do not paste either file into a chat or a bug
> report.

---

## No Wi-Fi

**Symptom:** the kiosk Wi-Fi list is empty after `Обновить список`, or
NetworkManager sees the card but every attempt fails.

```bash
nmcli device status                 # is the interface listed, and is it "unmanaged"?
rfkill list                         # soft/hard blocked?
ip link                             # does wlan0 exist at all?
lspci -k | grep -A3 -i network      # which driver is bound?
dmesg | grep -iE 'iwlwifi|ath|brcm|rtw|firmware' | tail -30
```

- **Hard blocked** (`rfkill` shows `Hard blocked: yes`): a physical switch or a
  firmware setting. Wi-Fi will not come up until it is enabled.
- **No interface at all:** the driver or its firmware is missing. The image
  ships `firmware-iwlwifi`, `firmware-atheros`, `firmware-realtek`,
  `firmware-brcm80211`, `firmware-mediatek`, `firmware-marvell-prestera`,
  `firmware-qcom-soc`, `firmware-ti-connectivity`, `firmware-libertas` and
  `firmware-zd1211`; a `dmesg` line like `Direct firmware load for ... failed`
  names the file that is not in that list. Plug in Ethernet or a phone instead
  and the session still works.
- **Unmanaged device:** `nmcli device set wlan0 managed yes`.
- **Some older Broadcom/MacBook adapters** need `broadcom-sta-dkms`, which is
  not preinstalled — use a USB Ethernet adapter or phone tethering for the
  rescue session.
- **Hidden network:** the kiosk has a `Скрытая сеть` (hidden SSID) control, so
  the network does not have to be broadcast.

## iPhone is not trusted (no USB tethering)

**Symptom:** the phone is plugged in, Personal Hotspot is on, but no new
interface appears and the kiosk still says there is no internet.

The iOS trust dialog is the gate: until the phone trusts the computer, `usbmuxd`
refuses to enumerate it and `ipheth` never creates the network interface.

1. Unlock the iPhone and keep it unlocked while plugging it in.
2. Tap **Trust This Computer** / **Доверять** on the phone and enter the passcode.
3. Confirm the pairing from the LiveUSB side:

```bash
idevicepair validate        # "SUCCESS: Valid pairing record"
ideviceinfo | head          # device identity, proves usbmuxd sees it
nmcli device status         # an ipheth interface should now be listed
systemctl status usbmuxd
```

- If `idevicepair validate` fails, run `idevicepair pair` and tap Trust again.
- If nothing happens at all: `systemctl restart usbmuxd`, unplug, replug.
- On iOS 16+, also check **Settings → Personal Hotspot → Allow Others to Join**
  and that the hotspot is actually on (the screen must show the blue banner).
- Android is simpler: enable USB tethering and NetworkManager picks up
  `rndis_host`/`cdc_ether`/`cdc_ncm` on its own.

## No tunnel (QR page or MCP link does not work)

**Symptom:** the booted LiveUSB has internet, but the `/connect/<code>` page
says the node is offline, or the agent service keeps restarting.

```bash
systemctl status rhm-live-agent
journalctl -u rhm-live-agent -b --no-pager | tail -50
cat /run/rhm-live/session.json            # connect_url, session_code, tunnel_status
curl -sS -o /dev/null -w '%{http_code}\n' https://<your-gateway>/health
getent hosts <your-gateway>
```

- **`does not resolve` in the log:** DNS is not up yet or the gateway name is
  wrong. Test with `getent hosts`, then `nmcli networking on`.
- **Registration fails with an empty base URL:** the gateway value carries a
  scheme the node refuses — see *Wrong gateway scheme* below.
- **401/403 from the gateway:** the session was revoked (the token was
  regenerated, or the 24-hour TTL expired). Press **`Новый токен`** in the kiosk
  to register a fresh one, then re-scan the QR code.
- **Session expired after a reboot:** expected. Credentials live in RAM
  (`/run/rhm-live/`), so a new boot is a new node and a new link.
- **Clock far in the past/future:** TLS validation fails and the tunnel dies.
  `timedatectl set-ntp true`, or set the time by hand.
- **Tunnel is up but the AI cannot call tools:** the gateway shows the node
  online while the client uses a stale link key. Re-copy the URL from the kiosk
  QR / `session.json` instead of an old chat message.

## dmesg I/O errors and processes stuck in D state

**Symptom:** commands touching a disk hang, `ls` in a mount never returns, and
`dmesg` fills with I/O errors. This is a **dying drive**, not a LiveUSB bug.

```bash
dmesg -T | grep -iE 'I/O error|medium error|critical medium|reset|ata[0-9]+.*failed' | tail -40
cat /proc/loadavg                       # a high load with no CPU use is the tell
ps -eo pid,stat,wchan:24,comm | awk '$2 ~ /D/'
smartctl -a /dev/sdX                    # SMART health, reallocated sectors, pending
```

- `rescue_info` section `dmesg_io` counts the active errors. `rescue_action`
  refuses `mount_safe` / `mount_auto` once the kernel logs **more than 10**
  I/O errors for the device:

  ```json
  {"error": "refusing to mount /dev/sdb2: 42 active kernel I/O errors; pass force=true to override"}
  ```

  Do not pass `force=true` on a drive you still hope to recover. Clone it first
  (`ddrescue_plan`, destination on a **real** disk, never `/tmp`), then work on
  the clone.
- **D state is unkillable.** A process in `D` is waiting inside the kernel for
  a request the device will never complete; `kill -9` does nothing. Unmount it
  lazily with `umount -l /mnt/rescue/<dir>` (or the `umount` action with
  `lazy=true`) so the rest of the session keeps working, and treat the device as
  read-only from then on.
- If kernel messages stop entirely and even the console freezes, power-cycle.
  Nothing is lost that was not already written to the stick or a target disk.
- **Writes to a failing drive make it worse.** Prefer `writable=false`
  (`ro,noload`), and only remount read-write after a successful clone.

## RAM exhaustion (OOM) in the `toram` mode

**Symptom:** the system boots, then applications are killed, the kiosk dies, or
the machine hard-freezes while copying a large file.

`toram` copies the whole `filesystem.squashfs` into RAM, so the RAM left for
your recovery work is `total − image size`.

```bash
free -m
dmesg -T | grep -iE 'out of memory|killed process|oom'
cat /run/rhm-live/session.json          # is the session still alive?
```

- Reboot and pick **“Remote-Harness LiveUSB (Low RAM < 4 GB, boot from USB)”**
  in GRUB: no `toram`, the squashfs is read from the stick. In that mode the
  stick must stay plugged in until shutdown.
- Never write images or dumps into `/tmp`, `/run` or `/var/tmp`: those are
  tmpfs. `rescue_action` already refuses volatile destinations, and `journald`
  is capped at 50 MB (`Storage=volatile`, `SystemMaxUse=50M`) to keep logs from
  eating the RAM the recovery needs.
- Attach the destination disk over USB/eSATA instead of copying through RAM,
  and run one heavy operation at a time (`ddrescue`, `memtester` and a browser
  together will not fit in 4 GB).
- A zram/swap-less LiveUSB cannot recover from OOM: once the killer fires, save
  what you can and reboot into the Low RAM entry.

## Secure Boot blocks APFS and ZFS

**Symptom:** the machine boots fine, but `modprobe apfs` / `zpool import` fail
and `dmesg` complains about an unsigned module. `rescue_info`'s filesystem
report shows `apfs`/`zfs` unavailable.

```bash
mokutil --sb-state                      # SecureBoot enabled / disabled
dmesg -T | grep -iE 'Loading of unsigned module|Key was rejected|unusable module'
modprobe zfs; echo "rc=$?"
```

The kernel refuses every out-of-tree module that is not signed by a key in the
firmware's trust store: `linux-apfs-rw` (`apfs-dkms`), `zfs` (`zfs-dkms`) and
`broadcom-sta-dkms`. The image's `shim-signed` + Debian kernel only cover
in-tree drivers.

Fix, in order of preference:

1. Disable Secure Boot in the UEFI firmware for this rescue session (it can be
   re-enabled afterwards).
2. Enrol a MOK key and sign the modules (`mokutil --import <key>.der`, then
   `modprobe` again). This needs the signing key on the LiveUSB, so it is a
   planned workflow, not an emergency one.

Everything else — NTFS/BitLocker, ext2/3/4, Btrfs, XFS, F2FS, HFS+, LVM, RAID,
LUKS, exFAT — works with Secure Boot enabled. See
[`README.md` section 4, “Secure Boot”](README.md#4-secure-boot).

## Wrong gateway scheme

**Symptom:** the agent logs a gateway error, registration never completes, and
the kiosk stays on “no tunnel” even though `curl https://<gateway>/health`
works.

`MESH_GATEWAY` is baked into the image at build time and read by
`rhm-live-agent.service`, the kiosk and `firstboot_tui.py`:

```bash
grep -n MESH_GATEWAY /etc/systemd/system/rhm-live-agent.service
journalctl -u rhm-live-agent -b --no-pager | grep -iE 'gateway|scheme|resolve'
```

Rules the node enforces (`core/liveusb_bootstrap.py`, `core/agent.py`):

- a **bare host** (`smart-server.online`) means `https://` for HTTP calls and
  `wss://` for the tunnel — the normal case;
- `https://host` is used as-is, `wss://host` is mapped to `https://host`;
- **`http://host` is refused for any non-loopback host**, and `ws://host` is
  translated to `http://host` and then refused too. The tunnel is TLS-only;
  plaintext is accepted only for `127.0.0.1` / `localhost` (local test
  gateway).

Fix by rebuilding with the right value, then re-test the ISO in QEMU:

```bash
sudo ops/live-usb/build-iso.sh --gateway https://your.gateway.example --build
```

A temporary change is possible on the booted system — edit the `Environment=`
lines in `/etc/systemd/system/rhm-live-agent.service` (and the kiosk's
environment), then `systemctl daemon-reload && systemctl restart rhm-live-agent`
— but it disappears on reboot, because the LiveUSB keeps no persistent state.

---

## Still stuck?

`rescue_info` with `section: "summary"` is the one call that answers “what does
the machine look like right now” (disks, SMART, dmesg I/O, partitions, installed
OS, UEFI boot). Include its JSON — with tokens removed — when asking for help,
and see [`README.md`](README.md) for the build and boot path.
