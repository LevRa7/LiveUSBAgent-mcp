# LiveUSBAgent-mcp

> English version: [README.md](README.md)

**LiveUSBAgent-mcp** — это загрузочная флешка на базе **Debian 13 (Trixie)**, которая превращает любой сломанный, «голый» или негрузящийся ПК/ноутбук в управляемую станцию для восстановления ОС, спасения данных, системного администрирования и диагностики железа с прямым подключением к **Gemini**, **ChatGPT** или **Claude** по протоколу MCP.

Проект вырос из [remote-harness-mcp](https://github.com/LevRa7/remote-harness-mcp) и развивается как самостоятельный репозиторий [LiveUSBAgent-mcp](https://github.com/LevRa7/LiveUSBAgent-mcp), заточенный специально под сценарии LiveUSB: нулевую настройку, максимальную совместимость с железом и файловыми системами и безопасную работу с повреждёнными дисками.

**Version:** 0.1.0 — заметки к релизу: [docs/releases/v0.1.0.md](docs/releases/v0.1.0.md).

---

## Зачем это нужно

Обычная загрузочная флешка с Linux помогает только тем, кто помнит наизусть ключи `ddrescue`, синтаксис `dislocker`, порядок сборки `mdadm` → `LUKS` → `LVM` → `chroot` и тонкости восстановления EFI/BCD.

С **LiveUSBAgent-mcp** всё выглядит иначе:

1. Вы загружаете проблемный компьютер с флешки.
2. Подключаете его к интернету — по кабелю, по Wi-Fi на экране или просто воткнув **iPhone / Android** по USB в режиме модема.
3. Сканируете **QR-код** с экрана телефоном, копируете готовую ссылку и вставляете её в **Gemini**, **ChatGPT** или **Claude**.
4. Дальше общаетесь с ИИ обычным языком: просите проверить здоровье диска, аккуратно вытащить файлы с зашифрованного раздела, починить загрузчик Windows/Linux или прогнать диагностику памяти.

Новичку не нужно гадать, какую команду ввести в чёрный терминал. Системному администратору не нужно вручную ставить пакеты, искать прошивки Wi-Fi и собирать драйверы APFS/ZFS на месте — всё уже запечено в образ.

```text
  Gemini (/sse) · ChatGPT (/mcp) · Claude (/mcp, CLI или JSON)
                          │  MCP поверх HTTPS
                          ▼
               Mesh Gateway (/connect/<session_code>)
                          │  Исходящий WebSocket-туннель (без открытых портов)
                          ▼
   Загруженный LiveUSB (Debian 13 Trixie · python3 -m core.agent --ephemeral-liveusb)
   ├─ tty1: Графический Kiosk UI (127.0.0.1:8787) — Wi-Fi, USB-модем, SVG QR
   ├─ tty2: Консольный fallback (firstboot_tui.py + nmtui + [S] Shell (bash))
   └─ Полные root MCP-инструменты + безопасные rescue_info / rescue_action + SKILL.md
```

---

## Ключевые возможности

### Сеть, драйверы и USB-тетеринг из коробки
- **Полный набор прошивок (`non-free-firmware`)**: `firmware-linux`, `firmware-linux-nonfree`, `firmware-misc-nonfree`, `firmware-iwlwifi` (Intel Wi-Fi 6/6E/7), `firmware-realtek`, `firmware-brcm80211` (Broadcom), `firmware-mediatek`, `firmware-atheros`, `firmware-marvell-prestera`, `firmware-qcom-soc`, `firmware-ti-connectivity`, `firmware-zd1211`, `firmware-libertas`, а также out-of-tree драйвер `broadcom-sta-dkms` для старых MacBook и ноутбуков с Broadcom.
- **USB-модем с iPhone без ручной настройки**: в образ встроены `usbmuxd`, `ipheth-utils`, `libimobiledevice-utils` (`idevicepair`) и `ifuse`, а также модуль ядра `ipheth`. Достаточно включить «Режим модема», подключить кабель и нажать «Доверять» на экране iPhone — NetworkManager сам поднимет интерфейс по DHCP.
- **USB-модем с Android**: модули `rndis_host`, `cdc_ether`, `cdc_ncm` и udev-правила (`99-rhm-network-tethering.rules`) автоматически поднимают USB-Ethernet с приоритетом над неактивным Wi-Fi.
- **Сетевой инструментарий**: `network-manager`, `wpasupplicant`, `iw`, `rfkill`, `ethtool`, `usb-modeswitch`, `modemmanager`, `wireguard-tools`, `openssh-client`.

### Графический Kiosk-экран и консольный fallback
- На `tty1` автоматически стартует лёгкий **графический Kiosk-интерфейс** (Xorg + Openbox + Chromium Kiosk поверх локального сервера `kiosk_server.py` на `127.0.0.1:8787`):
  - сканирование и выбор Wi-Fi сети с кнопками **`Обновить список`** и **`Скрытая сеть`**, ввод пароля с **экранной клавиатурой** (выручит, если сломана клавиатура ноутбука или это планшет/моноблок с тачскрином);
  - живая индикация Ethernet и USB-модема iPhone/Android (если iPhone не спарен, экран подскажет нажать **«Доверять»** и ввести код-пароль);
  - на экране готовности (Ready view) — крупный **SVG QR-код**, короткая ссылка, статус туннеля, сводка по найденным дискам и кнопки **`Сменить Wi-Fi`** и **`Новый токен`**.
- На `tty2` (переключение по `Ctrl+Alt+F2`, а также автоматически при отсутствии X11/видеовыхода) работает консольный fallback **`firstboot_tui.py`** с QR-кодом в терминале, доступом к `nmtui` и открытием локальной оболочки `bash` через `Ctrl+Alt+F2` → **`[S] Shell (bash)`**.

### Нулевая настройка и быстрое подключение к ИИ
Как только появляется интернет, сервис `rhm-live-agent.service` (`python3 -m core.agent --ephemeral-liveusb`) сам регистрирует временный узел (`live-*`) на шлюзе, получает эфемерный 24-часовой токен `lk_…` и выводит QR-код на мобильно-адаптивную страницу `/connect/<session_code>`.

На странице подключения одним нажатием копируются:
- **Gemini (`/sse`)**: `https://<gateway>/sse?user=<live_node_id>&token=<lk_...>`
- **ChatGPT (`/mcp`)**: `https://<gateway>/mcp?user=<live_node_id>&token=<lk_...>`
- **Claude**: ссылка для Custom Connectors (`/mcp`), CLI-команда `claude mcp add --transport http ...` для Claude Code или готовый JSON-фрагмент `mcpServers` для Claude Desktop / Cursor.

### Поддержка всех файловых систем из коробки

Все драйверы и утилиты уже находятся в SquashFS — работать с дисками Windows, Linux и macOS можно сразу после загрузки, даже без доустановки пакетов:

| Семейство | Файловая система | Пакеты и драйверы в ISO | Режим работы и нюансы |
| :--- | :--- | :--- | :--- |
| **Кроссплатформенные** | **exFAT** | Ядерный `exfat`, `exfatprogs`, `exfat-fuse` | Полный R/W, `fsck.exfat`, `mkfs.exfat`. |
| | **FAT32 / FAT16 (`vfat`)** | `dosfstools` | Полный R/W, работа с EFI System Partition (ESP). |
| **Windows** | **NTFS** | Ядерный `ntfs3` + `ntfs-3g` | Полный R/W. При «грязном» томе или Fast Startup — безопасный fallback на `ntfs-3g` (`ro`) или `ntfsfix`. |
| | **BitLocker** | `dislocker`, `cryptsetup` (`--type bitlk`) | Расшифровка по паролю или 48-значному Recovery Key с последующим монтированием NTFS. |
| **Linux** | **ext2 / ext3 / ext4** | `e2fsprogs` | Полный R/W, `e2fsck`, `resize2fs`, `dumpe2fs`. |
| | **Btrfs** | `btrfs-progs` | Полный R/W, сабволюмы, снапшоты, `btrfs check` / `restore`. |
| | **XFS** | `xfsprogs` | Полный R/W, `xfs_repair`, `xfs_info`. |
| | **F2FS** | `f2fs-tools` | Полный R/W (флешки, SSD, портативные консоли). |
| | **ZFS (OpenZFS)** | `zfsutils-linux`, `zfs-dkms` | Импорт пулов (`zpool import`), в том числе `readonly=on`. |
| | **LVM2 / mdadm / LUKS** | `lvm2`, `mdadm`, `cryptsetup`, `kpartx` | Ручная безопасная сборка массивов, расшифровка LUKS1/LUKS2 и активация томов. |
| **macOS** | **HFS / HFS+** | `hfsplus`, `libfshfs-utils`, ядерный `hfsplus` | Надёжный R/O и R/W (для журналируемых томов при необходимости `force,rw`). |
| | **APFS** | `apfs-dkms`, `apfsprogs`, `apfs-fuse` (собирается из исходников в chroot-хуке в `/usr/local/bin/apfs-fuse`), `libfsapfs-utils` | Незашифрованный APFS — нативный `apfs-dkms` (по умолчанию R/O; запись экспериментальная). Зашифрованный **FileVault** — R/O через `apfs-fuse`. Битые контейнеры — `fsapfsmount` (`libfsapfs-utils`). |

### Встроенная библиотека скиллов (`list_skills`, `load_skill`)
Чтобы не забивать контекст модели тысячами строк инструкций, LiveUSB использует прогрессивную загрузку скиллов:
- `list_skills` возвращает только компактные метаданные (имя, описание, категорию, теги, ОС, уровень риска).
- `load_skill` подгружает полный `SKILL.md` (или конкретный справочный файл из `references/`) только тогда, когда задача этого требует.

В образ встроены **4 специализированных LiveUSB-скилла** (`skills/liveusb/`):
1. `windows-offline-boot-repair` — починка UEFI/ESP и BCD, работа с оффлайн-реестром (`chntpw`, `libhivex-bin`), `ntfsfix`, расшифровка BitLocker (`dislocker` / `cryptsetup --type bitlk`).
2. `linux-chroot-grub-luks-rescue` — безопасная сборка `mdadm` → `cryptsetup open` → `vgchange -ay` → `chroot` → восстановление GRUB/systemd-boot и `initramfs`.
3. `macos-apfs-hfs-recovery` — аккуратное чтение и выгрузка данных с APFS (`apfs-dkms`, `apfs-fuse` для FileVault, `libfsapfs-utils`) и HFS+.
4. `ram-hardware-live-diagnostics` — инвентаризация железа (`dmidecode`, `lshw`, `lspci`, `lsusb`, `sensors`), проверка ECC (`edac-utils`), тест ОЗУ (`memtester`), live-дамп памяти (`/proc/kcore` работает из коробки; `avml` не предустановлен в ISO и используется опционально при отдельной установке).

Дополнительно скрипт `ops/live-usb/fetch-skills.sh` позволяет подтянуть проверенные GitHub-скиллы по манифесту `ops/live-usb/skills-manifest.json` с фиксацией точных commit SHA в `ops/live-usb/skills-lock.json` и автоматическим сканированием офлайн-сканером **NVIDIA SkillSpector** (`--recursive --no-llm`) на prompt injection и tool poisoning.

---

## Пошаговая инструкция

> **Примечание:** готовые ISO-релизы пока не публикуются — образ собирается локально на хосте или ВМ с Debian 13 (Trixie).

### Шаг 1. Сборка ISO-образа на Debian

Сборка выполняется на хосте или виртуальной машине с **Debian 13 (Trixie)**.

Установите зависимости:

```bash
sudo apt-get update
sudo apt-get install -y \
  live-build debootstrap squashfs-tools xorriso \
  grub-efi-amd64-bin grub-pc-bin mtools dosfstools \
  curl jq git ca-certificates
```

Клонируйте репозиторий и запустите сборку ISO (флаг `--with-external-skills` подгрузит внешние скиллы через `fetch-skills.sh`, а `--build` сразу запустит `lb build`):

```bash
git clone https://github.com/LevRa7/LiveUSBAgent-mcp.git
cd LiveUSBAgent-mcp
sudo ./ops/live-usb/build-iso.sh --with-external-skills --build
```

Полезные опции `build-iso.sh`:
- `--work-dir PATH` (или переменная `WORK_DIR`) — рабочий каталог сборки (по умолчанию `/tmp/rhm-live-build`; если в `/tmp` мало места, укажите, например, `--work-dir /var/tmp/rhm-live-build`).
- `--gateway HOST` (или `MESH_GATEWAY`) — адрес вашего шлюза MCP (по умолчанию `smart-server.online`).
- `--distro NAME` / `--arch ARCH` — выпуск и архитектура Debian (по умолчанию `trixie` / `amd64`).
- Без флага `--build` скрипт только подготовит конфигурацию `live-build`, после чего сборку можно запустить вручную: `cd /tmp/rhm-live-build && sudo lb build`.

Готовый гибридный образ (`live-image-amd64.hybrid.iso`) появится в `WORK_DIR`.

### Шаг 2. Проверка образа в QEMU (опционально, но рекомендуется)

Перед записью на флешку удобно проверить образ в QEMU/KVM — скрипт `test-qemu.sh` умеет автоматически создавать виртуальные диски с `exfat`, `ntfs`, `ext4`, `btrfs`, `xfs` и `vfat`:

```bash
# Установка зависимостей на Debian/Ubuntu
sudo apt-get install -y \
  qemu-system-x86 ovmf \
  exfatprogs ntfs-3g e2fsprogs btrfs-progs xfsprogs dosfstools

# Запуск в режиме UEFI с набором виртуальных тестовых дисков
./ops/live-usb/test-qemu.sh --mode uefi --create-fs-disks
```

Другие полезные режимы `test-qemu.sh`:
- `./ops/live-usb/test-qemu.sh --mode bios --ram 2048` — проверка Legacy BIOS и работы с малым объёмом ОЗУ (< 4 ГБ).
- `./ops/live-usb/test-qemu.sh --headless --create-fs-disks` — текстовая serial-консоль для CI/автопроверки.
- `./ops/live-usb/test-qemu.sh --dry-run --mode uefi --create-fs-disks` — показать итоговую команду QEMU без запуска.

### Шаг 3. Запись образа на USB-флешку

Образ гибридный (поддерживает **UEFI** и **Legacy BIOS**). Выберите удобный способ:

- **Ventoy (удобнее всего для мультизагрузочной флешки):** установите [Ventoy](https://www.ventoy.net/) на флешку и просто скопируйте `.iso` в её корень. Если при старте возникнут нюансы с обычным режимом, выберите в меню Ventoy **Grub2 mode**.
- **Rufus (Windows):** выберите флешку и `.iso`, нажмите **СТАРТ**. Когда Rufus предложит режим записи, выберите **DD-образ (DD Image)** — это сохранит гибридную разметку UEFI/BIOS без искажений.
- **balenaEtcher (Windows / Linux / macOS):** **Flash from file** → выберите `.iso` → **Select target** → **Flash!**.
- **`dd` (Linux / macOS):** сначала проверьте имя устройства через `lsblk` (Linux) или `diskutil list` (macOS), чтобы не перезаписать системный диск:

```bash
# Замените /dev/sdX на имя вашей флешки (без номера раздела!)
sudo dd if=/tmp/rhm-live-build/live-image-amd64.hybrid.iso of=/dev/sdX bs=4M status=progress oflag=sync
sync
```

### Шаг 4. Загрузка с флешки

1. Вставьте флешку, включите ПК и откройте **Boot Menu** (обычно `F12`, `F8`, `F11`, `Esc` или `Option/Alt` на Intel Mac).
2. **Важно про Secure Boot:** в образ включены подписанные `shim-signed` и ядро Debian, поэтому базовая загрузка пройдёт и с включённым Secure Boot. Однако неподписанные out-of-tree DKMS-модули — **`apfs-dkms`** (Apple APFS), **`zfs`** (OpenZFS) и **`broadcom-sta-dkms`** (часть Wi-Fi адаптеров Broadcom) — будут заблокированы ядром. Если вам нужны APFS, ZFS или Broadcom Wi-Fi, **отключите Secure Boot** в UEFI/BIOS (либо подпишите модули собственным MOK-ключом).
3. Выберите пункт в меню **GRUB**:
   - **`Remote-Harness LiveUSB (Load to RAM / toram, >= 4 GB RAM)`** — рекомендуется, если на машине **4 ГБ ОЗУ и больше**. Весь образ копируется в оперативную память, система работает максимально быстро, а флешку после загрузки можно вытащить.
   - **`Remote-Harness LiveUSB (Low RAM < 4 GB, boot from USB)`** — для старых ПК с **менее 4 ГБ ОЗУ**. Образ читается напрямую с USB; флешку нельзя извлекать до выключения.
   - **`Memtest86+`** — аппаратный тест оперативной памяти до запуска Linux.

### Шаг 5. Сеть, QR-код и подключение ИИ

1. **Подключите интернет любым способом:**
   - **Кабель Ethernet** — поднимется автоматически.
   - **iPhone по USB** — включите *Настройки → Режим модема*, подключите кабель, разблокируйте телефон и нажмите **«Доверять»** (*Trust*).
   - **Android по USB** — подключите кабель и включите **USB-модем** в шторке/настройках сети.
   - **Wi-Fi** — выберите сеть на экране Kiosk (при необходимости нажмите **`Обновить список`** или **`Скрытая сеть`**) и введите пароль с клавиатуры или экранной клавиатуры.
   - **Консольный fallback / локальная оболочка** — нажмите `Ctrl+Alt+F2` для `firstboot_tui.py`, где доступны `nmtui` и локальный `bash` через **`[S] Shell (bash)`**.
2. Через несколько секунд агент сам зарегистрирует временный узел и выведет на экран готовности **крупный QR-код**, короткую ссылку `/connect/<session_code>`, сводку по найденным дискам и кнопки **`Сменить Wi-Fi`** и **`Новый токен`**.
3. Отсканируйте QR-код с телефона (или откройте ссылку на другом ПК) и скопируйте готовый URL:
   - **Gemini**: добавьте MCP-сервер с URL вида `https://<gateway>/sse?user=live-...&token=lk_...`.
   - **ChatGPT**: *Settings / Apps → Add custom MCP server* → вставьте `https://<gateway>/mcp?user=live-...&token=lk_...`.
   - **Claude**: вставьте `/mcp` URL в Custom Connectors, используйте команду `claude mcp add --transport http ...` или скопируйте готовый JSON `mcpServers` со страницы `/connect/<session_code>`.
4. **Примеры первых запросов к ИИ:**
   - *«Проверь связь через `mesh_status`, покажи все диски, разделы и SMART-состояние через `rescue_info`.»*
   - *«Windows не грузится после обновления. Найди EFI-раздел и системный том NTFS, проверь состояние диска и предложи план восстановления.»*
   - *«Найди раздел BitLocker, помоги расшифровать его моим ключом восстановления и скопируй папку `Documents` на внешний SSD.»*
   - *«Посмотри, сколько планок ОЗУ установлено (`ram_info`), нет ли ошибок ECC, и спланируй тест памяти через `memtester`.»*

---

## Доступные MCP-инструменты и сценарии применения

### Специализированные LiveUSB-инструменты

| Инструмент | Назначение | Основные параметры / возможности |
| :--- | :--- | :--- |
| **`rescue_info`** | Read-only диагностика железа, дисков, ОЗУ и установленных ОС. Никогда не меняет данные на дисках. | Секции (`section`): `summary`, `all`, `disks`, `smart`, `partitions`, `raid_lvm`, `installed_os`, `boot_efi`, `win_artifacts`, `dmesg_io`, `ram_info`, `filesystems`. Можно указать конкретный `device` (например `/dev/nvme0n1`). |
| **`rescue_action`** | Контролируемые операции восстановления дисков, ОС и тестирования ОЗУ. По умолчанию работает в `dry_run=true` и read-only. | Действия (`action`): `mount_safe`, `mount_auto` (автовыбор драйвера для `exfat`, `ntfs`, `ext2/3/4`, `btrfs`, `xfs`, `f2fs`, `hfsplus`, `apfs`, `bitlocker`, `luks`, `zfs`), `umount`, `fsck_check`, `ntfs_fix`, `grub_repair`, `win_sam_reset`, `ddrescue_plan`, `chroot_exec`, `ram_test_plan`, `ram_dump_plan`. |
| **`list_skills`** | Каталог локально установленных скиллов (только компактные метаданные). | Фильтры: `category`, `query`, `os` (`linux`, `windows`, `macos`). |
| **`load_skill`** | Загрузка полного текста `SKILL.md` или справочного файла из `references/`. | `name` (имя скилла), опционально `reference_file` (относительный путь внутри `references/`). |

### Базовые системные инструменты узла

| Инструмент | Назначение |
| :--- | :--- |
| **`mesh_status`** | Проверка доступности узла, режима доступа и состояния соединения. |
| **`system_info`**, **`system_vitals`** | Общая сводка по хосту (ОС, диски, память, процессы) и текущая загрузка CPU / RAM / Disk. |
| **`bash_exec`** | Выполнение коротких shell-команд на LiveUSB-узле. |
| **`read_file`**, **`write_file`**, **`edit_file`** | Чтение, запись и точечное редактирование файлов (конфиги, `fstab`, логи, скрипты). |
| **`list_dir`**, **`glob_find`**, **`grep_search`** | Просмотр каталогов, поиск файлов по маске и рекурсивный поиск по содержимому. |
| **`run_job`**, **`job_output`**, **`job_list`**, **`job_kill`** | Запуск и контроль долгих фоновых задач (`ddrescue`, `rsync`, `memtester`, `photorec`, `fsck`), чтобы не упираться в таймаут одного вызова. |
| **`node_control`** | Просмотр и управление параметрами узла (режим доступа, рабочая директория, пауза/возобновление). |

### Типовые сценарии применения

- **Ремонт Windows и работа с BitLocker:** проверка диска и `dmesg_io`, расшифровка тома через `dislocker` или `cryptsetup --type bitlk`, снятие dirty-флага (`ntfsfix`), проверка EFI System Partition (`efibootmgr`), правка оффлайн-реестра и разблокировка локальной учётной записи (`chntpw`, `libhivex-bin`).
- **Восстановление Linux (GRUB, LUKS, LVM, RAID):** аккуратная сборка массивов `mdadm`, открытие зашифрованных разделов `cryptsetup`, активация томов `lvm2`, вход в `chroot` (`chroot_exec`), переустановка GRUB и пересборка `initramfs`.
- **Спасение данных с macOS (APFS / HFS+):** монтирование незашифрованного APFS через `apfs-dkms` (read-only по умолчанию), чтение зашифрованных томов FileVault через `apfs-fuse`, разбор повреждённых контейнеров через `libfsapfs-utils`, работа с HFS+ (`hfsplus`, `libfshfs-utils`) и выгрузка файлов на внешний накопитель.
- **Клонирование умирающих дисков:** сначала оценка SMART и ошибок I/O ядра (`rescue_info`), затем генерация безопасного плана `ddrescue_plan` с обязательным mapfile (`log_file`) для возобновляемого посекторного копирования на внешний диск, а при необходимости — восстановление разделов и файлов через `testdisk`, `photorec` и `sleuthkit`.
- **Диагностика ОЗУ и железа:** инвентаризация планок памяти и слотов (`dmidecode`), проверка ECC-ошибок (`edac-utils`), тест свободной памяти без перезагрузки (`memtester` через `ram_test_plan`), аппаратный тест `Memtest86+` из GRUB, снятие телеметрии (`smartctl`, `nvme-cli`, `sensors`) и планирование дампа ОЗУ (`ram_dump_plan` через `/proc/kcore` из коробки; `avml` не предустановлен в ISO и используется опционально при отдельной установке).

---

## Безопасность и защитные механизмы

LiveUSB-сессия намеренно даёт ИИ-агенту широкие права для реального ремонта системы — поэтому важно чётко понимать модель доверия и встроенные страховки.

### Модель доверия и жизненный цикл сессии
- **Полный root-доступ (`MESH_ACCESS_MODE=full`, `MESH_WORKSPACE=/`)**: сервис `rhm-live-agent.service` запускается от `root` без ручных подтверждений на каждую команду, чтобы вы могли полноценно чинить систему удалённо из чата.
- **Экран с QR-кодом и токеном = физическая граница доверия**: любой, у кого есть ссылка `/connect/<session_code>` или токен `lk_…`, получает полный контроль над загруженной машиной и всеми подключёнными к ней дисками. **Не показывайте QR-код и токен посторонним**, не отправляйте скриншоты экрана подключения в публичные чаты и нажимайте **`Новый токен`**, если подозреваете утечку.
- **Эфемерность и отзыв токена**:
  - срок жизни сессии ограничен **24 часами** (TTL);
  - при нажатии **`Новый токен`** в Kiosk UI или при штатном завершении агента (`atexit` при корректном выключении/перезагрузке) токен немедленно отзывается на шлюзе через `/v1/liveusb/revoke`; при жёстком отключении питания (hard power-off) действует fallback по 24-часовому TTL;
  - на шлюзе действует лимит одновременно активных LiveUSB-сессий (`LIVEUSB_MAX_ACTIVE`);
  - файл состояния `session.json` создаётся с правами **`0600`**, намеренно не хранит секретный `device_token`, а сама конфигурация сессии живёт только в оперативной памяти (`/run/rhm-live/`) и бесследно исчезает после выключения питания.

### Защита дисков, данных и оперативной памяти
- **Отключённое автомонтирование (`99-rhm-no-automount.rules`)**: при загрузке система не трогает и не монтирует разделы хоста самостоятельно — это защищает «сыплющиеся» и битые диски от лишних записей.
- **Отключённая автоактивация LVM и RAID**: в `/etc/lvm/lvm.conf` прописано `auto_activation_volume_list = []`, а в `/etc/mdadm/mdadm.conf` — `AUTO -all`, чтобы LiveUSB не попытался автоматически пересобрать деградировавший массив при старте.
- **Безопасные значения по умолчанию в `rescue_action`**:
  - `dry_run=True` по умолчанию — агент сначала видит точную команду и проверки, прежде чем выполнить действие;
  - монтирование по умолчанию выполняется в режиме **только чтения** (`writable=False`, опции `ro,noload`);
  - точки монтирования (`mountpoint`) строго ограничены каталогами `/mnt/rescue/*` и `/mnt/scratch/*`.
- **Защита RAM-диска от переполнения (OOM)**: `rescue_action` проверяет путь назначения по `/proc/mounts` и **запрещает** писать образы дисков (`ddrescue_plan`) и дампы ОЗУ (`ram_dump_plan`) в энергозависимые ФС (`tmpfs`, `ramfs`, `overlay`, `squashfs`, `devtmpfs`, каталоги `/tmp`, `/run`, `/var/tmp`). Логи `journald` ограничены 50 МБ в памяти (`Storage=volatile`, `SystemMaxUse=50M`).
- **APFS только для чтения**: `mount_auto` всегда монтирует APFS в **read-only**; зашифрованные тома FileVault открываются через `apfs-fuse` тоже только для чтения. Экспериментальная запись через `linux-apfs-rw` в `rescue_action` не экспонируется: запрос `writable=true` понижается до `ro` с предупреждением, поэтому инструмент никогда не пишет в APFS.

### Сетевая и локальная безопасность
- **`active_uplink_lock`**: агент отслеживает активный сетевой интерфейс, через который идёт туннель к шлюзу, и не даёт случайно положить его (например, через `ip link set down`) и потерять удалённый доступ к машине.
- **Локальная изоляция и защита Kiosk-сервера**: веб-интерфейс мастера настройки (`kiosk_server.py`) слушает строго `127.0.0.1:8787` (недоступен из локальной сети или интернета) и проверяет loopback-заголовки `Host`/`Origin`, а также CSRF-защиту на изменяющих запросах.
- **Защита страницы подключения `/connect/<session_code>`**: шлюз отдаёт страницу сопряжения с non-wildcard CORS, CSP nonce и заголовком `X-Frame-Options: DENY` для защиты от clickjacking и межсайтовой кражи токена.
- **Защита транспорта к шлюзу**: исходящие HTTP-запросы агента к шлюзу требуют **TLS 1.2+** с отключёнными редиректами.
- **Защита `load_skill` от path traversal**: при загрузке справочных файлов из `references/` любые попытки выхода за пределы каталога скилла (`..`, абсолютные пути) отклоняются.
- **Аудит внешних скиллов**: скрипт `ops/live-usb/fetch-skills.sh` фиксирует версии внешних репозиториев по commit SHA (`ops/live-usb/skills-lock.json`) и прогоняет загруженные файлы через **NVIDIA SkillSpector** (`--recursive --no-llm`) для выявления prompt injection и вредоносных инструкций.

---

## Дополнительная документация

- **Руководство по сборке LiveUSB ISO и тестированию в QEMU:** [ops/live-usb/README.md](ops/live-usb/README.md)
- **Сборка, запись и загрузка LiveUSB (англ.):** [docs/liveusb/README.md](docs/liveusb/README.md)
- **Стартовые экраны Kiosk (сеть vs QR):** [docs/liveusb/STARTUP_UI.md](docs/liveusb/STARTUP_UI.md)
- **Диагностика проблем LiveUSB — нет Wi-Fi, туннеля, ошибки I/O, OOM (англ.):** [docs/liveusb/TROUBLESHOOTING.md](docs/liveusb/TROUBLESHOOTING.md)
- **Английская версия README:** [README.md](README.md)
- **Архитектура и план развития:** [ProjectPlan.md](ProjectPlan.md)
