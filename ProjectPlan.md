# План разработки: `remote-harness-liveusb`

> Эволюция проекта `remote-harness-mcp` в загрузочный LiveUSB-агент для системного администрирования, восстановления ОС (Windows / Linux / macOS), спасения данных и низкоуровневой диагностики железа и ОЗУ с управлением напрямую из **Gemini**, **ChatGPT** и **Claude** по MCP.

---

## 0. Аудит текущей кодовой базы (`remote-harness-liveusb`)

Проект является форком `remote-harness-mcp` и уже содержит рабочий транспорт и заготовки, но требует архитектурной доработки под реальные сценарии LiveUSB:

| Компонент в репозитории | Текущее состояние | Что нужно изменить / дописать |
| :--- | :--- | :--- |
| `core/agent.py` + `gateway.py` | Рабочий WebSocket-туннель, `MESH_TOKEN`, `MESH_LINK_KEY` (`lk_…`), `/sse` (Gemini) и `/mcp` (ChatGPT/Streamable HTTP). | Добавить **ephemeral LiveUSB-регистрацию** (генерация временного узла + `lk_…` после появления сети без ручного `install.sh`) и автоперевод узла в режим `access_mode=full` по токену сессии. |
| `core/rescue.py` | Написаны базовые контракты `rescue_info` / `rescue_action` (`lsblk`, `smartctl`, `dmesg`, `mount_safe`, `ddrescue_plan`, `ntfs_fix`, `grub_repair`), но **не подключены в `core/mcp_tools.py`**. | Подключить в `core/mcp_tools.py`; добавить мульти-ФС монтирование (APFS, HFS+, NTFS3/NTFS-3G, exFAT, ZFS, Btrfs, XFS, F2FS, LUKS, BitLocker, LVM/RAID) и секции диагностики ОЗУ (`mem`, `dmi`, `kcore`). |
| `ops/live-usb/build-iso.sh` | Черновик Debian Trixie `live-build`. **Баг:** systemd-юнит запускает legacy `rhm_mcp_server.py` вместо `python3 -m core.agent`. Нет GUI, нет iOS USB tethering, нет macOS ФС (HFS+/APFS), нет ZFS/BitLocker. | Перевести юнит на `python3 -m core.agent`; добавить графический Kiosk UI, полный пакет драйверов/firmware + DKMS, USB tethering iOS/Android и все ФС. |
| `ops/live-usb/firstboot_tui.py` | Простой текстовый баннер на `tty1` с `qrencode` и заглушкой URL. | Оставить как fallback-режим на `tty2` (headless/без GPU), а основным стартовым экраном сделать **графический Kiosk Wizard** на `tty1`. |
| `skills/` | Только один файл `skills/orchestrator.md`. | Встроить курируемую офлайн-библиотеку `SKILL.md` (сисадминство, DevOps, восстановление ОС/данных/ОЗУ) и MCP-инструменты прогрессивной загрузки `list_skills` / `load_skill`. |

---

## 1. Курируемая библиотека скиллов (из Reddit и GitHub)

Вместо заливки тысяч сырых скиллов в контекст LLM, в SquashFS (`/opt/remote-harness-mcp/skills/`) на этапе сборки ISO запекается проверенный набор `SKILL.md` (зафиксированный по commit SHA через [vercel-labs/skills](https://github.com/vercel-labs/skills) и просканированный офлайн-сканером [NVIDIA/SkillSpector](https://github.com/NVIDIA/SkillSpector) на prompt injection / tool poisoning).

### 1.1. Системное администрирование, сети и DevOps
- **[peterbamuhigire/linux-skills](https://github.com/peterbamuhigire/linux-skills)** (MIT) — 48 `SKILL.md` + 26 bash-скриптов `sk-*`: `linux-troubleshooting`, `linux-disaster-recovery`, `linux-disk-storage`, `linux-filesystem-snapshots` (LVM/Btrfs/ZFS), `linux-service-management` (`systemd`), `linux-log-management` (`journald`), `linux-network-admin`, `linux-kernel-modules`, `linux-perf-profiling`.
- **[HermeticOrmus/linux-sysadmin-skills](https://github.com/HermeticOrmus/linux-sysadmin-skills)** (MIT) — 5 компактных read-first сценариев (`sysadmin-diagnose`, `sysadmin-performance`, `sysadmin-security`, `sysadmin-monitor`, `sysadmin-maintain`).
- **[sickn33/agentic-awesome-skills](https://github.com/sickn33/agentic-awesome-skills)** (MIT) — выборочно забрать [linux-administration/SKILL.md](https://github.com/sickn33/agentic-awesome-skills/blob/main/plugins/agentic-awesome-skills-claude/skills/linux-administration/SKILL.md) и [block-storage/SKILL.md](https://github.com/sickn33/agentic-awesome-skills/blob/main/plugins/agentic-awesome-skills-claude/skills/block-storage/SKILL.md) (LVM, `lvextend`, `mdadm` RAID).
- **[github/awesome-copilot](https://github.com/github/awesome-copilot)** (MIT) — дистро-специфичные триаж-скиллы [debian-linux-triage/SKILL.md](https://github.com/github/awesome-copilot/blob/main/skills/debian-linux-triage/SKILL.md) и [arch-linux-triage/SKILL.md](https://github.com/github/awesome-copilot/blob/main/skills/arch-linux-triage/SKILL.md).
- **[NotHarshhaa/devops-skills](https://github.com/NotHarshhaa/devops-skills)** (MIT) и **[bregman-arie/devops-sre-skills](https://github.com/bregman-arie/devops-sre-skills)** (Apache-2.0) — read-only инцидент-триаж, аудит, Docker/K8s/CI-CD ревью.
- **[ForceInjection/nvme-programming-skill](https://github.com/ForceInjection/nvme-programming-skill)** — глубокая диагностика NVMe (CQE error codes, SMART, namespaces).
- **[alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills)** (MIT) — stdlib-only Python-скрипты без `pip`-зависимостей (идеально для LiveUSB).
- **MCP-серверы как референс / опциональные модули:** [rhel-lightspeed/linux-mcp-server](https://github.com/rhel-lightspeed/linux-mcp-server) (Apache-2.0, read-only диагностика Linux + `dmidecode`) и [mvo5/systemd-mcp](https://github.com/mvo5/systemd-mcp) (Rust Varlink introspection).

### 1.2. Восстановление ОС, данных и форензика ОЗУ
- **[danielrosehill/Claude-System-Recovery-Mode](https://github.com/danielrosehill/Claude-System-Recovery-Mode)** (MIT) — готовые промпты и команды восстановления системы (`/profile-os`, `/boot-log`, `/failed`, `/fix-packages`, `/network-check`).
- **[0xDarkMatter/claude-mods](https://github.com/0xDarkMatter/claude-mods)** (MIT) — кросс-ОС плейбуки `windows-ops`, [mac-ops/SKILL.md](https://github.com/0xDarkMatter/claude-mods/blob/main/skills/mac-ops/SKILL.md) (APFS/SMART/kernel panic), `net-ops`.
- **[mukul975/Anthropic-Cybersecurity-Skills](https://github.com/mukul975/Anthropic-Cybersecurity-Skills)** (Apache-2.0) — выборочно: Linux log forensics, disk image analysis (Sleuth Kit), offline Windows Registry hives, UEFI/MBR/BCD bootkit & boot diagnostics, Volatility 3 memory forensics.
- **[crt106/c3rb3r-btrfs-recovery](https://github.com/crt106/c3rb3r-btrfs-recovery)** (MIT) & **[soimy/c3rb3r-rescue](https://github.com/soimy/c3rb3r-rescue)** — read-only восстановление Btrfs CoW, сборка `mdadm`/LVM, дамп ОЗУ через LiME.
- **[x746b/winforensics-mcp](https://github.com/x746b/winforensics-mcp)** (MIT), **[x746b/mac_forensics-mcp](https://github.com/x746b/mac_forensics-mcp)** (MIT), **[x746b/mem_forensics-mcp](https://github.com/x746b/mem_forensics-mcp)** (MIT) — Linux-нативный офлайн-разбор Windows Registry/EVTX/NTFS `$MFT`/`$UsnJrnl`, macOS `fsck_apfs.log`/FSEvents/Plists и дампов памяти (LiME + Windows dumps + Rust `memoxide`).
- **[efij/AgentDFIR](https://github.com/efij/AgentDFIR)** (MIT) — одиночный статический Go-бинарник для read-only анализа образов и ФС (NTFS, ext4, XFS, Btrfs, LVM2, APFS, HFS+, exFAT) без монтирования.
- **[danielrosehill/BTRFS-Snapper-MCP](https://github.com/danielrosehill/BTRFS-Snapper-MCP)** (MIT) и **[hamsolodev/zsnoop-mcp](https://github.com/hamsolodev/zsnoop-mcp)** (MIT) — диагностика и откат снапшотов Btrfs и ZFS.
- **[yanacuti1121/Yana-AI — recovering-deleted-files-with-photorec/SKILL.md](https://github.com/yanacuti1121/Yana-AI/blob/main/.claude/skills/recovering-deleted-files-with-photorec/SKILL.md)**, **[marysatasselshaped667/skills-collection-1 (Sleuth Kit CLI)](https://github.com/marysatasselshaped667/skills-collection-1/blob/main/SKILLS/analyzing-slack-space-and-file-system-artifacts/SKILL.md)**, **[Tokeii0/memprocfs-skill](https://github.com/Tokeii0/memprocfs-skill)**, **[yaklang/hack-skills (LiME / `/proc/kcore`)](https://github.com/yaklang/hack-skills/blob/main/skills/memory-forensics-volatility/SKILL.md)**, **[danielrosehill/Claude-Slash-Commands (`check-drive-health.md`)](https://github.com/danielrosehill/Claude-Slash-Commands/blob/main/commands-flat/check-drive-health.md)**.
- **Идеи и утилиты с Reddit (`r/datarecovery`, `r/ClaudeAI`, `r/mcp`):**
  - Обёртка над `ddrescue` + `dislocker` для выборочного спасения файлов с умирающих/BitLocker NTFS-дисков ([r/datarecovery: `nextechinc/ddtriage`](https://www.reddit.com/r/datarecovery/comments/1s0whju/github_nextechincddtriage_ntfsaware_selective/)).
  - Восстановление имён и целостности после PhotoRec ([r/datarecovery: OpenRecovr](https://www.reddit.com/r/datarecovery/comments/1xsi3ai/open_recovr_free_opensource_mit_gui_over_photorec/)).
  - Прогрессивная фильтрация скиллов/тулов, чтобы не переполнять контекст ([r/mcp](https://www.reddit.com/r/mcp/comments/1t73igk/how_to_connect_100_mcp_servers_without_the/), [r/ClaudeAI](https://www.reddit.com/r/ClaudeAI/comments/1xti4sx/i_used_claude_code_to_sort_5087_claude_code/)).

### 1.3. Собственные кастомные `SKILL.md` (закрываем пробелы публичных реп)
Написать 4 специализированных скилла внутри `skills/liveusb/`:
1. `windows-offline-boot-repair/SKILL.md` — починка UEFI/ESP, BCD, `chntpw`/`hivex` (реестр, сброс локального пароля, включение/отключение драйверов), `ntfsfix`, расшифровка BitLocker (`dislocker` / `cryptsetup --type bitlk`).
2. `linux-chroot-grub-luks-rescue/SKILL.md` — безопасная сборка `mdadm` → `cryptsetup open` → `vgchange -ay` → `chroot` → переустановка GRUB/systemd-boot и `initramfs`.
3. `macos-apfs-hfs-recovery/SKILL.md` — безопасное чтение/копирование данных с APFS (`apfs-fuse` для зашифрованных FileVault / `linux-apfs-rw` для нативного монтирования) и HFS+ (`hfsprogs`).
4. `ram-hardware-live-diagnostics/SKILL.md` — инвентаризация железа (`dmidecode`, `lshw`, `lspci`, `lsusb`, `sensors`), стресс-тест ОЗУ (`memtester`), live-дамп и инспекция ОЗУ (`/proc/kcore`, `avml` / LiME, Volatility 3).

---

## 2. База LiveUSB, драйверы сети и USB-тетеринг (iPhone & Android)

### 2.1. Выбор конвейера сборки ISO
- **Основной выбор: Debian 13 «Trixie» `live-build`** (развивает существующий `ops/live-usb/build-iso.sh`, собирается на `matebook16-deb` или в GitHub Actions, стабильный `main contrib non-free non-free-firmware`, свежие ядра 6.12 LTS + backports).
- *Альтернатива (если понадобятся Wi-Fi 7 адаптеры на ядре 6.18+ без DKMS):* Arch `archiso` с `linux-lts` + локальный AUR-репозиторий для `linux-apfs-rw-dkms` и `apfs-fuse`. Архитектура `core/` и Kiosk UI не привязана к дистрибутиву.

### 2.2. Максимальный набор сетевых драйверов и прошивок
- **Прошивки (Debian `non-free-firmware`):** `firmware-linux`, `firmware-linux-nonfree`, `firmware-misc-nonfree`, `firmware-iwlwifi` (Intel Wi-Fi 6/6E/7), `firmware-realtek`, `firmware-atheros`, `firmware-brcm80211` (Broadcom), `firmware-mediatek`, `firmware-marvell-prestera`, `firmware-qcom-soc`, `firmware-ti-connectivity`, `firmware-zd1211`, `firmware-libertas`.
- **Out-of-tree DKMS драйверы (chroot hook при сборке ISO):**
  - `broadcom-sta-dkms` (старые MacBook Air/Pro с Broadcom BCM43xx — с правильным blacklist конфликтующих `b43`/`bcma`/`ssb` или переключением по PCI ID);
  - Realtek USB Wi-Fi донглы (`rtl88xxau-dkms` / `rtw88` / `rtw89` при необходимости для свежих USB-свистков).
- **Сетевой стек:** `network-manager`, `wpasupplicant`, `iw`, `rfkill`, `ethtool`, `dnsmasq-base`, `usb-modeswitch`, `modemmanager`, `wireguard-tools`, `openssh-client`, `curl`, `ca-certificates`.

### 2.3. USB-тетеринг из коробки (iPhone + Android)
- **iPhone (iOS)** ([ArchWiki — iPhone tethering](https://wiki.archlinux.org/title/IPhone_tethering)):
  - Пакеты: `usbmuxd`, `libimobiledevice-utils` (`idevicepair`, `ideviceinfo`), `ipheth-utils`, `ifuse`.
  - Ядро: модуль `ipheth` (автозагрузка по USB ID Apple).
  - Systemd + udev: включённый `usbmuxd.service` + NetworkManager автоматически поднимает DHCP на появившемся интерфейсе `eth*`/`enp*` сразу после нажатия «Доверять» (Trust) на экране iPhone.
  - На экране Kiosk UI выводится подсказка-индикатор: если iPhone подключён, но не спарен (`idevicepair validate` != 0) — показать баннер «Нажмите "Доверять" на iPhone и введите код-пароль».
- **Android:**
  - Модули ядра: `rndis_host`, `cdc_ether`, `cdc_ncm`, `usbnet`.
  - NetworkManager + udev-правило: любой новый USB-Ethernet интерфейс (`usb0`, `enp*u*`) автоматически получает DHCP с более высоким приоритетом метрики, чем неактивный Wi-Fi.
- **Защита активного аплинка (`active_uplink_lock`):** агент определяет интерфейс маршрута по умолчанию до шлюза и предупреждает/блокирует случайный `ip link set down` этого интерфейса через MCP.

---

## 3. Графический стартовый экран (GUI Wi-Fi + статус + QR)

> **Обязательный стандарт для всех UI и анимаций проекта (`kiosk_ui/` и `/connect/<session_code>`):** применять скилл **[felipebasurto/unslop-ui](https://github.com/felipebasurto/unslop-ui)** с флагом `--surface` (правила U1–U20):
> - Без AI-визуального мусора: никаких декоративных eyebrow-лейблов, нумерации `01 //`, ботопояснений, фейковых `animate-pulse` точек и декоративных эмодзи/иконок (оставляем только реальные индикаторы состояния, привязанные к живым данным).
> - Поверхности и типографика (U17–U19): чистые нейтральные поверхности без hue-to-hue градиентов, цветных свечений и размытых блобов, тонкие hairline-рамки, тени не сильнее `0 1px 2px rgb(0 0 0 / 0.05)`, сетка отступов кратна 4px (`4/8/12/16/20/24/32/40/48/64`), начертание заголовков `500`/`600` (без `800`/`900`).
> - Анимации (U20): никаких `transition-all`, `hover:scale-*` на карточках и `animate-bounce` — только именованные CSS-свойства с `150ms ease-out`.
> - Автопроверка перед коммитом/сборкой: `python3 skills/unslop-ui/scripts/count_tells.py --surface --max 0 ops/live-usb/kiosk_ui/`.

Лёгкий Kiosk-стек (~120–180 МБ в SquashFS, мгновенный старт на любом GPU, включая fallback `vesa`/`fbdev`/`modesetting`):
- **Стек отображения:** `xserver-xorg-core`, `xserver-xorg-video-all`, `xinit`, `openbox` (или Wayland `cage`) + локальный веб-интерфейс в **Chromium/WebKit2GTK Kiosk** (полноэкранный режим без рамок, запуск от автологина `tty1`). На `tty2` остаётся резервный `firstboot_tui.py` + `nmtui` + `bash` на случай отсутствия видеовыхода.
- **Бэкенд Kiosk UI (`ops/live-usb/kiosk_server.py`, stdlib Python + `nmcli`):**
  - Слушает только `127.0.0.1:8787`.
  - **Экран 1 — Подключение к сети (интерактивный GUI):**
    - Автоматически показывает статус кабеля Ethernet и USB-тетеринга (iPhone / Android). Если интернет уже есть — сразу переходит к Экрану 2.
    - Кнопка **«Сканировать Wi-Fi»** (`nmcli -t -f SSID,SIGNAL,SECURITY,IN-USE dev wifi list --rescan yes`).
    - Список сетей с уровнем сигнала и иконкой замка; клик по сети открывает поле ввода пароля (с виртуальной экранной клавиатурой `onboard`/JS-клавиатурой на случай тачскрина или сломанной клавиатуры ноутбука) и кнопку **«Подключиться»** (`nmcli dev wifi connect ...`).
    - Поддержка скрытых сетей (Hidden SSID) и индикация Captive Portal.
  - **Экран 2 — Готовность к подключению MCP (QR + Токен):**
    - Как только появляется интернет (`curl` / проверка доступности `MESH_GATEWAY`), LiveUSB-агент автоматически регистрирует эфемерную сессию на шлюзе и поднимает WebSocket-туннель (`core/agent.py`).
    - На экране крупно отображаются:
      1. **Большой SVG QR-код** → ведёт на публичную страницу подключения (`https://<gateway>/connect/<session_code>`).
      2. **Короткий URL и одноразовый код/токен** (`lk_…`) с кнопкой копирования (если пользователь открывает со второго устройства вручную).
      3. **Индикатор состояния:** онлайн/офлайн туннель, IP, режим доступа (`FULL ACCESS`), подключённые диски (кол-во и объём), кнопка **«Открыть терминал»**, **«Сменить Wi-Fi»**, **«Перегенерировать токен»**.

---

## 4. Веб-страница подключения MCP (Gemini / ChatGPT / Claude)

После сканирования QR-кода с телефона или другого ПК открывается мобильно-адаптивная страница на шлюзе (`gateway.py` → новый маршрут `/connect/<session_code>`, защищённый одноразовым/TTL-токеном сессии, без логина):

1. **Единый блок копирования токена и URL:**
   - Кнопка **«Скопировать MCP URL для Gemini (`/sse`)»**: `https://<gateway>/sse?user=<live_node_id>&token=<lk_...>`
   - Кнопка **«Скопировать MCP URL для ChatGPT / Claude (`/mcp`)»**: `https://<gateway>/mcp?user=<live_node_id>&token=<lk_...>`
   - Кнопка **«Скопировать конфиг JSON (Claude Desktop / Claude Code / Cursor)»** — готовый фрагмент `mcpServers`.
2. **Пошаговые вкладки с инструкциями (RU / EN):**
   - **Gemini (Web / Mobile):** куда вставить `/sse` URL и как проверить связь (`mesh_status`).
   - **ChatGPT:** на основе `docs/CHATGPT_CONNECT_RU.md` (Plugins → Add custom MCP server → `/mcp` URL → No authentication; при необходимости — завершение Wave 2 OAuth 2.1 на шлюзе, если аккаунт требует OAuth).
   - **Claude (Claude.ai Custom Connectors / Claude Desktop / Claude Code):** пошаговая вставка URL или `claude mcp add --transport http ...`.
3. **Подтверждение активации:**
   - Страница по WebSocket/polling показывает статус: «Ожидание подключения AI-клиента…» → «Подключено! Полный доступ к узлу `<live_node_id>` активен».
   - При закрытии/перезагрузке LiveUSB эфемерный узел и его `lk_…` автоматически аннулируются на шлюзе (TTL / revoke-on-shutdown).

---

## 5. Полный доступ к системе, носителям и ОЗУ (Full Access Model)

По требованию #5, после добавления токена пользователь (через AI-клиент) получает **полный root-доступ** к загруженной Live-системе и всему оборудованию:

1. **Режим узла по умолчанию на LiveUSB:**
   - `rhm-live-agent.service` запускается от `root` с `MESH_SCENARIO=live_usb`, `MESH_ACCESS_MODE=full` (никаких блокировок `approval_required` на `system_change` / `system_write` / `bash_exec`, поскольку сама LiveUSB-сессия авторизована уникальным эфемерным токеном с экрана машины).
   - `MESH_WORKSPACE=/` (или `/mnt/rescue` с полным доступом ко всей ФС).
2. **Доступ ко всем носителям:**
   - Отключено опасное автомонтирование дисков в `udev` / `udisks2` при загрузке (чтобы не повредить битые разделы), но по запросу агента (через `rescue_action` или `bash_exec`) любой диск/раздел/LVM/RAID/LUKS/BitLocker монтируется в `/mnt/rescue/<name>` в режиме `ro` или `rw`.
   - Прямой доступ к блочным устройствам (`/dev/sd*`, `/dev/nvme*`, `/dev/mmcblk*`), таблицам разделов (`parted`, `gdisk`, `sfdisk`), клонированию (`ddrescue`), восстановлению (`testdisk`, `photorec`, `sleuthkit`, `chntpw`, `hivex`).
3. **Доступ к ОЗУ и низкоуровневому железу:**
   - Чтение и диагностика ОЗУ: `free`, `/proc/meminfo`, `dmidecode -t memory` (слоты, частоты, производители DIMM), `edac-util` (ECC-ошибки), `memtester` (тест свободной ОЗУ без перезагрузки), `memtest86+` в меню загрузчика GRUB LiveUSB.
   - Форензика и дамп ОЗУ: доступ к `/proc/kcore`, `avml` (статический бинарник для безопасного дампа физической памяти на внешний диск), `volatility3` (опционально в venv) + чтение `dmesg`, `lspci -vvv`, `lsusb -v`, `sensors`, `nvme smart-log`, `smartctl -a`.
   - Защита RAM-диска LiveUSB (`toram` / `tmpfs`): `journald` ограничен `50M`, а `rescue_action(ddrescue_plan)` и скиллы проверяют, чтобы образы дисков и дампы ОЗУ не писались в `/tmp` или `/run` (чтобы не уронить LiveUSB по OOM).

---

## 6. Матрица поддержки файловых систем «из коробки»

Критическое требование #6 — открытие и работа с данными на **exFAT**, **NTFS**, **Linux ФС** и **macOS ФС** сразу после загрузки без доустановки пакетов:

| Семейство | Файловая система | Пакеты и модули в ISO | Режим работы и технические нюансы |
| :--- | :--- | :--- | :--- |
| **Кроссплатформенные** | **exFAT** | `exfatprogs` + ядерный модуль `exfat` | Полный **R/W**, проверка (`fsck.exfat`), создание (`mkfs.exfat`). |
| | **FAT32 / FAT16 (ESP)** | `dosfstools` (`vfat`) | Полный **R/W**, работа с EFI System Partition. |
| **Windows** | **NTFS** | Ядерный модуль `ntfs3` + `ntfs-3g` | Полный **R/W**. По умолчанию `ntfs3` (высокая скорость), при «грязном» томе / Fast Startup / hibernation — безопасный fallback на `ntfs-3g` (`ro` или `ntfsfix -d`). |
| | **BitLocker (NTFS)** | `dislocker`, `cryptsetup` (`--type bitlk`) | Расшифровка по паролю или 48-значному Recovery Key с последующим R/W монтированием NTFS. |
| **Linux** | **ext2 / ext3 / ext4** | `e2fsprogs` | Полный **R/W**, `e2fsck`, `resize2fs`, `debugfs`. |
| | **Btrfs** | `btrfs-progs` | Полный **R/W**, сабволюмы, снапшоты, `btrfs scrub/check/restore`. |
| | **XFS** | `xfsprogs` | Полный **R/W**, `xfs_repair`. |
| | **F2FS** | `f2fs-tools` | Полный **R/W** (флешки, SSD, Steam Deck / Android). |
| | **ZFS (OpenZFS)** | `zfsutils-linux`, `zfs-dkms` (или prebuilt `zfs-modules`) | Полный **R/W** (`zpool import -f -R /mnt/rescue <pool>`, read-only режим `-o readonly=on`). |
| | **LVM2 / mdadm / LUKS** | `lvm2`, `mdadm`, `cryptsetup`, `kpartx` | Полная сборка массивов, расшифровка LUKS1/LUKS2 и активация томов. |
| **macOS** | **HFS / HFS+** | `hfsprogs`, `hfsplus`, `hfsutils` + ядерный модуль `hfsplus` | **R/O** всегда безопасно; **R/W** из коробки (для журналируемых томов HFS+ требуется `-o force,rw` или снятие флага журнала; `fsck.hfsplus`). |
| | **APFS** | **[linux-apfs-rw](https://github.com/linux-apfs/linux-apfs-rw)** (DKMS-модуль ядра + `apfsprogs`) **+** **[apfs-fuse](https://github.com/sgan81/apfs-fuse)** (FUSE) **+** **[libfsapfs-utils](https://github.com/libyal/libfsapfs)** | **Двухконтурная поддержка:**<br>1) **Незашифрованный APFS:** нативный `linux-apfs-rw` (`mount -t apfs`, быстрое чтение и экспериментальная запись с явным предупреждением о бэкапе перед RW).<br>2) **Зашифрованный APFS (FileVault):** `apfs-fuse` (надёжное чтение и выгрузка данных после ввода пароля/ключа).<br>3) **Битые контейнеры:** `fsapfsmount` (`libfsapfs`) / `AgentDFIR` (без монтирования). |

---

## 7. Поэтапная дорожная карта реализации

### Этап 1: Ядро MCP, интеграция `rescue` и библиотека скиллов (3–4 дня)
1. **Интеграция `core/rescue.py` в `core/mcp_tools.py`:**
   - Зарегистрировать `rescue_info` и `rescue_action` при `scenario() == "live_usb"`.
   - Расширить `rescue_info` секциями `ram_info` (`dmidecode -t memory`, `/proc/meminfo`, `edac-util`) и `filesystems` (поддержка APFS/HFS+/ZFS/BitLocker/LUKS).
   - Расширить `rescue_action` умным мульти-ФС монтированием (`mount_auto`: автоматический выбор `ntfs3`/`ntfs-3g`, `apfs`/`apfs-fuse`, `hfsplus`, `exfat`, `zpool import`, `dislocker`, `cryptsetup`).
   - Исправить `core/device.py::scenario()` для надёжного детектирования LiveUSB (`/run/live/medium`, `boot=live`, `MESH_SCENARIO=live_usb`).
2. **Прогрессивная подгрузка скиллов (`core/skills_loader.py`):**
   - Добавить MCP-инструменты `list_skills(category?)` (возвращает только frontmatter) и `load_skill(name)` (полный `SKILL.md` + `references/`).
   - Скрипт `ops/live-usb/fetch-skills.sh`: выкачивает отобранные скиллы из Раздела 1 по зафиксированным commit SHA, прогоняет `SkillSpector` и складывает в `skills/liveusb/`.
   - Написать 4 кастомных `SKILL.md` (Windows offline boot/BitLocker, Linux chroot/GRUB/LUKS, macOS APFS/HFS+, RAM & hardware diagnostics).
3. **Unit-тесты (`tests/test_rescue.py`, `tests/test_skills_loader.py`):**
   - Покрыть фикстурами `lsblk`, `smartctl`, `dmesg`, `mount_auto` (все типы ФС) и `list_skills`/`load_skill`.

### Этап 2: Эфемерный онбординг на шлюзе и страница подключения (2–3 дня)
1. **Gateway API (`gateway.py`):**
   - Добавить эндпоинт `POST /v1/liveusb/register` — выдаёт временный `node_id` (например `live-a4f9`), `device_token`, `link_key` (`lk_…`) и короткий `session_code` (TTL 24ч, автоудаление при отключении или по кнопке «Завершить сессию»).
   - Добавить веб-страницу `GET /connect/<session_code>` — адаптивный UI с кнопками копирования URL для **Gemini (`/sse`)**, **ChatGPT (`/mcp`)**, **Claude (URL + JSON)**, пошаговыми инструкциями и live-индикатором подключения AI-клиента.
2. **Агент (`core/agent.py`):**
   - Режим `--ephemeral-liveusb`: при старте без `agent.env` ждёт сеть, вызывает `/v1/liveusb/register`, сохраняет конфиг в `/run/rhm-live/agent.env` (в ОЗУ), выставляет `access_mode=full` и поднимает WebSocket-туннель.

### Этап 3: Графический стартовый экран (Kiosk UI) и сетевой стек (3–4 дня)
1. **Локальный Kiosk-сервер (`ops/live-usb/kiosk_server.py` + `ops/live-usb/kiosk_ui/`):**
   - REST API поверх `nmcli` + `idevicepair` + состояния `rhm-live-agent`: сканирование Wi-Fi, подключение с паролем, статус Ethernet / iPhone / Android USB tethering, статус туннеля, генерация SVG QR-кода (встроенный чистый Python или `qrencode -t SVG`).
   - Чистый, контрастный UI по правилам **[felipebasurto/unslop-ui](https://github.com/felipebasurto/unslop-ui)** (`--surface`, U1–U20: нейтральные поверхности, hairline-рамки, сетка 4px, анимации `150ms ease-out` по именованным свойствам, проверка `count_tells.py --surface --max 0`), с крупным шрифтом, экранной клавиатурой для Wi-Fi и кнопкой открытия локального терминала.
2. **Запуск Kiosk-сессии:**
   - Автологин на `tty1` → `startx` (`openbox` + полноэкранный браузер/WebKitGTK на `http://127.0.0.1:8787`).
   - `firstboot_tui.py` на `tty2` как резервная консоль без X11.
3. **Сетевые udev/NetworkManager правила:**
   - Автоподнятие Android RNDIS/CDC (`rndis_host`, `cdc_ether`, `cdc_ncm`) и iPhone (`ipheth` + `usbmuxd.service`).
   - Защита активного аплинка (`active_uplink_lock`) в `core/rescue.py`.

### Этап 4: Конвейер сборки ISO (`ops/live-usb/build-iso.sh`) (3–4 дня)
1. **Обновление `ops/live-usb/build-iso.sh`:**
   - Исправить systemd-юнит: `ExecStart=/usr/bin/python3 -m core.agent --ephemeral-liveusb` (вместо legacy `rhm_mcp_server.py`).
   - Полный `rhm-rescue.list.chroot`: все пакеты firmware, USB tethering (`usbmuxd`, `libimobiledevice-utils`, `ipheth-utils`), все ФС (`exfatprogs`, `ntfs-3g`, `e2fsprogs`, `btrfs-progs`, `xfsprogs`, `f2fs-tools`, `zfsutils-linux`, `hfsprogs`, `hfsplus`, `dislocker`, `lvm2`, `mdadm`, `cryptsetup`), диагностика ОЗУ/железа (`memtester`, `dmidecode`, `lshw`, `edac-utils`, `smartmontools`, `nvme-cli`, `gddrescue`, `testdisk`, `sleuthkit`, `chntpw`, `hivex`), минимальный Xorg + Kiosk browser.
   - Chroot hooks (`config/hooks/live/`):
     - Сборка и установка DKMS-модуля **[linux-apfs-rw](https://github.com/linux-apfs/linux-apfs-rw)** + сборка **[apfs-fuse](https://github.com/sgan81/apfs-fuse)** и загрузка статического `AgentDFIR` / `avml`.
     - Установка `broadcom-sta-dkms` / Realtek USB Wi-Fi DKMS (с проверкой сборки под целевое ядро ISO).
     - Отключение автомонтирования (`99-rhm-no-automount.rules`), настройка `journald` (`Storage=volatile`, `SystemMaxUse=50M`), включение `usbmuxd` и `NetworkManager`.
     - Запекание `/opt/remote-harness-mcp/` (код + проверенные `skills/liveusb/`).
   - Поддержка загрузки **UEFI (с Secure Boot / `shim-signed`) + Legacy BIOS** (гибридный ISO, совместимый с Ventoy, Rufus, `dd`, balenaEtcher).

### Этап 5: Тестирование и приёмка (2–3 дня)
1. **Автотесты в QEMU/KVM (`ops/live-usb/test-qemu.sh`):**
   - Загрузка собранного ISO в UEFI и BIOS с подключёнными виртуальными дисками, отформатированными в **exFAT, NTFS, ext4, Btrfs, XFS, ZFS, HFS+, APFS**.
   - Проверка чтения/записи данных на каждом разделе через MCP-вызовы.
2. **Тест на реальном железе (ноутбук + смартфон):**
   - Загрузка с флешки (Ventoy / Rufus) → проверка Wi-Fi через графический экран.
   - Проверка USB-тетеринга с **iPhone** (Trust → автоинтернет) и **Android** (USB tethering → автоинтернет).
   - Сканирование QR-кода с телефона → копирование токена → подключение в **Gemini**, **ChatGPT** и **Claude** → проверка полного доступа (чтение/запись файлов на дисках хоста, `smartctl`, `dmidecode`, `memtester`, вызов скилла через `load_skill`).

---

## 8. Риски и архитектурные ограничения

- **Запись в APFS (`linux-apfs-rw`):** открытый драйвер `linux-apfs-rw` официально помечает запись как экспериментальную (и не поддерживает запись в зашифрованные FileVault-тома). **Решение:** в `rescue_action` и скилле `macos-apfs-hfs-recovery` по умолчанию монтировать APFS в `ro`; для зашифрованных томов использовать `apfs-fuse` (R/O); включать R/W для незашифрованного APFS только по явному запросу пользователя с предупреждением.
- **OAuth в ChatGPT:** как зафиксировано в `docs/CHATGPT_CONNECT_RU.md`, если аккаунт ChatGPT не предлагает «No authentication» для Custom MCP, потребуется завершить Wave 2 OAuth 2.1 на `gateway.py` (для Gemini `/sse` и Claude URL с `lk_…` работает сразу).
- **Secure Boot и DKMS-модули (`linux-apfs-rw`, ZFS, Broadcom):** неподписанные out-of-tree модули не загрузятся при включённом Secure Boot. **Решение:** задокументировать отключение Secure Boot для работы с APFS/ZFS, либо добавить MOK-подпись модулей на этапе сборки ISO.
- **Размер ISO vs `toram`:** с Xorg + браузером + прошивками + ZFS/APFS размер SquashFS составит ~1.3–1.8 ГБ. В GRUB предусмотреть два пункта загрузки: **«Load to RAM (`toram`, рекомендуется при ОЗУ ≥ 4 ГБ)»** и **«Boot from USB (Low RAM < 4 ГБ)»**.
