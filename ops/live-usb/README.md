# LiveUSBAgent-mcp Live USB (`ops/live-usb`)

Сборка, локальное тестирование в QEMU/KVM и запись загрузочного rescue-образа **LiveUSBAgent-mcp** на базе Debian 13 (Trixie).

---

## 1. Сборка ISO на Debian

Сборка выполняется на хосте или виртуальной машине с **Debian 13 (Trixie)**.

### Зависимости

```bash
sudo apt-get update
sudo apt-get install -y \
  live-build debootstrap squashfs-tools xorriso \
  grub-efi-amd64-bin grub-pc-bin mtools dosfstools \
  curl jq git ca-certificates
```

### Профили размера (`--profile`) и архитектуры (`--arch`)

`build-iso.sh` поддерживает 3 профиля размера и 3 архитектуры:

```bash
# Компактный GUI (~750-950 МБ, лёгкий WebKit-киоск surf без Chromium/LXDE)
sudo ops/live-usb/build-iso.sh --arch amd64 --profile compact --build

# Минимальный TUI (~450-600 МБ, без X11, консольный firstboot_tui.py + QR + nmtui)
sudo ops/live-usb/build-iso.sh --arch amd64 --profile lite --build

# ARM64 (UEFI, cloudflared-linux-arm64)
sudo ops/live-usb/build-iso.sh --arch arm64 --profile compact --build

# 32-bit x86 (автоматически выбирает Debian 12 bookworm + 686-pae + cloudflared-linux-386 + lite)
sudo ops/live-usb/build-iso.sh --arch i386 --build
```

### Базовая сборка (без внешних скиллов)

Флаг `--build` запускает `lb build` сразу после подготовки конфигурации `live-build`:

```bash
cd /path/to/LiveUSBAgent-mcp
sudo ops/live-usb/build-iso.sh --build
```

### Сборка с внешними скиллами (`--with-external-skills`)

Если нужно включить в образ внешние скиллы из `ops/live-usb/skills-manifest.json`:

```bash
sudo ops/live-usb/build-iso.sh --with-external-skills --build
```

### Настройка через переменные окружения и ручной запуск `lb build`

По умолчанию рабочая директория — `/tmp/rhm-live-build` (`DISTRO=trixie`, `ARCH=amd64`). При нехватке места в `/tmp` укажите другой каталог (через `WORK_DIR` или `--work-dir`):

```bash
sudo WORK_DIR=/var/tmp/rhm-live-build ops/live-usb/build-iso.sh --with-external-skills --build
```

Если опустить флаг `--build`, скрипт только подготовит конфигурацию `live-build` (удобно для инспекции перед сборкой), после чего сборку ISO можно запустить вручную в `WORK_DIR`:

```bash
sudo ops/live-usb/build-iso.sh --with-external-skills
cd "${WORK_DIR:-/tmp/rhm-live-build}"
sudo lb build
```

Готовый гибридный образ (`*.iso`) появится в `WORK_DIR`.

---

## 2. Тестирование ISO в QEMU перед записью на флешку

Скрипт `ops/live-usb/test-qemu.sh` поднимает QEMU/KVM с пользовательской сетью (`virtio-net-pci`), логирует вывод загрузки в файл с меткой времени (`qemu-boot-*.log`) и умеет создавать тестовые виртуальные диски со всеми основными ФС.

### Зависимости на тестовом хосте

```bash
# Debian / Ubuntu
sudo apt-get install -y \
  qemu-system-x86 ovmf \
  exfatprogs ntfs-3g e2fsprogs btrfs-progs xfsprogs dosfstools

# Arch Linux
sudo pacman -S qemu-full edk2-ovmf exfatprogs ntfs-3g e2fsprogs btrfs-progs xfsprogs dosfstools
```

### Примеры запуска

```bash
# 1. UEFI-загрузка (режим по умолчанию, автопоиск *.iso в WORK_DIR, 4096 МБ ОЗУ)
ops/live-usb/test-qemu.sh

# 2. Явный путь к ISO + виртуальные virtio-blk диски (exfat, ntfs, ext4, btrfs, xfs, vfat)
ops/live-usb/test-qemu.sh \
  --iso /tmp/rhm-live-build/live-image-amd64.hybrid.iso \
  --mode uefi \
  --create-fs-disks

# 3. Проверка Legacy BIOS и сценария с малым объёмом ОЗУ (< 4 ГБ)
ops/live-usb/test-qemu.sh --mode bios --ram 2048

# 4. Headless-режим для CI / автоматической проверки через serial-консоль
ops/live-usb/test-qemu.sh --headless --create-fs-disks

# 5. Dry-run: вывести точную команду QEMU без запуска (безопасно на хосте без QEMU)
ops/live-usb/test-qemu.sh --dry-run --mode uefi --create-fs-disks
```

---

## 3. Запись ISO на USB-флешку

Образ гибридный (UEFI + Legacy BIOS). Подойдёт любой из способов ниже:

### Ventoy (рекомендуется для мультизагрузочной флешки)
1. Установите [Ventoy](https://www.ventoy.net/) на флешку.
2. Просто скопируйте готовый `.iso` в корень раздела Ventoy.
3. При загрузке выберите образ в меню Ventoy (при проблемах с обычным режимом используйте **Grub2 mode**).

### Rufus (Windows)
1. Выберите USB-накопитель и скачанный `.iso`.
2. Нажмите **СТАРТ**.
3. Если Rufus предложит выбор между *ISO Image* и *DD Image*, выберите **Записать в режиме DD-образа (DD Image)** — это гарантирует корректную работу гибридной таблицы разделов UEFI/BIOS.

### `dd` (Linux / macOS)
Сначала убедитесь в правильном имени устройства через `lsblk` (на Linux) или `diskutil list` (на macOS):

```bash
# Linux (замените /dev/sdX на вашу флешку, НЕ указывайте номер раздела!)
sudo dd if=/tmp/rhm-live-build/live-image-amd64.hybrid.iso of=/dev/sdX bs=4M status=progress oflag=sync
sync
```

### balenaEtcher (Windows / Linux / macOS)
1. Откройте balenaEtcher → **Flash from file** → выберите `.iso`.
2. Выберите целевую флешку (**Select target**) и нажмите **Flash!**.

---

## 4. Важные примечания по железу и загрузке

### Secure Boot и out-of-tree DKMS-модули
Образ содержит подписанные `shim-signed` и ядро Debian, поэтому базовая загрузка возможна при включённом Secure Boot. Однако **неподписанные out-of-tree DKMS-модули** будут заблокированы ядром, если Secure Boot активен:
- `linux-apfs-rw` (чтение/запись разделов Apple APFS),
- `zfs` (пулы OpenZFS),
- `broadcom-sta-dkms` (часть Wi-Fi адаптеров Broadcom).

Если на целевой машине нужны APFS, ZFS или Broadcom Wi-Fi — **отключите Secure Boot в настройках UEFI/BIOS** (либо подпишите модули собственным MOK-ключом).

### Пункты меню GRUB: `toram` (>= 4 ГБ ОЗУ) vs Low-RAM (< 4 ГБ ОЗУ)
- **Стандартный режим (`toram`, рекомендуется при >= 4 ГБ ОЗУ):** образ `filesystem.squashfs` полностью копируется в оперативную память при старте. После загрузки флешку можно безопасно извлечь, а система работает максимально отзывчиво.
- **Режим Low-RAM (без `toram`, для машин с < 4 ГБ ОЗУ):** система читает squashfs напрямую с USB-носителя, не расходуя ОЗУ под весь образ. В этом режиме **нельзя извлекать флешку** до выключения компьютера.

### USB-модем с iPhone (iPhone USB Tethering)
Чтобы раздать интернет на Live USB по кабелю с iPhone:
1. Включите **Режим модема** (*Personal Hotspot*) на iPhone и подключите его к ПК по USB.
2. Разблокируйте iPhone и в появившемся системном окне нажмите **«Доверять»** (*Trust This Computer*), затем введите код-пароль.
3. После подтверждения доверия `usbmuxd` / драйвер `ipheth` поднимут сетевой интерфейс, и NetworkManager автоматически получит IP по DHCP.
