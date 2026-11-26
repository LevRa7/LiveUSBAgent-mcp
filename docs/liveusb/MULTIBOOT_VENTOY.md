# Гибридная сервисная флешка (Ventoy + LiveUSBAgent + WinPE)

`LiveUSBAgent-mcp` закрывает большинство задач диагностики, работы с дисками (GParted, `parted`, `gdisk`, `ldmtool`), бэкапов (`partclone`, `restic`, `gddrescue`), офлайн-антивируса (`clamav`, `yara`), аудита Windows (`win_diag`, `core.autoruns`) и ремонта загрузчиков (`win_boot_repair`, `grub_repair`, `systemd_boot_repair`).

Однако несколько операций Windows не имеют полноценных аналогов в Linux:
- глубокая починка повреждённой NTFS (`chkdsk /f /r` — `ntfsfix` сбрасывает только dirty-флаг);
- пересборка сложного `BCD` с нуля (`bcdboot C:\Windows /s S: /f UEFI`);
- восстановление системных файлов (`sfc /scannow /offbootdir=...`, `dism /Image:... /Cleanup-Image`).

Оптимальная схема для сервисного инженера — мультизагрузочная флешка на базе **Ventoy**.

## 1. Рекомендуемая структура флешки (от 32–64 ГБ, exFAT/NTFS)

```text
/
├── LiveUSBAgent-0.1.0-amd64-full.iso     # Основная среда с ИИ-агентом, GParted, ClamAV, partclone
├── WinPE-WinRE-x64.iso                   # Компактный WinPE/WinRE для chkdsk, bcdboot, sfc, dism
├── ISO/
│   ├── Win11_24H2_x64.iso                # Дистрибутив Windows (загрузка или раскатка через win_deploy)
│   └── debian-13-netinst-amd64.iso       # Дистрибутив Linux
├── Tools/
│   └── (опционально: легальные UEFI-утилиты, добавляемые пользователем)
└── Scratch/                              # Рабочая область (монтируется в /mnt/scratch внутри LiveUSB)
    ├── sam-backups/
    ├── boot-backups/
    ├── part-backups/
    └── quarantine/
```

> **Важно:** Внутри `LiveUSBAgent` все бэкапы (`SAM`, `BCD`, MBR, таблицы разделов, образы `partclone` и карантин `malware_scan`) запрещено сохранять в оперативную память (`tmpfs` / `/tmp`). Примонтируйте раздел флешки или внешний диск в `/mnt/scratch`.

## 2. Когда какой образ загружать

| Задача | Среда | Инструмент |
| :--- | :--- | :--- |
| Диагностика железа, SMART/NVMe, RAM | `LiveUSBAgent` / GRUB | `rescue_info` (`smart`, `ram_info`, `dmesg_io`), `Memtest86+`, `stress-ng` |
| Диагностика нестартующей Windows | `LiveUSBAgent` | `rescue_info` (`win_artifacts`, `win_diag`), `core.autoruns` |
| Ресайз/перенос/конвертация разделов | `LiveUSBAgent` | **GParted** (меню «Приложения» в Kiosk) или `partition_ops` |
| Снятие/восстановление образа раздела | `LiveUSBAgent` | `partition_backup` / `partition_restore` (`partclone`), `ddrescue_plan` |
| Офлайн-проверка на вирусы и карантин | `LiveUSBAgent` | `malware_scan` (`clamav` / `yara`) |
| Сброс пароля локальной учётки Windows | `LiveUSBAgent` | `win_sam_reset` (`create_backup=true`) + интерактивный `chntpw` |
| Починка UEFI/MBR загрузчика Windows | `LiveUSBAgent` → `WinPE` | `win_boot_repair`; при битом BCD — `bcdboot` из WinPE |
| Починка повреждённой NTFS / системных файлов | `WinPE` | `chkdsk /f`, `sfc /scannow /offbootdir=...`, `dism /Cleanup-Image` |
| Чистая переустановка Windows | `LiveUSBAgent` или Ventoy | `win_deploy` (`wimlib-imagex`) или прямой запуск ISO из меню Ventoy |

## 3. Secure Boot и BitLocker

- **Secure Boot:**
  - `Memtest86+`, unsigned DKMS-модули (`zfs`, `apfs`, `amneziawg`) и сторонние UEFI-утилиты требуют отключения Secure Boot в UEFI BIOS (либо добавления MOK-ключа Ventoy).
- **BitLocker:**
  - Перед любыми операциями с диском (диагностика `win_diag`, сброс `SAM`, ремонт `BCD`, сканирование `malware_scan`) том BitLocker необходимо разблокировать 48-значным ключом восстановления (`mount_auto` с `recovery_key` или `cryptsetup open --type bitlk`).
- **Microsoft Account (MSA) и PIN (Windows Hello):**
  - `chntpw` работает только с локальными учётными записями `SAM`. Облачный пароль Microsoft Account сбрасывается только онлайн через сервис Microsoft; офлайн можно лишь активировать локальную учётную запись администратора для доступа к данным.
