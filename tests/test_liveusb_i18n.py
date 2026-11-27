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


def test_kiosk_ui_has_zero_untranslated_cyrillic_and_all_keys_exist():
    """Every visible label in index.html and every dynamic string in app.js must be translated across all 7 locales."""
    import re

    cyr = re.compile(r"[\u0400-\u04ff]")
    kiosk_dir = REPO_ROOT / "ops" / "live-usb" / "kiosk_ui"
    html = (kiosk_dir / "index.html").read_text(encoding="utf-8")
    app = (kiosk_dir / "app.js").read_text(encoding="utf-8")

    # 1. Zero unwrapped Cyrillic literals in app.js
    unwrapped = []
    for m in re.finditer(r'(["\'])(?:(?=(\\?))\2.)*?\1', app):
        s = m.group(0)
        if cyr.search(s):
            before = app[max(0, m.start() - 60) : m.start()]
            if not re.search(r'tr\("[^"]+",\s*$', before):
                unwrapped.append(s)
    assert unwrapped == [], f"Unwrapped Cyrillic literals in app.js: {unwrapped}"

    # 2. Every referenced key in index.html & app.js exists in all 7 locales
    html_keys = set(re.findall(r'data-i18n(?:-alt)?="([^"]+)"', html))
    js_keys = set(re.findall(r'\btr\("([a-z0-9_]+)"\s*,', app))
    for code in SUPPORTED_LANGUAGES:
        bundle = TRANSLATIONS[code]
        missing = sorted((html_keys | js_keys) - set(bundle.keys()))
        assert missing == [], f"Missing i18n keys in {code}: {missing}"
        if code != "ru":
            leaks = [
                (k, v)
                for k, v in bundle.items()
                if isinstance(v, str) and cyr.search(v) and k != "lang_select_title"
            ]
            assert leaks == [], f"Cyrillic leak in non-ru locale {code}: {leaks}"
        corrupted = [
            (k, v)
            for k, v in bundle.items()
            if isinstance(v, str)
            and ("??" in v or re.search(r"[A-Za-z\u0400-\u04ff]\?[A-Za-z\u0400-\u04ff]", v) or v.endswith("?"))
        ]
        assert corrupted == [], f"Corrupted '?' encoding in {code}: {corrupted}"

    # 3. No top-level frozen const calling tr(...) before i18n.js finishes loading
    frozen = re.findall(r"^\s{2}const [A-Z0-9_]+ =\s*tr\(", app, re.M)
    assert frozen == [], f"Top-level frozen tr() consts in app.js: {frozen}"

