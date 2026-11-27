#!/usr/bin/env bash
# ops/live-usb/open-kiosk-browser.sh
# Autostarted inside the LXDE / X11 desktop session (/etc/xdg/autostart/rhm-kiosk-browser.desktop).
# Waits for kiosk_server.py on http://127.0.0.1:8787 and opens Chromium automatically
# for Wi-Fi setup and QR scanning.

set -u

KIOSK_HOST="127.0.0.1"
KIOSK_PORT="${RHM_KIOSK_PORT:-8787}"
KIOSK_URL="http://${KIOSK_HOST}:${KIOSK_PORT}"

# Allow root-owned kiosk_server.py to spawn GUI apps (xterm, gparted, xfe) on this X display.
if command -v xhost >/dev/null 2>&1; then
    xhost +SI:localuser:root >/dev/null 2>&1 || true
fi

# Prevent screen blanking / DPMS while waiting for Wi-Fi setup or QR scan.
if command -v xset >/dev/null 2>&1; then
    xset s off -dpms s noblank >/dev/null 2>&1 || true
fi

# Wait up to 30 seconds for kiosk_server.py to answer on localhost:8787.
for _ in $(seq 1 150); do
    if python3 -c "import urllib.request; urllib.request.urlopen('${KIOSK_URL}/api/status', timeout=1)" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

BROWSER_BIN=""
for candidate in chromium chromium-browser google-chrome surf epiphany-browser midori; do
    if command -v "${candidate}" >/dev/null 2>&1; then
        BROWSER_BIN="${candidate}"
        break
    fi
done

if [[ -z "${BROWSER_BIN}" ]]; then
    exit 0
fi

case "${BROWSER_BIN}" in
    chromium|chromium-browser|google-chrome)
        extra_flags=()
        if [[ "$(id -u)" == "0" ]]; then
            extra_flags+=(--no-sandbox --disable-dev-shm-usage)
        fi
        PROFILE_DIR="/tmp/rhm-kiosk-profile-$(id -u)"
        exec "${BROWSER_BIN}" \
            ${extra_flags[@]+"${extra_flags[@]}"} \
            --start-maximized \
            --no-first-run \
            --noerrdialogs \
            --disable-infobars \
            --disable-session-crashed-bubble \
            --disable-translate \
            --disable-gpu \
            --overscroll-history-navigation=0 \
            --user-data-dir="${PROFILE_DIR}" \
            "${KIOSK_URL}"
        ;;
    surf)
        exec surf -F "${KIOSK_URL}"
        ;;
    epiphany-browser)
        exec epiphany-browser --application-mode "${KIOSK_URL}"
        ;;
    midori)
        exec midori -e Fullscreen -a "${KIOSK_URL}"
        ;;
    *)
        exec "${BROWSER_BIN}" "${KIOSK_URL}"
        ;;
esac
