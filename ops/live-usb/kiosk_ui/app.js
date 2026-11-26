(function () {
  "use strict";

  const state = {
    activeView: "network",
    userChoseNetworkView: false,
    lastConnectUrl: "",
    oskShift: false,
    activeInput: null,
    networkTab: "current",
    networkLinkKey: "",
    networkLinkKeyVisible: false,
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
    btnNetworkSettings: document.getElementById("btn-network-settings"),
    networkBackdrop: document.getElementById("network-settings-backdrop"),
    btnNetworkSettingsClose: document.getElementById("btn-network-settings-close"),
    networkTabButtons: Array.prototype.slice.call(
      document.querySelectorAll("[data-network-tab]")
    ),
    networkPanels: {
      current: document.getElementById("net-panel-current"),
      ip: document.getElementById("net-panel-ip"),
      pppoe: document.getElementById("net-panel-pppoe"),
      wireguard: document.getElementById("net-panel-wireguard"),
      openvpn: document.getElementById("net-panel-openvpn"),
      amnezia: document.getElementById("net-panel-amnezia"),
    },
    netCurrentFeedback: document.getElementById("net-current-feedback"),
    netCurrentHostname: document.getElementById("net-current-hostname"),
    netCurrentNodeId: document.getElementById("net-current-node-id"),
    netCurrentLinkKey: document.getElementById("net-current-link-key"),
    btnToggleLinkKey: document.getElementById("btn-toggle-link-key"),
    netCurrentInterfaces: document.getElementById("net-current-interfaces"),
    netCurrentRoute: document.getElementById("net-current-route"),
    netCurrentDns: document.getElementById("net-current-dns"),
    netIpForm: document.getElementById("net-ip-form"),
    netIpInterface: document.getElementById("net-ip-interface"),
    netIpModeAuto: document.getElementById("net-ip-mode-auto"),
    netIpModeManual: document.getElementById("net-ip-mode-manual"),
    netIpStaticFields: document.getElementById("net-ip-static-fields"),
    netIpAddress: document.getElementById("net-ip-address"),
    netIpPrefix: document.getElementById("net-ip-prefix"),
    netIpGateway: document.getElementById("net-ip-gateway"),
    netIpDns: document.getElementById("net-ip-dns"),
    netIpFeedback: document.getElementById("net-ip-feedback"),
    btnNetIpSubmit: document.getElementById("btn-net-ip-submit"),
    netPppoeForm: document.getElementById("net-pppoe-form"),
    netPppoeUsername: document.getElementById("net-pppoe-username"),
    netPppoePassword: document.getElementById("net-pppoe-password"),
    netPppoeService: document.getElementById("net-pppoe-service"),
    netPppoeFeedback: document.getElementById("net-pppoe-feedback"),
    btnNetPppoeSubmit: document.getElementById("btn-net-pppoe-submit"),
    netWgForm: document.getElementById("net-wg-form"),
    netWgName: document.getElementById("net-wg-name"),
    netWgPrivateKey: document.getElementById("net-wg-private-key"),
    netWgAddress: document.getElementById("net-wg-address"),
    netWgPrefix: document.getElementById("net-wg-prefix"),
    netWgPeerPublicKey: document.getElementById("net-wg-peer-public-key"),
    netWgEndpoint: document.getElementById("net-wg-endpoint"),
    netWgAllowedIps: document.getElementById("net-wg-allowed-ips"),
    netWgKeepalive: document.getElementById("net-wg-keepalive"),
    netWgFeedback: document.getElementById("net-wg-feedback"),
    btnNetWgSubmit: document.getElementById("btn-net-wg-submit"),
    netOpenvpnForm: document.getElementById("net-openvpn-form"),
    netOpenvpnName: document.getElementById("net-openvpn-name"),
    netOpenvpnConfig: document.getElementById("net-openvpn-config"),
    netOpenvpnFeedback: document.getElementById("net-openvpn-feedback"),
    btnNetOpenvpnSubmit: document.getElementById("btn-net-openvpn-submit"),
    netAwgForm: document.getElementById("net-awg-form"),
    netAwgName: document.getElementById("net-awg-name"),
    netAwgConfig: document.getElementById("net-awg-config"),
    netAwgFeedback: document.getElementById("net-awg-feedback"),
    btnNetAwgSubmit: document.getElementById("btn-net-awg-submit"),
    btnNetAwgDown: document.getElementById("btn-net-awg-down"),
    netAwgAvailable: document.getElementById("net-awg-available"),
    netAwgDir: document.getElementById("net-awg-dir"),
    netAwgFiles: document.getElementById("net-awg-files"),
    netAwgIfaces: document.getElementById("net-awg-ifaces"),
    netAwgShow: document.getElementById("net-awg-show"),
    netAwgSubForm: document.getElementById("net-awg-sub-form"),
    netAwgSubUrl: document.getElementById("net-awg-sub-url"),
    netAwgQr: document.getElementById("net-awg-qr"),
    netAwgQrFrame: document.getElementById("net-awg-qr-frame"),
    netAwgQrHint: document.getElementById("net-awg-qr-hint"),
    btnNetAwgSubLoad: document.getElementById("btn-net-awg-sub-load"),
    btnNetAwgSubCopy: document.getElementById("btn-net-awg-sub-copy"),
  };

  const INTERNET_BANNER_NO_INTERNET =
    "Интернет не обнаружен. Подключите Wi-Fi, кабель или USB-модем.";
  const INTERNET_BANNER_PENDING =
    "Интернет есть. Получаем токен и QR…";
  const INTERNET_BANNER_ONLINE = "Интернет подключён";

  const PENDING_TITLE_INTERNET = "Интернет есть, получаем токен…";
  const PENDING_TITLE_OFFLINE =
    "Потеряно соединение с интернетом. Ожидание восстановления сети…";
  const PENDING_TEXT_INTERNET =
    "QR-код и ссылка подключения появятся автоматически, как только сессия будет готова.";
  const PENDING_TEXT_OFFLINE =
    "Старые QR-код и ссылка скрыты. Как только интернет восстановится, туннель переподключится и появится новый QR-код.";

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
    const effectiveReady = Boolean(sessionReady && internetReachable);
    els.readyPending.classList.toggle("is-hidden", effectiveReady);
    els.readyContent.classList.toggle("is-hidden", !effectiveReady);
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
    if (state.activeView !== "network") {
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
      const effectiveReady = Boolean(sessionReady && internetReachable);

      updateInternetBanner(internetReachable, effectiveReady);
      updateReadyPending(sessionReady, internetReachable);

      const connectUrl = session.connect_url || "";
      els.sessionUrl.textContent = effectiveReady && connectUrl ? connectUrl : "Ожидание ссылки подключения";
      els.sessionToken.textContent = effectiveReady && session.token ? session.token : "Ожидание токена";
      const tunMode = session.tunnel_mode || "cloudflare";
      const tunStatus = internetReachable ? (session.tunnel_status || "offline") : "offline";
      const tunErr = session.tunnel_error ? (" — " + session.tunnel_error) : "";
      els.sessionTunnel.textContent = (tunMode === "cloudflare" ? "Cloudflare Quick Tunnel · " : "") + tunStatus + tunErr;

      if (!effectiveReady) {
        state.lastConnectUrl = "";
      } else if (connectUrl !== state.lastConnectUrl) {
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

  // --- Network settings dialog --------------------------------------------
  // Secrets rule: the link key lives in memory only, stays masked in the DOM
  // until the operator clicks "Показать", and submitted passwords/private keys
  // are wiped from the fields right away. Nothing is ever logged.

  const MASKED_LINK_KEY = "••••••••";
  const NETWORK_TABS = ["current", "ip", "pppoe", "wireguard", "openvpn", "amnezia"];
  const DEFAULT_OPENVPN_NAME = "rhm-openvpn";
  const DEFAULT_WIREGUARD_KEEPALIVE = 25;
  const DEFAULT_AMNEZIAWG_NAME = "rhm-awg";

  function setNetworkFeedback(feedbackEl, text, kind) {
    if (!feedbackEl) {
      return;
    }
    feedbackEl.textContent = text || "";
    feedbackEl.classList.remove("is-ok", "is-error");
    if (text && kind) {
      feedbackEl.classList.add(kind);
    }
    feedbackEl.classList.toggle("is-hidden", !text);
  }

  function clearNetworkFeedback() {
    setNetworkFeedback(els.netCurrentFeedback, "", "");
    setNetworkFeedback(els.netIpFeedback, "", "");
    setNetworkFeedback(els.netPppoeFeedback, "", "");
    setNetworkFeedback(els.netWgFeedback, "", "");
    setNetworkFeedback(els.netOpenvpnFeedback, "", "");
    setNetworkFeedback(els.netAwgFeedback, "", "");
  }

  function resetNetworkSecretFields() {
    els.netPppoePassword.value = "";
    els.netWgPrivateKey.value = "";
    els.netOpenvpnConfig.value = "";
    els.netAwgConfig.value = "";
    els.netAwgSubUrl.value = "";
    updateAmneziaQr();
  }

  function setNetworkTab(tabName) {
    const target = NETWORK_TABS.indexOf(tabName) === -1 ? "current" : tabName;
    state.networkTab = target;
    NETWORK_TABS.forEach(function (name) {
      const panel = els.networkPanels[name];
      if (panel) {
        panel.classList.toggle("is-hidden", name !== target);
      }
    });
    els.networkTabButtons.forEach(function (btn) {
      const isActive = btn.dataset.networkTab === target;
      btn.classList.toggle("is-active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    if (target === "amnezia") {
      loadAmneziaState();
    }
  }

  function renderNetworkLinkKey() {
    const hasKey = Boolean(state.networkLinkKey);
    const revealed = state.networkLinkKeyVisible && hasKey;
    els.btnToggleLinkKey.disabled = !hasKey;
    if (!hasKey) {
      els.netCurrentLinkKey.textContent = "—";
      els.netCurrentLinkKey.classList.remove("is-masked");
      els.btnToggleLinkKey.textContent = "Показать";
      return;
    }
    els.netCurrentLinkKey.textContent = revealed
      ? state.networkLinkKey
      : MASKED_LINK_KEY;
    els.netCurrentLinkKey.classList.toggle("is-masked", !revealed);
    els.btnToggleLinkKey.textContent = revealed ? "Скрыть" : "Показать";
  }

  function listText(value, fallback) {
    const empty = fallback || "—";
    if (Array.isArray(value)) {
      const parts = value.filter(Boolean);
      return parts.length > 0 ? parts.join(", ") : empty;
    }
    return value || empty;
  }

  function interfaceDnsText(iface) {
    return listText(iface.dns);
  }

  function describeRoute(route) {
    if (!route) {
      return "—";
    }
    if (typeof route === "string") {
      return route || "—";
    }
    const parts = [];
    if (route.destination) {
      parts.push(route.destination);
    }
    if (route.gateway) {
      parts.push("via " + route.gateway);
    }
    const device = route.device || route.dev;
    if (device) {
      parts.push("dev " + device);
    }
    if (route.metric !== undefined && route.metric !== null && route.metric !== "") {
      parts.push("metric " + route.metric);
    }
    return parts.join(" ") || "—";
  }

  function defaultRouteText(routes) {
    const items = Array.isArray(routes) ? routes : [];
    let preferred = null;
    items.forEach(function (route) {
      if (preferred || !route) {
        return;
      }
      const destination = route.destination || "";
      if (route.default === true || destination === "default" || destination === "0.0.0.0/0") {
        preferred = route;
      }
    });
    return describeRoute(preferred || items[0]);
  }

  function appendNetworkRow(parent, label, value) {
    const row = document.createElement("div");
    row.className = "net-kv";
    const keyEl = document.createElement("span");
    keyEl.className = "net-k";
    keyEl.textContent = label;
    const valueEl = document.createElement("span");
    valueEl.className = "net-v mono";
    valueEl.textContent = value || "—";
    row.appendChild(keyEl);
    row.appendChild(valueEl);
    parent.appendChild(row);
  }

  function renderNetworkInterfaces(interfaces) {
    const items = Array.isArray(interfaces) ? interfaces : [];
    els.netCurrentInterfaces.innerHTML = "";
    if (items.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-row";
      empty.textContent = "Интерфейсы не обнаружены";
      els.netCurrentInterfaces.appendChild(empty);
      return;
    }

    items.forEach(function (iface) {
      const box = document.createElement("div");
      box.className = "net-iface";
      box.setAttribute("role", "listitem");

      const head = document.createElement("div");
      head.className = "net-iface-head";
      const nameEl = document.createElement("span");
      nameEl.className = "net-iface-name mono";
      nameEl.textContent = iface.name || iface.interface || "—";
      const stateEl = document.createElement("span");
      stateEl.className = "net-iface-state";
      const rawState = String(iface.state || (iface.connected ? "up" : "")).trim();
      stateEl.textContent = rawState || "unknown";
      if (rawState.toLowerCase() === "up" || iface.connected === true) {
        stateEl.classList.add("is-ok");
      }
      head.appendChild(nameEl);
      head.appendChild(stateEl);
      box.appendChild(head);

      appendNetworkRow(box, "MAC", iface.mac);
      appendNetworkRow(box, "Соединение", iface.connection);
      appendNetworkRow(box, "IPv4", iface.ipv4 || iface.ip);
      appendNetworkRow(box, "Шлюз", iface.gateway);
      appendNetworkRow(box, "DNS", interfaceDnsText(iface));

      els.netCurrentInterfaces.appendChild(box);
    });
  }

  function renderNetworkInterfaceOptions(interfaces) {
    const items = Array.isArray(interfaces) ? interfaces : [];
    els.netIpInterface.innerHTML = "";
    if (items.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "Интерфейсы не обнаружены";
      els.netIpInterface.appendChild(opt);
      return;
    }
    items.forEach(function (iface) {
      const name = iface.name || iface.interface || "";
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name + (iface.state ? " (" + iface.state + ")" : "");
      els.netIpInterface.appendChild(opt);
    });
  }

  function renderNetworkInfo(data) {
    els.netCurrentHostname.textContent = data.hostname || "—";
    els.netCurrentNodeId.textContent = data.node_id || "—";
    state.networkLinkKey = data.link_key || "";
    renderNetworkLinkKey();
    renderNetworkInterfaces(data.interfaces);
    renderNetworkInterfaceOptions(data.interfaces);
    els.netCurrentRoute.textContent = defaultRouteText(data.routes);
    els.netCurrentDns.textContent = listText(data.dns);
  }

  async function loadNetworkInfo() {
    setNetworkFeedback(els.netCurrentFeedback, "Загрузка текущих настроек…", "");
    try {
      const resp = await fetch("/api/network", { cache: "no-store" });
      let data = {};
      try {
        data = await resp.json();
      } catch (_parseErr) {
        data = {};
      }
      if (!resp.ok || !data.ok) {
        setNetworkFeedback(
          els.netCurrentFeedback,
          data.message || data.error || "Текущие настройки недоступны",
          "is-error"
        );
        return;
      }
      renderNetworkInfo(data);
      setNetworkFeedback(els.netCurrentFeedback, "", "");
    } catch (_err) {
      setNetworkFeedback(els.netCurrentFeedback, "Сервер недоступен", "is-error");
    }
  }

  function syncNetworkIpMode() {
    const manual = els.netIpModeManual.checked;
    els.netIpStaticFields.classList.toggle("is-hidden", !manual);
  }

  function splitCommaList(text) {
    return String(text || "")
      .split(",")
      .map(function (part) {
        return part.trim();
      })
      .filter(Boolean);
  }

  async function postNetworkJson(path, payload, button, feedbackEl) {
    if (button) {
      button.disabled = true;
    }
    setNetworkFeedback(feedbackEl, "Отправка…", "");
    try {
      const resp = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      let data = {};
      try {
        data = await resp.json();
      } catch (_parseErr) {
        data = {};
      }
      const ok = resp.ok && data.ok !== false;
      const message = data.message || data.error ||
        (ok ? "Настройки применены" : "Не удалось применить настройки");
      setNetworkFeedback(feedbackEl, message, ok ? "is-ok" : "is-error");
      return ok;
    } catch (_err) {
      setNetworkFeedback(feedbackEl, "Ошибка связи с локальным сервером", "is-error");
      return false;
    } finally {
      if (button) {
        button.disabled = false;
      }
    }
  }

  function openNetworkSettings() {
    els.networkBackdrop.classList.remove("is-hidden");
    state.networkLinkKeyVisible = false;
    state.networkLinkKey = "";
    renderNetworkLinkKey();
    clearNetworkFeedback();
    resetNetworkSecretFields();
    renderAmneziaState({});
    syncNetworkIpMode();
    setNetworkTab("current");
    loadNetworkInfo();
  }

  function closeNetworkSettings() {
    els.networkBackdrop.classList.add("is-hidden");
    state.networkLinkKey = "";
    state.networkLinkKeyVisible = false;
    renderNetworkLinkKey();
    resetNetworkSecretFields();
    clearNetworkFeedback();
  }

  async function submitNetworkIp(event) {
    event.preventDefault();
    const mode = els.netIpModeManual.checked ? "manual" : "auto";
    const payload = {
      interface: els.netIpInterface.value,
      mode: mode,
      address: els.netIpAddress.value.trim(),
      prefix: els.netIpPrefix.value.trim(),
      gateway: els.netIpGateway.value.trim(),
      dns: splitCommaList(els.netIpDns.value),
    };
    if (!payload.interface) {
      setNetworkFeedback(els.netIpFeedback, "Выберите интерфейс", "is-error");
      return;
    }
    if (payload.mode === "manual" && !payload.address) {
      setNetworkFeedback(els.netIpFeedback, "Введите IPv4-адрес", "is-error");
      return;
    }

    const ok = await postNetworkJson(
      "/api/network/ip",
      payload,
      els.btnNetIpSubmit,
      els.netIpFeedback
    );
    if (ok) {
      await loadNetworkInfo();
    }
  }

  async function submitNetworkPppoe(event) {
    event.preventDefault();
    const payload = {
      username: els.netPppoeUsername.value.trim(),
      password: els.netPppoePassword.value,
      service: els.netPppoeService.value.trim(),
    };
    if (!payload.username) {
      setNetworkFeedback(els.netPppoeFeedback, "Введите имя пользователя PPPoE", "is-error");
      return;
    }
    if (!payload.password) {
      setNetworkFeedback(els.netPppoeFeedback, "Введите пароль PPPoE", "is-error");
      return;
    }

    await postNetworkJson(
      "/api/network/pppoe",
      payload,
      els.btnNetPppoeSubmit,
      els.netPppoeFeedback
    );
    els.netPppoePassword.value = "";
  }

  async function submitNetworkWireguard(event) {
    event.preventDefault();
    const keepaliveRaw = els.netWgKeepalive.value.trim();
    const payload = {
      name: els.netWgName.value.trim(),
      private_key: els.netWgPrivateKey.value,
      address: els.netWgAddress.value.trim(),
      prefix: els.netWgPrefix.value.trim(),
      peer_public_key: els.netWgPeerPublicKey.value.trim(),
      endpoint: els.netWgEndpoint.value.trim(),
      allowed_ips: splitCommaList(els.netWgAllowedIps.value),
      keepalive: keepaliveRaw === ""
        ? DEFAULT_WIREGUARD_KEEPALIVE
        : Number(keepaliveRaw),
    };
    if (!payload.name) {
      setNetworkFeedback(els.netWgFeedback, "Введите имя подключения", "is-error");
      return;
    }
    if (!payload.private_key) {
      setNetworkFeedback(els.netWgFeedback, "Введите приватный ключ", "is-error");
      return;
    }
    if (!payload.address) {
      setNetworkFeedback(els.netWgFeedback, "Введите адрес интерфейса", "is-error");
      return;
    }
    if (!payload.peer_public_key) {
      setNetworkFeedback(els.netWgFeedback, "Введите публичный ключ пира", "is-error");
      return;
    }

    await postNetworkJson(
      "/api/network/wireguard",
      payload,
      els.btnNetWgSubmit,
      els.netWgFeedback
    );
    els.netWgPrivateKey.value = "";
  }

  async function submitNetworkOpenvpn(event) {
    event.preventDefault();
    const config = els.netOpenvpnConfig.value.trim();
    const payload = {
      name: els.netOpenvpnName.value.trim() || DEFAULT_OPENVPN_NAME,
      config: config,
    };
    if (!payload.config) {
      setNetworkFeedback(els.netOpenvpnFeedback, "Вставьте конфигурацию .ovpn", "is-error");
      return;
    }

    await postNetworkJson(
      "/api/network/openvpn",
      payload,
      els.btnNetOpenvpnSubmit,
      els.netOpenvpnFeedback
    );
    els.netOpenvpnConfig.value = "";
  }

  // --- AmneziaWG import, subscription and QR ------------------------------
  // The pasted INI may contain PrivateKey/PresharedKey values: it is only
  // posted to the local server, wiped after a successful import, and any
  // status text is redacted before it reaches the DOM.

  function amneziaFileNames(files) {
    const items = Array.isArray(files) ? files : [];
    return items
      .map(function (entry) {
        if (typeof entry === "string") {
          return entry;
        }
        if (entry && typeof entry === "object") {
          return entry.name || entry.file || "";
        }
        return "";
      })
      .filter(Boolean);
  }

  function amneziaStatusText(status) {
    if (!status) {
      return "Статус недоступен";
    }
    if (typeof status === "string") {
      return status.trim() || "Статус недоступен";
    }
    const output = typeof status.output === "string" ? status.output.trim() : "";
    if (output) {
      return output;
    }
    return status.message || "Статус недоступен";
  }

  function redactAmneziaSecrets(text) {
    return String(text || "").replace(
      /^(\s*)(PrivateKey|PresharedKey)(\s*=\s*).*$/gim,
      function (_line, indent, key, separator) {
        return indent + key + separator + "[скрыто]";
      }
    );
  }

  function renderAmneziaState(data) {
    const source = data || {};

    if (source.awg_available === true) {
      els.netAwgAvailable.textContent = "доступен";
    } else if (source.awg_available === false) {
      els.netAwgAvailable.textContent = "недоступен";
    } else {
      els.netAwgAvailable.textContent = "—";
    }

    els.netAwgDir.textContent = source.config_dir || "—";

    const files = amneziaFileNames(source.files);
    els.netAwgFiles.textContent = files.length > 0 ? files.join(", ") : "нет конфигураций";

    const status = source.status || {};
    const interfaces = Array.isArray(status.interfaces)
      ? status.interfaces.filter(Boolean)
      : [];
    els.netAwgIfaces.textContent = interfaces.length > 0 ? interfaces.join(", ") : "нет";

    els.netAwgShow.textContent = redactAmneziaSecrets(amneziaStatusText(source.status));
  }

  async function loadAmneziaState(preserveFeedback) {
    try {
      const resp = await fetch("/api/network/amnezia", { cache: "no-store" });
      let data = {};
      try {
        data = await resp.json();
      } catch (_parseErr) {
        data = {};
      }
      if (!resp.ok || data.ok === false) {
        renderAmneziaState({});
        setNetworkFeedback(
          els.netAwgFeedback,
          data.message || data.error || "Состояние AmneziaWG недоступно",
          "is-error"
        );
        return;
      }
      renderAmneziaState(data);
      if (!preserveFeedback) {
        setNetworkFeedback(els.netAwgFeedback, "", "");
      }
    } catch (_err) {
      renderAmneziaState({});
      setNetworkFeedback(els.netAwgFeedback, "Сервер недоступен", "is-error");
    }
  }

  let amneziaQrTimer = null;

  function scheduleAmneziaQr() {
    if (amneziaQrTimer !== null) {
      window.clearTimeout(amneziaQrTimer);
    }
    amneziaQrTimer = window.setTimeout(function () {
      amneziaQrTimer = null;
      updateAmneziaQr();
    }, 400);
  }

  function flushAmneziaQr() {
    if (amneziaQrTimer !== null) {
      window.clearTimeout(amneziaQrTimer);
      amneziaQrTimer = null;
    }
    updateAmneziaQr();
  }

  function updateAmneziaQr() {
    const url = els.netAwgSubUrl.value.trim();
    if (!url) {
      els.netAwgQrFrame.classList.add("is-hidden");
      els.netAwgQr.removeAttribute("src");
      els.netAwgQrHint.textContent = "QR-код появится после ввода HTTPS-ссылки подписки.";
      els.netAwgQrHint.classList.remove("is-warn");
      return;
    }
    els.netAwgQr.src = "/api/network/subscription/qr?url=" + encodeURIComponent(url);
    els.netAwgQrFrame.classList.remove("is-hidden");
    els.netAwgQrHint.textContent = "QR-код ссылки подписки";
    els.netAwgQrHint.classList.remove("is-warn");
  }

  async function submitNetworkAmnezia(event) {
    event.preventDefault();
    const name = els.netAwgName.value.trim() || DEFAULT_AMNEZIAWG_NAME;
    const config = els.netAwgConfig.value.trim();
    const payload = { name: name, config: config };
    if (!payload.config) {
      setNetworkFeedback(els.netAwgFeedback, "Вставьте конфигурацию AmneziaWG", "is-error");
      return;
    }

    const ok = await postNetworkJson(
      "/api/network/amnezia",
      payload,
      els.btnNetAwgSubmit,
      els.netAwgFeedback
    );
    if (ok) {
      els.netAwgConfig.value = "";
      await loadAmneziaState(true);
    }
  }

  async function stopNetworkAmnezia() {
    const name = els.netAwgName.value.trim() || DEFAULT_AMNEZIAWG_NAME;
    const payload = { name: name };
    const ok = await postNetworkJson(
      "/api/network/amnezia/down",
      payload,
      els.btnNetAwgDown,
      els.netAwgFeedback
    );
    if (ok) {
      await loadAmneziaState(true);
    }
  }

  async function submitNetworkSubscription(event) {
    event.preventDefault();
    const url = els.netAwgSubUrl.value.trim();
    const name = els.netAwgName.value.trim() || DEFAULT_AMNEZIAWG_NAME;
    const payload = { url: url, name: name };
    if (!payload.url) {
      setNetworkFeedback(els.netAwgFeedback, "Введите URL подписки", "is-error");
      return;
    }

    flushAmneziaQr();
    const ok = await postNetworkJson(
      "/api/network/subscription",
      payload,
      els.btnNetAwgSubLoad,
      els.netAwgFeedback
    );
    if (ok) {
      await loadAmneziaState(true);
    }
  }

  async function copySubscriptionUrl() {
    const url = els.netAwgSubUrl.value.trim();
    if (!url) {
      setNetworkFeedback(els.netAwgFeedback, "Введите URL подписки", "is-error");
      return;
    }
    let copied = false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(url);
        copied = true;
      } catch (_err) {
        copied = false;
      }
    }
    if (copied) {
      setNetworkFeedback(els.netAwgFeedback, "Ссылка скопирована", "is-ok");
      return;
    }
    els.netAwgSubUrl.focus();
    els.netAwgSubUrl.select();
    setNetworkFeedback(
      els.netAwgFeedback,
      "Не удалось скопировать автоматически. Ссылка выделена — нажмите Ctrl+C.",
      "is-error"
    );
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
  els.btnNetworkSettings.addEventListener("click", openNetworkSettings);
  els.btnNetworkSettingsClose.addEventListener("click", closeNetworkSettings);
  els.networkTabButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      setNetworkTab(btn.dataset.networkTab);
    });
  });
  els.btnToggleLinkKey.addEventListener("click", function () {
    state.networkLinkKeyVisible = !state.networkLinkKeyVisible;
    renderNetworkLinkKey();
  });
  els.netIpModeAuto.addEventListener("change", syncNetworkIpMode);
  els.netIpModeManual.addEventListener("change", syncNetworkIpMode);
  els.netIpForm.addEventListener("submit", submitNetworkIp);
  els.netPppoeForm.addEventListener("submit", submitNetworkPppoe);
  els.netWgForm.addEventListener("submit", submitNetworkWireguard);
  els.netOpenvpnForm.addEventListener("submit", submitNetworkOpenvpn);
  els.netAwgForm.addEventListener("submit", submitNetworkAmnezia);
  els.btnNetAwgDown.addEventListener("click", stopNetworkAmnezia);
  els.netAwgSubForm.addEventListener("submit", submitNetworkSubscription);
  els.btnNetAwgSubCopy.addEventListener("click", copySubscriptionUrl);
  els.netAwgSubUrl.addEventListener("input", scheduleAmneziaQr);
  els.netAwgQr.addEventListener("load", function () {
    els.netAwgQrFrame.classList.remove("is-hidden");
    els.netAwgQrHint.textContent = "QR-код ссылки подписки";
    els.netAwgQrHint.classList.remove("is-warn");
  });
  els.netAwgQr.addEventListener("error", function () {
    els.netAwgQrFrame.classList.add("is-hidden");
    els.netAwgQrHint.textContent =
      "QR-код не построен: проверьте HTTPS-ссылку и qrencode на сервере.";
    els.netAwgQrHint.classList.add("is-warn");
  });
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
