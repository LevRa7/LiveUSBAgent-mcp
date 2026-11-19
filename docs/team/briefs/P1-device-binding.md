# P1 — привязка устройств и единый список (бриф для исполнителей)

**Ветка:** `feat/p1-device-binding`
**Основа:** `docs/PLAN_oauth_panel_main.md`, §3.1
**Цель P1:** устройство, добавленное на шлюз, автоматически появляется в кабинете у своего
владельца; кабинет показывает единый список (registry + account.db); владелец может привязать
устройство, подтвердив код привязки в браузере, без ручного копирования link-key.

## Инварианты

1. `registry.json` — истина по учётным данным и владельцу (`owner_subject`, `account_id`).
   `account.db` — истина по аккаунту, тенанту и отображаемому имени.
2. Никакая операция кабинета не выполняется без сессии владельца, CSRF и проверки `Origin`.
3. Чужое устройство отвечает `404`, а не `403` (не подтверждаем существование).
4. Ключи (`link_key`, device token) не рендерятся в HTML кабинета.
5. Доказательство владения при ручной привязке — только `link_key`/device token
   (`token_matches_link`/`token_matches_device`). Имя узла доказательством не является.
6. Существующие тесты и строки интерфейса не ломать; файлы править через `edit`/`write`
   (UTF-8), не перезаписывать PowerShell-ом.

## Задачи

### P1-A. Мост и единый список

- `gateway.py`, класс `CabinetNodeControl`:
  - `devices() -> dict[str, dict]`: снимок registry для кабинета: `hostname`, `os`, `arch`,
    `owner_subject`, `account_id`, `last_seen`, `online` (без ключей!);
  - `claim(node, credential, owner_subject="", account_id="")`: после проверки credential
    записывает `owner_subject`/`account_id` в registry (атомарно, `save_registry`);
  - ничего не импортировать из `account/` (циклический импорт запрещён).
- `account/app.py`:
  - `nodes_for(account)` возвращает объединение: строки `store.list_devices(tenant)` +
    registry-записи с `owner_subject == account["subject"]` (для них лениво создаётся строка
    в `store.claim_device`), плюс признак `missing` для строк без registry-записи;
  - главная страница показывает: имя, hostname, ОС, онлайн, режим, дату привязки, источник
    (`browser`/`adopted`), «нет связи с реестром» для missing.
- Тесты: `tests/test_account_control.py` + `tests/test_account_mount.py` (fake bridge).

### P1-B. Подтверждение пары из кабинета

- `gateway.py`: вынести общую функцию `approve_pending_pair(code, owner_subject, account_id)`,
  которую используют и админ-API, и кабинет; мост получает `pair(code)` (метаданные без ключей)
  и `approve_pair(code, owner_subject, account_id)`.
- `account/app.py`: `GET /account/pair?code=` (страница: устройство, ОС, шаги, кнопка
  «Подключить»), `POST /account/pair/{code}/approve` (CSRF/Origin/сессия).
- Тесты: подтверждение создаёт registry-запись с владельцем и строку в `account.db`; чужой
  код/повтор/истечение → 404/409; без сессии → редирект/403.

### P1-C. Непривязанные устройства и документация

- `account/app.py`: секция «Непривязанные» только при `MESH_CABINET_SHOW_UNCLAIMED=1`
  (по умолчанию выключено), имена без ключей; привязка — существующей формой по `link_key`.
- `docs/PLAN_oauth_panel_main.md`: отметить P1 как выполненный, обновить §3.1 и §4.
- Финальный прогон: `python -m pytest tests -q` (должно быть зелёным).

## Команды для исполнителя

```powershell
cd D:\MyProjects\Antigravity-mash
$env:PYTHONPATH="D:\MyProjects\Antigravity-mash\.win-test-deps\py3.13"
& "C:\Users\Lev\AppData\Local\Programs\Python\Python313\python.exe" -m pytest tests/test_account_control.py tests/test_account_mount.py -q
```

## Критерии приёмки P1

- [ ] `POST /account/devices/claim` с валидным `link_key` проставляет `owner_subject` в registry.
- [ ] Устройство, у которого `owner_subject` уже стоит, видно в кабинете без ручной привязки.
- [ ] `GET /account/pair?code=…` + `POST …/approve` привязывают устройство к вошедшему владельцу.
- [ ] Второй аккаунт не видит и не может привязать чужое устройство.
- [ ] `pytest tests -q` зелёный; ключи не попадают в HTML кабинета.
