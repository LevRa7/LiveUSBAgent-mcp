# План изменений: `remote-harness-liveusb` (LiveUSB Edition)

> **Нумерация независимого форка начинается с `0.1.0`.** Upstream-база — `0.6.0`; форк не продолжает её номер, а начинает собственную линейку. Упоминания версии ниже приведены к `0.1.0`.

## 0. Паспорт проекта

| Параметр | Значение |
| :--- | :--- |
| База форка | `LevRa7/remote-harness-mcp` @ `c31973b` (`chore(release): 0.6.0`) |
| Рабочая копия | `D:\MyProjects\remote-harness-liveusb` |
| Git | ветка `main`, remote `upstream` → `https://github.com/LevRa7/remote-harness-mcp.git`, `origin` не задан (добавить свой репозиторий перед первым push) |
| Наследуемый roadmap | `ProjectPlan.md` (этапы 1–3), `Mistakes.md`, `docs/team/LIVEUSB_SEPARATION.md` |
| Продукт | Загрузочный Debian Live USB с узлом remote-harness и rescue-инструментами (диагностика и восстановление дисков/ОС) |
| Язык/стек | Python 3.8+ stdlib-only в ядре; bash для сборки ISO; systemd/udev в образе |
| Базовая линия тестов | `1585 passed, 8 skipped` (Windows, Python 3.13, 150 с) |

**Определение готовности продукта:** образ собирается одной командой, грузится в QEMU и на железе, узел сам поднимает туннель до шлюза, в `tools/list` присутствуют `rescue_info` и `rescue_action`, а `rescue_action` по умолчанию ничего не пишет на диск (`dry_run=true`).

---

## 1. Что реально лежит в базе (проверено, не по документации)

| № | Область | Факт в скопированном дереве | Следствие для форка |
| :-- | :--- | :--- | :--- |
| 1 | `core/rescue.py` | 336 строк: схемы двух инструментов, коллекторы `disks`/`smart`/`dmesg_io`, действия `mount_safe`, `ddrescue_plan`, `ntfs_fix`, `grub_repair`; остальные действия возвращают `{"status": "schema_ready"}` (`core/rescue.py:336`) | Дописать коллекторы и действия, иначе инструмент врёт клиенту |
| 2 | Публикация инструментов | `rescue_info` есть в `_READ_ONLY_ALLOWED` (`core/mcp_tools.py:500`), но отсутствует в `TOOLS` (`:3313`) и `_HANDLERS` (`:3860`) | Инструмент не виден клиенту и не вызывается — интеграция не начата |
| 3 | Аннотации | `TOOL_ANNOTATIONS` (`core/mcp_tools.py:3254`) не содержит `rescue_*`; `tests/test_tool_annotations.py:72` требует аннотацию для каждого опубликованного инструмента | Без правки политики тест упадёт сразу после публикации |
| 4 | Тесты rescue | `tests/test_rescue.py` и фикстуры `lsblk`/`smartctl`/`sfdisk`/`efibootmgr` отсутствуют | Писать с нуля (этап 1) |
| 5 | Сборка ISO | `ops/live-usb/build-iso.sh:83-84` кладёт в образ `core/` и `rhm_mcp_server.py`, а тот импортирует `starlette`, `uvicorn`, `mcp`, `gdrive_client` (`rhm_mcp_server.py:22-34`); в образе этих пакетов нет | Сервис в образе не стартует; запускать надо `core/agent.py` (stdlib-only) |
| 6 | Режим `live_usb` | `MESH_SCENARIO` встречается только в `ops/live-usb/build-iso.sh:21,96` и `ProjectPlan.md:8`; ни один `.py` его не читает | Переменная мертва — нужен реальный детектор режима |
| 7 | First-boot TUI | `ops/live-usb/firstboot_tui.py:48-49` читает несуществующий `domain.PUBLIC_DOMAIN` (в `core/domain.py` есть `public_base_url()`:214 и `public_url()`:225) и строит URL `/rescue/<host>`, которого нет в `gateway.py`; канонический формат ссылки — `/sse?user=<node>&token=<key>` (`gateway.py:180-182,220`) | TUI показывает выдуманный адрес; переписать на реальный контракт |
| 8 | Сценарии устройства | `SCENARIOS = ("windows","termux","darwin","linux")` (`core/device.py:124`), `matrix_problems()` (`:2243`) требует реализацию на каждую пару «секция × сценарий» | `live_usb` как 5-й сценарий = 11 новых реализаций; так делать не нужно (решение D1) |
| 9 | Установщики | `install.sh:906` и `install.ps1:835` перечисляют `core/rescue.py` в bootstrap-списке | В форке rescue — штатный модуль, список сохраняется |
| 10 | Поверхность инструментов | `tests/test_web_share.py:130-145` фиксирует ровно 27 инструментов и порядок блоков | Публикация rescue меняет контракт: тест правится осознанно |

---

## 2. Целевая архитектура

```
UEFI/BIOS → Debian Live (squashfs + tmpfs, toram)
        ├─ rhm-live-agent.service   → python3 -m core.agent  (stdlib, туннель wss)
        │        └─ MCP tools: 27 базовых + rescue_info + rescue_action
        ├─ rhm-live-tui.service     → ops/live-usb/firstboot_tui.py на tty1
        ├─ rhm-live-net.service     → сеть (Ethernet / Wi-Fi / RNDIS) без сброса аплинка
        └─ udev/udisks             → автомонтирование запрещено
                    │
                    ▼
            gateway (существующий)  ← координатор / MCP-клиент
```

Принципы, унаследованные и обязательные:

1. `core/` — только стандартная библиотека, никаких необработанных исключений: наружу `{"error": ...}` или `{"available": false, "reason": ...}`.
2. Чтение и разрушение разделены: `rescue_info` — read-only, `rescue_action` — destructive, `dry_run=true` по умолчанию.
3. Умирающий носитель не трогаем без проверки `dmesg` на I/O-ошибки и без таймаутов в отдельной сессии процесса (`Mistakes.md:4-7`).
4. `mount` только `ro,noload` / `ro,noatime`; `rw` — лишь по явному флагу после SMART (`Mistakes.md:11`).
5. Ничего не пишем в `tmpfs`/`overlay` (`Mistakes.md:19`).

---

## 3. Принятые решения (ADR)

| ID | Решение | Обоснование | Следствие |
| :-- | :--- | :--- | :--- |
| D1 | **`live_usb` — режим, а не сценарий.** `core/device.py::scenario()` не трогаем; добавляем `core/liveusb.py::is_live_usb()` | `scenario()` — это платформа (Windows/Termux/Darwin/Linux), и матрица требует реализацию на каждую пару; Live USB — это Linux + флаг развёртывания | Устройство-телеметрия остаётся честной, `matrix_problems()` не разрастается; `ProjectPlan.md:8` подлежит правке (этап 2) |
| D2 | **В ISO работает туннельный агент `core/agent.py`, а не `rhm_mcp_server.py`** | Агент — stdlib-only; standalone-сервер требует PyPI-зависимостей и не нужен на образе | `build-iso.sh` перестаёт копировать `rhm_mcp_server.py`; standalone — опциональный флаг сборки |
| D3 | **Rescue включён по умолчанию в этом форке** (`MESH_RESCUE=1`), но уважает `MESH_RESCUE=0` | Продукт форка — именно rescue; обратная совместимость с upstream-семантикой сохраняется | Публикация инструментов условная, тесты гоняют оба режима |
| D4 | **Весь форк-специфичный код изолирован**: `core/liveusb.py`, `core/liveusb_net.py`, `core/rescue.py`, `ops/live-usb/**`; в `core/mcp_tools.py` — один блок под маркером `# LIVEUSB-FORK` | Упрощает `git merge upstream/main` | Конфликты при синхронизации — только в 2–3 точках |
| D5 | **Обрезка поверхности — только из релиза, не из дерева.** Windows GUI-установщик, трей, `status_web/`, npm-алиас, Telegram-QR не входят в релизные артефакты и документацию, но остаются в репозитории | Удаление увеличивает конфликты с upstream и не даёт выигрыша | Релиз = ISO + `install.sh` + `core/` |
| D6 | **Версия форка — `0.1.0`** в `VERSION` и `package.json` одновременно | `tests/test_version_single_source.py` сверяет оба файла и шаблон `MAJOR.MINOR.PATCH` | Обновляется одним коммитом (этап 0) |

---

## 4. Сводка изменений по файлам

| Файл | Действие | Этап |
| :--- | :--- | :--- |
| `core/rescue.py` | Дописать 6 коллекторов, 4 действия, `dry_run`-гейт; выделить общий `_safe`-контракт | 1 |
| `core/rescue_jobs.py` | Новый: долгие операции (`ddrescue`, `fsck`) через `run_job`/`job_output` | 1 |
| `core/mcp_tools.py` | Условная публикация `rescue_*` в `TOOLS`/`_HANDLERS`, аннотации, вызов `_read_only_active()` | 1 |
| `tests/test_rescue.py` | Новый: контракт схем, `dry_run`, tmpfs-отказ, read-only, отсутствие исключений | 1 |
| `tests/fixtures/rescue/*` | Новые: `lsblk.json`, `smartctl.json`, `sfdisk.json`, `efibootmgr.txt`, `dmesg_io.txt` | 1 |
| `core/liveusb.py` | Новый: `is_live_usb()`, `report()`, чтение `/run/live/medium`, `/proc/cmdline`, `MESH_SCENARIO` | 2 |
| `tests/test_liveusb_mode.py` | Новый: детектор на подменённых входах | 2 |
| `ops/live-usb/firstboot_tui.py` | Переписать: цикл обновления, реальный pairing-URL, статус туннеля, Wi-Fi/RNDIS | 3 |
| `core/liveusb_net.py` | Новый: разбор `ip route`, защита аплинка (`active_uplink_lock`), обёртки `nmcli` с таймаутом | 3 |
| `ops/live-usb/systemd/*.service` | Новые: `rhm-live-agent`, `rhm-live-tui`, `rhm-live-net`, `rhm-live-pair` | 3 |
| `ops/live-usb/udev/99-rhm-no-automount.rules` | Новый: запрет автомонтирования | 3 |
| `ops/live-usb/build-iso.sh` | Переписать: манифест, хуки, `--check`, `--clean`, без `rhm_mcp_server.py` | 4 |
| `ops/live-usb/manifest.txt` | Новый: единственный источник списка файлов образа | 4 |
| `ops/live-usb/smoke-qemu.sh` | Новый: загрузка ISO в QEMU и ожидание узла в шлюзе | 4 |
| `.github/workflows/liveusb-iso.yml` | Новый: `--check` на PR, сборка ISO по тегу/вручную | 4 |
| `tests/test_live_iso_assets.py` | Новый: манифест ↔ файлы, юниты, отсутствие лишних зависимостей | 4 |
| `README_RU.md`, `README.md`, `docs/liveusb/*` | Раздел LiveUSB, сборка, запись флешки, troubleshooting | 5 |
| `ProjectPlan.md` | Синхронизировать с D1/D2, закрыть чекбоксы | 2, 5 |
| `VERSION`, `package.json` | `0.6.0` → `0.1.0` | 0 |
| `tests/test_tool_annotations.py` | Разрешить `rescue_action` в `DESTRUCTIVE`, `rescue_info` в `READ_ONLY` | 1 |
| `tests/test_web_share.py` | Обновить закреплённый порядок/количество инструментов | 1 |

---

## 5. Этапы работ

### Этап 0 — Идентичность проекта и базовая линия (0,5 дня)

- **T0.1** `VERSION` и `package.json` → `0.1.0`; `py -m pytest tests/test_version_single_source.py -q` зелёный.
- **T0.2** `docs/UPSTREAM.md`: база `c31973b`, политика синхронизации (раздел 9), список конфликтных точек, правило маркеров `# LIVEUSB-FORK`.
- **T0.3** Раздел «LiveUSB Edition» в `README_RU.md` и `README.md`: что это, чем отличается от upstream, MIT-атрибуция, ссылка на этот план.
- **T0.4** Зафиксировать базовую линию: `py -m pytest tests -q` → `1585 passed, 8 skipped` (Windows), прогон на Linux — в CI.
- **T0.5** Первый коммит форка.

**DoD:** тесты версии зелёные, `git log` содержит коммит идентичности, план лежит в репозитории.

### Этап 1 — Rescue как полноценный MCP-модуль (4–5 дней)

- **T1.1 Коллекторы** (`core/rescue.py`). Дописать до контракта `RESCUE_SECTIONS`:
  - `partitions`: `sfdisk -J <dev>` + `blkid -o export` (fallback — `lsblk -J`);
  - `raid_lvm`: `lsblk -J` (типы `raid*`, `lvm`), `pvs/vgs/lvs --noheadings`, `cryptsetup status` по найденным LUKS-устройствам;
  - `installed_os`: кандидаты из `lsblk`, монтирование `ro,noload` во временный каталог, чтение `/etc/os-release`, `/etc/fstab`; Windows — наличие `Windows/System32/config/SOFTWARE` через `ntfsls` без монтирования;
  - `boot_efi`: `efibootmgr -v`; при отсутствии — `available:false` с причиной (`/sys/firmware/efi` нет);
  - `win_artifacts`: пути `SAM`/`SYSTEM`/`SOFTWARE`, признак BitLocker (`cryptsetup isLuks`);
  - `summary`: агрегат (число дисков, худший SMART, найденные ОС, флаги I/O-ошибок).
  Каждый коллектор: жёсткий таймаут, `_run_cmd`, никаких исключений, на Windows — `available:false` с причиной.

- **T1.2 Действия** (`core/rescue.py`): `umount` (проверка занятости, `-l` только по флагу), `fsck_check` (`fsck -n`, без починки), `win_sam_reset` (`chntpw`, `dry_run` печатает argv и целевой пользователь), `chroot_exec` (только после `mount_safe`; bind-монтирования `/dev,/proc,/sys,/run`; таймаут; в `dry_run` — полный список шагов). `ddrescue_plan` остаётся планировщиком, запуск — T1.3.

- **T1.3 Фоновые операции** (`core/rescue_jobs.py`): обёртка над менеджером заданий узла (`run_job`/`job_output`) для `ddrescue_start` и долгого `fsck`; проверка целевого пути через `df -T` (отказ на `tmpfs`/`overlay`), возобновляемый mapfile, прогресс в `job_output`.

- **T1.4 Публикация** (`core/mcp_tools.py`), одним блоком `# LIVEUSB-FORK`:
  ```python
  if _rescue_enabled():          # MESH_RESCUE != "0"
      TOOLS.extend(rescue.RESCUE_TOOLS_SPEC)
      _HANDLERS["rescue_info"] = _tool_rescue_info
      _HANDLERS["rescue_action"] = _tool_rescue_action
      TOOL_ANNOTATIONS["rescue_info"] = _ANNOTATIONS_READ_ONLY
      TOOL_ANNOTATIONS["rescue_action"] = _ANNOTATIONS_DESTRUCTIVE
  ```
  `_tool_rescue_action` обязан вызывать `_read_only_active()` и подтверждение оператора; `rescue_info` остаётся в `_READ_ONLY_ALLOWED`.

- **T1.5 Тесты** (`tests/test_rescue.py` + фикстуры): enum-ы схем совпадают с константами; каждая секция возвращает словарь с `available`; `dry_run` по умолчанию; отказ писать образ в `tmpfs`; отказ `rescue_action` при `MESH_READ_ONLY=1`; аннотации и наличие обработчиков; мусорные входные данные не дают исключений; Linux-only тесты помечены `skipif`.

- **T1.6 Обновить политики-пины**: `tests/test_tool_annotations.py` (`DESTRUCTIVE` += `rescue_action`, `READ_ONLY` += `rescue_info`), `tests/test_web_share.py` (порядок: базовые блоки → shares → rescue, количество `27 + 2`). Дивергенция от upstream фиксируется комментарием со ссылкой на этот план.

**DoD:** `tools/list` содержит оба инструмента; `rescue_info(section="summary")` отвечает на Linux; `rescue_action` без `dry_run` требует подтверждения; все тесты зелёные.

### Этап 2 — Режим LiveUSB (1 день)

- **T2.1** `core/liveusb.py`:
  - `is_live_usb()` — три сигнала: каталог `/run/live/medium`, `MESH_SCENARIO=live_usb`, `/proc/cmdline` содержит `boot=live`;
  - `report()` — какой сигнал сработал (для `mesh_status` и bug-report);
  - зависимости инъектируются (пути/строки) — тестируется на Windows.
- **T2.2** Потребители: `_rescue_enabled()` (этап 1), `mesh_status` (поле `mode`), TUI (этап 3), systemd-окружение.
- **T2.3** `tests/test_liveusb_mode.py`: все комбинации сигналов, отсутствие ложных срабатываний на обычном Linux.
- **T2.4** Правка `ProjectPlan.md:8`: сценарий не расширяем, ссылка на D1.

**DoD:** `MESH_SCENARIO=live_usb` реально влияет на поведение; на обычном Linux `is_live_usb()` = `False`.

### Этап 3 — Сеть, защита аплинка и TUI (2–3 дня)

- **T3.1** `core/liveusb_net.py` (stdlib, тестируемое ядро): разбор `ip route show default`, список интерфейсов из `/sys/class/net`, детект RNDIS (`usb0` + `ID_USB_DRIVER`), обёртки `nmcli` с таймаутом, `active_uplink_lock`: интерфейс, через который держится туннель, запрещён к `down`/`flush`/`restart` без отложенного отката (`Mistakes.md:15`).
- **T3.2** `ops/live-usb/firstboot_tui.py`: цикл с обновлением 2 с, блоки «сеть / аплинк / диски / туннель», QR по каноническому URL (`gateway.py:180-182`):
  `https://<public>/sse?user=<node>&token=<link_key>`, fallback без `qrencode`.
  Клавиши: `[W]` `nmtui`, `[S]` шелл, `[R]` рестарт агента, `[Q]` выход. Никаких исключений при отсутствии сети/утилит.
- **T3.3** systemd/udev/provisioning в `ops/live-usb/`:
  - `rhm-live-agent.service`: `ExecStart=/usr/bin/python3 -m core.agent`, `EnvironmentFile=/etc/remote-harness-liveusb/agent.env`, `Restart=always`, `After=network-online.target`;
  - `rhm-live-tui.service`: `TTYPath=/dev/tty1`, `StandardInput=tty`, маскирует `getty@tty1`;
  - `rhm-live-net.service`: поднятие сохранённого Wi-Fi-профиля (oneshot);
  - `rhm-live-pair.service` + `pair.sh`: получает `link_key` у шлюза и пишет `agent.env` (опционально, если токен не зашит при сборке);
  - `99-rhm-no-automount.rules`: `ENV{UDISKS_AUTO}="0"`, `ENV{UDISKS_IGNORE}="1"`; `journald` — `Storage=volatile`, `SystemMaxUse=50M`.
- **T3.4** Тесты: `tests/test_liveusb_net.py` (разбор маршрутов, lock, RNDIS), тест рендера TUI без терминала, `tests/test_live_iso_assets.py` — юниты и правила на месте.

**DoD:** TUI переживает отсутствие сети; аплинк нельзя снести вызовом из MCP; QR ведёт на существующий маршрут шлюза.

### Этап 4 — Сборка ISO (3–4 дня)

- **T4.1** `ops/live-usb/manifest.txt`: явный список того, что попадает в `/opt/remote-harness-liveusb` (`core/**`, `skills/orchestrator.md`, `VERSION`). `build-iso.sh` копирует строго по нему; `tests/test_live_iso_assets.py` сверяет манифест с диском.
- **T4.2** Переписать `build-iso.sh`:
  - аргументы `--distro trixie|bookworm`, `--arch`, `--out`, `--clean`, `--check` (проверка ассетов без сборки), `--with-standalone` (опционально ставит PyPI-зависимости для `rhm_mcp_server.py`);
  - `lb config`: `--archive-areas "main contrib non-free non-free-firmware"`, `--bootappend-live "boot=live components quiet toram"`, `--checksums sha256`;
  - списки пакетов: firmware (`iwlwifi`, `realtek`, `atheros`, `brcm80211`, `mediatek`, `misc-nonfree`), rescue-тулкит (`smartmontools`, `nvme-cli`, `gddrescue`, `testdisk`, `chntpw`, `efibootmgr`, `lvm2`, `mdadm`, `cryptsetup`, `btrfs-progs`, `ntfs-3g`, `dosfstools`, `exfatprogs`), сеть (`network-manager`, `wpasupplicant`, `usb-modeswitch`, `wireguard-tools`), рантайм (`python3`, `tmux`, `jq`, `qrencode`);
  - `lb build` только под root, на выходе ISO + `.sha256`.
- **T4.3** Хуки `config/hooks/normal/*.hook.chroot`: udev-правило, `agent.env` из переменных сборки, отключение `udisks2`-автомонтирования.
- **T4.4** `ops/live-usb/smoke-qemu.sh`: `qemu-system-x86_64 -m 2048 -cdrom <iso> -netdev user`, ожидание появления узла в реестре шлюза, таймаут 5 минут, лог загрузки.
- **T4.5** CI `.github/workflows/liveusb-iso.yml`: на PR — `--check` + `shellcheck`; по тегу/вручную — сборка ISO в `debian:trixie` (root в контейнере) и загрузка артефакта.

**DoD:** `build-iso.sh --check` зелёный без root; сборка даёт загрузочный ISO; QEMU-смоук доходит до узла в шлюзе.

### Этап 5 — Документация и релиз (1 день)

- `docs/liveusb/README.md`: требования к хосту сборки, команды, запись (`dd`/Rufus), Secure Boot, отсутствие firmware, диагностика D-state.
- `docs/liveusb/TROUBLESHOOTING.md`: таблица «симптом → причина → действие» (нет Wi-Fi, нет туннеля, `dmesg` I/O-ошибки, RAM OOM).
- Обновить `ProjectPlan.md` (чекбоксы), `CHANGELOG`, `README_RU.md`.
- Тег `v0.1.0`, артефакты: ISO, `.sha256`, заметки к релизу.

---

## 6. Порядок и оценки

| Этап | Зависит от | Оценка |
| :--- | :--- | :--- |
| 0. Идентичность | — | 0,5 дня |
| 1. Rescue-модуль | 0 | 4–5 дней |
| 2. Режим `live_usb` | 0 | 1 день (параллельно с 1) |
| 3. Сеть и TUI | 2 | 2–3 дня |
| 4. ISO | 1, 3 | 3–4 дня |
| 5. Релиз | 4 | 1 день |
| **Итого** | | **12–15 дней** |

---

## 7. Матрица приёмки

| Проверка | Команда/действие | Ожидание |
| :--- | :--- | :--- |
| Юнит-тесты | `py -m pytest tests -q` | ≥ `1585 + новые`, 0 падений |
| Rescue-контракт | `py -m pytest tests/test_rescue.py -q` | зелёный |
| Публикация | `py -c "from core import mcp_tools; print([t['name'] for t in mcp_tools.TOOLS][-2:])"` | `['rescue_info', 'rescue_action']` |
| Read-only | `MESH_READ_ONLY=1` + вызов `rescue_action` | отказ без выполнения |
| `dry_run` | `rescue_action(action="mount_safe", device="/dev/sda1")` | только argv, диск не тронут |
| tmpfs-защита | `rescue_action(action="ddrescue_start", target_image="/tmp/x.img")` | отказ с причиной |
| Сборка | `bash ops/live-usb/build-iso.sh --check` | код 0 без root |
| Загрузка | `bash ops/live-usb/smoke-qemu.sh <iso>` | узел в реестре шлюза ≤ 5 мин |
| Автомонтирование | загрузка с воткнутым диском | разделы не смонтированы |
| Ресурсы | `free -m` после загрузки | tmpfs не переполнен, журнал ≤ 50 МБ |
| Устойчивость | `rescue_info` на сбойном устройстве | `available:false`/таймаут, процесс жив |

---

## 8. Риски и предохранители

| Риск | Проявление | Мера |
| :--- | :--- | :--- |
| D-state на сбойном диске | вызов `smartctl`/`mount` вешает воркер | `dmesg`-предпроверка, `timeout`, `start_new_session`, отказ при >10 I/O-ошибок (`Mistakes.md:4-7`) |
| Journal replay убивает ФС | `mount -o ro` пишет в журнал | только `ro,noload` / `ro,norecovery`; `rw` — по флагу после SMART (`Mistakes.md:11`) |
| Обрыв собственного туннеля | сброс аплинка при настройке сети | `active_uplink_lock` + отложенный откат (`Mistakes.md:15`) |
| OOM в tmpfs | дамп/образ в `/tmp` | `df -T`-проверка, требование внешнего накопителя (`Mistakes.md:19`) |
| Опасные операции на реальных дисках | пользователь подтверждает не то | `destructiveHint`, `dry_run` по умолчанию, явное имя устройства в подтверждении |
| Secure Boot | образ не грузится | подписанный `shim` в пакетах либо документированное требование отключить SB |
| Несвободные firmware | нет Wi-Fi на части ноутбуков | `non-free-firmware` + список чипсетов в документации |
| Невоспроизводимость сборки | ISO «плывёт» | фиксированные `--distribution`, `--checksums`, версия live-build в CI |
| Дрейф от upstream | конфликты при merge | маркеры `# LIVEUSB-FORK`, изоляция кода (D4), ежемесячный merge |
| Windows-разработка | Linux-коллекторы не тестируются локально | `skipif`, обязательный Linux-джоб в CI |

---

## 9. Синхронизация с upstream

```bash
git fetch upstream
git merge upstream/main          # или rebase, если ветка ещё не опубликована
py -m pytest tests -q            # после каждого merge
```

- Конфликтные точки: `core/mcp_tools.py` (TOOLS/_HANDLERS/TOOL_ANNOTATIONS), `tests/test_tool_annotations.py`, `tests/test_web_share.py`, `install.sh`/`install.ps1` (список модулей), `README*`.
- Правило: форк-код — только в `core/liveusb*.py`, `core/rescue*.py`, `ops/live-usb/**`; в общих файлах — минимальный блок под `# LIVEUSB-FORK` с ссылкой на этот план.
- Каждый merge сопровождается прогоном `tests/test_rescue.py` и `tests/test_liveusb_mode.py`, чтобы upstream-изменения не отключили rescue молча.

---

## 10. Первые коммиты

1. `docs: add LiveUSB fork change plan and upstream policy`
2. `chore: set fork version 0.1.0`
3. `test(rescue): fixtures and contract tests for rescue_info (red)`
4. `feat(rescue): partitions, raid_lvm, installed_os, boot_efi, win_artifacts collectors`
5. `feat(rescue): register rescue_info as a published MCP tool`
6. `feat(rescue): rescue_action with dry_run gate, annotations and read-only refusal`
7. `feat(rescue): background ddrescue/fsck jobs with tmpfs guard`
8. `feat(liveusb): is_live_usb() mode detector`
9. `feat(liveusb): network helpers and protected uplink`
10. `feat(liveusb): tty1 dashboard with real pairing URL`
11. `build(liveusb): manifest-driven ISO builder, systemd units and udev rules`
12. `ci(liveusb): asset checks, shellcheck and ISO build workflow`

---

## 11. Открытые вопросы

1. **Веб-интерфейс rescue.** В `gateway.py` маршрута `/rescue/...` нет; доступ к инструментам — только через MCP (`/sse?user=...&token=...`). Нужен ли отдельный экран координатора для rescue-сессии или достаточно MCP-клиента?
2. **Secure Boot.** Включать подписанный `shim` (образ тяжелее, нужен `shim-signed`) или требовать отключения SB в документации?
3. **Pairing.** Токен зашивается при сборке (`agent.env` в хуке) или образ нейтрален и получает `link_key` при первом boot через `rhm-live-pair`?
4. **Персистентность.** Нужен ли persistence-раздел для логов/образов (`ddrescue` на 500 ГБ) или всегда внешний накопитель?
5. **Судьба standalone-сервера.** Оставляем `rhm_mcp_server.py` в дереве как dev-инструмент (флаг `--with-standalone`) или выносим в отдельный репозиторий?
6. **Локализация.** TUI и документация — только `ru`/`en` или нужен выбор языка на tty1?
