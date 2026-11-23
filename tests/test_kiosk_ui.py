# -*- coding: utf-8 -*-
"""tests/test_kiosk_ui.py - Static contract tests for the kiosk frontend.

These tests read the real ``ops/live-usb/kiosk_ui`` assets and assert the
markup/JS contract the kiosk server and the UX plan rely on:

* the network view is the initial view and the ready view starts hidden;
* the network view carries a prominent internet banner;
* both views expose a terminal button with its own visible feedback line;
* both views expose the application launcher, backed by ``/api/apps``;
* the ready view teaches the QR -> connect page -> Gemini/ChatGPT/Claude flow;
* the pending-session panel replaces the QR until ``session.ready``;
* the automatic view choice depends on ``internet.reachable`` and
  ``session.ready`` and respects an explicit operator choice;
* the network view carries a «Настройки сети» dialog with the five documented
  tabs, the ``/api/network`` read-only snapshot with a masked link key, and the
  four JSON editors for IP/DNS, PPPoE, WireGuard and OpenVPN.

No browser is required: every assertion is a static contract over the shipped
files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KIOSK_UI_DIR = REPO_ROOT / "ops" / "live-usb" / "kiosk_ui"
INDEX_PATH = KIOSK_UI_DIR / "index.html"
APP_JS_PATH = KIOSK_UI_DIR / "app.js"
STYLES_PATH = KIOSK_UI_DIR / "styles.css"


@pytest.fixture(scope="module")
def html_text() -> str:
    assert INDEX_PATH.is_file(), f"Missing kiosk markup: {INDEX_PATH}"
    return INDEX_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js_text() -> str:
    assert APP_JS_PATH.is_file(), f"Missing kiosk script: {APP_JS_PATH}"
    return APP_JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_text() -> str:
    assert STYLES_PATH.is_file(), f"Missing kiosk stylesheet: {STYLES_PATH}"
    return STYLES_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def network_html(html_text: str) -> str:
    head, sep, rest = html_text.partition('id="view-network"')
    assert sep, "Network view section is missing"
    return head + sep + rest.split('id="view-ready"', 1)[0]


@pytest.fixture(scope="module")
def ready_html(html_text: str) -> str:
    head, sep, rest = html_text.partition('id="view-ready"')
    assert sep, "Ready view section is missing"
    return head + sep + rest


# The five documented network-settings sections, in tab order.
NETWORK_SETTINGS_TABS = (
    ("current", "Текущие"),
    ("ip", "IP и DNS"),
    ("pppoe", "PPPoE"),
    ("wireguard", "WireGuard"),
    ("openvpn", "OpenVPN"),
)


@pytest.fixture(scope="module")
def network_modal(html_text: str) -> str:
    """Markup of the network-settings dialog only."""
    head, sep, rest = html_text.partition('id="network-settings-backdrop"')
    assert sep, "Network settings dialog is missing"
    return head + sep + rest


def network_panel(html_text: str, tab: str) -> str:
    """Slice one settings panel out of the dialog markup."""
    order = [name for name, _label in NETWORK_SETTINGS_TABS]
    marker = f'id="net-panel-{tab}"'
    assert marker in html_text, f"Missing panel: {marker}"
    tail = html_text.split(marker, 1)[1]
    later = [f'id="net-panel-{name}"' for name in order[order.index(tab) + 1 :]]
    later.append("<!-- /network-settings -->")
    cut = len(tail)
    for next_id in later:
        if next_id in tail:
            cut = min(cut, tail.index(next_id))
    return marker + tail[:cut]


def html_tag(html_text: str, element_id: str) -> str:
    """Return the full opening tag that carries ``element_id``."""
    marker = f'id="{element_id}"'
    assert marker in html_text, f"Missing element id: {element_id}"
    head = html_text.split(marker, 1)[0]
    start = head.rfind("<")
    end = html_text.index(">", html_text.index(marker))
    return html_text[start : end + 1]


def test_initial_view_shows_network_and_hides_ready(html_text: str, js_text: str) -> None:
    """The kiosk boots on the network screen; the QR screen starts hidden."""
    assert '<section id="view-network" class="view-panel"' in html_text
    assert '<section id="view-ready" class="view-panel is-hidden"' in html_text
    assert 'id="nav-network"' in html_text
    assert 'id="nav-ready"' in html_text
    assert "fetchStatus();" in js_text
    assert "scanWifi();" in js_text


def test_internet_banner_exists_and_has_all_three_states(html_text: str, js_text: str) -> None:
    """The network screen owns a prominent internet banner with three states."""
    assert 'id="internet-banner"' in html_text
    banner_html = html_text.split('id="internet-banner"', 1)[1].split("</div>", 1)[0]
    assert "Интернет не обнаружен. Подключите Wi-Fi, кабель или USB-модем." in banner_html

    combined = html_text + "\n" + js_text
    assert "Интернет есть. Получаем токен и QR…" in combined
    assert "Интернет подключён" in combined

    # The banner is driven by the status payload, not by link state.
    assert "data.internet" in js_text
    assert "internet.reachable" in js_text
    assert "updateInternetBanner" in js_text
    assert "session.ready" in js_text


def test_terminal_button_on_both_views_with_feedback(html_text: str, js_text: str, network_html: str, ready_html: str) -> None:
    """Network and ready screens both offer a terminal escape."""
    assert 'id="btn-terminal"' in html_text
    assert 'id="btn-terminal-network"' in network_html
    assert 'id="btn-terminal"' in ready_html
    # The network variant must not shadow the ready button id.
    assert 'id="btn-terminal"' not in network_html

    assert 'id="terminal-feedback-network"' in network_html
    assert 'id="terminal-feedback-ready"' in ready_html
    assert "Открыть терминал" in html_text
    assert "/api/terminal" in js_text
    assert "openTerminal" in js_text
    # A JSON body is always sent; the browser cannot supply a command.
    assert 'body: "{}"' in js_text
    assert 'headers: { "Content-Type": "application/json" }' in js_text
    # Each button is wired to its own feedback element.
    assert "openTerminal(els.btnTerminal, els.terminalFeedbackReady)" in js_text
    assert "openTerminal(els.btnTerminalNetwork, els.terminalFeedbackNetwork)" in js_text


def test_apps_button_and_modal_contract(html_text: str, js_text: str, network_html: str, ready_html: str) -> None:
    """The launcher button exists on both views and opens one shared modal."""
    assert 'id="btn-apps"' in ready_html
    assert 'id="btn-apps-network"' in network_html
    assert "Приложения" in html_text

    assert 'id="apps-modal-backdrop"' in html_text
    assert 'id="apps-modal-title"' in html_text
    assert 'id="apps-list"' in html_text
    assert 'id="btn-apps-close"' in html_text
    assert 'id="btn-apps-cancel"' in html_text
    assert 'id="apps-feedback"' in html_text
    assert 'aria-modal="true"' in html_text

    # Fetch the allowlisted list on open, launch by id only.
    assert 'fetch("/api/apps"' in js_text
    assert 'fetch("/api/apps/launch"' in js_text
    assert "JSON.stringify({ id: appId })" in js_text
    assert "openAppsModal" in js_text
    assert "closeAppsModal" in js_text
    # Unavailable apps are disabled and explain themselves via title.
    assert "app.available" in js_text
    assert "item.disabled = !available" in js_text
    assert "item.title" in js_text
    assert "app.reason" in js_text
    # The backend also serves a files entry: it is rendered like any other app.
    assert "app.label" in js_text
    assert "renderApps" in js_text
    assert "data.apps" in js_text
    # The allowlist lives on the server; the browser must not hardcode it.
    assert "pcmanfm" not in js_text
    assert "mousepad" not in js_text
    assert "xterm" not in js_text


def test_ready_pending_panel_hides_qr_until_session_ready(html_text: str, js_text: str, ready_html: str) -> None:
    """Internet but no session -> pending panel instead of a stale QR."""
    assert 'id="ready-pending"' in ready_html
    pending_html = ready_html.split('id="ready-pending"', 1)[1].split('id="ready-content"', 1)[0]
    assert "Интернет есть, получаем токен…" in pending_html
    assert 'id="ready-pending-title"' in pending_html
    assert 'id="ready-pending-text"' in pending_html

    # The QR/URL/token block has its own container so it can be hidden.
    assert 'id="ready-content"' in ready_html
    assert 'id="qr-image"' in ready_html
    assert 'id="session-url"' in ready_html
    assert 'id="session-token"' in ready_html

    assert "updateReadyPending" in js_text
    assert "els.readyPending.classList.toggle" in js_text
    assert "els.readyContent.classList.toggle" in js_text
    assert "sessionReady" in js_text
    # Offline without a session must not claim that internet exists.
    assert "PENDING_TITLE_OFFLINE" in js_text
    assert "QR-код появится, когда появится интернет." in js_text


def test_ready_view_has_qr_instruction(html_text: str, ready_html: str) -> None:
    """The QR card explains the connect-page app choice."""
    assert 'id="qr-instruction"' in ready_html
    instruction = ready_html.split('id="qr-instruction"', 1)[1].split("</p>", 1)[0]
    assert "QR" in instruction
    assert "ссылку подключения" in instruction
    assert "Gemini" in instruction
    assert "ChatGPT" in instruction
    assert "Claude" in instruction
    assert "странице подключения" in instruction


def test_auto_view_rule_uses_internet_and_session_state(html_text: str, js_text: str) -> None:
    """Auto-selection: internet -> ready (pending or full), else network."""
    assert "state.userChoseNetworkView" in js_text
    assert "applyAutoView" in js_text
    assert "internetReachable" in js_text
    assert "sessionReady" in js_text
    # The ready view is selected without an explicit operator choice.
    assert 'setView("ready", false)' in js_text
    assert 'setView("network", false)' in js_text
    # An explicit choice must win over the automatic rule.
    assert re.search(
        r"if \(state\.userChoseNetworkView\) \{\s*return;", js_text
    ), "applyAutoView must bail out when the operator chose a view"


def test_no_duplicate_ids_and_every_lookup_exists(html_text: str, js_text: str) -> None:
    """Both views coexist in the DOM, so ids must stay unique and resolvable."""
    ids = re.findall(r'\bid="([^"]+)"', html_text)
    duplicates = {value for value in ids if ids.count(value) > 1}
    assert not duplicates, f"Duplicate element ids: {sorted(duplicates)}"

    lookup_ids = set(re.findall(r'getElementById\("([^"]+)"\)', js_text))
    assert lookup_ids, "app.js must resolve its elements by id"
    missing = sorted(lookup_ids - set(ids))
    assert not missing, f"app.js looks up ids absent from index.html: {missing}"


def test_view_switching_keeps_existing_controls(html_text: str, js_text: str) -> None:
    """The Wi-Fi modal, hidden SSID flow, token regeneration and QR refresh stay."""
    for required_id in (
        "wifi-modal-backdrop",
        "wifi-connect-form",
        "btn-hidden-wifi",
        "btn-refresh-wifi",
        "btn-change-wifi",
        "btn-new-token",
        "input-hidden",
        "btn-toggle-keyboard",
        "regen-feedback",
        "qr-image",
    ):
        assert f'id="{required_id}"' in html_text, f"Missing preserved control: {required_id}"

    assert '"/api/wifi/scan"' in js_text
    assert '"/api/wifi/connect"' in js_text
    assert '"/api/session/regenerate"' in js_text
    assert "refreshQrImage" in js_text
    assert '"/api/qr.svg?t="' in js_text


def test_styles_keep_the_existing_dark_theme_and_timing(css_text: str) -> None:
    """Only the existing tokens and 150ms ease-out motion are used."""
    for token in ("#0e1116", "#161b22", "#2b313a", "150ms ease-out"):
        assert token in css_text
    # New surfaces reuse the palette instead of introducing a framework.
    assert ".pending-card" in css_text
    assert ".qr-instruction" in css_text
    assert ".app-item" in css_text
    assert ".alert-banner.is-ok" in css_text
    assert "linear-gradient" not in css_text
    assert "radial-gradient" not in css_text
    assert "transition: all" not in css_text


def test_network_settings_button_opens_one_modal(
    html_text: str, js_text: str, network_html: str
) -> None:
    """The network view owns the «Настройки сети» trigger for one dialog."""
    assert 'id="btn-network-settings"' in network_html
    assert "Настройки сети" in network_html
    trigger = html_tag(network_html, "btn-network-settings")
    assert "btn" in trigger

    assert 'id="network-settings-backdrop"' in html_text
    backdrop_tag = html_tag(html_text, "network-settings-backdrop")
    assert 'role="dialog"' in backdrop_tag
    assert 'aria-modal="true"' in backdrop_tag
    assert "modal-backdrop" in backdrop_tag
    # A closed dialog must not be reachable by click.
    assert "is-hidden" in backdrop_tag

    assert 'id="network-settings-title"' in html_text
    assert 'id="btn-network-settings-close"' in html_text

    assert "openNetworkSettings" in js_text
    assert "closeNetworkSettings" in js_text
    assert 'els.btnNetworkSettings.addEventListener("click", openNetworkSettings)' in js_text
    assert 'els.btnNetworkSettingsClose.addEventListener("click", closeNetworkSettings)' in js_text
    # The existing Wi-Fi dialog is untouched and still distinct.
    assert 'id="wifi-modal-backdrop"' in html_text
    assert html_text.count('id="network-settings-backdrop"') == 1


def test_network_settings_has_five_tabs_and_matching_panels(
    html_text: str, network_modal: str, js_text: str
) -> None:
    """Exactly the five documented sections exist, current visible on open."""
    for tab, label in NETWORK_SETTINGS_TABS:
        assert f'id="net-tab-{tab}"' in network_modal, f"Missing tab button: {tab}"
        assert f'data-network-tab="{tab}"' in network_modal
        assert label in network_modal, f"Missing tab label: {label}"
        assert f'id="net-panel-{tab}"' in network_modal, f"Missing panel: {tab}"
        assert f'aria-controls="net-panel-{tab}"' in network_modal

    assert 'id="net-panel-current" class="network-panel"' in network_modal
    for tab in ("ip", "pppoe", "wireguard", "openvpn"):
        assert f'id="net-panel-{tab}" class="network-panel is-hidden"' in network_modal

    assert 'role="tablist"' in network_modal
    assert "setNetworkTab" in js_text
    assert "NETWORK_TABS" in js_text
    assert "els.networkTabButtons" in js_text
    assert 'setNetworkTab("current")' in js_text
    assert 'btn.setAttribute("aria-selected"' in js_text


def test_network_settings_current_tab_is_read_only_and_masks_link_key(
    html_text: str, network_modal: str, js_text: str
) -> None:
    """«Текущие» renders GET /api/network; the link key starts masked."""
    panel = network_panel(network_modal, "current")
    for element_id in (
        "net-current-hostname",
        "net-current-node-id",
        "net-current-link-key",
        "btn-toggle-link-key",
        "net-current-interfaces",
        "net-current-route",
        "net-current-dns",
        "net-current-feedback",
    ):
        assert f'id="{element_id}"' in panel, f"Missing current-state element: {element_id}"

    # Shipped markup already masks the token: no key can leak without a click.
    key_tail = panel.split('id="net-current-link-key"', 1)[1][:40]
    assert "••••" in key_tail
    # A read-only panel must not contain a form that could submit the snapshot.
    assert "<form" not in panel

    toggle = html_tag(panel, "btn-toggle-link-key")
    assert "button" in toggle.split(">", 1)[0]
    assert "Показать" in panel

    assert 'fetch("/api/network"' in js_text
    assert "data.link_key" in js_text
    assert "data.node_id" in js_text
    assert "data.interfaces" in js_text
    assert "data.routes" in js_text
    assert "data.dns" in js_text
    assert "renderNetworkInterfaces" in js_text
    assert "MASKED_LINK_KEY" in js_text
    assert "state.networkLinkKey" in js_text
    assert "renderNetworkLinkKey" in js_text
    assert "networkLinkKeyVisible" in js_text
    assert '"Показать"' in js_text
    assert '"Скрыть"' in js_text
    # The reveal is an explicit operator action only.
    assert 'els.btnToggleLinkKey.addEventListener("click"' in js_text
    # Interface rows show every documented column.
    for field in (
        "iface.mac",
        "iface.state",
        "iface.connection",
        "iface.gateway",
        "iface.ipv4",
    ):
        assert field in js_text, f"Interface field not rendered: {field}"


def test_network_settings_ip_and_dns_form_posts_json(
    html_text: str, network_modal: str, js_text: str
) -> None:
    """The IP/DNS editor offers DHCP/static and posts the documented body."""
    panel = network_panel(network_modal, "ip")
    for element_id in (
        "net-ip-form",
        "net-ip-interface",
        "net-ip-mode-auto",
        "net-ip-mode-manual",
        "net-ip-address",
        "net-ip-prefix",
        "net-ip-gateway",
        "net-ip-dns",
        "net-ip-feedback",
        "btn-net-ip-submit",
    ):
        assert f'id="{element_id}"' in panel, f"Missing IP editor field: {element_id}"

    assert 'value="auto"' in panel
    assert 'value="manual"' in panel
    assert 'type="radio"' in panel
    assert "<select" in panel

    assert '"/api/network/ip"' in js_text
    assert '? "manual" : "auto"' in js_text
    assert "els.netIpInterface.value" in js_text
    assert "els.netIpAddress.value" in js_text
    assert "els.netIpPrefix.value" in js_text
    assert "els.netIpGateway.value" in js_text
    assert "splitCommaList" in js_text
    assert 'split(",")' in js_text
    assert "els.netIpForm.addEventListener(\"submit\", submitNetworkIp)" in js_text


def test_network_settings_pppoe_form_keeps_password_out_of_markup(
    html_text: str, network_modal: str, js_text: str
) -> None:
    """PPPoE has username/password/service; the password is a real password."""
    panel = network_panel(network_modal, "pppoe")
    for element_id in (
        "net-pppoe-form",
        "net-pppoe-username",
        "net-pppoe-password",
        "net-pppoe-service",
        "net-pppoe-feedback",
        "btn-net-pppoe-submit",
    ):
        assert f'id="{element_id}"' in panel, f"Missing PPPoE field: {element_id}"

    password_tag = html_tag(panel, "net-pppoe-password")
    assert 'type="password"' in password_tag
    assert "value=" not in password_tag

    assert '"/api/network/pppoe"' in js_text
    assert "els.netPppoeUsername.value" in js_text
    assert "els.netPppoePassword.value" in js_text
    assert "els.netPppoeService.value" in js_text
    assert 'els.netPppoeForm.addEventListener("submit", submitNetworkPppoe)' in js_text


def test_network_settings_wireguard_form_keeps_private_key_out_of_markup(
    html_text: str, network_modal: str, js_text: str
) -> None:
    """WireGuard exposes the eight documented fields; private key is masked."""
    panel = network_panel(network_modal, "wireguard")
    for element_id in (
        "net-wg-form",
        "net-wg-name",
        "net-wg-private-key",
        "net-wg-address",
        "net-wg-prefix",
        "net-wg-peer-public-key",
        "net-wg-endpoint",
        "net-wg-allowed-ips",
        "net-wg-keepalive",
        "net-wg-feedback",
        "btn-net-wg-submit",
    ):
        assert f'id="{element_id}"' in panel, f"Missing WireGuard field: {element_id}"

    private_key_tag = html_tag(panel, "net-wg-private-key")
    assert 'type="password"' in private_key_tag
    assert "value=" not in private_key_tag

    assert '"/api/network/wireguard"' in js_text
    for field in (
        "private_key: els.netWgPrivateKey.value",
        "peer_public_key",
        "allowed_ips",
        "keepalive",
        "endpoint",
    ):
        assert field in js_text, f"WireGuard payload field missing: {field}"
    assert 'els.netWgForm.addEventListener("submit", submitNetworkWireguard)' in js_text


def test_network_settings_openvpn_form_warns_about_secrets(
    html_text: str, network_modal: str, js_text: str
) -> None:
    """OpenVPN takes a pasted .ovpn config and warns it may hold secrets."""
    panel = network_panel(network_modal, "openvpn")
    for element_id in (
        "net-openvpn-form",
        "net-openvpn-name",
        "net-openvpn-config",
        "net-openvpn-feedback",
        "btn-net-openvpn-submit",
    ):
        assert f'id="{element_id}"' in panel, f"Missing OpenVPN field: {element_id}"

    textarea_tag = html_tag(panel, "net-openvpn-config")
    assert textarea_tag.startswith("<textarea")
    assert "secret" in panel.lower() or "секрет" in panel.lower()
    assert "закрытые ключи" in panel or "может содержать" in panel

    assert '"/api/network/openvpn"' in js_text
    assert "config: config" in js_text
    assert 'els.netOpenvpnForm.addEventListener("submit", submitNetworkOpenvpn)' in js_text


def test_network_settings_submits_json_and_disable_buttons_in_flight(
    html_text: str, js_text: str
) -> None:
    """Every editor posts JSON through one helper that locks its button."""
    # Wrapped calls are allowed: compare on a whitespace-normalised source.
    flat_js = re.sub(r"\s+", " ", js_text)
    for path in (
        "/api/network/ip",
        "/api/network/pppoe",
        "/api/network/wireguard",
        "/api/network/openvpn",
    ):
        assert f'postNetworkJson( "{path}", payload' in flat_js, (
            f"{path} is not posted via the JSON helper"
        )

    assert 'headers: { "Content-Type": "application/json" }' in js_text
    assert "body: JSON.stringify(payload)" in js_text
    assert 'method: "POST"' in js_text
    assert "button.disabled = true" in js_text
    assert "button.disabled = false" in js_text
    assert "setNetworkFeedback" in js_text
    # The server message is always shown in a visible feedback element.
    assert "data.message || data.error" in js_text
    assert 'setNetworkFeedback(feedbackEl, message, ok ? "is-ok" : "is-error")' in js_text
    # A 503 (tool/root unavailable) still reaches the operator.
    assert 'resp.ok && data.ok !== false' in js_text
    # Each editor has its own visible feedback element and its own reset.
    for feedback_id, feedback_ref in (
        ("net-current-feedback", "els.netCurrentFeedback"),
        ("net-ip-feedback", "els.netIpFeedback"),
        ("net-pppoe-feedback", "els.netPppoeFeedback"),
        ("net-wg-feedback", "els.netWgFeedback"),
        ("net-openvpn-feedback", "els.netOpenvpnFeedback"),
    ):
        assert f'id="{feedback_id}"' in html_text
        assert feedback_ref in js_text
        assert f'id="{feedback_id}" class="feedback-line is-hidden"' in html_text


def test_network_settings_never_logs_or_retains_secrets(js_text: str, html_text: str) -> None:
    """No console logging, no browser storage, no secrets left in the DOM."""
    assert "console." not in js_text
    assert "localStorage" not in js_text
    assert "sessionStorage" not in js_text
    assert "document.cookie" not in js_text
    # Submitted secrets are wiped from the fields immediately afterwards.
    assert 'els.netPppoePassword.value = ""' in js_text
    assert 'els.netWgPrivateKey.value = ""' in js_text
    assert 'els.netOpenvpnConfig.value = ""' in js_text
    assert "resetNetworkSecretFields" in js_text
    # The link key is dropped from memory when the dialog closes.
    assert "state.networkLinkKey = \"\"" in js_text
    # Nothing secret ships in the markup.
    for element_id in ("net-pppoe-password", "net-wg-private-key"):
        assert "value=" not in html_tag(html_text, element_id)


def test_network_settings_styles_reuse_existing_tokens(css_text: str) -> None:
    """New surfaces reuse the palette, tokens and 150ms ease-out motion."""
    for selector in (
        ".modal-card-wide",
        ".network-tabs",
        ".network-panel",
        ".net-iface",
        ".net-kv",
        ".net-grid-2",
        ".radio-row",
        ".field-hint",
        ".text-area",
    ):
        assert selector in css_text, f"Missing style: {selector}"
    assert ".feedback-line.is-ok" in css_text
    assert ".feedback-line.is-error" in css_text
    assert "150ms ease-out" in css_text
    assert "linear-gradient" not in css_text
    assert "radial-gradient" not in css_text
    assert "transition: all" not in css_text
