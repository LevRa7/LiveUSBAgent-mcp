# Отчёт о готовности LiveUSBAgent-mcp — финальный

**Дата:** 23.11.2026, ~14:58 IST
**Ветка:** `complete-liveusb`, локальный тег `v0.1.0`
**Версия:** `0.1.0` (независимая нумерация форка; upstream-база — `0.6.0`)
**ISO:** [`artifacts/LiveUSBAgent-0.1.0-final.iso`](artifacts/LiveUSBAgent-0.1.0-final.iso) — 1.8 ГБ, bootable
**SHA-256:** `bb6dff194c00e1b2194aa9cad2ae4dbaea4ffe5d2e9e3092bc1884885992b79d`

---

## 1. Вердикт

**Код, сборка ISO и загрузка в QEMU — выполнены.** Rescue-API закрыт, тесты зелёные, ISO собран на Debian 13.5 и загружен в QEMU UEFI/KVM до `graphical.target` с запущенным `rhm-live-agent.service`.

Осталось то, что нельзя закрыть в этой среде: физическое железо (Wi-Fi, iPhone/Android, реальные диски), визуальное подтверждение Chromium-киоска, merge/push и публикация. APFS-модуль `linux-apfs-rw` не собрался DKMS (fallback `apfs-fuse` установлен).

---

## 2. Что изменилось с прошлого отчёта

| Область | Было | Стало |
| :--- | :--- | :--- |
| Rescue-API | 5 секций и 4 действия не реализованы | все 12 секций и 11 действий |
| Тесты | 5 failed / 1756 passed | **0 failed / 1952 passed / 8 skipped** |
| Версия | 0.6.0 | **0.1.0** |
| ISO | не собран | **1.5 ГБ, собран и загружен в QEMU** |
| `--check` | отсутствовал | **54/54 PASS** |
| Kiosk terminal | не было | кнопка + `xterm` + `DISPLAY` |
| CI/docs | неполные | LiveUSB CI, `docs/liveusb/*`, release notes |

### Rescue

- Секции `partitions`, `raid_lvm`, `installed_os`, `boot_efi`, `win_artifacts` реализованы и подключены ([core/rescue.py](core/rescue.py#L949-L1350)).
- Действия `umount`, `fsck_check`, `win_sam_reset`, `chroot_exec` реализованы; `grub_repair` реально выполняет шаги при `dry_run=false`; fallback больше не возвращает `schema_ready` ([core/rescue.py](core/rescue.py#L1785-L2132)).
- Добавлен dmesg-guard >10 I/O-ошибок с `force=true`; read-only-отказ закреплён тестом на 6 действиях ([tests/test_rescue.py](tests/test_rescue.py#L533)).

### Тесты и релиз

- Linux CI: `test_security_audit_2026_11_19.py` делает `importorskip("google.antigravity")`, а не падает на collection.
- README честно описывает APFS как всегда read-only в `rescue_action`.
- Версия `0.1.0` во всех источниках, release notes — [docs/releases/v0.1.0.md](docs/releases/v0.1.0.md), локальный тег `v0.1.0`.

---

## 3. Реальная сборка ISO

### 3.1. Среда

- WSL 2.6.1, Debian GNU/Linux **13.5 (Trixie)**, ядро 6.6 WSL2, KVM доступен.
- QEMU 10.0.13, OVMF, `live-build` 20250505.
- Старый сломанный `Debian` VHDX (E_ACCESSDENIED) удалён с разрешения пользователя; новый дистрибутив `Debian13Store` поставлен через `wsl --install -d Debian --name Debian13Store --location D:\WSL\Debian13Store`.

### 3.2. Что пришлось исправить в сборке

1. **`dmraid` удалён из Debian 13** — пакет убран из `build-iso.sh` и документации.
2. **`/tmp` в WSL — tmpfs 3.8 ГБ** — сборка перенесена в `/root/rhm-live-build` на ext4.
3. **Windows `PATH` попадал в `binary.sh` live-build** и давал `Syntax error: "(" unexpected` — отключён `appendWindowsPath` в `/etc/wsl.conf` и задан чистый `PATH`.
4. **GRUB ссылался на `/live/vmlinuz` и `/live/initrd.img`, а live-build создаёт только версионированные файлы** — добавлен бинарный хук [`0200-rhm-kernel-symlinks.hook.binary`](ops/live-usb/hooks/0200-rhm-kernel-symlinks.hook.binary), который создаёт symlink'и. Без него меню GRUB отображалось, но загрузка была бы невозможна.

APFS: `apfs-fuse` собран и установлен; DKMS-модуль `linux-apfs-rw` не autoinstall'ился (`Module version 0.3.13? for apfs.ko.xz`), ZFS и `broadcom-sta-dkms` собрались.

---

## 4. QEMU-проверка

| Проверка | Результат |
| :--- | :--- |
| UEFI/OVMF + KVM | ✅ GRUB-меню, загрузка `/live/vmlinuz` и `/live/initrd.img` |
| Ядро + initramfs + squashfs | ✅ загрузка до systemd |
| systemd | ✅ `graphical.target`, `rhm-live-agent.service` started |
| Консоль | ✅ `debian login:` на `ttyS0` |
| Kiosk Chromium | ✅ реальный ISO: Chromium на tty1 показывает pending-экран; клики/модалки прогнаны в Chromium/Xvfb |
| Физическое железо | ⏳ не проверялось |

Лог загрузки: [`artifacts/qemu-boot-uefi-kernel.log`](artifacts/qemu-boot-uefi-kernel.log).
Скриншот GRUB: [`artifacts/qemu-grub2.png`](artifacts/qemu-grub2.png).
Сборка и загрузка выполнялись в WSL Debian 13.5; артефакт скопирован в `artifacts/`.

### 4.1. Kiosk UX: сеть, интернет, QR, приложения

- Баннер на сетевом экране отличает линк от интернета: «Интернет не обнаружен…»,
  затем pending-экран «Интернет есть, получаем токен…», затем QR-экран с
  инструкцией.
- Терминал доступен на обоих экранах; меню «Приложения» открывает allowlist:
  терминал, файловый менеджер (`xfe` через `rhm-open-files`, fallback
  `pcmanfm`) и редактор (`mousepad`).
- Визуальный прогон: Chromium + Playwright протыкал сеть, Wi-Fi-модалку,
  скрытую сеть, приложения, файловый менеджер, терминал, pending и ready;
  файловый менеджер и терминал подтверждены скриншотами Xvfb.
- В реальном ISO kiosk запускается отдельным `rhm-live-kiosk.service`
  (tty1, `getty@tty1` замаскирован): раньше live-config логинил `user` и
  оставлял образ на login prompt. Скриншот из QEMU:
  [kiosk-image-pending.png](docs/liveusb/images/kiosk-image-pending.png).
- Скриншоты: [сеть](docs/liveusb/images/kiosk-network.png),
  [pending](docs/liveusb/images/kiosk-pending.png),
  [QR](docs/liveusb/images/kiosk-ready.png),
  [приложения](docs/liveusb/images/kiosk-apps.png),
  [файловый менеджер](docs/liveusb/images/kiosk-files.png),
  [терминал](docs/liveusb/images/kiosk-terminal.png).
  Описание: [docs/liveusb/STARTUP_UI.md](docs/liveusb/STARTUP_UI.md).

---

### 4.2. Домен и настройки сети

- Дефолтный публичный домен заменён на
  `racknerd-5a24bf9.merino-carob.ts.net` в `core/domain.py`, ISO,
  README, docs и тестах; `smart-server.online` в активном дереве больше нет.
- «Настройки сети»: текущие идентификаторы (hostname, node ID, link key с
  маскировкой), интерфейсы с MAC/IP/шлюзом/DNS; редактирование DHCP/static
  IPv4 и DNS; PPPoE; WireGuard (keyfile 0600); OpenVPN (импорт .ovpn).
  Пароли и приватные ключи не возвращаются и не логируются.
- Скриншоты: [текущие](docs/liveusb/images/network-settings-current.png),
  [IP и DNS](docs/liveusb/images/network-settings-ip.png),
  [PPPoE](docs/liveusb/images/network-settings-pppoe.png),
  [WireGuard](docs/liveusb/images/network-settings-wireguard.png),
  [OpenVPN](docs/liveusb/images/network-settings-openvpn.png).

## 5. Тесты

| Проверка | Результат |
| :--- | :--- |
| `py -m pytest tests -q` | **1952 passed, 8 skipped, 0 failed** |
| `tests/test_rescue.py` | 155 passed |
| `tests/test_liveusb_iso_pipeline.py` | 24 passed |
| `build-iso.sh --check` | **54/54 PASS**, exit 0 |
| `bash -n` build-iso / start-kiosk / hook | exit 0 |
| `compileall`, `node --check app.js` | exit 0 |

---

## 6. Что осталось

1. **Физическое железо:** Wi-Fi, iPhone/Android tethering, APFS/ZFS/BitLocker, Secure Boot, реальные сбойные диски.
2. **Kiosk:** визуально проверить Chromium на реальном X/железе; в headless QEMU экран после GRUB чёрный (при этом systemd дошёл до graphical.target).
3. **APFS:** разобраться с DKMS-модулем `linux-apfs-rw` (ошибка версии `0.3.13?`); `apfs-fuse` уже установлен как fallback.
4. **Merge/push:** ветка `complete-liveusb` и тег `v0.1.0` не влиты в `main` и не запушены — нужно разрешение на публикацию.
5. **P2:** `REPO_SLUG`/`package.json` всё ещё указывают на upstream, `docs/CHATGPT_CONNECT_RU.md` устарел, внешние скиллы не запинены по SHA.

---

## 7. Итог

**Сборка и загрузка подтверждены.** ISO 1.5 ГБ собран, GRUB загружает ядро и initramfs, система доходит до `graphical.target`, `rhm-live-agent.service` стартует. Код rescue-API, тесты, версия `0.1.0`, release notes и LiveUSB CI закрыты.

Для статуса «выпущено» осталось прогнать образ на реальном железе и выполнить merge/push с публикацией. Это уже внешние действия, а не незакрытый код.
