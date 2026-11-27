/* ops/live-usb/kiosk_ui/i18n.js - 7-language switcher and startup picker for LiveUSB Kiosk UI */
(function () {
  "use strict";

  var STORAGE_KEY = "rhm_liveusb_lang";
  var SUPPORTED = ["en", "de", "ru", "fr", "es", "ar", "zh"];
  var translations = {};
  var currentLang = "ru";

  function normalizeLang(raw) {
    var code = String(raw || "").trim().toLowerCase().split("-")[0];
    return SUPPORTED.indexOf(code) !== -1 ? code : "ru";
  }

  function t(key, fallback) {
    var bundle = translations[currentLang] || translations.ru || {};
    if (Object.prototype.hasOwnProperty.call(bundle, key)) {
      return bundle[key];
    }
    var enBundle = translations.en || {};
    if (Object.prototype.hasOwnProperty.call(enBundle, key)) {
      return enBundle[key];
    }
    return fallback !== undefined ? fallback : key;
  }

  function applyLanguage(lang, persist) {
    currentLang = normalizeLang(lang);
    var bundle = translations[currentLang] || {};
    var meta = bundle._meta || { code: currentLang, dir: currentLang === "ar" ? "rtl" : "ltr" };

    document.documentElement.lang = meta.code || currentLang;
    document.documentElement.dir = meta.dir === "rtl" ? "rtl" : "ltr";

    if (persist) {
      try {
        window.localStorage.setItem(STORAGE_KEY, currentLang);
      } catch (_err) {}
    }

    var switcher = document.getElementById("lang-switcher");
    if (switcher && switcher.value !== currentLang) {
      switcher.value = currentLang;
    }

    var nodes = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < nodes.length; i += 1) {
      var el = nodes[i];
      var key = el.getAttribute("data-i18n");
      if (!key) continue;
      var val = t(key, null);
      if (val !== null) {
        el.textContent = val;
      }
    }

    var btns = document.querySelectorAll("[data-lang-pick]");
    for (var j = 0; j < btns.length; j += 1) {
      var b = btns[j];
      b.classList.toggle("is-active", b.getAttribute("data-lang-pick") === currentLang);
    }

    var appItems = document.querySelectorAll("[data-app-id]");
    for (var k = 0; k < appItems.length; k += 1) {
      var aid = appItems[k].getAttribute("data-app-id");
      var translated = t("app_" + aid, null);
      if (translated) {
        var nameNode = appItems[k].querySelector(".app-name") || appItems[k];
        nameNode.textContent = translated;
      }
    }
  }

  function openPicker() {
    var backdrop = document.getElementById("lang-picker-backdrop");
    if (backdrop) {
      backdrop.classList.remove("is-hidden");
    }
  }

  function closePicker() {
    var backdrop = document.getElementById("lang-picker-backdrop");
    if (backdrop) {
      backdrop.classList.add("is-hidden");
    }
  }

  function initI18n() {
    var switcher = document.getElementById("lang-switcher");
    if (switcher) {
      switcher.addEventListener("change", function () {
        applyLanguage(switcher.value, true);
      });
    }

    var pickBtns = document.querySelectorAll("[data-lang-pick]");
    for (var i = 0; i < pickBtns.length; i += 1) {
      pickBtns[i].addEventListener("click", function (ev) {
        var code = ev.currentTarget.getAttribute("data-lang-pick");
        applyLanguage(code, true);
        closePicker();
      });
    }

    var saved = null;
    try {
      saved = window.localStorage.getItem(STORAGE_KEY);
    } catch (_err) {}

    fetch("/api/i18n", { cache: "no-store" })
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .then(function (data) {
        if (data && data.translations) {
          translations = data.translations;
        }
        if (saved && SUPPORTED.indexOf(saved) !== -1) {
          applyLanguage(saved, false);
          closePicker();
        } else {
          applyLanguage("en", false);
          openPicker();
        }
      })
      .catch(function () {
        if (saved && SUPPORTED.indexOf(saved) !== -1) {
          applyLanguage(saved, false);
        }
      });
  }

  window.rhmT = t;
  window.rhmSetLang = function (code) { applyLanguage(code, true); };
  window.rhmGetLang = function () { return currentLang; };
  window.rhmOpenLangPicker = openPicker;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initI18n);
  } else {
    initI18n();
  }
})();
