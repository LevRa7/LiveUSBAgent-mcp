# План: OAuth-авторизация и веб-панель управления по образцу `remote-devices-mcp`

**Версия:** 1.0 (к исполнению)
**Дата:** 2026-11-18
**База:** локальный `Antigravity-mash` @ `2ec7f1a` (0.5.2)
**Образец:** `nicelight/remote-devices-mcp` @ `2bbc9a0`
**Связанные документы:** [COMPARE_remote_devices_mcp.md](COMPARE_remote_devices_mcp.md), [GOOGLE_OAUTH.md](GOOGLE_OAUTH.md), [wave-1-auth-account-log-settings.md](wave-1-auth-account-log-settings.md), [WEB_SETTINGS.md](WEB_SETTINGS.md)

---

## 0. Результат, который должен получиться

Владелец шлюза проходит путь:

```
curl -fsSL https://<gateway>/install.sh | bash
        │
        ├─ установщик создаёт заявку: POST /v1/devices/pair → {pair_code, device_secret, pair_url}
        ├─ открывает https://<gateway>/pair?code=XXXX-XXXX
        │     └─ вход через Google → «Подключить устройство» → устройство в аккаунте
        ├─ забирает ключи: GET /v1/devices/pair/{code} + X-Pair-Secret
        ├─ сохраняет agent.env, поднимает службу
        └─ проверяет реальный tools/call mesh_status и печатает Server URL

дальше: https://<gateway>/account — панель владельца
        ├─ список только своих устройств (онлайн, ОС, дата привязки)
        ├─ проверка связи, замена ссылки, отзыв доступа
        └─ удалённое управление: режим, папка, sandbox, пауза
```

Ключевое отличие от референса: **SQLite-кабинет, Drive, удалённое управление и локальная панель
узла остаются**, а из референса добавляются **браузерная привязка, `X-Pair-Secret`, публикация
установщиков и ops-артефакты**. MCP-транспорт и link-key URL при этом не ломаются ни на одном шаге.

---

## 1. Исходное состояние (baseline)

| Компонент | Сейчас | Что делаем |
| :--- | :--- | :--- |
| `account/` (кабинет) | Google OIDC (PyJWT), SQLite `accounts/tenants/devices/sessions/auth_flows`, Drive, remote-control, CSP | ужесточаем, расширяем устройства и действия, добавляем `/pair` |
| `gateway.py` | `/v1/devices/pair*` без секрета инициатора, approve только по admin-токену, `/api/register` открыт | добавляем `X-Pair-Secret`, owner-approve, гейтинг регистрации, публикацию установщиков |
| `/pair` | отсутствует (ссылка из API → 404) | страница + редирект с корня |
| `core/onboarding.py` | отсутствует | новый модуль (адаптация референса под `core.paths`/`core.domain`) |
| `install.sh` / `install.ps1` | `POST /api/register` | ветка `--pair` / `-Pair`, автоопределение по `/health` |
| Публикация файлов | нет | `/install.sh`, `/install.ps1`, `/core/**`, `/ops/...`, `/assets/*` |
| Ops | `deploy_gateway.sh`, nginx-шаблоны | + systemd unit, `gateway.env.example`, `Caddyfile.example`, docs |

**Ограничение, которое нельзя нарушать:** шлюз — один процесс (`uvicorn --workers 1`), потому что
`active_tunnels`, `pending_pairs` и SSE-очереди живут в памяти. Любая новая in-memory структура
обязана это переживать (или явно задокументировать потерю при рестарте).

---

## 2. Целевая архитектура

```
                    Браузер владельца                    Облачный агент (Gemini/ChatGPT/Claude)
                          │                                       │
                          │ HTTPS                                 │ HTTPS MCP (link-key URL)
                          ▼                                       ▼
        ┌──────────────────────────────────────────────────────────────────────┐
        │ gateway.py (один процесс, Starlette)                                 │
        │  /pair ─────► 303 /account/pair?code=…                               │
        │  /account (Mount)                                                    │
        │    ├─ GET  /                 кабинет: устройства, Drive, remote-ctl  │
        │    ├─ GET  /pair?code=…      подтверждение устройства                │
        │    ├─ POST /pair/{code}/approve                                      │
        │    ├─ POST /devices/{id}/keys/{check|rotate|revoke}                  │
        │    ├─ POST /devices/{id}/control  (есть: mode/cd/sandbox/pause)      │
        │    └─ GET  /auth/google/{start,callback}, POST /logout               │
        │  /v1/devices/pair*           заявки + X-Pair-Secret                  │
        │  /api/register               закрыт при включённом OAuth             │
        │  /install.sh,/install.ps1,/core/**,/ops/**,/assets/**  публикация    │
        │  /sse,/mcp,/messages,/ws/tunnel   MCP-транспорт (без изменений)      │
        └───────┬───────────────────────────────┬──────────────────────────────┘
                │ registry.json (ключи, owner)  │ account.db (аккаунты, тенанты, устройства)
                ▼                               ▼
        ┌───────────────────────┐      ┌──────────────────────────────────────┐
        │ registry.json         │      │ SQLite: accounts, tenants, devices,   │
        │  user → {token_sha256,│      │ sessions, auth_flows, google_tokens   │
        │  link_key, owner_subject,      └──────────────────────────────────────┘
        │  account_id, paired_at}│
        └───────────────────────┘
```

**Разделение ответственности (инвариант):** `registry.json` — единственный источник истины по
**учётным данным узла**; `account.db` — источник истины по **аккаунту, тенанту и отображению**.
Связка — `owner_subject` + `account_id` в записи registry и `node_name` в `devices`.
Ни одна из сторон не хранит чужой секрет.

---

## 3. Инварианты (проверяются тестами)

1. **Вход ≠ доступ к MCP.** Cookie кабинета не принимается ни в `/sse`, ни в `/mcp`, ни в `/ws/tunnel`.
2. **Link-key остаётся первоклассным.** Любой сценарий установки обязан работать без Google (admin-подтверждение пары или `MESH_ENROLL_SECRET`), OAuth только улучшает.
3. **Секрет инициатора.** Ключи узла выдаются только предъявителю `device_secret`; короткий код не является секретом.
4. **Одна выдача.** Повторный `GET /v1/devices/pair/{code}` с тем же секретом не создаёт второе устройство (до TTL).
5. **Чужое — 404.** Устройство другого владельца не отличается от несуществующего.
6. **CSRF + Origin** на каждом POST кабинета; токен CSRF привязан к сессии.
7. **Нет секретов в URL/логах.** `code`, `state`, `token`, `lk_…` не попадают в access-log, HTML-кэш и сообщения об ошибках.
8. **Рестарт шлюза** завершает браузерные сессии (в референсе) или сохраняет их (локально — сохраняем), но **заявки на привязку теряются**; это задокументировано.
9. **Один воркер.** `--workers 1`; в `deploy_gateway.sh` — проверка и предупреждение.
10. **Транспорт независим.** Ошибка в кабинете/OAuth не роняет `/sse`, `/mcp`, `/ws/tunnel`, шары и туннели.

---

## 4. Этапы

| Этап | Содержание | Оценка | Зависит от |
| :--- | :--- | ---: | :--- |
| **P0** | Решения, конфиг, валидация, флаги | 0.5 pd | — |
| **P1** | Ужесточение OAuth (binding, Origin, rate-limit, CSP, azp) | 2.5 pd | P0 |
| **P2** | Backend привязки: `X-Pair-Secret`, owner-approve, гейтинг регистрации | 2.5 pd | P0 |
| **P3** | Страница `/pair` и `/account/pair/*` | 2.0 pd | P2 |
| **P4** | Кабинет: устройства из registry, check/rotate/revoke, adopt | 2.5 pd | P2 |
| **P5** | `core/onboarding.py` + установщики (`--pair`) | 2.5 pd | P2 |
| **P6** | Публикация установщиков и `/health.onboarding` | 1.5 pd | P5 |
| **P7** | Ops: systemd, env, Caddy, docs, фильтр логов | 1.5 pd | P6 |
| **P8** | QA, e2e, приёмка, раскатка | 2.0 pd | P1–P7 |
| | **Итого** | **17.5 pd** | 1 разработчик ≈ 3.5 недели; 2 разработчика ≈ 2 недели |

Порядок жёсткий только для P2 → P3/P4/P5; P1 можно вести параллельно P2.

---

### P0. Решения и каркас (0.5 pd)

**T0.1 — ADR-004 «Личность и ключи устройства».**
Файл: `docs/adr/0004-owner-identity-vs-device-credentials.md`.
Зафиксировать: Google-личность владельца, `owner_subject = "google:<sub>"`, разделение
`registry.json`/`account.db`, отказ от `email` как идентификатора (смена email не меняет владельца),
поведение при рестарте, привязка к одному воркеру.
**Приёмка:** ADR принят, ссылки из `docs/GOOGLE_OAUTH.md`.

**T0.2 — Валидация конфигурации на старте.**
Файлы: `account/config.py`, `gateway.py` (инициализация `PUBLIC_BASE_URL`).
Добавить `AccountConfig.validate()` и `ConfigError`:

```python
def validate(self) -> None:
    if not self.enabled:
        return
    if not (self.client_id and self.client_secret and self.session_secret):
        raise ConfigError("MESH_ACCOUNT=1 требует MESH_GOOGLE_CLIENT_ID, "
                          "MESH_GOOGLE_CLIENT_SECRET и MESH_SESSION_SECRET")
    parsed = urlparse(self.base_url)
    if parsed.scheme != "https" or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ConfigError("MESH_ACCOUNT_BASE_URL должен быть https-origin без пути и query")
    if len(self.session_secret) < 32:
        raise ConfigError("MESH_SESSION_SECRET короче 32 символов")
```

Вызывать в `gateway.py` рядом с монтированием кабинета: при `MESH_ACCOUNT=1` и ошибке — **падение
на старте с понятным сообщением** (как в референсе), а не молчаливый «кабинет не настроен».
Разрешить HTTP только при `MESH_ACCOUNT_ALLOW_HTTP=1` (локальная разработка).
**Тест:** `tests/test_account_config.py::test_invalid_config_fails_fast` (4 кейса: нет секрета,
http-base, base с путём, короткий session secret).

**T0.3 — Флаги.**
`MESH_PAIRING=auto|owner|admin|off` (по умолчанию `auto`: owner — если кабинет настроен, иначе admin),
`MESH_ALLOW_SELF_REGISTER=0|1` (аварийный люк для лабораторий), `MESH_PAIR_TTL=600`,
`MESH_PAIR_MAX_PENDING=200`.
**Приёмка:** флаги описаны в `gateway.env.example` (P7) и в `docs/PAIRING.md`.

**T0.4 — Зависимости.**
`requirements-gateway.txt`: `PyJWT[crypto]>=2.8` (есть), явно `cryptography>=42` (для `account/crypto.py`),
`google-auth` **не добавляем** — проверка ID token остаётся на PyJWT, чтобы не тянуть `requests` в шлюз.

---

### P1. Ужесточение OAuth (2.5 pd)

**T1.1 — Привязка входа к браузеру (login binding).**
Файлы: `account/auth.py`, `account/app.py`, `account/db.py`.
Сейчас `auth_flows.state` ничем не связан с браузером: злоумышленник может начать вход, получить
`state` и подсунуть жертве ссылку. Добавить cookie `__Host-rhm-login` (на http — `rhm_login`, как
уже сделано в `account/sessions.py::cookie_name`; 32 байта, HttpOnly, Secure при https,
SameSite=Lax, TTL 600 с) и колонку `auth_flows.binding_hash`:

```sql
ALTER TABLE auth_flows ADD COLUMN binding_hash TEXT NOT NULL DEFAULT '';
```

`start()` пишет `sha256(binding)` в flow; `callback()` сверяет `hmac.compare_digest` и удаляет cookie.
**Тест:** `tests/test_account_auth.py::test_callback_requires_same_browser` (чужой cookie → 400,
`state` погашен).

**T1.2 — Проверка `Origin` на POST.**
Файл: `account/app.py`, функция `csrf_ok`:

```python
def csrf_ok(request, account, form) -> bool:
    supplied = str(form.get("csrf") or "")
    if not supplied or not hmac.compare_digest(supplied, str(account.get("csrf") or "")):
        return False
    origin = request.headers.get("origin")
    return origin is None or origin == cfg.base_url
```

Тот же помощник — для `/pair/{code}/approve`.
**Тест:** `tests/test_account_security.py::test_post_with_foreign_origin_is_rejected` (403 при
`Origin: https://evil.example`, 200 при отсутствии заголовка — curl/не-браузер).

**T1.3 — Ограничение частоты.**
Файл: `account/app.py` (небольшой `SlidingWindow` в памяти процесса, ключ — IP или sid).
Лимиты: `/auth/google/start` 20/мин/IP; `callback` 20/мин/IP; `/pair/{code}/approve` 10/мин/сессия;
`claim` — существующий лимит сохранить. Ответ 429 с `Retry-After`.
**Тест:** `tests/test_account_security.py::test_login_is_rate_limited` (21-й запрос → 429).

**T1.4 — Строгая проверка ID token.**
Файл: `account/auth.py::verify_id_token`.
Добавить: `azp` (если есть — равен `client_id`), `iat` не в будущем, `exp` с `leeway=60`,
`aud` строго `client_id`, `email_verified is True`, `sub` непустой ≤ 255, отказ при неизвестном `kid`
после одного обновления JWKS (уже есть), таймауты JWKS 10 с и **fail-closed** при недоступности.
**Тест:** `tests/test_account_auth.py` — параметризованно: битая подпись, чужой `aud`, `azp`,
просроченный, отсутствующий `nonce`, `email_verified=false`.

**T1.5 — Минимизация скоупов.**
`SCOPES = "openid email"`; `profile` убрать из входа (имя берём из `name`, если пришло, иначе email).
Drive остаётся отдельным flow (`DRIVE_SCOPES`, `access_type=offline`, `prompt=consent` только там).
**Тест:** `tests/test_account_auth.py::test_login_requests_minimal_scopes`.

**T1.6 — CSP и заголовки HTML.**
Файл: `account/app.py::page/_headers`.
Перейти на nonce для `<style>` (как в референсе) и убрать `'unsafe-inline'`:

```
default-src 'none'; style-src 'nonce-<n>'; img-src 'self' data:;
form-action 'self'; frame-ancestors 'none'; base-uri 'none'
```

OAuth-редиректы: `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
**Тест:** `tests/test_account_mount.py::test_pages_have_nonce_csp` (нет `unsafe-inline`, nonce совпадает).

**T1.7 — Безопасный HTTP-клиент Google.**
`account/auth.py::_post_form`: собственный opener с `NoRedirect` (запрет 30x, чтобы `client_secret`
не ушёл на чужой хост), таймаут 15 с, `Content-Type` строго form-urlencoded, тело ≤ 4 КБ на ответ.
**Тест:** `tests/test_account_auth.py::test_token_endpoint_redirect_is_refused` (мок 302 → ошибка, не переход).

**Приёмка P1:** все новые тесты зелёные; ручная проверка: вход, выход, повторный вход, чужой
`state`, чужой `Origin`, 429; `curl -I /account` показывает nonce-CSP и `no-store`.

---

### P2. Backend привязки (2.5 pd)

**T2.1 — Модель заявки.**
Файл: `gateway.py` (`pending_pairs`, `purge_pairs`).

```python
{
  "device_secret_sha256": "…",        # 32 байта, sha256
  "created_at": ts, "expires_at": ts + MESH_PAIR_TTL,
  "hostname": "…", "os": "…", "arch": "…",
  "approved": False, "owner_subject": None, "account_id": None, "tenant_id": None,
  "issued": None, "collected": False, "attempts": 0,
}
```

`purge_pairs()` удаляет только истёкшие (сейчас дополнительно удаляет `collected` — это ломает
идемпотентную повторную выдачу; убрать).
**Тест:** `tests/test_pairing.py::test_pair_expires_after_ttl`.

**T2.2 — `POST /v1/devices/pair`.**
Добавить `device_secret` (32 байта `token_urlsafe`), `pair_url` строить из `PUBLIC_BASE_URL`,
`Cache-Control: no-store`, лимит 20/мин/IP, `PAIR_MAX_PENDING` → 503, в лог — только код и hostname
(без секрета).
**Тест:** `tests/test_pairing.py::test_start_returns_secret_and_url`, `test_too_many_pending`.

**T2.3 — `GET /v1/devices/pair/{code}`.**
Требовать `X-Pair-Secret`; сравнивать `sha256` в постоянном времени; пока не подтверждено — `{status:
"pending", expires_in}`; после подтверждения отдавать ключи **идемпотентно** до TTL, но проверять,
что выданный токен ещё действителен (`token_matches_device`), иначе `410 pair_revoked`.
Актуальный `link_key` подставлять из registry (мог быть ротирован владельцем, пока установщик ждал).
**Тест:** `tests/test_pairing.py` — 5 кейсов: без секрета 403, неверный 403, повторная выдача,
ротация link key во время ожидания, отзыв → 410.

**T2.4 — `POST /v1/devices/pair/{code}/approve`.**
Два пути авторизации:
* owner-сессия (вызывается из кабинета, P3) — `owner_subject`, `account_id`, `tenant_id`;
* `X-Admin-Token` — как сейчас, но с `owner_subject = "admin:<token-id>"` или `None` (self-host без OAuth).

Отказы: 409 `already_approved` другим владельцем, 410 истекла, 404 неизвестна. Никаких `await` между
чтением registry, выдачей ключей и сохранением (один воркер сериализует).
**Тест:** `tests/test_pairing.py::test_approve_by_admin`, `test_approve_by_owner_binds_account`,
`test_second_owner_cannot_approve`.

**T2.5 — `approve_pair()` с владельцем + атомарность.**
Сигнатура: `approve_pair(code, pair, data, owner_subject=None, account_id=None, tenant_id=None,
claim=None)`; после создания записи registry вызывает `account.claim_device(tenant_id, node,
display_name)` (через bridge) и при ошибке откатывает запись registry. Уникальность имени — как
сейчас (`unique_node_name`, `RESERVED_NAMES`). В запись добавляются `owner_subject`, `account_id`,
`paired_at`, `pair_source="browser"|"admin"`.
**Тест:** `tests/test_pairing.py::test_registry_and_db_stay_consistent` (сбой БД → записи нет).

**T2.6 — Гейтинг регистрации.**
Файл: `gateway.py::enrolment_refusal`.

```python
if REGISTER_MODE == "pair" or (owner_configured() and not ALLOW_SELF_REGISTER):
    return ("Self-registration is disabled on this gateway. "
            "Use pairing: POST /v1/devices/pair, or install from /install.sh")
```

`owner_configured()` — `AccountConfig.from_env().configured` **без** импорта кабинета (ленивый вызов,
чтобы падение кабинета не ломало шлюз).
**Тест:** `tests/test_register_gating.py` — 4 кейса: open, pair, OAuth включён → 410, OAuth + `MESH_ALLOW_SELF_REGISTER=1` → 200.

**T2.7 — Интерфейс для кабинета.**
Файл: `gateway.py` (класс `CabinetNodeControl`, передаётся в `create_app`). Добавить методы:

```python
class CabinetBridge:                      # протокол, реализуется в gateway.py
    def available(self) -> bool: ...
    def online(self, node: str) -> bool: ...
    def claim(self, node, credential, owner_subject="", account_id="") -> dict: ...
    async def call(self, node, payload) -> dict: ...
    # новое для привязки и панели
    def pair(self, code: str) -> dict | None: ...
    def approve(self, code, owner_subject, account_id, tenant_id) -> dict: ...
    def device(self, user: str) -> dict | None: ...          # hostname/os/link_key/owner_subject
    def rotate_link(self, user: str) -> dict: ...
    def revoke(self, user: str) -> dict: ...                 # registry + закрыть туннель
    async def check(self, user: str) -> dict: ...            # реальный tools/call mesh_status
```

`account/app.py::create_app(config=None, node_control=None)` сохраняет имя параметра — существующие
тесты (`tests/test_account_*.py`, фикстура `cabinet`) не ломаются.
**Тест:** `tests/test_account_mount.py` дополняется фейковым bridge с новыми методами.

---

### P3. Страница `/pair` (2.0 pd)

**T3.1 — Маршрут с корня.**
`gateway.py`, перед catch-all шаром:

```python
Route("/pair", pair_redirect, methods=["GET"]),
```

`pair_redirect` валидирует `code` регуляркой `[A-Z2-9]{4}-[A-Z2-9]{4}` и отвечает
`303 → /account/pair?code=…` (+ `Cache-Control: no-store`). Неверный код → 400 с текстом «Откройте
ссылку из установщика». Если кабинет не настроен → 503 с причиной (OAuth не сконфигурирован).
**Тест:** `tests/test_pair_route.py` — 303 с сохранением кода, 400 на мусор, 503 без кабинета.

**T3.2 — Страница `/account/pair` (`GET`).**
Файл: `account/app.py`.
Содержимое по состояниям:

| Состояние | Что видно |
| :--- | :--- |
| заявки нет/истекла | «Ссылка истекла. Запустите установщик ещё раз» (404) |
| не вошёл | имя/ОС/арх устройства, код в `<details>` «сверить код», кнопка «Войти через Google» |
| вошёл, не подтверждено | «Вы вошли как …», кнопка «Подключить устройство», «Сменить аккаунт» (logout с `return_code`) |
| подтверждено этим владельцем | 303 → `/account` |
| подтверждено другим | 409 «Устройство привязано к другому аккаунту» |

Никакого автоподтверждения после логина. Код — в `<details>`, вводить не нужно.
**Тест:** `tests/test_pair_page.py` — рендер состояний, экранирование hostname (`<script>` → `&lt;`).

**T3.3 — `POST /account/pair/{code}/approve`.**
Проверки: сессия владельца, CSRF, Origin, лимит 10/мин, заявка существует и не подтверждена.
Вызов `bridge.approve(code, owner_subject, account_id, tenant_id)`; при успехе 303 → `/account`
с `notice`; при конфликте — 409; при истечении — 404.
**Тест:** `tests/test_pair_page.py::test_approve_creates_registry_and_device`,
`test_approve_requires_csrf`, `test_approve_of_foreign_pair_is_409`.

**T3.4 — Доступность и мобильный вид.**
375 px, клавиатурная навигация, `details/summary`, `aria-current="step"`, `role="status"` для
результата, `prefers-reduced-motion`. Без внешних CDN и шрифтов.
**Приёмка:** ручная проверка в Chrome DevTools (375 px) и с клавиатуры.

**T3.5 — Тексты ошибок без утечек.** Ни код, ни токен, ни ответ Google не попадают в сообщение
об ошибке и в HTML.

---

### P4. Кабинет: устройства и действия (2.5 pd)

**T4.1 — Данные списка.**
`account/app.py::root`: устройства берём из `store.list_devices(tenant)`, к каждому —
`bridge.device(node_name)`; показываем hostname, ОС, «онлайн/офлайн», дату привязки, источник
(`browser` / `adopted`), режим и `remote_control`. Устройства без записи в registry (удалённые
вручную) помечаем «нет связи с реестром» и предлагаем отвязать.
**Тест:** `tests/test_account_devices.py::test_dashboard_lists_only_own_devices`.

**T4.2 — Действия по ключам.**
Новый маршрут `POST /account/devices/{device_id}/keys/{action}` (`check|rotate|revoke`):

| Действие | Что делает bridge | Подтверждение |
| :--- | :--- | :--- |
| `check` | `tools/call mesh_status` через туннель | нет |
| `rotate` | новый `link_key`, старый URL перестаёт работать | `confirm=yes`, отдельная страница с объяснением |
| `revoke` | удаление записи registry + закрытие туннеля | `confirm=yes`, отдельная страница |

После `revoke` — `store.revoke_device(device_id)` (`status='revoked'`), устройство исчезает из списка.
**Тест:** `tests/test_account_devices.py` — 6 кейсов (успех, без confirm, чужое устройство 404,
неверный CSRF 403, отзыв закрывает туннель, ротация меняет URL).

**T4.3 — Совместимость с legacy.**
Существующая привязка по link key (`POST /devices/claim`) остаётся как **adopt** для узлов,
установленных до OAuth: `bridge.claim(node, credential, owner_subject, account_id)` проставляет
владельца в registry, затем `store.claim_device`. В UI — отдельный блок «Устройство уже установлено?
Привяжите его ключом» с пояснением, что ключ одноразово доказывает владение.
**Тест:** `tests/test_account_control.py::test_claim_sets_owner_subject`.

**T4.4 — Remote-control сохраняем без изменений** (`/devices/{id}/control`, `lock_controls`,
`device_settings`). Проверить, что новые маршруты не перехватывают `/control`.
**Тест:** существующие `tests/test_account_control.py` остаются зелёными.

**T4.5 — Ссылка и инструкция.** В карточке устройства — MCP URL (без токена в HTML-исходнике?
Ссылка нужна; показываем как в референсе, `textarea readonly` + копирование через Clipboard API с
ручным fallback), кнопка «Как добавить в ChatGPT» и (опционально)
`gateway_assets/chatgpt-add-mcp.png` (P6).
**Тест:** `tests/test_account_devices.py::test_device_card_shows_link_and_copy`.

**T4.6 — Пределы.** Не более 50 устройств на страницу, `?page=`, пустое состояние с инструкцией
«подключите первое устройство» и ссылкой на `/install.sh`.

---

### P5. Онбординг в установщиках (2.5 pd)

**T5.1 — `core/onboarding.py`** (новый файл, адаптация референса):

```python
def gateway_url(value, allow_local_http=False) -> str     # https-only, нормализация
def request_json(base, path, *, data=None, headers=None, timeout=35)  # NoRedirect, ошибки с кодом
def pair(base, config_dir, name, *, timeout=600, open_browser=True, announce=print, progress=None)
def save_config(root, config)      # domain.env, chatgpt-url.txt, agent.env (0600)
def load_device(config_dir) -> dict
def verify_device(base, config, timeout=40, announce=None)
def main(argv=None)                # pair | status | link
```

Требования: `onboarding.pending.json` и `onboarding.receipt.json` с правами 0600; повторный запуск
использует ту же заявку; чужой шлюз в существующем `agent.env` → ошибка без перезаписи; URL для
браузера собирается локально (`base + /pair?code=`), а не берётся из ответа; ожидание с шагом 2 с и
понятным прогрессом; `confirm_browser()` читает `CONIN$`/`/dev/tty`, если stdin не TTY (curl | bash).
**Тест:** `tests/test_onboarding.py` (мок HTTP): reuse config, pending reuse, expired pending,
missing keys, verify_device retry, receipt recovery, чужой gateway.

**T5.2 — `install.sh`.**
Флаг `--pair`; автоопределение: `GET /health` → `onboarding.owner_login == true` → pair по умолчанию
(при доступном TTY), иначе register. На `410 registration_disabled` — подсказка «используйте
`--pair` или `/install.sh`» и ненулевой код возврата. Ветка register остаётся (`--no-pair`,
`MESH_ALLOW_SELF_REGISTER=1`). После pair — `tools/call mesh_status` и печать Server URL.
**Тест:** `tests/test_installer_pairing.py` — статические проверки: есть `--pair`, есть вызов
`core.onboarding`, нет пароля/токена в командной строке.

**T5.3 — `install.ps1`.**
`-Pair` (switch), `-NoBrowser`, та же логика; вывод — `Write-Host`; коды ошибок; для GUI-режима
(`install-gui.ps1`) — чекбокс «Подключить через браузер» во вторую очередь.

**T5.4 — Узел после установки.** `rhm` (меню) — пункт «Подключить устройство» → `python -m
core.onboarding pair`; `rhm --link` печатает Server URL после реальной проверки.
**Тест:** `tests/test_menu.py::test_connect_device_is_in_the_menu` (если меню уже покрыто).

**T5.5 — Документация.** `docs/PAIRING.md`: сценарий, коды ошибок, «что делать, если браузер на
другой машине», «рестарт шлюза = новая заявка», «повторная установка не создаёт второе устройство».

---

### P6. Публикация установщиков с шлюза (1.5 pd)

**T6.1 — Маршруты** (`gateway.py`, перед catch-all):

```python
Route("/install.sh", installer_asset, methods=["GET", "HEAD"]),
Route("/install.ps1", installer_asset, methods=["GET", "HEAD"]),
Route("/core/{path:path}", installer_asset, methods=["GET", "HEAD"]),
Route("/ops/windows/{name}", installer_asset, methods=["GET", "HEAD"]),
Route("/assets/{name}", installer_asset, methods=["GET", "HEAD"]),
```

**T6.2 — Белый список.** `install.sh`, `install.ps1`, `requirements-menu.txt`, все `core/**/*.py`
(рекурсивно, включая `core/platforms/**`), `ops/windows/{agent-watchdog.ps1,run-hidden.vbs,rhm-tray.ps1}`,
`ops/update.ps1`, `ops/doctor.ps1`, `skills/orchestrator.md`, `gateway_assets/*.{png,md}`.
Запрет: `tests/`, `docs/`, `*.db`, `*.env`, `registry.json`, `account/`, `.git`, симлинки,
файлы > 1 МБ. Отдельный тест на попытку достать `../gateway.py` и `agent.env`.

**T6.3 — Подстановка домена при отдаче.** Для `install.sh` — замена строки, анкоренной на
`PUBLISHED_DOMAIN=`; для `install.ps1` — `$PublishedDomain = …` с сохранением BOM; UTF-8.
(Локальные установщики уже содержат плейсхолдер `__MESH_DOMAIN__`, правки deploy не нужны.)
**Тест:** `tests/test_published_assets.py::test_installer_carries_gateway_domain`.

**T6.4 — Заголовки.** `Cache-Control: no-cache`, `X-Content-Type-Options: nosniff`,
`Content-Length`, корректный `HEAD`, `text/plain; charset=utf-8`.

**T6.5 — `/health.onboarding`.** Аддитивно:

```json
"onboarding": {"pairing": true, "owner_login": true, "installers": true}
```

Не менять существующие поля (их читают установщики и мониторинг).
**Тест:** `tests/test_mcp_endpoints.py` — старые ключи на месте, новые присутствуют.

---

### P7. Ops и документация (1.5 pd)

**T7.1 — `ops/systemd/rhm-gateway.service`** (по образцу референса, под локальные пути):
`--workers 1`, `EnvironmentFile=/etc/remote-harness-mcp/gateway.env`, `User=rhm-gateway`,
`NoNewPrivileges=true`, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/rhm-gateway`,
`Restart=always`.

**T7.2 — `ops/systemd/gateway.env.example`** с комментариями: `MESH_PUBLIC_URL`,
`MESH_REGISTRY_PATH`, `MESH_ACCOUNT=1`, `MESH_ACCOUNT_BASE_URL`, `MESH_GOOGLE_CLIENT_ID/SECRET`,
`MESH_SESSION_SECRET`, `MESH_TOKEN_KEY`, `MESH_ACCOUNT_DB`, `MESH_PAIRING=auto`, `MESH_PAIR_TTL`,
`MESH_REGISTER_MODE=pair`, `MESH_ADMIN_TOKEN`, `MESH_ENROLL_SECRET`, `MESH_ALLOW_SELF_REGISTER=0`.
Плюс `ops/systemd/Caddyfile.example` и `ops/systemd/rhm-node.service`.

**T7.3 — `docs/SELF_HOSTED_GATEWAY_RU.md`**: установка с нуля (venv, env, unit, Caddy, Google
Console), проверка `curl /health`, первый вход, привязка, бэкап (`registry.json` + `account.db`),
откат, ограничение одного воркера, «рестарт = потеря заявок и (опционально) сессий».

**T7.4 — Фильтр логов.** `logging.Filter`, вычищающий `token|code|state|secret|Authorization` из
`uvicorn.access` и сообщений шлюза; тест `tests/test_log_redaction.py` (строка с `code=...` не
попадает в лог).

**T7.5 — `deploy_gateway.sh`**: проверка `--workers 1`, валидация env (`python -c "from
account.config import AccountConfig; AccountConfig.from_env().validate()"`), бэкап
`registry.json`+`account.db` перед рестартом, smoke (`/health`, `/account` 200/302).

---

### P8. QA, e2e и раскатка (2.0 pd)

**T8.1** `python -m pytest tests/ -q` — зелёный (в CI; локально нужен `pip install pytest` в
доступный интерпретатор, сейчас `pytest` нет ни в 3.12, ни в 3.13).

**T8.2** `tests/test_onboarding_live.py` (маркер `live`): поднять шлюз на `127.0.0.1:<port>` с
временным registry, пройти `pair()` без браузера, подтвердить пару через owner-сессию, проверить
`agent.env`, `mesh_status`, ротацию и отзыв. Не требует Google.
В `pytest.ini` добавить `markers = live: требует локального uvicorn и сети на loopback` и
исключение по умолчанию (`-m "not live"` в CI-джобе, `live` — отдельным шагом).
Новые фикстуры — в `tests/conftest.py`: `cabinet_bridge` (фейковый bridge с pair/approve/rotate/revoke),
`gateway_client` (TestClient на временном registry).

**T8.3** Ручная приёмка 30 минут (см. §10) на Ubuntu 24.04 и Windows 11.

**T8.4** Раскатка: сначала P6+P7 на один канирее-шлюз, проверить `curl /install.sh | bash`,
затем включить `MESH_ACCOUNT=1`, затем `MESH_REGISTER_MODE=pair`. Откат: снять `MESH_ACCOUNT`
(кабинет отключается, транспорт живёт), вернуть `MESH_REGISTER_MODE=open`.

---

## 5. Конфигурация

| Переменная | Значения | Назначение |
| :--- | :--- | :--- |
| `MESH_ACCOUNT` | `0/1` | включает кабинет и (по умолчанию) закрывает self-register |
| `MESH_ACCOUNT_BASE_URL` | `https://host` | origin для redirect URI и cookie |
| `MESH_GOOGLE_CLIENT_ID/SECRET` | строки | Web OAuth client |
| `MESH_SESSION_SECRET` | ≥32 символа | HMAC cookie |
| `MESH_TOKEN_KEY` | ≥32 символа | ключ шифрования Drive-токенов |
| `MESH_ACCOUNT_DB` | путь | `account.db` (по умолчанию `<config>/account.db`) |
| `MESH_PAIRING` | `auto|owner|admin|off` | кто подтверждает заявку |
| `MESH_PAIR_TTL` | секунды (600) | срок заявки |
| `MESH_PAIR_MAX_PENDING` | число (200) | предел одновременных заявок |
| `MESH_ALLOW_SELF_REGISTER` | `0/1` | аварийный люк: не закрывать `/api/register` |
| `MESH_REGISTER_MODE` | `open|pair` | как сейчас |
| `MESH_ENROLL_SECRET` | строка | общий секрет установки (без OAuth) |
| `MESH_ADMIN_TOKEN` | строка | админ-подтверждение пары и API |
| `MESH_ACCOUNT_ALLOW_HTTP` | `0/1` | только для локальной разработки |

Правило: при `MESH_ACCOUNT=1` и невалидной конфигурации шлюз **не стартует** (P0/T0.2).

---

## 6. Данные и миграции

### 6.1. `account.db`

```sql
ALTER TABLE auth_flows ADD COLUMN binding_hash TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS pair_audit (
    code_hash   TEXT PRIMARY KEY,
    account_id  TEXT,
    device_id   TEXT,
    action      TEXT NOT NULL,          -- started|approved|denied|expired
    ip_hash     TEXT,
    created_at  TEXT NOT NULL
);

ALTER TABLE devices ADD COLUMN owner_subject TEXT;
ALTER TABLE devices ADD COLUMN source TEXT NOT NULL DEFAULT 'browser'; -- browser|adopted|admin
ALTER TABLE devices ADD COLUMN revoked_at TEXT;
```

Миграции идемпотентные, как сейчас (`PRAGMA table_info` + `ALTER`), без Alembic — сохраняем стиль
`account/db.py`. `revoke_device()` вместо физического удаления.

### 6.2. `registry.json`

Новые ключи записи узла: `owner_subject`, `account_id`, `paired_at`, `pair_source`.
Старые записи читаются как «без владельца» и доступны для `adopt`.
Обратная совместимость: отсутствие ключей не меняет поведение MCP/туннеля.

### 6.3. Бэкап и восстановление

`scripts/backup_gateway.sh` (или шаг в `deploy_gateway.sh`): `registry.json` + `account.db`
(`sqlite3 .backup`, не `cp`), права 0600, retention 14 дней. Восстановление описано в
`docs/SELF_HOSTED_GATEWAY_RU.md`.

---

## 7. API (итоговая таблица)

| Метод | Путь | Auth | Назначение |
| :--- | :--- | :--- | :--- |
| GET | `/pair?code=` | — | 303 на `/account/pair?code=` |
| POST | `/v1/devices/pair` | — (rate limit) | заявка + `device_secret` + `pair_url` |
| GET | `/v1/devices/pair/{code}` | `X-Pair-Secret` | статус/выдача ключей (идемпотентно) |
| POST | `/v1/devices/pair/{code}/approve` | owner-сессия или `X-Admin-Token` | подтверждение, запись registry+DB |
| GET | `/account` | cookie | кабинет |
| GET | `/account/pair?code=` | cookie (опц.) | страница подтверждения |
| POST | `/account/pair/{code}/approve` | cookie + CSRF + Origin | подтверждение из браузера |
| POST | `/account/devices/{id}/keys/{check|rotate|revoke}` | cookie + CSRF + Origin | действия по ключам |
| POST | `/account/devices/{id}/control` | cookie + CSRF + Origin | режим/папка/sandbox/пауза (есть) |
| POST | `/account/devices/claim` | cookie + CSRF + Origin | adopt узла по link key |
| GET | `/account/auth/google/start|callback` | — | вход (есть) |
| POST/GET | `/account/logout` | cookie + CSRF | выход |
| GET | `/install.sh`, `/install.ps1`, `/core/**`, `/ops/**`, `/assets/**` | — | публикация (белый список) |
| GET | `/health` | — | + `onboarding` (аддитивно) |

Ошибки: `400` неверный код/state, `401` нет сессии, `403` CSRF/Origin/секрет, `404` чужая заявка,
`409` уже подтверждена, `410` истекла/отозвана, `429` лимит, `503` кабинет не настроен.

---

## 8. Безопасность: угрозы и меры

| Угроза | Мера | Этап |
| :--- | :--- | :--- |
| Кража короткого кода пары | `device_secret` + `X-Pair-Secret`, TTL 10 мин, лимит заявок | P2 |
| Подбор кода | 32-буквенный алфавит, 10 мин, rate limit, 404 без деталей | P2 |
| Login-CSRF / фиксация сессии | binding-cookie + hash в flow | P1 |
| Чужой `Origin` на POST | сравнение с `MESH_ACCOUNT_BASE_URL` | P1 |
| Brute force входа | 20/мин/IP, 429 | P1 |
| Подделка ID token | PyJWT + JWKS + `azp`/`aud`/`nonce`/`email_verified`, fail-closed | P1 |
| Утечка `client_secret` через редирект | `NoRedirect` opener | P1 |
| XSS в кабинете | экранирование всего, CSP nonce, без inline-скриптов | P1/P3 |
| Кликджекинг | `frame-ancestors 'none'`, `X-Frame-Options: DENY` | есть/P1 |
| Кэширование приватных страниц | `Cache-Control: no-store` + `Referrer-Policy` | есть/P1 |
| Утечка в логи | фильтр `code|state|token|secret` | P7 |
| Анонимная регистрация при включённом аккаунте | `enrolment_refusal` + `MESH_ALLOW_SELF_REGISTER=0` | P2 |
| Чужое устройство в кабинете | `tenant_id` в запросе + 404 вместо 403 | P4 |
| Отзыв не действует сразу | `revoke` закрывает туннель и удаляет запись | P4 |
| Подмена файла при публикации | белый список, запрет `..`/симлинков, лимит размера | P6 |
| Рестарт шлюза рвёт привязку | явно в docs + понятная ошибка 404 | P7 |

---

## 9. Тесты

| Файл | Что проверяет |
| :--- | :--- |
| `tests/test_account_config.py` | `validate()`: нет секрета, http, base с путём, короткий secret, валидный конфиг |
| `tests/test_account_auth.py` *(расширить)* | binding-cookie, чужой браузер, `azp`, `aud`, `nonce`, просроченный, `email_verified=false`, минимальные скоупы, NoRedirect |
| `tests/test_account_security.py` | Origin, rate limit входа/approve, CSP nonce, no-store |
| `tests/test_pairing.py` | start/status/approve: секрет, идемпотентность, ротация во время ожидания, отзыв 410, TTL, лимиты, атомарность registry+DB |
| `tests/test_pair_route.py` | `/pair` → 303, мусорный код → 400, без кабинета → 503 |
| `tests/test_pair_page.py` | 5 состояний страницы, экранирование, CSRF, 409/404, нет автоподтверждения |
| `tests/test_account_devices.py` | список только своих, check/rotate/revoke, confirm, чужое 404, ссылка и копирование |
| `tests/test_account_control.py` *(не ломать)* | существующее удалённое управление |
| `tests/test_register_gating.py` | open/pair/owner/`MESH_ALLOW_SELF_REGISTER` |
| `tests/test_onboarding.py` | pair reuse, pending, receipt, чужой gateway, verify retry, `CONIN$` fallback |
| `tests/test_onboarding_live.py` (`live`) | сквозной сценарий на локальном uvicorn без Google |
| `tests/test_installer_pairing.py` | `--pair`/`-Pair`, вызов onboarding, отсутствие секретов в argv |
| `tests/test_published_assets.py` | домен в установщиках, белый список, `..`, симлинки, HEAD, BOM |
| `tests/test_log_redaction.py` | фильтр логов |
| `tests/test_mcp_endpoints.py` *(дополнить)* | `/health.onboarding`, старые ключи на месте |

Требование: каждый новый модуль — минимум один тест на happy-path и один на отказ; на каждую
строку таблицы §8 — по тесту, где это возможно без сети.

---

## 10. Приёмка

### 10.1. Definition of Done (по этапу)

- [ ] код в `main` (или feature-ветке) с тестами, `pytest tests/ -q` зелёный;
- [ ] `docs/PAIRING.md`/`docs/SELF_HOSTED_GATEWAY_RU.md` обновлены;
- [ ] нет новых секретов в git (`git secrets`/ревью diff);
- [ ] проверен откат фича-флагом;
- [ ] ссылка на прогон тестов в описании PR (протокол доказательств).

### 10.2. Ручной сценарий (30 минут)

1. `curl -fsSL https://<gw>/install.sh | bash` → появляется ссылка `/pair?code=`.
2. Открыть ссылку **на другом устройстве** → Google-вход → «Подключить устройство».
3. Установщик печатает `Device approved: <name>`, сохраняет `agent.env`, печатает Server URL.
4. В `/account` устройство видно: онлайн, ОС, дата; ссылка копируется.
5. «Проверить связь» → «Связь подтверждена» (реальный `mesh_status`).
6. «Заменить ссылку» → старый URL даёт 401, новый работает; кабинет показывает новый.
7. «Отозвать доступ» → узел офлайн, старые ключи не работают, устройство исчезло.
8. Повторный `install.sh` на той же машине → «конфигурация переиспользована», второго устройства нет.
9. Чужой аккаунт: устройство не видно, прямой URL `/account/devices/<id>` → 404, approve чужой пары → 409.
10. `MESH_ACCOUNT=0` → кабинет показывает причину, `/sse` и туннель работают.
11. Проверить логи: нет `code=`, `token=`, `secret`.

---

## 11. Риски

| Риск | Вероятность | Влияние | Смягчение |
| :--- | :--- | :--- | :--- |
| Google-приложение в Testing / нет верификации | высокая | среднее | Test users на старте; публикация брендинга заранее |
| `redirect_uri_mismatch` из-за домена/слэша | средняя | высокое | `validate()` + тест на точное совпадение; документировать |
| Включили `MESH_ACCOUNT` до установщиков → установка ломается | средняя | высокое | порядок P5→P2.6, флаг `MESH_ALLOW_SELF_REGISTER` |
| Потеря `pending_pairs` при рестарте | высокая (по природе) | низкое | понятная ошибка 404 + инструкция «перезапустите установщик» |
| Второй воркер uvicorn | средняя | высокое | проверка в deploy + docs |
| Утечка `link_key` через историю браузера | средняя | среднее | предупреждение в UI, ротация одним кликом, `no-store` |
| Потеря `account.db` = потеря привязок | низкая | высокое | бэкап в deploy, `sqlite3 .backup` |
| Смешение старого `VERSION` 0.4.8 и 0.5.2 | есть | низкое | отдельная задача: единый источник версии (`core/version.py`) |
| Долгая поддержка двух путей (link-key и OAuth) | высокая | среднее | матрица совместимости в docs, одинаковые действия в UI |

---

## 12. Откат

| Что сломали | Откат |
| :--- | :--- |
| OAuth/кабинет | `MESH_ACCOUNT=0` + рестарт; транспорт и туннели не затрагиваются |
| Гейтинг регистрации | `MESH_ALLOW_SELF_REGISTER=1` (без рестарта не применится — планировать окно) |
| Привязка | вернуть `MESH_REGISTER_MODE=open`, установщики `--no-pair` |
| Публикация файлов | убрать маршруты `/install.*` (флаг `MESH_PUBLISH_INSTALLERS=0`) |
| Код целиком | `git revert` коммита этапа; `registry.json`/`account.db` не мигрируют необратимо (только аддитивные колонки) |

---

## Приложение A. Последовательность привязки

```
install.sh                     gateway                        браузер                account.db
    │  POST /v1/devices/pair      │                              │                       │
    ├────────────────────────────►│ pending_pairs[code] = {secret_sha256,…}
    │◄── {code, device_secret, pair_url}                          │                       │
    │  открыть /pair?code=…       │                              │                       │
    ├────────────────────────────────────────────────────────────►│                       │
    │                             │  303 /account/pair?code=…     │                       │
    │                             │◄─────────────────────────────┤                       │
    │                             │  GET (нет сессии) → login     │                       │
    │                             │◄─────────────────────────────┤                       │
    │                             │  Google callback → session    │                       │
    │                             │◄─────────────────────────────┤                       │
    │                             │  POST /account/pair/{code}/approve                    │
    │                             │◄─────────────────────────────┤                       │
    │                             │ approve_pair(): registry[owner_subject] ──────────────►│ devices
    │  GET /v1/devices/pair/{code} + X-Pair-Secret               │                       │
    ├────────────────────────────►│ проверка sha256 → ключи (идемпотентно)                │
    │◄── {username, token, link_key, sse_url}                    │                       │
    │  agent.env, служба, mesh_status                            │                       │
```

## Приложение B. Cookie и сессии

| Cookie | Назначение | Атрибуты |
| :--- | :--- | :--- |
| `__Host-rhm_session` | сессия кабинета | Secure, HttpOnly, SameSite=Lax, Path=/, подпись HMAC, id → SQLite |
| `__Host-rhm-login` | привязка OAuth-потока к браузеру | TTL 600 с, удаляется в callback |

## Приложение C. Чек-лист Google Cloud

- [ ] OAuth client типа **Web application**;
- [ ] Authorized redirect URI: `https://<домен>/account/auth/google/callback` (побайтово);
- [ ] Scopes: `openid`, `.../userinfo.email` (и `.../userinfo.profile`, если нужно имя);
- [ ] для Drive — отдельное согласие `.../auth/drive.file` в `/account/drive/connect`;
- [ ] приложение опубликовано или тестовые пользователи добавлены;
- [ ] секрет — только в `gateway.env` (0600), не в git и не в чате.

## Приложение D. Порядок деплоя

1. `pip install -r requirements-gateway.txt` в venv шлюза.
2. Скопировать `gateway.py`, `account/`, `core/onboarding.py`, `core/platforms/`, `ops/systemd/*`.
3. `install -m 600 ops/systemd/gateway.env.example /etc/remote-harness-mcp/gateway.env`, заполнить.
4. Бэкап `registry.json` и `account.db`.
5. `systemctl daemon-reload && systemctl restart rhm-gateway`.
6. `curl -fsS https://<gw>/health` → `onboarding.pairing=true`.
7. `curl -fsS https://<gw>/install.sh | head` → домен подставлен.
8. Пройти ручной сценарий §10.2 на тестовой машине.
9. Включить `MESH_REGISTER_MODE=pair` только после п.8.
