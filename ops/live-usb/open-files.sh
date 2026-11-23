#!/bin/bash
# ops/live-usb/open-files.sh
#
# Open the Xfe file manager on the kiosk display and dismiss its
# "Running Xfe as root!" warning automatically. Xfe has no documented
# command-line switch to suppress that modal, so the wrapper waits for the
# Warning window and sends Return once; the main window then appears normally.
#
# Usage: rhm-open-files [DIRECTORY]
set -u

export DISPLAY="${DISPLAY:-:0}"

target="${1:-/mnt/rescue}"
if [ ! -d "${target}" ]; then
    target="/root"
fi

xfe "${target}" &
pid=$!

# Give Xfe time to map the warning, then dismiss it. Keep this bounded so a
# missing xdotool or a changed dialog never leaves the launcher hanging.
i=0
while [ "${i}" -lt 30 ]; do
    if command -v xdotool >/dev/null 2>&1; then
        if xdotool search --name '^Warning$' >/dev/null 2>&1; then
            xdotool search --name '^Warning$' key Return >/dev/null 2>&1 || true
            break
        fi
    fi
    sleep 0.1
    i=$((i + 1))
done

wait "${pid}"
