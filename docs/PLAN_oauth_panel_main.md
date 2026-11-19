# Google OAuth и веб-панель управления в основном проекте

**Дата:** 2026-11-19
**Ветка:** `fix/security-audit-2026-11-19`
**База:** `main` @ `2ec7f1a`, после коммитов `8a07179…c749813`

Документ отвечает на два вопроса: что нужно, чтобы включить вход через Google, и что нужно, чтобы
веб-панель управляла **всеми** устройствами теми же действиями, что доступны в `rhm`.

### Статус P1 (ветка `feat/p1-device-binding`)

| Задача | Содержание | Статус |
| :--- | :--- | :--- |
| P1-A | Мост и единый список: `devices()` / `claim()` в `gateway.py`, объединение registry + `account.db` в кабинете | **выполнено** |
| P1-B | Подтверждение пары из кабинета: `GET /account/pair?code=…` + `POST …/approve`, общий `approve_pending_pair` | **выполнено** |
| P1-C | Секция «Непривязанные устройства» по флагу, ключи не рендерятся, документация | **выполнено** |

---

## 1. Что уже есть (не нужно писать с нуля)

| Компонент | Состояние |
| :--- | :--- |
| `account/` — кабинет | Google OIDC (Authorization Code + PKCE S256, `state`, `nonce`, проверка `id_token`), SQLite (`accounts`, `tenants`, `devices`, `account_sessions`, `auth_flows`), серверные сессии, CSRF + `Origin`, привязка входа к браузеру, лимиты входа, allowlist владельцев |
| Монтирование | `gateway.py`: `Mount("/account", app=_create_account_app(node_control=CabinetNodeControl()))` — кабинет уже часть шлюза |
| Передача действий на узел | `CabinetNodeControl.call()` → `call_remote_tool(node, "node_control", …)` → `core/mcp_tools._tool_node_control` → `core/settings.apply` |
| Действия, уже поддержанные из кабинета | `status`, `mode` (read_only/approval/full), `cd`, `sandbox` (on/off, network), `pause`/`resume`, `remote_control` on/off |
| Устройства | список из `account.db` по `tenant_id`; привязка по `link_key` или device-token (`POST /account/devices/claim`) |
| `rhm` | меню + one-shot команды: `status`, `mode`, `cd`, `isolation`, `remote-control`, `pause`, `resume`, `goal`, `approvals`, `schedule`; локальные: лог, QR, web-настройки, обновление |
| Локальная web-панель узла | `rhm --web` (`core/web_settings.py`, loopback) — не путать с кабинетом: это панель **одного** узла |

Вывод: **OAuth и панель уже реализованы**; не хватает трёх вещей: (а) включить и настроить OAuth,
(б) автоматически привязывать устройства к аккаунту (сейчас — вручную ключом), (в) довести набор
действий кабинета до паритета с `rhm`.

---

## 2. Что нужно для Google OAuth (включение)

### 2.1. Google Cloud
1. Проект → **APIs & Services → OAuth consent screen**: External (или Internal), scopes `openid`,
   `.../userinfo.email`, `.../userinfo.profile` (Drive — отдельным согласием).
2. **Credentials → Create credentials → OAuth client ID → Web application**.
3. **Authorized redirect URI** — побайтово:
   `https://<домен>/account/auth/google/callback`.
4. Для Testing — добавить себя в **Test users**; для публичного доступа — Publish app.

### 2.2. Переменные шлюза
```dotenv
MESH_ACCOUNT=1
MESH_ACCOUNT_BASE_URL=https://<домен>        # origin без пути
MESH_GOOGLE_CLIENT_ID=<...>.apps.googleusercontent.com
MESH_GOOGLE_CLIENT_SECRET=<...>
MESH_SESSION_SECRET=<32+ случайных байта>    # подпись cookie; смена = разлогин всех
MESH_TOKEN_KEY=<32+ байта>                   # шифрование Drive-токенов (опционально)
MESH_ACCOUNT_DB=/var/lib/rhm-gateway/account.db
# рекомендуемое для общего шлюза:
MESH_OWNER_EMAILS=you@example.com            # allowlist (иначе — любой Google-аккаунт)
MESH_CLOSE_REGISTER_WITH_ACCOUNT=1           # закрыть /api/register при включённом кабинете
```
Требования: HTTPS (Google не примет http-origin), точное совпадение redirect URI, `PyJWT[crypto]`
в venv шлюза (`requirements-gateway.txt`), запись в `account.db` и `registry.json`.

### 2.3. Проверка
```
GET /account              → 200, страница входа
GET /account/auth/google/start → 302 на accounts.google.com с PKCE
GET /account/auth/google/callback?... → 303 на /account, cookie __Host-rhm-session
```
Типовые ошибки: `redirect_uri_mismatch` (домен/слэш), `invalid_client` (secret), `access_denied`
(нет в Test users).

### 2.4. Безопасность (уже в коде, не отключать)
`__Host-` cookie, HttpOnly/Secure/SameSite=Lax, CSRF + `Origin`, привязка `state` к cookie,
лимит входа, allowlist владельцев, сессии в SQLite, кабинет не принимается как доступ к MCP.

---

## 3. Что нужно для панели «все устройства + действия как в rhm»

### 3.1. Учёт устройств и владение

Сейчас `account.db.devices` заполняется вручную (`/account/devices/claim` по `link_key`).
Чтобы панель показывала **все** добавленные устройства автоматически:

1. **Привязка при установке.** Добавить в установщики шаг: после `POST /api/register` (или пары)
   устройство получает `owner_subject`; либо перенести сценарий пары из референса
   (`/pair?code=…` + `X-Pair-Secret`), чтобы владелец подтверждал устройство в браузере.
2. **Единый список.** Читать `registry.json` (источник истины по ключам) и `account.db`
   (владелец, тенант, отображаемое имя), объединяя по `node_name`. В карточке: hostname, ОС/арх,
   онлайн (туннель), `last_seen`, режим, remote-control.
3. **Миграция существующих устройств.** Первый вход владельца: показать «непривязанные» узлы и
   предложить привязать один раз по `link_key` (как сейчас), затем проставлять `owner_subject`.

**Флаг миграции:** секция «Непривязанные устройства» на главной кабинета включается только
`MESH_CABINET_SHOW_UNCLAIMED=1` (по умолчанию **выключено**; принимаются `1/true/yes/on`).
В ней — имя узла, hostname, ОС/арх и `last_seen` (не больше 50 строк) для записей реестра без
`owner_subject` и без `account_id`; ключи (`link_key`, device token) не рендерятся, чужие узлы
не показываются, привязка идёт существующей формой `POST /account/devices/claim`. На общем шлюзе
флаг оставляют выключенным, чтобы не афишировать ещё не привязанные машины.

### 3.2. Карта действий: `rhm` → панель → транспорт

| `rhm` | Что делает | Есть в кабинете | Как выполнять |
| :--- | :--- | :--- | :--- |
| `rhm status` | режим, папка, пауза, изоляция, remote, approvals, online | да (карточка) | `node_control {action:"status"}` |
| `rhm mode read_only\|approval\|full` | режим доступа | да | `node_control {action:"mode"}` |
| `rhm cd <path>` | рабочая папка | да | `node_control {action:"cd"}` |
| `rhm isolation strict\|network\|off` | контейнер | да | `node_control {action:"sandbox"}` |
| `rhm remote-control on\|off` | удалённое управление (off — только с консоли) | да | `node_control {action:"remote_control"}` |
| `rhm pause` / `resume` | пауза приёма вызовов | да | `node_control {action:"pause"/"resume"}` |
| `rhm approvals list\|approve\|deny\|allow-all\|revoke` | очередь подтверждений | **нет** | нужен новый `node_control` action `approvals` |
| `rhm goal get\|set\|done\|blocked\|clear` | постоянная цель узла | **нет** | `goal` tool уже есть — прокинуть через `node_control` |
| `rhm schedule list\|add\|cancel` | таймеры/кроны | **нет** | `schedule` tool — прокинуть |
| лог `l` / `_open_log` | хвост `agent.log` | **нет** | новый action `log_tail` (лимит строк, редакция секретов) |
| `usage` | статистика вызовов | **нет** | новый action `usage` (`core/usage.py`) |
| QR / MCP URL | ссылка для коннектора | **нет** (намеренно: ключ не рендерится) | отдельная кнопка «показать ссылку» с шагом подтверждения |
| `rhm --web` | локальная панель узла | n/a | остаётся локальной |
| rotate / rekey | смена link-key/token | **нет** | `POST /v1/devices/{user}/rotate` из кабинета (админ/узел) |
| update | обновление узла | **нет** | `mesh_update` через `node_control`/tool, с подтверждением |
| share / drive | шары и Drive | Drive — да; шары — нет | `share_*` tools + список шар |

Локальные по своей природе (нельзя из панели): автозапуск, трей, установка/удаление, `doctor`,
`--web` (это панель самого узла). Их в кабинете показывать как «только на устройстве».

### 3.3. Транспорт: расширить `node_control`

Сейчас `core/settings.apply` знает только `status/mode/cd/sandbox/pause/resume/remote_control`.
Нужно добавить в `_tool_node_control` маршрутизацию (с `_origin=web`, проверкой `MESH_READ_ONLY`
и подтверждением для мутаций):

```python
# предлагаемые actions
"approvals": {"list"|"approve"|"deny"|"allow_all"|"revoke", "id", "seconds"}
"goal":      {"get"|"set"|"complete"|"blocked"|"clear", "text", "note"}
"schedule":  {"list"|"add"|"cancel", "when", "text"}
"log_tail":  {"lines": 50}
"usage":     {"days": 1}
"link":      {"show": true}          # только после step-up подтверждения
"rekey":     {"kind": "link"|"token"} # узел сам зовёт /v1/devices/.../rotate
"update":    {"action": "status"|"check"|"apply"}
```
Каждый action — тонкая обёртка над существующими модулями (`control`, `goal`, `schedule`,
`usage`, `updater`), чтобы панель и `rhm` не расходились.

### 3.4. Маршруты кабинета

Добавить (в стиле текущих `/devices/{id}/control`):
```
POST /account/devices/{id}/approvals/{action}
POST /account/devices/{id}/goal
POST /account/devices/{id}/schedule/{action}
GET  /account/devices/{id}/log
GET  /account/devices/{id}/usage
POST /account/devices/{id}/keys/rotate        # link-key / token
POST /account/devices/{id}/update/{action}
GET  /account/devices                          # полный список (сейчас — на главной)
```
Все POST: сессия + CSRF + `Origin` + проверка `tenant_id`; разрушительные — страница подтверждения
(`confirm=yes`), как уже сделано для `remote_control`.

### 3.5. UI

- Главная: таблица устройств (онлайн, ОС, режим, remote, последняя активность), «+ привязать».
- Карточка: блоки «Режим», «Папка», «Изоляция», «Пауза», «Remote control», «Подтверждения»,
  «Цель», «Расписания», «Лог», «Использование», «Ключи», «Обновление».
- Каждое действие — отдельная форма (без JS), результат — redirect с `notice/error`
  (как сейчас). Никаких ключей в HTML кабинета.
- Состояние «узел офлайн»: действия недоступны с понятным текстом.

---

## 4. Этапы и оценка

| Этап | Содержание | Оценка |
| :--- | :--- | ---: |
| **P0. Включить OAuth** | env, Google client, HTTPS, `MESH_OWNER_EMAILS`, документация, smoke-тест | **1–2 pd** |
| **P1. Все устройства** | объединение registry+DB, миграция «непривязанных», авто-привязка при установке/паре | **сделано** (было 3–5 pd) |
| **P2. Паритет действий (база)** | `approvals`, `log_tail`, `usage`, `goal`, `schedule` через `node_control` + маршруты/UI | **4–6 pd** |
| **P3. Ключи и обновление** | rotate link/token из кабинета, `mesh_update` с подтверждением, список шар | **3–4 pd** |
| **P4. Качество** | тесты (маршруты, права тенанта, CSRF, офлайн), мобильный вид, аудит действий | **3–4 pd** |
| | **Итого** | **11–16 pd** (оценка P1 снята) |

P1 закрыта в ветке `feat/p1-device-binding`: единый список устройств, подтверждение пары из
кабинета и opt-in секция «непривязанных» с привязкой по `link_key`.

MVP сегодня (без нового кода): включить OAuth (P0), привязать устройства по `link_key`,
пользоваться уже готовыми действиями (mode/cd/sandbox/pause/remote_control).

---

## 5. Тесты и приёмка

- `tests/test_account_*`: вход, cookie, CSRF/Origin, allowlist, rate limit (есть), плюс:
  чужой `device_id` → 404; действие на офлайн-узле → понятная ошибка; `confirm` обязателен.
- `tests/test_node_control*`: каждый новый action — happy path + отказ в `read_only` +
  `_origin=web` нельзя подделать из модели.
- `tests/test_gateway_node_control.py`: маршрутизация `call_remote_tool` для новых действий.
- Ручной сценарий: два аккаунта, по два устройства; A не видит B; действия A не влияют на B;
  rotate ломает старый URL; обновление требует подтверждения.

---

## 6. Ограничения, которые нужно принять

1. **Оператор шлюза видит трафик** (терминация MCP) — панель не меняет модель доверия, см.
   [ARCH_admin_cannot_access_users.md](ARCH_admin_cannot_access_users.md).
2. Часть команд `rhm` **локальна по смыслу** (автозапуск, трей, установка) — в панели только
   отображается статус, менять нельзя.
3. `rhm --web` — панель одного узла на loopback; кабинет её не заменяет (разные контуры).
4. Для `isolation` нужен Docker/Podman на узле; для `update` — перезапуск службы.

---

## 7. Порядок включения «сегодня»

1. Google Cloud: OAuth client + redirect URI + test users.
2. На шлюзе: `MESH_ACCOUNT=1`, `MESH_ACCOUNT_BASE_URL`, `MESH_GOOGLE_CLIENT_ID/SECRET`,
   `MESH_SESSION_SECRET`, `MESH_OWNER_EMAILS`, `MESH_CLOSE_REGISTER_WITH_ACCOUNT=1`; перезапуск.
3. Проверить `/account` → вход → «Привязать устройство» по `link_key` из `rhm --once`.
4. Убедиться, что действия mode/cd/sandbox/pause/remote-control работают; остальное — по плану выше.
