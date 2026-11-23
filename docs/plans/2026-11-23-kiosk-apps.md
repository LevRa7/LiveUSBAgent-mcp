# Kiosk UX + app launcher plan (2026-11-23)

## Problem

The network screen shows Ethernet "Подключено" when the link is up, even if
there is no internet. The operator reads that as "connected" and expects the QR,
but the QR only exists after `session.ready`. The ready screen also lacks app
launching: only the terminal button exists, there is no file manager and no
application list.

## Requirements

1. **Separate link from internet.**
   - Interface cards keep link state, but the network screen gets a prominent
     internet banner:
     - no internet: «Интернет не обнаружен. Подключите Wi-Fi, кабель или USB-модем.»
     - internet present, session not ready: «Интернет есть. Получаем токен и QR…»
     - internet present and session ready: the ready view is shown.
   - The QR view is selected as soon as `internet.reachable` is true, even while
     the session is still pending; it shows a pending panel with instructions
     until `session.ready` and `connect_url` arrive.
   - If no internet and no ready session, the network view is selected (unless
     the operator explicitly chose the network view).
   - The ready view contains a short instruction: scan the QR or open the
     connect URL, then choose Gemini/ChatGPT/Claude on `/connect/...`.

2. **Terminal on both views.**
   - `Открыть терминал` is available on the network screen and on the ready
     screen.
   - It calls `POST /api/terminal`; no user-supplied command is accepted.

3. **Application launcher.**
   - `Приложения` button opens a modal with an allowlisted application list.
   - Required apps: **Файловый менеджер** (`pcmanfm`), **Терминал** (`xterm`),
     **Текстовый редактор** (`mousepad`), optionally browser.
   - Backend contract:
     - `GET /api/apps` → `{"ok": true, "apps": [{"id", "label", "available", "reason"}]}`
     - `POST /api/apps/launch` body `{"id": "files"}` →
       `{"ok": true, "pid": 123, "command": [...]}` or
       `{"ok": false, "message": "..."}`; 400 unknown id, 503 unavailable.
   - Launcher spawns a fixed argv with `shell=False`, `start_new_session=True`,
     `DISPLAY` from the environment, stdout/stderr `DEVNULL`.
   - `files` opens `/mnt/rescue` if it exists, otherwise `/root`.
   - No arbitrary command/path from the browser.

4. **ISO.**
   - Add `pcmanfm` and `mousepad` to the Kiosk GUI package list.
   - Add them to `build-iso.sh --check` required packages.
   - `start-kiosk.sh` creates `/mnt/rescue` before the kiosk server starts.
   - Update `docs/liveusb/STARTUP_UI.md` with new screenshots and behaviour.

5. **Tests.**
   - Backend: `/api/apps`, unknown id, unavailable app, Popen failure, no shell.
   - Frontend: initial network view, internet banner states, pending QR view,
     terminal buttons on both views, app modal and launch call, instructions.
   - ISO: packages present in package list and `--check`.
   - Visual: Chromium + Playwright click-through of network, Wi-Fi modal,
     hidden network, app modal, terminal, pending QR, ready QR, change Wi-Fi,
     new token; screenshots; then QEMU/VNC if the image renders.

## File ownership

| Area | Files |
| :--- | :--- |
| Backend | `ops/live-usb/kiosk_server.py`, `tests/test_kiosk_server.py` |
| Frontend | `ops/live-usb/kiosk_ui/index.html`, `app.js`, `styles.css`, new `tests/test_kiosk_ui.py` |
| ISO | `ops/live-usb/build-iso.sh`, `ops/live-usb/start-kiosk.sh`, `tests/test_liveusb_iso_pipeline.py` |
| Docs/visual | `docs/liveusb/STARTUP_UI.md`, `docs/liveusb/images/*`, `artifacts/*` |

Agents must not edit files outside their area. Do not commit; the controller
integrates and commits after all reports.
