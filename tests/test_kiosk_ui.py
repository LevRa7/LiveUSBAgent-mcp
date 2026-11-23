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
  ``session.ready`` and respects an explicit operator choice.

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
