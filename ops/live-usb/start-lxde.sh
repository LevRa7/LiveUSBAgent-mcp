#!/usr/bin/env bash
# ops/live-usb/start-lxde.sh
# Launch a full lightweight LXDE desktop on VT2/:1 from the kiosk app launcher.
# The kiosk stays on VT1; the user goes back with Ctrl+Alt+F1.
set -u

LOG="/var/log/rhm-lxde.log"
if ! command -v startlxde >/dev/null 2>&1; then
    echo "[rhm-lxde] startlxde not found" >&2
    exit 1
fi

if [[ -S /tmp/.X11-unix/X1 ]]; then
    chvt 2 2>/dev/null || true
    exit 0
fi

{
    echo "[rhm-lxde] starting LXDE on :1 vt2 at $(date -Is)"
} >>"${LOG}" 2>&1

setsid xinit /usr/bin/startlxde -- :1 vt2 >>"${LOG}" 2>&1 &

for _ in $(seq 1 40); do
    if [[ -S /tmp/.X11-unix/X1 ]]; then
        break
    fi
    sleep 0.25
done

if [[ ! -S /tmp/.X11-unix/X1 ]]; then
    echo "[rhm-lxde] X server did not come up; see ${LOG}" >&2
    exit 1
fi

chvt 2 2>/dev/null || true
exit 0
