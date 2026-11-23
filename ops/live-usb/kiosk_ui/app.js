(function () {
  "use strict";

  const state = {
    activeView: "network",
    userChoseNetworkView: false,
    lastConnectUrl: "",
    oskShift: false,
    activeInput: null,
  };

  const els = {
    hostnameLabel: document.getElementById("hostname-label"),
    navNetwork: document.getElementById("nav-network"),
    navReady: document.getElementById("nav-ready"),
    viewNetwork: document.getElementById("view-network"),
    viewReady: document.getElementById("view-ready"),
    iphoneTrustBanner: document.getElementById("iphone-trust-banner"),
    statusEthernet: document.getElementById("status-ethernet"),
    metaEthernet: document.getElementById("meta-ethernet"),
    statusAndroid: document.getElementById("status-android"),
    metaAndroid: document.getElementById("meta-android"),
    statusIphone: document.getElementById("status-iphone"),
    metaIphone: document.getElementById("meta-iphone"),
    wifiScanMeta: document.getElementById("wifi-scan-meta"),
    wifiList: document.getElementById("wifi-list"),
    btnRefreshWifi: document.getElementById("btn-refresh-wifi"),
    btnHiddenWifi: document.getElementById("btn-hidden-wifi"),
    btnChangeWifi: document.getElementById("btn-change-wifi"),
    btnTerminal: document.getElementById("btn-terminal"),
    btnTerminalNetwork: document.getElementById("btn-terminal-network"),
    btnApps: document.getElementById("btn-apps"),
    btnAppsNetwork: document.getElementById("btn-apps-network"),
    btnNewToken: document.getElementById("btn-new-token"),
    internetBanner: document.getElementById("internet-banner"),
    terminalFeedbackNetwork: document.getElementById("terminal-feedback-network"),
    terminalFeedbackReady: document.getElementById("terminal-feedback-ready"),
    readyPending: document.getElementById("ready-pending"),
    readyPendingTitle: document.getElementById("ready-pending-title"),
    readyPendingText: document.getElementById("ready-pending-text"),
    readyContent: document.getElementById("ready-content"),
    qrImage: document.getElementById("qr-image"),
    sessionUrl: document.getElementById("session-url"),
    sessionToken: document.getElementById("session-token"),
    sessionTunnel: document.getElementById("session-tunnel"),
    disksSummary: document.getElementById("disks-summary"),
    disksCountMeta: document.getElementById("disks-count-meta"),
    disksList: document.getElementById("disks-list"),
    regenFeedback: document.getElementById("regen-feedback"),
    modalBackdrop: document.getElementById("wifi-modal-backdrop"),
    modalTitle: document.getElementById("modal-title"),
    modalError: document.getElementById("modal-error"),
    wifiForm: document.getElementById("wifi-connect-form"),
    inputSsid: document.getElementById("input-ssid"),
    inputPassword: document.getElementById("input-password"),
    inputHidden: document.getElementById("input-hidden"),
    btnTogglePassword: document.getElementById("btn-toggle-password"),
    btnToggleKeyboard: document.getElementById("btn-toggle-keyboard"),
    btnModalClose: document.getElementById("btn-modal-close"),
    btnModalCancel: document.getElementById("btn-modal-cancel"),
    btnModalSubmit: document.getElementById("btn-modal-submit"),
    oskContainer: document.getElementById("osk-container"),
    appsBackdrop: document.getElementById("apps-modal-backdrop"),
    appsList: document.getElementById("apps-list"),
    appsFeedback: document.getElementById("apps-feedback"),
    btnAppsClose: document.getElementById("btn-apps-close"),
    btnAppsCancel: document.getElementById("btn-apps-cancel"),
  };

  const INTERNET_BANNER_NO_INTERNET =
    "Интернет не обнаружен. Подключите Wi-Fi, кабель или USB-модем.";
  const INTERNET_BANNER_PENDING =
    "Интернет есть. Получаем токен и QR…";
  const INTERNET_BANNER_ONLINE = "Интернет подключён";

  const PENDING_TITLE_INTERNET = "Интернет есть, получаем токен…";
  const PENDING_TITLE_OFFLINE =
    "Интернет не обнаружен. Подключите Wi-Fi, кабель или USB-модем.";
  const PENDING_TEXT_INTERNET =
    "QR-код и ссылка подключения появятся автоматически, как только сессия будет готова.";
  const PENDING_TEXT_OFFLINE = "QR-код появится, когда появится интернет.";

  function setView(viewName, explicit) {
    state.activeView = viewName;
    if (explicit && viewName === "network") {
      state.userChoseNetworkView = true;
    } else if (explicit && viewName === "ready") {
      state.userChoseNetworkView = false;
    }

    const isNetwork = viewName === "network";
    els.viewNetwork.classList.toggle("is-hidden", !isNetwork);
    els.viewReady.classList.toggle("is-hidden", isNetwork);
    els.navNetwork.classList.toggle("is-active", isNetwork);
    els.navReady.classList.toggle("is-active", !isNetwork);
  }

  function formatIfaceStatus(iface) {
    if (!iface) {
      return { text: "Отключено", meta: "Нет интерфейса", cls: "" };
    }
    if (iface.connected) {
      const meta = [iface.interface, iface.ip].filter(Boolean).join(" / ");
      return { text: "Подключено", meta: meta || "Активно", cls: "is-ok" };
    }
    if (iface.needs_trust) {
      return {
        text: "Требуется доверие",
        meta: iface.interface || "USB-устройство",
        cls: "is-warn",
      };
    }
    if (iface.plugged_in || iface.present) {
      return {
        text: "Обнаружено",
        meta: iface.interface || "Ожидание IP",
        cls: "",
      };
    }
    return { text: "Не подключено", meta: "Нет соединения", cls: "" };
  }

  function applyIfaceCard(valueEl, metaEl, info) {
    valueEl.textContent = info.text;
    valueEl.className = "iface-value" + (info.cls ? " " + info.cls : "");
    metaEl.textContent = info.meta;
  }

  function renderDisks(disks) {
    const items = (disks && Array.isArray(disks.items)) ? disks.items : [];
    const count = disks ? Number(disks.count || items.length) : 0;
    const totalHuman = (disks && disks.total_human) ? disks.total_human : "0 B";

    els.disksSummary.textContent = count > 0 ? count + " (" + totalHuman + ")" : "0";
    els.disksCountMeta.textContent = totalHuman;

    els.disksList.innerHTML = "";
    if (items.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-row";
      empty.textContent = "Диски не обнаружены";
      els.disksList.appendChild(empty);
      return;
    }

    items.forEach(function (disk) {
      const row = document.createElement("div");
      row.className = "disk-row";

      const name = document.createElement("div");
      name.className = "disk-name mono";
      name.textContent = "/dev/" + (disk.name || "");

      const model = document.createElement("div");
      model.className = "disk-model";
      model.textContent = disk.model || disk.transport || "Диск";

      const size = document.createElement("div");
      size.className = "disk-size mono";
      size.textContent = disk.size_human || "0 B";

      row.appendChild(name);
      row.appendChild(model);
      row.appendChild(size);
      els.disksList.appendChild(row);
    });
  }

  function refreshQrImage() {
    els.qrImage.src = "/api/qr.svg?t=" + Date.now();
  }

  function updateInternetBanner(reachable, sessionReady) {
    let text;
    if (!reachable) {
      text = INTERNET_BANNER_NO_INTERNET;
    } else if (!sessionReady) {
      text = INTERNET_BANNER_PENDING;
    } else {
      text = INTERNET_BANNER_ONLINE;
    }
    els.internetBanner.textContent = text;
    els.internetBanner.classList.toggle("is-ok", reachable);
  }

  function updateReadyPending(sessionReady, internetReachable) {
    els.readyPending.classList.toggle("is-hidden", sessionReady);
    els.readyContent.classList.toggle("is-hidden", !sessionReady);
    els.readyPendingTitle.textContent = internetReachable
      ? PENDING_TITLE_INTERNET
      : PENDING_TITLE_OFFLINE;
    els.readyPendingText.textContent = internetReachable
      ? PENDING_TEXT_INTERNET
      : PENDING_TEXT_OFFLINE;
  }

  function applyAutoView(internetReachable, sessionReady) {
    if (state.userChoseNetworkView) {
      return;
    }
    if (internetReachable) {
      // Internet is up: show the ready view, pending or complete.
      if (state.activeView !== "ready") {
        setView("ready", false);
      }
      return;
    }
    if (!sessionReady && state.activeView !== "network") {
      setView("network", false);
    }
  }

  async function fetchStatus() {
    try {
      const resp = await fetch("/api/status", { cache: "no-store" });
      if (!resp.ok) {
        return;
      }
      const data = await resp.json();
      els.hostnameLabel.textContent = data.hostname || "live-usb";

      const ifaces = data.interfaces || {};
      applyIfaceCard(els.statusEthernet, els.metaEthernet, formatIfaceStatus(ifaces.ethernet));
      applyIfaceCard(els.statusAndroid, els.metaAndroid, formatIfaceStatus(ifaces.android_usb));
      applyIfaceCard(els.statusIphone, els.metaIphone, formatIfaceStatus(ifaces.iphone_usb));

      const needsTrust = Boolean(ifaces.iphone_usb && ifaces.iphone_usb.needs_trust);
      els.iphoneTrustBanner.classList.toggle("is-hidden", !needsTrust);

      const session = data.session || {};
      const internet = data.internet || {};
      const internetReachable = Boolean(internet.reachable);
      const sessionReady = Boolean(session.ready);

      updateInternetBanner(internetReachable, sessionReady);
      updateReadyPending(sessionReady, internetReachable);

      const connectUrl = session.connect_url || "";
      els.sessionUrl.textContent = connectUrl || "Ожидание ссылки подключения";
      els.sessionToken.textContent = session.token || "Ожидание токена";
      els.sessionTunnel.textContent = session.tunnel_status || "offline";

      if (sessionReady && connectUrl !== state.lastConnectUrl) {
        state.lastConnectUrl = connectUrl;
        refreshQrImage();
      }

      renderDisks(data.disks);

      // Internet wins the view; no internet falls back to the network screen.
      applyAutoView(internetReachable, sessionReady);
    } catch (_err) {
      // Keep previous rendered state on transient polling errors
    }
  }

  function renderWifiList(networks, errorText) {
    els.wifiList.innerHTML = "";

    if (errorText) {
      const errRow = document.createElement("div");
      errRow.className = "empty-row";
      errRow.textContent = errorText;
      els.wifiList.appendChild(errRow);
      els.wifiScanMeta.textContent = "Ошибка сканирования";
      return;
    }

    if (!networks || networks.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-row";
      empty.textContent = "Сети Wi-Fi не найдены";
      els.wifiList.appendChild(empty);
      els.wifiScanMeta.textContent = "0 сетей";
      return;
    }

    els.wifiScanMeta.textContent = "Найдено: " + networks.length;

    networks.forEach(function (net) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "wifi-row";
      row.setAttribute("role", "listitem");

      const ssidEl = document.createElement("span");
      ssidEl.className = "wifi-ssid";
      ssidEl.textContent = net.ssid;

      const secEl = document.createElement("span");
      secEl.className = "wifi-sec";
      secEl.textContent = net.security || "OPEN";

      const sigEl = document.createElement("span");
      sigEl.className = "wifi-signal mono";
      sigEl.textContent = String(net.signal) + "%";

      const stateEl = document.createElement("span");
      stateEl.className = "wifi-state" + (net.in_use ? " is-ok" : "");
      stateEl.textContent = net.in_use ? "Подключено" : "Выбрать";

      row.appendChild(ssidEl);
      row.appendChild(secEl);
      row.appendChild(sigEl);
      row.appendChild(stateEl);

      row.addEventListener("click", function () {
        openWifiModal(net.ssid, false);
      });

      els.wifiList.appendChild(row);
    });
  }

  async function scanWifi() {
    els.btnRefreshWifi.disabled = true;
    els.wifiScanMeta.textContent = "Сканирование...";
    try {
      const resp = await fetch("/api/wifi/scan", { cache: "no-store" });
      const data = await resp.json();
      renderWifiList(data.networks || [], data.ok ? "" : (data.error || "Ошибка сканирования"));
    } catch (_err) {
      renderWifiList([], "Сервер недоступен");
    } finally {
      els.btnRefreshWifi.disabled = false;
    }
  }

  function openWifiModal(ssid, isHidden) {
    els.inputSsid.value = ssid || "";
    els.inputSsid.readOnly = Boolean(ssid && !isHidden);
    els.inputPassword.value = "";
    els.inputPassword.type = "password";
    els.btnTogglePassword.textContent = "Показать";
    els.inputHidden.checked = Boolean(isHidden);
    els.modalTitle.textContent = isHidden ? "Скрытая сеть Wi-Fi" : "Подключение к Wi-Fi";
    els.modalError.classList.add("is-hidden");
    els.modalError.textContent = "";
    els.modalBackdrop.classList.remove("is-hidden");

    state.activeInput = (ssid && !isHidden) ? els.inputPassword : els.inputSsid;
    state.activeInput.focus();
  }

  function closeWifiModal() {
    els.modalBackdrop.classList.add("is-hidden");
    els.oskContainer.classList.add("is-hidden");
  }

  async function submitWifiConnect(event) {
    event.preventDefault();
    const ssid = els.inputSsid.value.trim();
    const password = els.inputPassword.value;
    const hidden = Boolean(els.inputHidden.checked);

    if (!ssid) {
      els.modalError.textContent = "Введите имя сети (SSID)";
      els.modalError.classList.remove("is-hidden");
      return;
    }

    els.btnModalSubmit.disabled = true;
    els.modalError.classList.add("is-hidden");
    try {
      const resp = await fetch("/api/wifi/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ssid: ssid, password: password, hidden: hidden }),
      });
      const data = await resp.json();
      if (data.ok) {
        closeWifiModal();
        state.userChoseNetworkView = false;
        await fetchStatus();
        await scanWifi();
      } else {
        els.modalError.textContent = data.message || "Не удалось подключиться";
        els.modalError.classList.remove("is-hidden");
      }
    } catch (_err) {
      els.modalError.textContent = "Ошибка связи с локальным сервером";
      els.modalError.classList.remove("is-hidden");
    } finally {
      els.btnModalSubmit.disabled = false;
    }
  }

  async function regenerateSession() {
    els.btnNewToken.disabled = true;
    els.regenFeedback.classList.remove("is-hidden");
    els.regenFeedback.textContent = "Запрос нового токена...";
    try {
      const resp = await fetch("/api/session/regenerate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await resp.json();
      els.regenFeedback.textContent = data.message || (data.ok ? "Токен обновлён" : "Ошибка обновления");
      refreshQrImage();
      await fetchStatus();
    } catch (_err) {
      els.regenFeedback.textContent = "Не удалось обновить токен";
    } finally {
      els.btnNewToken.disabled = false;
    }
  }

  async function openTerminal(button, feedbackEl) {
    const trigger = button || els.btnTerminal;
    const feedback = feedbackEl || els.terminalFeedbackReady;
    trigger.disabled = true;
    feedback.classList.remove("is-hidden");
    feedback.textContent = "Открытие терминала...";
    try {
      const resp = await fetch("/api/terminal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      let data = {};
      try {
        data = await resp.json();
      } catch (_parseErr) {
        data = {};
      }
      feedback.textContent = data.message || data.error ||
        (data.ok ? "Терминал открыт" : "Не удалось открыть терминал");
    } catch (_err) {
      feedback.textContent = "Не удалось открыть терминал";
    } finally {
      trigger.disabled = false;
    }
  }

  function showAppsMessage(text) {
    els.appsFeedback.textContent = text;
    els.appsFeedback.classList.remove("is-hidden");
  }

  function renderAppsError(text) {
    els.appsList.innerHTML = "";
    const row = document.createElement("div");
    row.className = "empty-row";
    row.textContent = text;
    els.appsList.appendChild(row);
  }

  function renderApps(apps) {
    els.appsList.innerHTML = "";
    if (!apps || apps.length === 0) {
      renderAppsError("Приложения не найдены");
      return;
    }

    apps.forEach(function (app) {
      const available = app.available !== false;
      const label = app.label || app.id || "Приложение";

      const item = document.createElement("button");
      item.type = "button";
      item.className = "app-item";
      item.setAttribute("role", "listitem");
      item.disabled = !available;
      item.dataset.appId = app.id || "";
      item.title = available ? label : (app.reason || "Недоступно на этой системе");

      const labelEl = document.createElement("span");
      labelEl.className = "app-label";
      labelEl.textContent = label;

      const noteEl = document.createElement("span");
      noteEl.className = "app-note";
      noteEl.textContent = available ? "Запустить" : (app.reason || "Недоступно");

      item.appendChild(labelEl);
      item.appendChild(noteEl);

      if (available) {
        item.addEventListener("click", function () {
          launchApp(app.id, label);
        });
      }

      els.appsList.appendChild(item);
    });
  }

  async function launchApp(appId, label) {
    if (!appId) {
      return;
    }
    showAppsMessage("Запуск: " + (label || appId) + "…");
    try {
      const resp = await fetch("/api/apps/launch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: appId }),
      });
      let data = {};
      try {
        data = await resp.json();
      } catch (_parseErr) {
        data = {};
      }
      if (data.ok) {
        const parts = [data.message || ("Запущено: " + (label || appId))];
        if (data.pid) {
          parts.push("pid " + data.pid);
        }
        if (Array.isArray(data.command) && data.command.length > 0) {
          parts.push(data.command.join(" "));
        }
        showAppsMessage(parts.join(" · "));
      } else {
        showAppsMessage(data.message || "Не удалось запустить приложение");
      }
    } catch (_err) {
      showAppsMessage("Не удалось запустить приложение");
    }
  }

  async function openAppsModal() {
    els.appsBackdrop.classList.remove("is-hidden");
    els.appsFeedback.classList.add("is-hidden");
    els.appsFeedback.textContent = "";
    els.appsList.innerHTML = "";

    const loading = document.createElement("div");
    loading.className = "empty-row";
    loading.textContent = "Загрузка списка приложений…";
    els.appsList.appendChild(loading);

    try {
      const resp = await fetch("/api/apps", { cache: "no-store" });
      let data = {};
      try {
        data = await resp.json();
      } catch (_parseErr) {
        data = {};
      }
      if (!resp.ok || !data.ok) {
        renderAppsError(data.message || "Список приложений недоступен");
        return;
      }
      renderApps(data.apps || []);
    } catch (_err) {
      renderAppsError("Сервер недоступен");
    }
  }

  function closeAppsModal() {
    els.appsBackdrop.classList.add("is-hidden");
  }

  const OSK_ROWS_LOWER = [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "_"],
    ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
    ["SHIFT", "z", "x", "c", "v", "b", "n", "m", ".", "BKSP"],
    ["SPACE", "CLEAR"],
  ];

  const OSK_ROWS_UPPER = [
    ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")", "+", "="],
    ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L"],
    ["SHIFT", "Z", "X", "C", "V", "B", "N", "M", ":", "BKSP"],
    ["SPACE", "CLEAR"],
  ];

  function renderOsk() {
    const rows = state.oskShift ? OSK_ROWS_UPPER : OSK_ROWS_LOWER;
    els.oskContainer.innerHTML = "";

    rows.forEach(function (keys) {
      const rowEl = document.createElement("div");
      rowEl.className = "osk-row";

      keys.forEach(function (key) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "osk-key";

        if (key === "SHIFT") {
          btn.textContent = "Shift";
          btn.classList.add("osk-key-wide");
          if (state.oskShift) {
            btn.classList.add("is-active");
          }
        } else if (key === "BKSP") {
          btn.textContent = "Удалить";
          btn.classList.add("osk-key-wide");
        } else if (key === "SPACE") {
          btn.textContent = "Пробел";
          btn.classList.add("osk-key-space");
        } else if (key === "CLEAR") {
          btn.textContent = "Очистить";
          btn.classList.add("osk-key-wide");
        } else {
          btn.textContent = key;
        }

        btn.addEventListener("click", function () {
          handleOskKey(key);
        });
        rowEl.appendChild(btn);
      });

      els.oskContainer.appendChild(rowEl);
    });
  }

  function handleOskKey(key) {
    const target = state.activeInput || els.inputPassword;
    if (!target || target.readOnly) {
      return;
    }
    if (key === "SHIFT") {
      state.oskShift = !state.oskShift;
      renderOsk();
      return;
    }
    if (key === "BKSP") {
      target.value = target.value.slice(0, -1);
    } else if (key === "CLEAR") {
      target.value = "";
    } else if (key === "SPACE") {
      target.value += " ";
    } else {
      target.value += key;
    }
    target.focus();
  }

  els.navNetwork.addEventListener("click", function () {
    setView("network", true);
  });
  els.navReady.addEventListener("click", function () {
    setView("ready", true);
  });
  els.btnRefreshWifi.addEventListener("click", scanWifi);
  els.btnHiddenWifi.addEventListener("click", function () {
    openWifiModal("", true);
  });
  els.btnChangeWifi.addEventListener("click", function () {
    setView("network", true);
  });
  els.btnNewToken.addEventListener("click", regenerateSession);
  els.btnTerminal.addEventListener("click", function () {
    openTerminal(els.btnTerminal, els.terminalFeedbackReady);
  });
  els.btnTerminalNetwork.addEventListener("click", function () {
    openTerminal(els.btnTerminalNetwork, els.terminalFeedbackNetwork);
  });
  els.btnApps.addEventListener("click", openAppsModal);
  els.btnAppsNetwork.addEventListener("click", openAppsModal);
  els.btnAppsClose.addEventListener("click", closeAppsModal);
  els.btnAppsCancel.addEventListener("click", closeAppsModal);
  els.btnModalClose.addEventListener("click", closeWifiModal);
  els.btnModalCancel.addEventListener("click", closeWifiModal);
  els.wifiForm.addEventListener("submit", submitWifiConnect);

  els.inputSsid.addEventListener("focus", function () {
    state.activeInput = els.inputSsid;
  });
  els.inputPassword.addEventListener("focus", function () {
    state.activeInput = els.inputPassword;
  });

  els.btnTogglePassword.addEventListener("click", function () {
    const showing = els.inputPassword.type === "text";
    els.inputPassword.type = showing ? "password" : "text";
    els.btnTogglePassword.textContent = showing ? "Показать" : "Скрыть";
  });

  els.btnToggleKeyboard.addEventListener("click", function () {
    const willShow = els.oskContainer.classList.contains("is-hidden");
    els.oskContainer.classList.toggle("is-hidden", !willShow);
    if (willShow) {
      renderOsk();
    }
  });

  fetchStatus();
  scanWifi();
  window.setInterval(fetchStatus, 3000);
})();
