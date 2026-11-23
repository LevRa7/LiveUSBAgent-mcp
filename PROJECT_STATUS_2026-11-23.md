# Отчёт о готовности LiveUSBAgent-mcp — финальный

**Дата:** 23.11.2026, ~06:50 IST  
**Ветка:** `complete-liveusb` (worktree), HEAD `a9d954e`  
**База:** `e4eebfa`; сверх неё 10 коммитов, 28 файлов, `+3 840 / −29`  
**Версия:** `0.7.0`  
**Статус:** код и тесты доведены; ISO/QEMU/железо и публикация остаются внешними шагами

> Изменения лежат в отдельной ветке `complete-liveusb`. `main` не изменён; создан локальный тег `v0.7.0` на `584ff8a`, merge и push не выполнялись. Чтобы получить доработки в основном дереве, ветку нужно влить в `main`.

---

## 1. Короткий ответ

**Все P0-разрывы кода закрыты, полный набор тестов зелёный.** Проект готов к сборке ISO и приёмке на Debian, но ещё не «выпущен»: реальный ISO не собран, QEMU/железо не проверены, тег создан только локально, публикации нет.

| Область | Было | Стало |
| :--- | :--- | :--- |
| Rescue-API | 5 секций и 4 действия не реализованы | все 12 секций и 11 действий имеют реализацию или честный отказ |
| Тесты | 5 failed / 1756 passed | **1856 passed / 8 skipped / 0 failed** |
| ISO-проверка | `--check` отсутствовал | `build-iso.sh --check` — **32/32 PASS** без root и `lb` |
| Релиз | 0.6.0, без notes/CI | **0.7.0**, release notes, LiveUSB CI, docs |
| Kiosk | без кнопки терминала | кнопка + `xterm` в образе + `DISPLAY` для сервера |
| CI | без LiveUSB-джобы | `.github/workflows/liveusb-iso.yml` |
| Документация | README обещал несуществующее | README/EN/RU + `docs/liveusb/*` приведены к коду |

---

## 2. Rescue-API: что теперь работает

### 2.1. Секции `rescue_info`

Все 12 секций из [`core/rescue.py`](core/rescue.py#L32-L44) диспетчеризуются в реальные коллекторы:

| Секция | Реализация |
| :--- | :--- |
| `summary` | диски + dmesg + RAM + ФС + partitions + installed_os + boot_efi (с ограничением таймаута) |
| `all` | всё выше + `smart`, `raid_lvm`, `win_artifacts` |
| `disks` | `lsblk -J` |
| `smart` | `smartctl -a -j` + dmesg-предпроверка |
| `dmesg_io` | фильтр по устройству, счётчик I/O-ошибок |
| `ram_info` | `/proc/meminfo`, `free`, `dmidecode`, `edac-util` |
| `filesystems` | 13 групп драйверов/утилит |
| `partitions` | `sfdisk -J` + `blkid`, fallback `lsblk` с фильтром по устройству ([`#L949`](core/rescue.py#L949)) |
| `raid_lvm` | `lsblk`, `pvs`/`vgs`/`lvs`, `cryptsetup status` ([`#L1055`](core/rescue.py#L1055)) |
| `installed_os` | `ro,noload`-проба Linux-корней, `ntfsls` для Windows, гарантированный `umount` ([`#L1237`](core/rescue.py#L1237)) |
| `boot_efi` | `efibootmgr -v`, честный отказ вне UEFI ([`#L1294`](core/rescue.py#L1294)) |
| `win_artifacts` | `ntfsls` + BitLocker-флаг без монтирования ([`#L1332`](core/rescue.py#L1332)) |

### 2.2. Действия `rescue_action`

Все 11 действий из [`core/rescue.py`](core/rescue.py#L47-L58) больше не возвращают `schema_ready`:

| Действие | Поведение |
| :--- | :--- |
| `mount_safe` / `mount_auto` | `ro,noload` по умолчанию, `rw` по флагу; dmesg-guard >10 ошибок, `force=true` — обход ([`#L1414`](core/rescue.py#L1414)) |
| `umount` | проверка mountpoint и `os.path.ismount`, `-l`/`-f` только при strict `true` ([`#L1785`](core/rescue.py#L1785)) |
| `fsck_check` | только read-only (`e2fsck -n`, `xfs_repair -n`, `btrfs check --readonly`, `fsck.* -n`, `ntfsfix -n`), `repair:false` ([`#L1836`](core/rescue.py#L1836)) |
| `win_sam_reset` | `dry_run` — argv; `dry_run=false` — честный `requires_interactive`/`executed:false`, без скрытой записи в SAM ([`#L1874`](core/rescue.py#L1874)) |
| `chroot_exec` | bind `/dev,/proc,/sys,/run`, `chroot`, timeout, unmount в `finally` ([`#L1918`](core/rescue.py#L1918)) |
| `grub_repair` | `dry_run` — шаги; `dry_run=false` — реальное выполнение с остановкой на ошибке и cleanup ([`#L2040`](core/rescue.py#L2040)) |
| `ntfs_fix`, `ram_test_plan`, `ddrescue_plan`, `ram_dump_plan` | прежние безопасные реализации; последние два — планировщики по контракту |

Финальный fallback теперь не маскируется под успех: `{"error": "rescue action is not implemented in this build: ..."}` ([`#L2132`](core/rescue.py#L2132)).

Тесты: [`tests/test_rescue.py`](tests/test_rescue.py) — **155 passed**, включая read-only-отказ для всех действий ([`#L533`](tests/test_rescue.py#L533)), ZFS-guard ([`#L2187`](tests/test_rescue.py#L2187)) и preflight `grub_repair` ([`#L2109`](tests/test_rescue.py#L2109)).

---

## 3. Релиз, ISO и Kiosk

### 3.1. Версия и релиз

- `0.7.0` в [`VERSION`](VERSION#L1), [`core/version.py`](core/version.py#L23), [`package.json`](package.json#L2-L3), npm-алиасе.
- [`docs/releases/v0.7.0.md`](docs/releases/v0.7.0.md) — release notes с ограничениями.
- README EN/RU содержат `**Version:** 0.7.0` и ссылку на notes.
- `.gitattributes` фиксирует LF для `*.sh`, `*.hook.chroot`, `*.rules` ([`.gitattributes`](.gitattributes)).

### 3.2. `build-iso.sh --check`

[`ops/live-usb/build-iso.sh`](ops/live-usb/build-iso.sh#L103) проверяет 11 обязательных файлов, `bash -n` шести скриптов/хука, `py_compile` рантайма, 99 пакетов, systemd-юнит и GRUB-меню. Запуск:

```text
PASS: 32 asset checks passed (root and live-build not required)
```

`--check` не создаёт `WORK_DIR` и не требует `lb`/root; обычный путь сборки не изменён. `xterm` добавлен в пакетный список ([`#L405`](ops/live-usb/build-iso.sh#L405)) и в обязательные проверки.

### 3.3. Kiosk terminal

- `launch_local_terminal()` требует `DISPLAY`, выбирает `x-terminal-emulator`/`xterm`/`lxterminal`/`gnome-terminal`, запускает через `Popen` без `shell=True` ([`ops/live-usb/kiosk_server.py`](ops/live-usb/kiosk_server.py#L599)).
- `POST /api/terminal` — за существующим loopback/CSRF/JSON-гейтом; malformed JSON → 400 ([`#L923`](ops/live-usb/kiosk_server.py#L923), [`#L983`](ops/live-usb/kiosk_server.py#L983)).
- `start-kiosk.sh` экспортирует `DISPLAY=:0` **до** запуска сервера ([`ops/live-usb/start-kiosk.sh`](ops/live-usb/start-kiosk.sh#L97-L106)).
- В ready-view добавлена кнопка «Открыть терминал» ([`ops/live-usb/kiosk_ui/index.html`](ops/live-usb/kiosk_ui/index.html#L66)) и обработчик в [`app.js`](ops/live-usb/kiosk_ui/app.js).

### 3.4. CI и документация

- [`.github/workflows/liveusb-iso.yml`](.github/workflows/liveusb-iso.yml): PR — LiveUSB-тесты + `--check` + shellcheck; ручная/теговая сборка ISO в `debian:trixie` с `continue-on-error`.
- [`docs/liveusb/README.md`](docs/liveusb/README.md) — сборка, запись, Secure Boot, QEMU.
- [`docs/liveusb/TROUBLESHOOTING.md`](docs/liveusb/TROUBLESHOOTING.md) — Wi-Fi/iPhone/туннель/dmesg/D-state/OOM/Secure Boot.
- README EN/RU ссылаются на оба документа.

---

## 4. Тесты и проверки

| Проверка | Результат |
| :--- | :--- |
| `py -m pytest tests -q -p no:cacheprovider` | **1856 passed, 8 skipped, 0 failed** |
| `py -m pytest tests/test_rescue.py -q` | **155 passed** |
| `py -m pytest tests/test_kiosk_server.py tests/test_liveusb_iso_pipeline.py -q` | **49 passed** |
| `bash ops/live-usb/build-iso.sh --check` | **32/32 PASS**, exit 0 |
| `py -m compileall core gateway.py ops/live-usb` | exit 0 |
| `bash -n` build-iso / start-kiosk / test-qemu / fetch-skills / hook | exit 0 |
| `node --check ops/live-usb/kiosk_ui/app.js` | exit 0 |

Дополнительно исправлено: `tests/test_security_audit_2026_11_19.py` теперь делает `pytest.importorskip("google.antigravity")`, поэтому Linux CI без приватного SDK получает skip, а не collection ERROR. README честно описывает APFS как **всегда read-only** в `rescue_action`.

---

## 5. Матрица приёмки

| Проверка | Ожидание | Факт |
| :--- | :--- | :--- |
| Юнит-тесты | 0 падений | ✅ 1856/8/0 |
| Rescue-контракт | все секции/действия | ✅ 12/12 и 11/11 (с честными plan-only/interactive) |
| Публикация | rescue/skills tools | ✅ 27 базовых + 4 LiveUSB = 31 |
| Read-only | отказ `rescue_action` | ✅ тест на 6 действиях |
| `dry_run` | только argv | ✅ |
| tmpfs-защита | отказ | ✅ |
| `build-iso.sh --check` | exit 0 без root | ✅ 32 проверки |
| QEMU-смоук | узел в реестре ≤ 5 мин | ⏳ нет Debian/QEMU в этой среде |
| Автомонтирование | разделы не смонтированы | 🟡 правила есть, живой загрузки не было |
| Ресурсы | tmpfs/journald | 🟡 настроено, не измерено |
| Устойчивость | `available:false`/таймаут | 🟡 моки, не сбойный диск |

---

## 6. Что осталось (внешние шаги)

1. **Собрать и проверить ISO на Debian 13 Trixie** — в этой среде нет `lb`, QEMU/OVMF и места; WSL Debian недоступен (`E_ACCESSDENIED`), Docker отсутствует.
   ```bash
   sudo ./ops/live-usb/build-iso.sh --with-external-skills --build
   cd /tmp/rhm-live-build && sha256sum live-image-amd64.hybrid.iso
   cd - && ./ops/live-usb/test-qemu.sh --mode uefi --create-fs-disks
   ./ops/live-usb/test-qemu.sh --mode bios --ram 2048
   ```
2. **Проверить на реальном железе:** Wi-Fi, iPhone/Android tethering, APFS/ZFS/BitLocker, Secure Boot, кнопка «Открыть терминал» на `xterm`/`:0`.
3. **Влить ветку `complete-liveusb` в `main`**, запушить ветку и локальный тег `v0.7.0`, выпустить ISO + `.sha256` + release notes.
4. **P1-8:** внешние скиллы всё ещё не запинены по commit SHA — `fetch-skills.sh` клонирует HEAD и только записывает SHA в lockfile; `skills-lock.json` не используется для checkout. Это риск воспроизводимости/снабжения.
5. **P2:** `REPO_SLUG`/`package.json` всё ещё указывают на upstream `remote-harness-mcp`; `docs/CHATGPT_CONNECT_RU.md` остаётся untracked и устаревшим; `AgentDFIR`/`avml` не запекаются, хотя `ProjectPlan` их упоминает.

---

## 7. Итог

**Кодовая доработка завершена.** Rescue-API больше не врёт: все заявленные секции и действия реализованы, опасные операции по умолчанию `dry_run=true`, read-only-режим и dmesg-guard закреплены тестами. Полный набор — **1856 passed, 8 skipped, 0 failed**; `build-iso.sh --check` — **32/32**.

**Продуктовая готовность — на уровне «готово к сборке».** Осталось то, что нельзя сделать на Windows-хосте: реальная сборка ISO, QEMU/железо, merge/push тега и публикация. После их выполнения проект можно называть выпущенным.
