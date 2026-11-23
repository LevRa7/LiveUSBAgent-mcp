#!/usr/bin/env bash
# ops/live-usb/start-kiosk.sh
# Launches kiosk_server.py on 127.0.0.1:8787 and opens a fullscreen kiosk
# browser inside openbox, with automatic fallback to firstboot_tui.py.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$(cd "${SCRIPT_DIR}/../.." && pwd):${PYTHONPATH:-}"
KIOSK_HOST="127.0.0.1"
KIOSK_PORT="${RHM_KIOSK_PORT:-8787}"
KIOSK_URL="http://${KIOSK_HOST}:${KIOSK_PORT}"
SERVER_PID=""

cleanup() {
    if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
        kill "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

fallback_tui() {
    echo "[rhm-kiosk] Switching to console fallback (firstboot_tui.py)..." >&2
    exec python3 "${SCRIPT_DIR}/firstboot_tui.py"
}

find_browser() {
    local candidate
    for candidate in chromium chromium-browser google-chrome surf epiphany-browser midori; do
        if command -v "${candidate}" >/dev/null 2>&1; then
            echo "${candidate}"
            return 0
        fi
    done
    return 1
}

wait_for_server() {
    local attempts=30
    local i=0
    while (( i < attempts )); do
        if python3 -c "import urllib.request; urllib.request.urlopen('${KIOSK_URL}/api/status', timeout=1)" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.2
        i=$((i + 1))
    done
    return 1
}

launch_browser() {
    case "${BROWSER_BIN}" in
        chromium|chromium-browser|google-chrome)
            local -a extra_flags=()
            if [[ "$(id -u)" == "0" ]]; then
                extra_flags+=(--no-sandbox --disable-dev-shm-usage)
            fi
            exec "${BROWSER_BIN}" \
                ${extra_flags[@]+"${extra_flags[@]}"} \
                --kiosk \
                --incognito \
                --no-first-run \
                --noerrdialogs \
                --disable-infobars \
                --disable-session-crashed-bubble \
                --disable-translate \
                --disable-gpu \
                --overscroll-history-navigation=0 \
                --user-data-dir=/tmp/rhm-kiosk-profile \
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
}

if [[ "${1:-}" == "--inner-browser" ]]; then
    BROWSER_BIN="$(find_browser || true)"
    if [[ -z "${BROWSER_BIN}" ]]; then
        fallback_tui
    fi
    launch_browser || fallback_tui
    exit 0
fi

# 1. Start localhost kiosk HTTP server.
# The kiosk X session is :0. Export DISPLAY before the server starts so
# kiosk_server.py inherits it and «Открыть терминал» (POST /api/terminal) can
# spawn xterm on the local display. X11_WAS_RUNNING keeps the branch below on
# the pre-export value, so a fresh boot still goes through xinit as before.
X11_WAS_RUNNING=""
if [[ -n "${DISPLAY:-}" ]]; then
    X11_WAS_RUNNING="1"
fi
export DISPLAY="${DISPLAY:-:0}"

# The app launcher's «Файловый менеджер» entry opens /mnt/rescue when it exists
# and falls back to /root otherwise, so create the mount point before the kiosk
# server starts serving /api/apps. mkdir -p must never abort the kiosk: a
# read-only or already-mounted /mnt/rescue is fine either way.
mkdir -p /mnt/rescue 2>/dev/null || true

python3 "${SCRIPT_DIR}/kiosk_server.py" --host "${KIOSK_HOST}" --port "${KIOSK_PORT}" &
SERVER_PID=$!

if ! wait_for_server; then
    echo "[rhm-kiosk] kiosk_server.py did not respond on ${KIOSK_URL}" >&2
    fallback_tui
fi

# 2. Pick browser binary
BROWSER_BIN="$(find_browser || true)"
if [[ -z "${BROWSER_BIN}" ]]; then
    echo "[rhm-kiosk] No Chromium or WebKitGTK browser found." >&2
    fallback_tui
fi

# 3. Launch under existing X11 session or start xinit + openbox
if [[ -n "${X11_WAS_RUNNING}" ]]; then
    if command -v openbox >/dev/null 2>&1; then
        openbox &
    fi
    if command -v xset >/dev/null 2>&1; then
        xset s off -dpms s noblank 2>/dev/null || true
    fi
    launch_browser || fallback_tui
elif command -v xinit >/dev/null 2>&1 && command -v openbox >/dev/null 2>&1; then
    XINITRC_TMP="$(mktemp /tmp/rhm-xinitrc.XXXXXX)"
    cat > "${XINITRC_TMP}" <<EOF
#!/usr/bin/env bash
xset s off -dpms s noblank 2>/dev/null || true
openbox &
exec "${SCRIPT_DIR}/start-kiosk.sh" --inner-browser
EOF
    chmod +x "${XINITRC_TMP}"
    xinit "${XINITRC_TMP}" -- :0 vt1 || fallback_tui
    rm -f "${XINITRC_TMP}"
else
    echo "[rhm-kiosk] No DISPLAY and xinit/openbox unavailable." >&2
    fallback_tui
fi
