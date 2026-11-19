"""The account cabinet app (mounted at ``/account`` by gateway.py).

Besides the login, the cabinet is where a logged-in person controls the node
they own: the remote-control lock, the access mode, the working directory, the
isolated container and pause. Every button posts to this app, which forwards one
``node_control`` call over the gateway tunnel - the same tool a model calls, with
the same validation on the node (``core/settings.py``). The cabinet never writes
a node's settings itself.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from . import auth, drive as drive_api, sessions
from .config import AccountConfig
from .db import Store

STYLE = """
:root{color-scheme:light dark}
body{font:16px/1.5 system-ui,Segoe UI,Arial,sans-serif;max-width:820px;margin:40px auto;padding:0 20px}
.card{border:1px solid #8884;border-radius:14px;padding:22px;margin:0 0 18px}
.card h1{font-size:24px;margin:0 0 4px}
.card h2{font-size:18px;margin:0 0 10px}
h1{font-size:26px;margin:0 0 4px}
.muted{color:#777}
.btn{display:inline-block;background:#2563eb;color:#fff;padding:9px 15px;border-radius:9px;text-decoration:none;border:0;cursor:pointer;font:inherit}
.btn.secondary{background:#666}
.btn.danger{background:#b91c1c}
.btn:disabled{background:#999;cursor:not-allowed}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:8px 0}
code{background:#8881;padding:2px 6px;border-radius:6px}
table{width:100%;border-collapse:collapse;margin-top:16px}
td,th{text-align:left;padding:8px;border-bottom:1px solid #8883}
.notice{border:1px solid #16a34a;background:#16a34a22;padding:10px 14px;border-radius:10px;margin:0 0 16px}
.error{border:1px solid #dc2626;background:#dc262622;padding:10px 14px;border-radius:10px;margin:0 0 16px}
.warn{border:1px solid #d97706;background:#d9770622;padding:10px 14px;border-radius:10px}
input[type=text],input[type=password]{padding:8px 10px;border:1px solid #8886;border-radius:8px;font:inherit;min-width:280px}
.pill{display:inline-block;border-radius:999px;padding:1px 10px;font-size:13px;background:#8882}
.pill.on{background:#16a34a33}
.pill.off{background:#dc262633}
.modal{position:fixed;inset:0;background:#0007;display:flex;align-items:center;justify-content:center;padding:20px;z-index:10}
.modalbox{background:Canvas;max-width:640px;width:100%;box-shadow:0 18px 60px #0006;border-radius:14px;padding:8px 20px 20px}
.modalbox .card{border:0}
"""

CHANNEL_UNAVAILABLE = (
    "Шлюз не подключён к этому узлу: удалённое управление сейчас недоступно. "
    "Выполните действия на самой машине через <code>rhm</code>."
)

CLAIM_ERRORS = {
    "missing": "Укажите имя узла и его ключ.",
    "unknown_node": "Узел с таким именем не зарегистрирован на этом шлюзе.",
    "bad_credential": "Ключ не подходит к этому узлу.",
    "registry_unavailable": "Реестр шлюза недоступен; привязка временно запрещена.",
    "already_linked": "Этот узел уже привязан к другому аккаунту.",
    "rate_limited": "Слишком много попыток привязки. Подождите несколько минут.",
}

PAIR_ERRORS = {
    "unknown_pair": "Код привязки не найден или истёк.",
    "already_approved": "Этот код уже подтверждён.",
}


def _headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    return response


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    document = (
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>%s</title><style>%s</style></head><body>%s</body></html>"
        % (html.escape(title), STYLE, body)
    )
    return _headers(HTMLResponse(document, status_code=status))


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def create_app(config: Optional[AccountConfig] = None,
               node_control: Any = None) -> Starlette:
    cfg = config or AccountConfig.from_env()
    store = Store(cfg.db_path) if cfg.configured else None
    #: Per-tenant claim attempts: a code or credential is guessable only if the
    #: caller may try forever, so the cabinet slows the obvious attack down.
    claim_guard: Dict[str, List[float]] = {}
    #: Per-IP windows for the OAuth start/callback. Without them a login loop
    #: fills the auth_flows table and turns /account into a CPU burner.
    login_hits: Dict[str, List[float]] = {}

    def login_rate_limited(request: Request, bucket: str, limit: int = 20,
                           window: float = 60.0) -> bool:
        key = "%s:%s" % (bucket, request.client.host if request.client else "unknown")
        now = time.time()
        if len(login_hits) > 4096:
            for stale in [k for k, stamps in login_hits.items()
                          if not stamps or now - stamps[-1] > 300]:
                login_hits.pop(stale, None)
        seen = [stamp for stamp in login_hits.get(key, []) if now - stamp < window]
        if len(seen) >= limit:
            login_hits[key] = seen
            return True
        seen.append(now)
        login_hits[key] = seen
        return False

    def owner_allowed(claims: Dict[str, Any]) -> bool:
        """Optional allowlist: only these Google accounts may sign in.

        Empty MESH_OWNER_EMAILS/MESH_OWNER_DOMAINS keeps the old behaviour
        (any verified Google account), which is fine for a personal gateway but
        not for a shared one.
        """
        emails = {item.strip().lower() for item in
                  os.environ.get("MESH_OWNER_EMAILS", "").split(",") if item.strip()}
        domains = {item.strip().lower() for item in
                   os.environ.get("MESH_OWNER_DOMAINS", "").split(",") if item.strip()}
        if not emails and not domains:
            return True
        email = str(claims.get("email") or "").lower()
        if not email:
            return False
        return email in emails or any(email.endswith("@" + domain) for domain in domains)

    def current(request: Request) -> Optional[Dict[str, Any]]:
        if store is None or not cfg.configured:
            return None
        sid = sessions.session_id(request, cfg.session_secret, cfg.base_url)
        return store.session(sid) if sid else None

    async def require(request: Request) -> Optional[Dict[str, Any]]:
        return current(request)

    def gateway_ready() -> bool:
        return node_control is not None and bool(getattr(node_control, "available", lambda: False)())

    def online(node: str) -> bool:
        try:
            return bool(node_control.online(node))
        except Exception:
            return False

    def owner_subject(account: Dict[str, Any]) -> str:
        """The registry-side owner id: the Google subject, namespaced.

        ``registry.json`` stores a provider-prefixed subject (``google:<sub>``)
        rather than a bare sub, so a second identity provider can be added
        without rewriting existing ownership.
        """
        sub = str(account.get("google_sub") or "").strip()
        return ("google:" + sub) if sub else str(account.get("owner_subject") or "")

    def registry_devices() -> Dict[str, Dict[str, Any]]:
        """The bridge's registry snapshot; an absent or old bridge means none."""
        if node_control is None:
            return {}
        reader = getattr(node_control, "devices", None)
        if not callable(reader):
            return {}
        try:
            snapshot = reader()
        except Exception:                         # a broken registry is not a 500
            return {}
        if not isinstance(snapshot, dict):
            return {}
        return {str(name): dict(entry) for name, entry in snapshot.items()
                if isinstance(entry, dict)}

    def pair_code_from(request: Request) -> str:
        """The pairing code from the query or the path, reduced to its alphabet.

        Codes are generated as ``XXXX-XXXX`` from an unambiguous alphabet. Only
        those characters survive here, which keeps the value safe to echo into a
        redirect and an HTML form even when the URL was typed by hand.
        """
        raw = str(request.query_params.get("code")
                  or request.path_params.get("code") or "")
        return "".join(ch for ch in raw.strip().upper()
                       if ch.isalnum() or ch == "-")[:32]

    def pair_metadata(code: str) -> Optional[Dict[str, Any]]:
        """The bridge's metadata for a pending code; no key ever passes through.

        An absent or failing bridge means "unknown code", which the page answers
        with a 404 - a bridge exception must not become a 500 on a public page.
        """
        if not code or node_control is None:
            return None
        reader = getattr(node_control, "pair", None)
        if not callable(reader):
            return None
        try:
            metadata = reader(code)
        except Exception:                     # a broken bridge is an unknown code
            return None
        return dict(metadata) if isinstance(metadata, dict) else None

    def pair_problem_page(status: int, heading: str, hint: str) -> HTMLResponse:
        return page(
            heading,
            "<div class=card><h1>%s</h1><p class=muted>%s</p>"
            "<p><a class=btn href=\"/account\">В кабинет</a></p></div>"
            % (_esc(heading), _esc(hint)),
            status,
        )

    def merged_device(row: Dict[str, Any], entry: Dict[str, Any], node: str,
                      source: str) -> Dict[str, Any]:
        """One list row: the account.db record plus the registry's metadata.

        The account database knows the tenant, the display name and the claim
        date; the registry knows the hostname, the OS and the tunnel state. A row
        without a registry entry is kept and flagged (``missing``) instead of
        disappearing, so a wiped registry does not silently hide a device.
        """
        item = dict(row)
        item["hostname"] = str(entry.get("hostname") or "")
        item["os"] = str(entry.get("os") or "")
        item["arch"] = str(entry.get("arch") or "")
        item["last_seen"] = str(entry.get("last_seen") or "")
        item["online"] = bool(entry.get("online")) if entry else online(node)
        item["source"] = source
        item["missing"] = not entry
        return item

    def nodes_for(account: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The one device list: account.db rows plus the registry's owned nodes.

        ``registry.json`` is the truth about ownership and credentials,
        ``account.db`` about the tenant and the display name. Merging by
        ``node_name`` makes a node that already carries ``owner_subject`` appear
        in its owner's cabinet without a manual link-key dance; the first visit
        creates the account.db row lazily (``adopted``), because every later
        page and control route addresses devices by their database id.
        """
        if store is None:
            return []
        tenant = account["tenant_id"]
        snapshot = registry_devices()
        subject = owner_subject(account)
        owner_id = str(account.get("id") or "")
        merged: List[Dict[str, Any]] = []
        seen = set()
        for row in store.list_devices(tenant):
            node = str(row.get("node_name") or "")
            seen.add(node)
            merged.append(merged_device(row, snapshot.get(node) or {}, node, "browser"))
        for node in sorted(snapshot):
            if node in seen:
                continue
            entry = snapshot[node]
            owned = ((subject and entry.get("owner_subject") == subject)
                     or (owner_id and entry.get("account_id") == owner_id))
            if not owned:
                continue
            hostname = str(entry.get("hostname") or "") or node
            try:
                row = store.claim_device(tenant, node, display_name=hostname)
            except Exception:                     # a concurrent first visit already won
                row = None
            if row is None:                       # already linked to another tenant
                continue
            merged.append(merged_device(row, entry, node, "adopted"))
        return merged

    def show_unclaimed() -> bool:
        """Whether the migration list of ownerless nodes is shown at all.

        Off by default: a shared gateway must not advertise machines that belong
        to somebody else, and the list only helps while existing devices are
        being moved into accounts.
        """
        return _is_true(os.environ.get("MESH_CABINET_SHOW_UNCLAIMED", ""))

    def unclaimed_devices(limit: int = 50) -> List[Dict[str, Any]]:
        """Registry nodes with no owner and no account: candidates for a claim.

        A node counts as unclaimed only when both ``owner_subject`` and
        ``account_id`` are empty - a half-written entry still belongs to
        somebody, and showing it would leak the existence of a foreign device.
        Keys never reach this list: the bridge's snapshot carries metadata only.
        """
        snapshot = registry_devices()
        found: List[Dict[str, Any]] = []
        for node in sorted(snapshot):
            entry = snapshot[node]
            if str(entry.get("owner_subject") or "").strip():
                continue
            if str(entry.get("account_id") or "").strip():
                continue
            found.append({
                "node": node,
                "hostname": str(entry.get("hostname") or ""),
                "os": str(entry.get("os") or ""),
                "arch": str(entry.get("arch") or ""),
                "last_seen": str(entry.get("last_seen") or ""),
            })
            if len(found) >= limit:
                break
        return found

    def unclaimed_card(csrf: str) -> str:
        """The opt-in section: names and metadata, the proof is still a key."""
        if not show_unclaimed():
            return ""
        items = unclaimed_devices()
        if not items:
            return ("<div class=card><h2>Непривязанные устройства</h2>"
                    "<p class=muted>Непривязанных устройств нет.</p></div>")
        rows = []
        for item in items:
            os_arch = " ".join(part for part in (item.get("os"), item.get("arch"))
                               if part) or "—"
            rows.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (_esc(item.get("node")), _esc(item.get("hostname") or "—"),
                   _esc(os_arch), _esc(item.get("last_seen") or "—"))
            )
        # One form per node: the name is fixed (the key is the proof, not the
        # name), so a single shared form would leave all but one row unusable.
        forms = []
        for item in items:
            forms.append(
                "<form method=post action=\"/account/devices/claim\">"
                "<input type=hidden name=csrf value=\"%s\">"
                "<div class=row><input type=text name=node value=\"%s\" readonly>"
                "<input type=password name=credential "
                "placeholder=\"lk_... или токен устройства\">"
                "<button class=btn type=submit>Привязать</button></div></form>"
                % (_esc(csrf), _esc(item.get("node")))
            )
        return (
            "<div class=card><h2>Непривязанные устройства</h2>"
            "<p class=muted>Эти узлы зарегистрированы на шлюзе, но ещё не принадлежат "
            "аккаунту. Имя узла уже подставлено; ключ показывает сама машина "
            "(<code>rhm --once</code>).</p>"
            "<table><tr><th>Узел</th><th>Hostname</th><th>ОС</th>"
            "<th>Последняя активность</th></tr>%s</table>%s</div>"
            % ("".join(rows), "".join(forms))
        )

    async def snapshot(node: str) -> Dict[str, Any]:
        """One ``node_control`` status call; never raises."""
        if not gateway_ready():
            return {"error": CHANNEL_UNAVAILABLE}
        try:
            result = await node_control.call(node, {"action": "status"})
        except Exception as exc:                      # a bridge failure is a page, not a 500
            return {"error": "%s: %s" % (type(exc).__name__, exc)}
        return result if isinstance(result, dict) else {"error": "invalid response"}

    async def control(node: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not gateway_ready():
            return {"error": CHANNEL_UNAVAILABLE}
        try:
            result = await node_control.call(node, payload)
        except Exception as exc:
            return {"error": "%s: %s" % (type(exc).__name__, exc)}
        return result if isinstance(result, dict) else {"error": "invalid response"}

    def back(path: str, result: Optional[Dict[str, Any]] = None) -> RedirectResponse:
        """Redirect with one line of feedback; secrets and paths stay out of it."""
        notice = ""
        error = ""
        if result:
            if result.get("error"):
                error = str(result["error"])[:300]
            else:
                notice = str(result.get("message") or "Готово.")[:300]
        target = path + "?" + "&".join(
            part for part in (
                "notice=" + quote(notice) if notice else "",
                "error=" + quote(error) if error else "",
            ) if part
        )
        return _headers(RedirectResponse(target, status_code=303))

    def feedback(request: Request) -> str:
        notice = request.query_params.get("notice") or ""
        error = request.query_params.get("error") or ""
        block = ""
        if notice:
            block += "<p class=notice>%s</p>" % _esc(notice)
        if error:
            block += "<p class=error>%s</p>" % _esc(error)
        return block

    def csrf_ok(request: Request, account: Dict[str, Any], form: Any) -> bool:
        supplied = str(form.get("csrf") or "")
        if not supplied or supplied != str(account.get("csrf") or ""):
            return False
        # A browser always sends Origin on a cross-site POST; a mismatch means
        # the form was submitted from another site even if the token leaked.
        origin = request.headers.get("origin")
        return origin is None or origin == cfg.base_url.rstrip("/")

    # -- rendering ---------------------------------------------------------

    def lock_controls(device: Dict[str, Any], state: Dict[str, Any],
                      csrf: str, compact: bool = False) -> str:
        """The remote-control switch, plus a warning once it is off."""
        node = str(device.get("node_name") or "")
        lock = state.get("remote_control") or {}
        enabled = lock.get("enabled")
        if enabled is None:
            return "<p class=muted>Состояние переключателя неизвестно: узел не отвечает.</p>"
        badge = ("<span class=\"pill on\">включено</span>" if enabled
                 else "<span class=\"pill off\">выключено</span>")
        head = "<div class=row><strong>Удалённое управление доступом к этому MCP</strong> %s</div>" % badge
        if enabled:
            return head + (
                "<p class=muted>Кабинет может включать read-only, менять рабочую папку, "
                "запускать изолированную среду и ставить узел на паузу. Модель может "
                "только ужесточать режим, но не снимать read-only.</p>"
                "<form method=post action=\"/account/devices/%s/control\">"
                "<input type=hidden name=csrf value=\"%s\">"
                "<input type=hidden name=action value=remote_control>"
                "<input type=hidden name=enabled value=false>"
                "<button class=\"btn danger\" type=submit>Выключить удалённое управление</button>"
                "</form>"
                "<p class=muted>После выключения включить его обратно можно будет только "
                "на самой машине: <code>rhm remote-control on</code>.</p>"
                % (_esc(device.get("id")), _esc(csrf))
            )
        return head + (
            "<p class=warn>Удалённое управление выключено. Включить его снова можно "
            "только на самой машине: <code>rhm remote-control on</code> "
            "(или клавишей <code>r</code> в консольном меню <code>rhm</code>).</p>"
        )

    def device_settings(device: Dict[str, Any], state: Dict[str, Any], csrf: str) -> str:
        node = _esc(device.get("node_name"))
        device_id = _esc(device.get("id"))
        lock = (state.get("remote_control") or {}).get("enabled")
        if lock is False:
            return "<p class=warn>Настройки недоступны: удалённое управление выключено " \
                   "на этом узле.</p>"

        mode = state.get("access_mode") or "?"
        buttons = []
        for value, label in (("read_only", "Read-only"),
                             ("approval", "С подтверждением"),
                             ("full", "Полный доступ")):
            buttons.append(
                "<button class=\"btn%s\" type=submit name=mode value=\"%s\">%s</button>"
                % ("" if value != mode else " secondary", value, label)
            )
        mode_form = (
            "<form method=post action=\"/account/devices/%s/control\">"
            "<input type=hidden name=csrf value=\"%s\">"
            "<input type=hidden name=action value=mode>"
            "<div class=row>%s</div></form>"
            % (device_id, _esc(csrf), "".join(buttons))
        )

        workspace = state.get("workspace") or ""
        cd_form = (
            "<form method=post action=\"/account/devices/%s/control\">"
            "<input type=hidden name=csrf value=\"%s\">"
            "<input type=hidden name=action value=cd>"
            "<div class=row><input type=text name=path value=\"%s\" placeholder=\"%s\">"
            "<button class=btn type=submit>Сменить папку</button></div></form>"
            % (device_id, _esc(csrf), _esc(workspace), "D:\\\\work")
        )

        isolation = state.get("sandbox") or {}
        if isolation.get("enabled"):
            isolation_form = (
                "<form method=post action=\"/account/devices/%s/control\">"
                "<input type=hidden name=csrf value=\"%s\">"
                "<input type=hidden name=action value=sandbox>"
                "<input type=hidden name=enabled value=false>"
                "<button class=\"btn secondary\" type=submit>Выключить изоляцию</button>"
                "</form><p class=muted>Контейнер: <code>%s</code> · <code>%s</code></p>"
                % (device_id, _esc(csrf), _esc(isolation.get("container")),
                   _esc(isolation.get("workspace")))
            )
        else:
            isolation_form = (
                "<form method=post action=\"/account/devices/%s/control\">"
                "<input type=hidden name=csrf value=\"%s\">"
                "<input type=hidden name=action value=sandbox>"
                "<input type=hidden name=enabled value=true>"
                "<div class=row><input type=text name=workspace value=\"%s\" "
                "placeholder=\"Папка проекта\"></div>"
                "<div class=row><label><input type=checkbox name=network value=true%s> "
                "разрешить сеть внутри контейнера</label></div>"
                "<div class=row><button class=btn type=submit>"
                "Запустить изолированную среду</button></div></form>"
                "<p class=muted>Docker/Podman, в контейнер монтируется только папка проекта "
                "(<code>/workspace</code>), без доступа к остальным путям хоста. Сеть по "
                "умолчанию выключена; она нужна только для установки пакетов внутри "
                "контейнера.</p>"
                % (device_id, _esc(csrf), _esc(workspace),
                   " checked" if isolation.get("network") else "")
            )

        paused = bool(state.get("paused"))
        pause_action = "resume" if paused else "pause"
        pause_label = "Снять паузу" if paused else "Поставить на паузу"
        pause_form = (
            "<form method=post action=\"/account/devices/%s/control\">"
            "<input type=hidden name=csrf value=\"%s\">"
            "<input type=hidden name=action value=%s>"
            "<button class=\"btn secondary\" type=submit>%s</button></form>"
            % (device_id, _esc(csrf), pause_action, pause_label)
        )

        return (
            "<p>Текущий режим: <strong>%s</strong>%s</p>"
            "<h2>Режим доступа</h2>%s"
            "<h2>Рабочая папка</h2>%s"
            "<h2>Изолированная среда</h2>%s"
            "<h2>Активность</h2>%s"
            % (_esc(mode), " · на паузе" if paused else "", mode_form, cd_form,
               isolation_form, pause_form)
        )

    # -- routes ------------------------------------------------------------

    async def root(request: Request) -> Response:
        if not cfg.configured:
            return page(
                "Remote Harness MCP",
                "<div class=card><h1>Кабинет не настроен</h1>"
                "<p class=muted>Задайте <code>MESH_ACCOUNT=1</code>, "
                "<code>MESH_GOOGLE_CLIENT_ID</code>, <code>MESH_GOOGLE_CLIENT_SECRET</code>, "
                "<code>MESH_SESSION_SECRET</code> и перезапустите шлюз. MCP-транспорт работает.</p></div>",
                status=200,
            )
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account", status_code=303)
        devices = nodes_for(account)
        csrf = str(account.get("csrf") or "")

        rows = []
        for device in devices:
            node = str(device.get("node_name") or "")
            state = await snapshot(node)
            label = str(device.get("display_name") or node)
            hostname = str(device.get("hostname") or "") or "—"
            os_arch = " ".join(str(part) for part in
                               (device.get("os"), device.get("arch")) if part) or "—"
            claimed = str(device.get("claimed_at") or "")[:10] or "—"
            lock = (state.get("remote_control") or {}).get("enabled")
            lock_label = ("—" if lock is None else ("включено" if lock else "выключено"))
            # The registry is the truth about ownership; a row whose entry is
            # gone cannot be reached or controlled, and the page must say so.
            missing_note = (" <span class=pill off>нет связи с реестром</span>"
                            if device.get("missing") else "")
            rows.append(
                "<tr><td><a href=\"/account/devices/%s\">%s</a>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td></tr>"
                % (_esc(device.get("id")), _esc(label), missing_note,
                   _esc(hostname), _esc(os_arch),
                   "онлайн" if device.get("online") else "офлайн",
                   _esc(state.get("access_mode") or "—"),
                   _esc(lock_label),
                   _esc(claimed), _esc(device.get("source") or "browser"))
            )

        device_table = (
            "<table><tr><th>Устройство</th><th>Hostname</th><th>ОС</th>"
            "<th>Связь</th><th>Режим</th><th>Удалённое управление</th>"
            "<th>Привязано</th><th>Источник</th></tr>%s</table>"
            % "".join(rows)
            if rows else
            "<p class=muted>Устройства ещё не привязаны.</p>"
        )

        # The setting the product asks about right after login: it is shown on
        # the dashboard, not buried in a submenu.
        if devices:
            primary = devices[0]
            state = await snapshot(str(primary.get("node_name") or ""))
            lock_card = (
                "<div class=card><h2>Удалённое управление MCP</h2>%s"
                "<p class=muted>Это тот же переключатель, что и "
                "<code>rhm remote-control</code> на самой машине.</p></div>"
                % lock_controls(primary, state, csrf)
            )
        else:
            lock_card = (
                "<div class=card><h2>Привяжите устройство</h2>"
                "<p class=muted>Введите имя узла и его ключ (ссылка MCP содержит "
                "<code>lk_...</code>; ключ устройства также принимается). Ключ нужен один "
                "раз, чтобы доказать, что устройство ваше.</p>"
                "<form method=post action=\"/account/devices/claim\">"
                "<input type=hidden name=csrf value=\"%s\">"
                "<div class=row><input type=text name=node placeholder=\"имя узла\"></div>"
                "<div class=row><input type=password name=credential placeholder=\"lk_... или токен устройства\">"
                "<button class=btn type=submit>Привязать</button></div></form></div>"
                % _esc(csrf)
            )

        account_card = (
            "<div class=card><h1>Remote Harness MCP</h1>"
            "<p class=muted>%s · аккаунт %s</p>"
            "<p>Устройств: %d</p>"
            "<form method=post action=/account/logout>"
            "<input type=hidden name=csrf value=\"%s\">"
            "<button class=\"btn secondary\" type=submit>Выйти</button></form></div>"
            % (_esc(account.get("email") or ""),
               _esc(str(account.get("id") or "")[:12]), len(devices), _esc(csrf))
        )
        devices_card = "<div class=card><h2>Устройства</h2>%s</div>" % device_table
        drive_card = (
            "<div class=card><h2>Google Drive</h2>%s</div>"
            % ("<p>Подключён · <a href=\"/account/drive\">открыть файлы</a></p>"
               if store.google_tokens(account["id"]) else
               "<p class=muted>Не подключён.</p>"
               "<a class=btn href=\"/account/drive/connect\">Подключить Drive</a>")
        )
        # The migration list sits right below the device table: it is opt-in
        # (MESH_CABINET_SHOW_UNCLAIMED), so the common page is unchanged.
        body = (feedback(request) + lock_card + account_card + drive_card
                + devices_card + unclaimed_card(csrf))
        if request.query_params.get("welcome") == "1":
            # The setting the product asks about right after the login: a modal
            # over the dashboard, with the same controls as the page below.
            body = (
                "<div class=modal><div class=modalbox>%s"
                "<div class=row><a class=btn href=\"/account\">Понятно</a></div>"
                "</div></div>"
                % lock_card
            ) + body
        return page("Remote Harness MCP", body)

    async def device_page(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account", status_code=303)
        if store is None:
            return page("Кабинет", "<div class=card><h1>Кабинет не настроен</h1></div>")
        device = store.device(str(request.path_params.get("device_id") or ""),
                              account["tenant_id"])
        if device is None:
            return page("Не найдено", "<div class=card><h1>Устройство не найдено</h1>"
                                      "<p><a href=\"/account\">В кабинет</a></p></div>", status=404)
        node = str(device.get("node_name") or "")
        state = await snapshot(node)
        body = (
            "%s<div class=card><h1>%s</h1>"
            "<p class=muted>%s</p>%s"
            "<p class=muted>Ссылка MCP с ключом узла остаётся на самой машине: "
            "<code>rhm --once</code> показывает её и QR-код.</p>"
            "<p><a class=\"btn secondary\" href=\"/account\">В кабинет</a> "
            "<a class=\"btn secondary\" href=\"/account/devices/%s\">Обновить</a></p></div>"
            "<div class=card>%s</div>"
            % (
                feedback(request), _esc(node),
                "онлайн" if online(node) else "офлайн (изменения не пройдут, пока агент не подключится)",
                "" if not state.get("error") else "<p class=error>%s</p>" % _esc(state["error"]),
                _esc(device.get("id")),
                "%s<hr><h2>Настройки</h2>%s"
                % (lock_controls(device, state, str(account.get("csrf") or "")),
                   device_settings(device, state, str(account.get("csrf") or ""))),
            )
        )
        return page("Настройки — %s" % node, body)

    async def pair_page(request: Request) -> Response:
        """The one page that turns a pairing code into a device of this account."""
        code = pair_code_from(request)
        account = await require(request)
        if account is None:
            # The code survives the login round-trip: the device is polling and
            # the person must land back on the same confirmation.
            return RedirectResponse("/account/login?next=/account/pair?code=" + code,
                                    status_code=303)
        metadata = pair_metadata(code)
        if metadata is None:
            return pair_problem_page(
                404, "Код привязки не найден",
                "Код действует 10 минут. Запустите привязку на устройстве заново "
                "и подтвердите новый код.")
        hostname = str(metadata.get("hostname") or "") or "устройство"
        os_arch = " ".join(str(part) for part in
                           (metadata.get("os"), metadata.get("arch")) if part) or "неизвестно"
        if metadata.get("approved"):
            return pair_problem_page(
                409, "Этот код уже подтверждён",
                "Коды одноразовые: устройство, которому он выдавался, уже получило "
                "доступ. Если это не ваше устройство, ничего не делайте.")
        minutes = max(0, int(metadata.get("expires_in") or 0)) // 60
        body = (
            "%s<div class=card><h1>%s</h1>"
            "<p class=muted>Устройство просит подключить его к вашему кабинету. "
            "Код <code>%s</code> действует ещё %d мин.</p>"
            "<p>Система: %s</p>"
            "<p class=muted>Подтверждайте только ту машину, которую вы сами только что "
            "подключали: после подтверждения она получит доступ к вашему кабинету.</p>"
            "<form method=post action=\"/account/pair/%s/approve\">"
            "<input type=hidden name=csrf value=\"%s\">"
            "<button class=btn type=submit>Подключить</button></form>"
            "<p><a class=\"btn secondary\" href=\"/account\">В кабинет</a></p></div>"
            % (feedback(request), _esc(hostname), _esc(code), minutes, _esc(os_arch),
               _esc(code), _esc(account.get("csrf")))
        )
        return page("Привязка — %s" % hostname, body)

    async def pair_approve(request: Request) -> Response:
        """Confirm a code: a session, a CSRF token and a matching Origin are required."""
        code = pair_code_from(request)
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account/pair?code=" + code,
                                    status_code=303)
        if request.method != "POST" or store is None:
            return _headers(Response("Use POST", status_code=405))
        form = await request.form()
        if not csrf_ok(request, account, form):
            return page("Ошибка", "<div class=card><h1>CSRF-токен неверен</h1></div>", 403)
        metadata = pair_metadata(code)
        if metadata is None:
            return pair_problem_page(
                404, "Код привязки не найден",
                "Код действует 10 минут. Запустите привязку на устройстве заново.")
        if metadata.get("approved"):
            return pair_problem_page(
                409, "Этот код уже подтверждён",
                "Коды одноразовые: второй раз один и тот же код не срабатывает.")
        if not gateway_ready():
            return back("/account", {"error": "Шлюз не подключён: привязка недоступна."})
        try:
            result = node_control.approve_pair(
                code,
                owner_subject=owner_subject(account),
                account_id=str(account.get("id") or ""),
            )
        except Exception as exc:
            return back("/account", {"error": "%s: %s" % (type(exc).__name__, exc)})
        if not result.get("ok"):
            error = str(result.get("error") or "")
            if error == "unknown_pair":
                return pair_problem_page(
                    404, "Код привязки не найден",
                    "Код действует 10 минут. Запустите привязку на устройстве заново.")
            if error == "already_approved":
                return pair_problem_page(
                    409, "Этот код уже подтверждён",
                    "Коды одноразовые: второй раз один и тот же код не срабатывает.")
            return back("/account", {"error": PAIR_ERRORS.get(
                error, "Не удалось подтвердить привязку.")})
        node = str(result.get("username") or "").strip()
        if not node:
            return back("/account", {"error": "Шлюз не вернул имя устройства."})
        hostname = str(result.get("hostname") or metadata.get("hostname") or "") or node
        # The registry now carries the owner; the cabinet row is what every later
        # page addresses by id, so a successful approval always writes one.
        device = store.claim_device(account["tenant_id"], node, display_name=hostname)
        if device is None:
            return back("/account", {"error": CLAIM_ERRORS["already_linked"]})
        # Keys are not shown on purpose: the device that asked for the code
        # collects its token and link key itself.
        return back("/account", {"message": "Устройство %s привязано." % node})

    async def claim(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account", status_code=303)
        if request.method != "POST" or store is None:
            return _headers(Response("Use POST", status_code=405))
        form = await request.form()
        if not csrf_ok(request, account, form):
            return page("Ошибка", "<div class=card><h1>CSRF-токен неверен</h1></div>", 403)
        tenant = account["tenant_id"]
        now = time.time()
        attempts = [stamp for stamp in claim_guard.get(tenant, []) if now - stamp < 600]
        if len(attempts) >= 10:
            return back("/account", {"error": CLAIM_ERRORS["rate_limited"]})
        attempts.append(now)
        claim_guard[tenant] = attempts

        node = str(form.get("node") or "").strip().lower()
        credential = str(form.get("credential") or "").strip()
        if not gateway_ready():
            return back("/account", {"error": "Шлюз не подключён: привязка недоступна."})
        try:
            verified = node_control.claim(
                node, credential,
                owner_subject=owner_subject(account),
                account_id=str(account.get("id") or ""),
            )
        except Exception as exc:
            return back("/account", {"error": "%s: %s" % (type(exc).__name__, exc)})
        if not verified.get("ok"):
            return back("/account", {"error": CLAIM_ERRORS.get(
                str(verified.get("error") or ""), "Не удалось привязать устройство.")})
        device = store.claim_device(tenant, node)
        if device is None:
            return back("/account", {"error": CLAIM_ERRORS["already_linked"]})
        return back("/account/devices/%s" % device["id"],
                    {"message": "Устройство %s привязано." % node})

    async def device_control(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account", status_code=303)
        if request.method != "POST" or store is None:
            return _headers(Response("Use POST", status_code=405))
        form = await request.form()
        device = store.device(str(request.path_params.get("device_id") or ""),
                              account["tenant_id"])
        if device is None:
            return page("Не найдено", "<div class=card><h1>Устройство не найдено</h1></div>", 404)
        if not csrf_ok(request, account, form):
            return page("Ошибка", "<div class=card><h1>CSRF-токен неверен</h1></div>", 403)
        payload: Dict[str, Any] = {"action": str(form.get("action") or "status")}
        if form.get("mode"):
            payload["mode"] = str(form.get("mode"))
        if form.get("path"):
            payload["path"] = str(form.get("path"))
        if form.get("workspace"):
            payload["workspace"] = str(form.get("workspace"))
        if form.get("enabled") is not None and str(form.get("enabled")) != "":
            payload["enabled"] = _is_true(form.get("enabled"))
        if payload["action"] == "sandbox" and payload.get("enabled"):
            payload["network"] = _is_true(form.get("network"))
        node = str(device.get("node_name") or "")
        result = await control(node, payload)
        if not result.get("error"):
            store.touch_device(device["id"])
        return back("/account/devices/%s" % device["id"], result)

    async def drive_page(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account/drive", status_code=303)
        record = store.google_tokens(account["id"])
        files: list = []
        problem = ""
        if record:
            try:
                token = drive_api.access_token(cfg, store, account["id"])
                files = drive_api.list_files(token)
            except Exception as exc:
                problem = str(exc)
        if record and not problem:
            body = (
                "<div class=card><h1>Google Drive</h1>"
                "<p class=muted>Подключён · scope <code>drive.file</code> · файлов: %d</p>"
                "<table><tr><th>Файл</th><th>Тип</th><th>Изменён</th></tr>%s</table>"
                "<form method=post action=/account/drive/disconnect>"
                "<input type=hidden name=csrf value=\"%s\">"
                "<button class=\"btn secondary\" type=submit>Отключить Drive</button></form>"
                "<p class=muted>Агент сможет читать/загружать только эти файлы; "
                "остальной Drive недоступен.</p></div>"
                % (len(files), "".join(
                    "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (_esc(item.get("name")), _esc(item.get("mimeType")),
                       _esc(item.get("modifiedTime")))
                    for item in files
                ) or "<tr><td colspan=3 class=muted>Пока нет файлов, созданных приложением.</td></tr>",
                   _esc(account.get("csrf")))
            )
        else:
            body = (
                "<div class=card><h1>Google Drive</h1>"
                "<p class=muted>%s</p>"
                "<p>Нужен доступ <code>drive.file</code> — только файлы, созданные/открытые "
                "этим приложением. Весь Drive не открывается.</p>"
                "<a class=btn href=/account/drive/connect>Подключить Google Drive</a>"
                "<p class=muted>Токен хранится зашифрованным на шлюзе, отдельно для каждого "
                "аккаунта.</p></div>"
                % (_esc(problem or "Drive ещё не подключён."))
            )
        return page("Google Drive — Remote Harness MCP", body)

    async def drive_start(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account/drive", status_code=303)
        state = auth.new_state()
        nonce = auth.new_nonce()
        verifier = auth.new_verifier()
        binding = auth.new_state()
        store.create_flow(state, nonce, verifier, "/account/drive",
                          kind="drive", account_id=account["id"],
                          binding_hash=hashlib.sha256(binding.encode()).hexdigest())
        response = RedirectResponse(
            auth.build_auth_url(cfg, state, nonce, verifier, scopes=auth.DRIVE_SCOPES,
                                access_type="offline", prompt="consent"),
            status_code=302,
        )
        sessions.set_login_cookie(response, binding, cfg.base_url)
        return response

    async def drive_disconnect(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login", status_code=303)
        form = await request.form()
        if not csrf_ok(request, account, form):
            return page("Ошибка", "<div class=card><h1>CSRF-токен неверен</h1></div>", 403)
        store.delete_google_tokens(account["id"])
        return RedirectResponse("/account/drive?disconnected=1", status_code=303)

    async def login(request: Request) -> Response:
        if not cfg.configured:
            return RedirectResponse("/account", status_code=303)
        if await require(request) is not None:
            return RedirectResponse("/account", status_code=303)
        nxt = auth.sanitize_next(request.query_params.get("next"))
        body = (
            "<div class=card><h1>Вход в кабинет</h1>"
            "<p class=muted>Войдите через Google, чтобы управлять своими устройствами.</p>"
            "<a class=btn href=\"/account/auth/google/start?next=%s\">Войти через Google</a>"
            "<p class=muted>Ссылка MCP не публикуется. Кабинет хранит только метаданные.</p></div>"
            % html.escape(nxt, quote=True)
        )
        return page("Вход — Remote Harness MCP", body)

    async def start(request: Request) -> Response:
        if not cfg.configured:
            return page("Не настроено", "<div class=card><h1>OAuth не настроен</h1></div>", 503)
        if login_rate_limited(request, "start"):
            return page("Подождите", "<div class=card><h1>Слишком много попыток входа</h1>"
                                     "<p class=muted>Повторите через минуту.</p></div>", 429)
        state = auth.new_state()
        nonce = auth.new_nonce()
        verifier = auth.new_verifier()
        binding = auth.new_state()
        store.create_flow(
            state, nonce, verifier, auth.sanitize_next(request.query_params.get("next")),
            binding_hash=hashlib.sha256(binding.encode()).hexdigest(),
        )
        response = RedirectResponse(auth.build_auth_url(cfg, state, nonce, verifier), status_code=302)
        # The callback must arrive in the same browser that started the flow:
        # otherwise a link with somebody else's state can attach their identity
        # to this session.
        sessions.set_login_cookie(response, binding, cfg.base_url)
        return response

    async def callback(request: Request) -> Response:
        if not cfg.configured:
            return page("Не настроено", "<div class=card><h1>OAuth не настроен</h1></div>", 503)
        if login_rate_limited(request, "callback"):
            return page("Подождите", "<div class=card><h1>Слишком много попыток входа</h1></div>", 429)
        error = request.query_params.get("error")
        state = request.query_params.get("state", "")
        if error:
            return page("Вход отменён", "<div class=card><h1>Вход отменён</h1><p>%s</p></div>"
                        % html.escape(error), 400)
        flow = store.consume_flow(state)
        if flow is None:
            return page("Вход отклонён", "<div class=card><h1>Сессия входа истекла</h1>"
                        "<p class=muted>Повторите вход.</p><a class=btn href=\"/account/login\">Войти</a></div>", 400)
        binding = sessions.login_binding(request, cfg.base_url)
        expected = str(flow.get("binding_hash") or "")
        if expected and (not binding or not hmac.compare_digest(
                hashlib.sha256(binding.encode()).hexdigest(), expected)):
            response = page("Вход отклонён", "<div class=card><h1>Ссылка входа открыта в другом браузере</h1>"
                                             "<p class=muted>Начните вход заново из кабинета.</p>"
                                             "<a class=btn href=\"/account/login\">Войти</a></div>", 400)
            sessions.clear_login_cookie(response, cfg.base_url)
            return response
        code = request.query_params.get("code", "")
        if not code:
            return page("Вход отклонён", "<div class=card><h1>Нет кода авторизации</h1></div>", 400)
        if str(flow.get("kind") or "login") == "drive":
            account = await require(request)
            if account is None or str(account.get("id")) != str(flow.get("account_id")):
                return page("Drive отклонён", "<div class=card><h1>Сессия не совпадает</h1>"
                            "<p class=muted>Начните подключение Drive заново из кабинета.</p></div>", 403)
            try:
                tokens = auth.exchange_code(cfg, code, flow["verifier"])
                claims, problem = auth.verify_id_token(cfg, tokens.get("id_token", ""), flow["nonce"])
                if claims is None:
                    raise RuntimeError(problem)
                drive_api.save_tokens(cfg, store, account["id"], tokens)
            except Exception as exc:
                return page("Drive не подключён", "<div class=card><h1>Не удалось подключить Drive</h1>"
                            "<p class=muted>%s</p><a class=btn href=\"/account/drive\">Назад</a></div>"
                            % html.escape(str(exc)), 400)
            return RedirectResponse("/account/drive?connected=1", status_code=303)
        try:
            tokens = auth.exchange_code(cfg, code, flow["verifier"])
            claims, problem = auth.verify_id_token(cfg, tokens.get("id_token", ""), flow["nonce"])
        except Exception as exc:
            return page("Ошибка входа", "<div class=card><h1>Google недоступен</h1>"
                        "<p class=muted>%s</p></div>" % html.escape(str(exc)), 502)
        if claims is None:
            return page("Ошибка входа", "<div class=card><h1>Токен отклонён</h1>"
                        "<p class=muted>%s</p></div>" % html.escape(problem), 400)
        if not owner_allowed(claims):
            response = page("Доступ закрыт", "<div class=card><h1>Этот аккаунт не в списке владельцев</h1>"
                                             "<p class=muted>Обратитесь к оператору шлюза.</p></div>", 403)
            sessions.clear_login_cookie(response, cfg.base_url)
            return response
        account = store.upsert_account(claims)
        session = store.create_session(account["id"])
        target = flow.get("next_path") or "/account"
        if target == "/account":
            # Right after the login, ask the one question the product cares
            # about: is this MCP controllable remotely, and by whom.
            target = "/account?welcome=1"
        response = RedirectResponse(target, status_code=303)
        sessions.set_cookie(response, session["id"], cfg.session_secret, cfg.base_url)
        sessions.clear_login_cookie(response, cfg.base_url)
        return _headers(response)

    async def logout(request: Request) -> Response:
        if request.method == "GET":
            return _headers(Response("Use POST", status_code=405))
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login", status_code=303)
        form = await request.form()
        if not csrf_ok(request, account, form):
            return page("Ошибка", "<div class=card><h1>CSRF-токен неверен</h1></div>", 403)
        sid = sessions.session_id(request, cfg.session_secret, cfg.base_url)
        if sid:
            store.revoke_session(sid)
        response = RedirectResponse("/account/login", status_code=303)
        sessions.clear_cookie(response, cfg.base_url)
        return _headers(response)

    app = Starlette(routes=[
        Route("/", root, methods=["GET", "HEAD"]),
        # Pairing: the code is typed by the device, the confirmation by the owner.
        Route("/pair", pair_page, methods=["GET", "HEAD"]),
        Route("/pair/{code}/approve", pair_approve, methods=["POST"]),
        Route("/devices/claim", claim, methods=["POST"]),
        Route("/devices/{device_id}", device_page, methods=["GET", "HEAD"]),
        Route("/devices/{device_id}/control", device_control, methods=["POST"]),
        Route("/drive", drive_page, methods=["GET", "HEAD"]),
        Route("/drive/connect", drive_start, methods=["GET"]),
        Route("/drive/disconnect", drive_disconnect, methods=["POST"]),
        Route("/login", login, methods=["GET", "HEAD"]),
        Route("/auth/google/start", start, methods=["GET"]),
        Route("/auth/google/callback", callback, methods=["GET"]),
        Route("/logout", logout, methods=["GET", "POST"]),
    ])
    app.state.account_config = cfg
    app.state.account_store = store
    app.state.node_control = node_control
    return app
