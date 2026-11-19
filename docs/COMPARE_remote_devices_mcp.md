# Сравнение: nicelight/remote-devices-mcp ↔ Antigravity-mash (remote-harness-mcp)

**Дата:** 2026-11-18
**Сравниваемые срезы:**

| | Референс | Локальный проект |
| :--- | :--- | :--- |
| Репозиторий | `nicelight/remote-devices-mcp` | `LevRa7/remote-harness-mcp` (`origin`), рабочая копия `D:\MyProjects\Antigravity-mash` |
| Коммит | `2bbc9a0` (2026-11-12, «onboarding clarification») | `2ec7f1a` (2026-11-16, «keep the gemini-computer-use alias at 0.5.2») |
| Версия | `package.json` 0.4.8, файла `VERSION` нет | `package.json` 0.5.2, `VERSION` 0.4.8 (**расхождение внутри репозитория**) |
| Лицензия | MIT | MIT |
| Язык | Python + shell/PowerShell-установщики | Python + shell/PowerShell + Avalonia-трей (PS) + npm-обёртка |

Репозитории — **одна кодовая линия** (общий предок ~0.4.8): совпадают `gateway.py`‑маршруты,
`pending_pairs`, `device_rotate`/`revoke`, `registry.json`, модель ключей `device token + link key`,
шары файлов, установщики. Дальше линии разошлись в разные продуктовые слои.

---

## 1. Краткий вывод

| | Референс (`remote-devices-mcp`) | Локальный (`Antigravity-mash`) |
| :--- | :--- | :--- |
| **OAuth** | Google OIDC для владельца, реализован инлайн: `gateway_identity.py` (`OwnerAuth`), сессии в памяти, без БД | Google OIDC в отдельном пакете `account/` (PyJWT), аккаунты и сессии в SQLite, отдельно Google Drive |
| **Веб-панель** | `/account` — список устройств владельца + `/pair` — подтверждение устройства в браузере | `/account` — кабинет с удалённым управлением узлом; `/pair` **нет**; плюс локальная панель узла `rhm --web` (`core/web_settings.py`) |
| **Онбординг** | Установщик → `POST /v1/devices/pair` → ссылка `/pair?code=` → Google‑вход → «Подключить устройство» → установщик забирает ключи с `X-Pair-Secret` → проверка `mesh_status` | Установщик → `POST /api/register` (анонимно) → ключи сразу в ответе; `pair_url` возвращается, но маршрута `/pair` нет |
| **Публикация установщиков** | Шлюз сам отдаёт `/install.sh`, `/install.ps1`, `/core/*.py`, `/ops/*`, картинку‑инструкцию, подставляя свой домен | Нет: установщики берутся из GitHub/npm |
| **Хранилище** | `registry.json` + память процесса, БД нет | `registry.json` + SQLite (`account.db`), dual‑source без явной связки |
| **Эксплуатация** | `ops/systemd/{rhm-gateway.service, gateway.env.example, Caddyfile.example}`, три документа по онбордингу/self‑host | `deploy_gateway.sh`, `ops/nginx/*`, но без systemd‑юнита и шаблона env для шлюза |
| **Богатство кабинета** | 3 действия на устройство: проверка связи, ротация ссылки, отзыв | Режим доступа (read-only/approval/full), рабочая папка, sandbox, пауза, `remote_control` |
| **Платформа узла** | монолитный `core/device.py`/`core/mcp_tools.py` | разбито по ОС: `core/platforms/{windows,darwin,linux,android}`, трей, планировщик |

**Одной фразой:** референс сильнее в **сценарии подключения устройства и публикации шлюза**
(OAuth‑онбординг + отдача установщиков + self‑host ops), локальный — в **кабинете и управлении
узлом** (SQLite‑аккаунты, Drive, удалённые настройки, локальная веб‑панель, трей, платформенные
бэкенды). Для «oauth + веб‑панель по аналогии» не хватает именно **связки** «установщик ↔ браузер ↔
кабинет»: в локальной ветке она разорвана.

---

## 2. Файловый diff

### 2.1. Есть только в референсе (19 файлов)

| Файл | Роль |
| :--- | :--- |
| `gateway_identity.py` | `OwnerAuth`: Google Authorization Code + PKCE, state/nonce, cookie‑binding, сессии в памяти (8 ч), CSRF, HTML‑страница с CSP‑nonce |
| `core/onboarding.py` | Клиентский сценарий привязки: `pair`/`status`/`link`, `X-Pair-Secret`, receipt‑файл, проверка реального `mesh_status` |
| `tools/enroll_node.py`, `tools/verify_live_mcp.py` | Служебные утилиты онбординга и живой проверки |
| `ops/systemd/rhm-gateway.service`, `gateway.env.example`, `Caddyfile.example`, `rhm-node.service` | Готовое развёртывание шлюза/узла |
| `ops/termux/connect-phone.sh` | Подключение телефона |
| `docs/GOOGLE_OWNER_ONBOARDING.md`, `docs/ONBOARDING_DEPLOYMENT.md`, `docs/SELF_HOSTED_GATEWAY_RU.md` | Архитектура, деплой, self‑host |
| `gateway_assets/chatgpt-add-mcp.png` | Картинка‑инструкция в кабинете |
| `AGENTS.md`, `requirements-integration.txt` | Правила для агентов, интеграционные зависимости |
| `tests/test_onboarding.py`, `test_onboarding_live.py`, `test_gateway_single_process.py` | Тесты сценария привязки |

### 2.2. Есть только локально (сгруппировано)

| Группа | Файлы |
| :--- | :--- |
| Кабинет и аккаунты | `account/{app,auth,config,crypto,db,drive,sessions}.py`, `tests/test_account_*.py` |
| Локальная веб‑панель узла | `core/web_settings.py`, `tests/test_web_settings.py`, `docs/WEB_SETTINGS.md`, `status_web/index.html` |
| Управление узлом | `core/control.py`, `core/policy.py`, `core/sandbox.py`, `core/schedule.py`, `core/goal.py`, `core/usage.py`, `core/settings.py`, `core/rescue.py`, `core/menu.py` |
| Платформы | `core/platforms/**` (windows/darwin/linux/android, screen, clipboard, telemetry) |
| Трей и Windows | `ops/windows/rhm-tray.ps1`, `win_dashboard_server.py`, `win_telemetry*.ps1` |
| LiveUSB | `ops/live-usb/{build-iso.sh,firstboot_tui.py}` |
| Командная работа | `docs/team/**` (WBS, OWNERSHIP_MATRIX, REVIEW_LOG, VERIFICATION_PROTOCOL), `tools/check_team_coverage.py`, `vendor/superpowers/**` |
| Планы | `docs/remote_harness_mcp_v2_plan.md`, `docs/wave-1-auth-account-log-settings.md`, `docs/GOOGLE_OAUTH.md` |

### 2.3. Совпадающие по смыслу, но реализованные по‑разному

| Возможность | Референс | Локально |
| :--- | :--- | :--- |
| Google‑вход | `gateway_identity.py: OwnerAuth.exchange()` на `google-auth` + `requests` | `account/auth.py` на `PyJWT` + `urllib`, `account/sessions.py` (HMAC‑cookie) |
| Сессия браузера | dict в памяти шлюза, TTL 8 ч, `__Host-rhm-owner`, привязана к `LOGIN_COOKIE` | SQLite `account_sessions`, idle 7 дн / absolute 30 дн, `__Host-rhm_session`, CSRF в строке сессии |
| Владение устройством | `registry[user]["owner_subject"] = "google:<sub>"` | `account.db: devices(tenant_id, node_name)`, привязка через `POST /account/devices/claim` по link key |
| Подтверждение в браузере | `/pair` + `POST /account/pair/{code}/approve` | нет |
| Кабинет | `/account`: ссылка, копирование, check/rotate/revoke, CSP‑nonce | `/account`: remote‑control, режим, папка, sandbox, пауза, Drive |
| HTML/CSP | `page()` с nonce, `default-src 'none'` | `page()` в `account/app.py`: `style-src 'unsafe-inline'`, `img-src data:` |

---

## 3. OAuth: матрица реализации

| Свойство | Референс | Локально | Комментарий |
| :--- | :--- | :--- | :--- |
| Поток | Authorization Code + PKCE S256 | Authorization Code + PKCE S256 | паритет |
| `state`/`nonce` | в памяти, TTL 10 мин, одноразовые | в SQLite `auth_flows`, TTL 10 мин, одноразовые | паритет по смыслу |
| Привязка входа к браузеру | cookie `__Host-rhm-login` + sha256 в `state` | **нет** | уязвимость к login‑CSRF/фиксации (низкий, но чинится) |
| Проверка ID token | `google-auth.verify_oauth2_token` (проверяет подпись, iss, aud, exp, azp) | `PyJWT.decode` + JWKS‑кэш, проверяет `iss/aud/exp/nonce/email_verified` | нет проверки `azp` |
| Скоупы | `openid email` | `openid email profile` (+ отдельный flow `drive.file`) | минимальность лучше у референса |
| Хранилище личности | нет, только `owner_subject` в registry | таблицы `accounts`, `tenants`, `google_tokens` | локально богаче |
| Сессии | память, рестарт = разлогин | SQLite, переживают рестарт | локально удобнее |
| Cookie | `__Host-`, Secure, HttpOnly, SameSite=Lax | то же (при https), HMAC‑подпись | паритет |
| CSRF | токен в сессии + проверка `Origin` | токен в сессии, `Origin` **не проверяется** | доработать |
| Rate limit входа | 20 попыток/IP/мин (`/auth/google/login`) | нет (есть только лимит `claim`) | доработать |
| Валидация конфигурации | падает на старте: HTTPS‑origin без пути, оба секрета обязательны, `google-auth` установлен | молчаливые fallback‑и, проверяется только `configured` | доработать |
| Запрос к Google | фиксированные endpoint‑ы, timeout 15 с, `allow_redirects=False`, семафор 8 | фиксированные endpoint‑ы, timeout 15 с | паритет |
| Регистрация при включённом OAuth | `/api/register` закрыт автоматически (`enrolment_refusal` учитывает `owner_auth.configured`) | **открыт**: `enrolment_refusal` смотрит только `MESH_REGISTER_MODE`/`MESH_ENROLL_SECRET` | критично |
| Приватность | `Referrer-Policy: strict-origin`, `no-referrer` на OAuth‑редиректах, `no-store` | `no-referrer` в `_headers`, CSP без nonce | доработать CSP |
| Зависимости | `google-auth`, `requests` | `PyJWT[crypto]` | оставить локальные, добавить `azp`/`Origin` |

---

## 4. Веб‑панель

| Экран/действие | Референс | Локально |
| :--- | :--- | :--- |
| `/account` без входа | кнопка «Войти через Google» | редирект на `/account/login` |
| Список устройств | из `registry.json` по `owner_subject` | из `account.db` по `tenant_id` (registry не участвует) |
| Ссылка MCP | `textarea` + копирование + картинка «как добавить в ChatGPT» | только текст «ссылка остаётся на машине» |
| Проверка связи | `POST /account/devices/{user}/check` → реальный `mesh_status` | нет (есть онлайн/офлайн по туннелю) |
| Ротация ссылки | `rotate` с подтверждением (`confirm=yes`) | нет в кабинете (только API/CLI) |
| Отзыв доступа | `revoke` с подтверждением | нет в кабинете |
| Удалённое управление | нет | режим, папка, sandbox, пауза, `remote_control` |
| Google Drive | нет | `/account/drive`, `drive.file`, шифрование токенов (`account/crypto.py`) |
| Вход `/pair` | есть, с шагами и кодом для сверки | нет |
| CSP | nonce для style/script, `default-src 'none'` | `style-src 'unsafe-inline'`, нет script‑nonce |
| CORS на кабинете | — (нет глобального wildcard) | `AccountCorsFilter` вырезает CORS‑заголовки для `/account` |
| Локальная панель узла | нет | `rhm --web` на `127.0.0.1:8098`, одноразовый токен → HttpOnly‑cookie, CSRF, loopback‑only |

---

## 5. Онбординг и установщики

### 5.1. Референс (работает)

```
install.sh ──POST /v1/devices/pair──────────► {pair_code, device_secret, pair_url}
           ──открывает /pair?code=XXXX-XXXX──► Google login → «Подключить устройство»
                                              └─ approve_pair(): registry[user].owner_subject, token, link_key
           ──GET /v1/devices/pair/{code} с X-Pair-Secret─► ключи (идемпотентно до TTL)
           ──python -m core.onboarding pair (ожидание, receipt, agent.env)
           ──tools/call mesh_status────────► реальное подтверждение → печать Server URL
```

Особенности, которые стоит перенять:

- `device_secret` (32 байта) выдаётся только инициатору; короткий код сам по себе бесполезен;
- повторное получение ключей с тем же секретом не создаёт второе устройство;
- код живёт 10 минут и умирает при рестарте шлюза (осознанное ограничение, задокументировано);
- `onboarding.receipt.json` пишется до `agent.env`, чтобы сбой не приводил к новой привязке;
- существующий `agent.env` переиспользуется, привязка к другому шлюзу — ошибка;
- браузер открывается только на URL, собранный локально из `base + /pair?code=` (никакого URL из ответа сервера);
- публикация установщиков с шлюза: белый список файлов, подстановка домена, запрет каталогов/симлинков, `HEAD`.

### 5.2. Локально (разорвано)

```
install.sh ──POST /api/register──────────────► {username, token, link_key}   ← анонимно
           ──сохраняет agent.env, поднимает службу
           ──печатает MCP URL
```

- `/v1/devices/pair` есть, но `pair_url` = `https://<gw>/pair?code=...` — **маршрута `/pair` нет**, ссылка 404;
- `approve` требует `X-Admin-Token`; при включённом кабинете и пустом `MESH_ADMIN_TOKEN` привязка невозможна (`pairing_not_configured`, 503);
- в `pending_pairs` нет `device_secret_sha256`; `GET /v1/devices/pair/{code}` отдаёт ключи **любому**, кто знает код, и делает `pop` (второй запрос → 404). Код живёт 10 минут и подбирается 8 символами из 32‑буквенного алфавита — это единственный барьер;
- установщики про pairing не знают (`--pair` нет), кабинет про установщик не знает (привязка — ручной ввод link key в `/account/devices/claim`);
- `MESH_ACCOUNT=1` не закрывает `/api/register`.

---

## 6. Эксплуатация и безопасность

| Тема | Референс | Локально |
| :--- | :--- | :--- |
| Юнит шлюза | `ops/systemd/rhm-gateway.service`, `--workers 1`, `ProtectSystem=strict`, `NoNewPrivileges` | нет юнита; `deploy_gateway.sh` (16 КБ) |
| Env‑шаблон | `gateway.env.example` с комментариями и `MESH_REGISTER_MODE=pair` | нет; переменные разбросаны по `docs/GOOGLE_OAUTH.md` |
| Reverse proxy | `Caddyfile.example` | `ops/nginx/{remote-harness-mcp.conf,mesh-domain.vhost.template}` |
| Ограничение одного воркера | задокументировано (`test_gateway_single_process.py`) | неявно (in‑memory `pending_pairs`, `active_tunnels`) |
| Логи и секреты | фильтр uvicorn скрывает `token/code/state` | `core/redact.py` для логов узла; для шлюза явного фильтра нет |
| Развёртывание | 3 документа + резервные копии + откат | `deploy_gateway.sh` + `docs/DOMAIN.md` |

---

## 7. Дефекты и пробелы локальной ветки (по коду)

| # | Проблема | Где | Следствие |
| :--- | :--- | :--- | :--- |
| 1 | Нет маршрута `/pair` | `gateway.py` (список `routes`) | `pair_url` в API ведёт на 404 |
| 2 | Подтверждение пары только администратором | `device_pair_approve` требует `MESH_ADMIN_TOKEN` | владелец не может подтвердить устройство из браузера |
| 3 | Нет `X-Pair-Secret`, выдача ключей по короткому коду | `device_pair_start`/`device_pair_status` | кто первым узнал код — получил ключи; нет идемпотентности |
| 4 | `/api/register` открыт при включённом кабинете | `enrolment_refusal` | аккаунт не является границей допуска узлов |
| 5 | Кабинет и registry не связаны | `account/db.py: claim_device`, `CabinetNodeControl.claim` | новое устройство не появляется в кабинете само; нужен ручной ввод link key |
| 6 | В кабинете нет check/rotate/revoke | `account/app.py` | управление ключами только через API/CLI |
| 7 | Нет проверки `Origin` в CSRF, нет rate‑limit входа | `account/app.py: csrf_ok`, `login`/`callback` | login‑CSRF, brute force |
| 8 | CSP со `unsafe-inline`, без nonce | `account/app.py: _headers` | ослабленная защита HTML |
| 9 | Нет публикации установщиков с шлюза | `gateway.py` | нельзя ставить «одной ссылкой» и нельзя гарантировать pairing по умолчанию |
| 10 | Нет валидации OAuth‑конфигурации на старте | `account/config.py` | при одном заданном секрете/HTTP‑base кабинет «тихо» не работает |
| 11 | `VERSION` = 0.4.8 при `package.json` 0.5.2 | корень | путаница в релизах и апдейте |

---

## 8. Что перенять (карта переноса)

| Возможность референса | Источник | Куда в локальном проекте |
| :--- | :--- | :--- |
| Браузерное подтверждение устройства | `gateway.py: /pair`, `owner_pair_approve` | `account/app.py` (страница `/account/pair`) + редирект `gateway.py: GET /pair` |
| Секрет инициатора пары | `device_pair_start/status` | `gateway.py`, тесты `tests/test_pairing.py` |
| CLI онбординга | `core/onboarding.py` | `core/onboarding.py` (адаптировать `core.paths`, `core.domain`) |
| Onboarding в установщиках | `install.sh` блок `PAIR`, `install.ps1 -Pair` | `install.sh`, `install.ps1`, при желании `install-gui.ps1` |
| Публикация установщиков | `installer_asset()` | `gateway.py` + `tests/test_published_assets.py` |
| Картинка‑инструкция | `gateway_assets/chatgpt-add-mcp.png` | `gateway_assets/` (локально файла нет) |
| Ужесточение OAuth | `OwnerAuth` | `account/auth.py`, `account/config.py`, `account/app.py` |
| Self‑host ops | `ops/systemd/*`, `docs/SELF_HOSTED_GATEWAY_RU.md` | `ops/systemd/*`, `docs/SELF_HOSTED_GATEWAY_RU.md` |
| Тесты онбординга | `tests/test_onboarding*.py` | `tests/test_pairing.py`, `tests/test_onboarding_live.py` (marker `live`) |

---

## 9. Что не переносить

- **Сессии в памяти** — локальные SQLite‑сессии переживают рестарт и переживут несколько воркеров; менять это назад не нужно.
- **Отсутствие БД** — локальные `accounts/tenants/devices` нужны для Drive, статистики и мультиустройства; референс просто не дошёл до этого слоя.
- **`google-auth` как зависимость шлюза** — локальный `PyJWT` уже стоит в `requirements-gateway.txt`; достаточно добавить проверку `azp` и защиту от подмены `kid`.
- **Тексты страниц** — писать свои (в референсе они смешаны с HTML в одном файле, у локального пакета `account/` уже своя вёрстка).
- **Имена переменных** — оставить локальные (`MESH_ACCOUNT`, `MESH_ACCOUNT_BASE_URL`, `MESH_SESSION_SECRET`), добавив только `MESH_PAIRING*`.

---

## 10. Если целевым репозиторием окажется nicelight/remote-devices-mcp

План [PLAN_oauth_web_panel.md](PLAN_oauth_web_panel.md) написан для локальной копии (здесь уже есть
`account/`, кабинет и Drive — достраиваем привязку и публикацию). Зеркальная задача («перенести
локальный кабинет в референс») выглядит так:

| Что переносить | Откуда | Куда в референсе | Сложность |
| :--- | :--- | :--- | :--- |
| SQLite-аккаунты и сессии | `account/{db,sessions,auth}.py` | новый пакет `account/` + монтирование в `gateway.py` | средняя: в референсе нет БД, `store` придётся вводить с нуля |
| Drive | `account/{drive,crypto}.py` | `account/`, отдельный scope-flow | низкая (код изолирован) |
| Удалённое управление узлом | `account/app.py`, `CabinetNodeControl` | `gateway.py` (уже есть `call_remote_tool`) | средняя: нужен `node_control`-инструмент в `core/mcp_tools.py` |
| Локальная панель узла | `core/web_settings.py` | `core/` (в референсе аналога нет) | низкая |
| Трей/платформы | `core/platforms/**`, `ops/windows/rhm-tray.ps1` | — | высокая, вне темы OAuth |

Порядок в этом случае: сначала БД-слой (`account/`), затем перенос `/account`-маршрутов из
`gateway.py` в пакет, затем Drive и remote-control; существующие `OwnerAuth`-сессии заменяются
SQLite-сессиями (иначе рестарт шлюза разлогинивает и теряет историю).

---

## 11. Проверенные факты (как получены)

- `git ls-tree`/`git clone --depth 1` обоих репозиториев; diff списков файлов — 217 blob‑ов в референсе.
- Чтение `gateway.py` (референс, строки 770–1310) и `gateway.py` (локально, 880–1260) — сравнение `api_register`, `device_pair_*`, `approve_pair`.
- Чтение `gateway_identity.py` целиком (208 строк) и `core/onboarding.py` (283 строки).
- Чтение `account/{auth,config,db,sessions,app}.py` локально.
- `docs/GOOGLE_OWNER_ONBOARDING.md`, `docs/ONBOARDING_DEPLOYMENT.md`, `docs/GOOGLE_OAUTH.md`, `docs/WEB_SETTINGS.md`.
- Маршруты: `routes = [...]` в обоих `gateway.py`; в локальном нет `/pair`, `/auth/google/*`, `/install.*`, `/assets/*`.
- Тесты локального кабинета существуют (`tests/test_account_*.py`), но не запускались: в доступных интерпретаторах нет `pytest`
  (`.win-test-deps` — только site‑packages для Python 3.13; `py -3.13 -m pytest` → `ModuleNotFoundError`).
