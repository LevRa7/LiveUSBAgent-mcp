"""Tests for 7-language LiveUSB i18n (en, de, ru, fr, es, ar, zh)."""
from pathlib import Path

from core.liveusb_connect_page import render_connect_page
from core.liveusb_i18n import SUPPORTED_LANGUAGES, TRANSLATIONS, get_connect_locales, get_locale_bundle

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_all_seven_locales_have_complete_matching_keys():
    assert set(SUPPORTED_LANGUAGES) == {"en", "de", "ru", "fr", "es", "ar", "zh"}
    base_keys = set(TRANSLATIONS["en"].keys())
    for code in SUPPORTED_LANGUAGES:
        bundle = TRANSLATIONS[code]
        assert set(bundle.keys()) == base_keys, f"Key mismatch in locale {code}"
        assert bundle["_meta"]["code"] == code
        assert bundle["_meta"]["dir"] == ("rtl" if code == "ar" else "ltr")
        for key, val in bundle.items():
            if key == "_meta":
                continue
            assert isinstance(val, str) and val.strip(), f"Empty string {code}.{key}"


def test_locale_fallback_and_connect_subset():
    assert get_locale_bundle("de-DE")["_meta"]["code"] == "de"
    assert get_locale_bundle("unknown")["_meta"]["code"] == "ru"
    connect = get_connect_locales()
    assert set(connect.keys()) == set(SUPPORTED_LANGUAGES)
    for code in SUPPORTED_LANGUAGES:
        assert "connect_gemini_step1" in connect[code]
        assert "connect_chatgpt_step1" in connect[code]
        assert "connect_claude_step1" in connect[code]
        assert "btn_copy" in connect[code]


def test_connect_page_and_kiosk_ui_include_language_picker_and_switcher():
    html_doc = render_connect_page(
        session_code="s1",
        node_id="live-1234",
        link_key="lk_abc",
        host="demo.trycloudflare.com",
        expires_at="2026-12-01T00:00:00Z",
        online=True,
        standalone=True,
    )
    assert 'id="connect-lang"' in html_doc
    for code in SUPPORTED_LANGUAGES:
        assert f'value="{code}"' in html_doc

    kiosk_html = (REPO_ROOT / "ops" / "live-usb" / "kiosk_ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="lang-picker-backdrop"' in kiosk_html
    assert 'id="lang-switcher"' in kiosk_html
    assert '<script src="/i18n.js"></script>' in kiosk_html
    for code in SUPPORTED_LANGUAGES:
        assert f'data-lang-pick="{code}"' in kiosk_html
