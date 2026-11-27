from __future__ import annotations

import html
import json
import shlex
from urllib.parse import quote

from core.liveusb_i18n import get_connect_locales


def _safe_json_for_script(value: object) -> str:
    """Serialize a Python object for safe embedding inside a <script> block."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_connect_page(
    *,
    session_code: str,
    node_id: str,
    link_key: str,
    host: str,
    expires_at: str,
    public_base_url: str = "",
    online: bool = False,
    csp_nonce: str = "",
    standalone: bool = False,
    tunnel_mode: str = "",
) -> str:
    """Render the mobile-friendly Russian LiveUSB connect page.

    Uses Python 3.8+ stdlib only. Produces a self-contained HTML document
    with Gemini, ChatGPT, and Claude tabs, one-click copy controls, and
    live status polling against /v1/liveusb/status/<session_code> (or /health
    in standalone Cloudflare Quick Tunnel mode).
    """
    base = (public_base_url or "").strip().rstrip("/")
    if not base:
        clean_host = (host or "").strip().rstrip("/")
        if clean_host.startswith("http://") or clean_host.startswith("https://"):
            base = clean_host
        else:
            base = f"https://{clean_host}" if clean_host else ""

    enc_node = quote(node_id, safe="")
    enc_link_key = quote(link_key, safe="")
    if standalone:
        sse_url = f"{base}/sse?token={enc_link_key}"
        mcp_url = f"{base}/mcp/{enc_link_key}"
    else:
        sse_url = f"{base}/sse?user={enc_node}&token={enc_link_key}"
        mcp_url = f"{base}/mcp?user={enc_node}&token={enc_link_key}"

    claude_json = json.dumps(
        {
            "mcpServers": {
                node_id: {
                    "url": mcp_url,
                }
            }
        },
        indent=2,
        ensure_ascii=False,
    )
    claude_cli_cmd = f"claude mcp add --transport http {shlex.quote(node_id)} {shlex.quote(mcp_url)}"
    script_open = f'<script nonce="{html.escape(csp_nonce, quote=True)}">' if csp_nonce else "<script>"

    esc_node = html.escape(node_id, quote=True)
    esc_expires = html.escape(expires_at, quote=True)
    esc_sse_url = html.escape(sse_url, quote=True)
    esc_mcp_url = html.escape(mcp_url, quote=True)
    esc_claude_json = html.escape(claude_json, quote=True)
    esc_claude_cli = html.escape(claude_cli_cmd, quote=True)
    esc_link_key = html.escape(link_key, quote=True)

    initial_dot_class = "status-dot status-dot-online" if online else "status-dot status-dot-offline"
    initial_status_text = "Туннель в сети" if online else "Туннель не в сети"

    js_config = _safe_json_for_script(
        {
            "sessionCode": session_code,
            "nodeId": node_id,
            "expiresAt": expires_at,
            "online": bool(online),
            "standalone": bool(standalone),
            "tunnelMode": tunnel_mode or ("cloudflare" if standalone else "gateway"),
            "copyTargets": {
                "gemini-sse": sse_url,
                "chatgpt-mcp": mcp_url,
                "claude-mcp": mcp_url,
                "claude-json": claude_json,
                "claude-cli": claude_cli_cmd,
                "raw-token": link_key,
            },
            "i18n": get_connect_locales(),
        }
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Connect {esc_node}</title>
<style>
  *, *::before, *::after {{
    box-sizing: border-box;
  }}

  html, body {{
    margin: 0;
    padding: 0;
    background-color: #0e1116;
    color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, sans-serif;
    font-size: 15px;
    font-weight: 400;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }}

  .page {{
    max-width: 640px;
    margin: 0 auto;
    padding: 24px 16px 48px;
  }}

  .card {{
    background-color: #161b22;
    border: 1px solid #2b313a;
    border-radius: 8px;
    box-shadow: 0 1px 2px rgb(0 0 0 / 0.05);
    padding: 20px;
  }}

  h1 {{
    margin: 0 0 16px;
    font-size: 20px;
    font-weight: 600;
    line-height: 1.3;
    color: #f0f6fc;
    word-break: break-word;
  }}

  .status-bar {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px 16px;
    padding: 12px;
    margin: 0 0 20px;
    background-color: #0e1116;
    border: 1px solid #2b313a;
    border-radius: 8px;
    font-size: 13px;
  }}

  .status-state {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-weight: 500;
    color: #e6edf3;
  }}

  .status-dot {{
    width: 8px;
    height: 8px;
    border-radius: 4px;
    flex-shrink: 0;
    background-color: #8b949e;
    transition: background-color 150ms ease-out;
  }}

  .status-dot-online {{
    background-color: #3fb950;
  }}

  .status-dot-offline {{
    background-color: #8b949e;
  }}

  .status-dot-error {{
    background-color: #f85149;
  }}

  .status-meta {{
    color: #8b949e;
    font-size: 13px;
  }}

  .tabs {{
    display: flex;
    gap: 8px;
    margin: 0 0 20px;
    border-bottom: 1px solid #2b313a;
    padding-bottom: 12px;
  }}

  .tab-btn {{
    flex: 1 1 0;
    min-height: 40px;
    padding: 8px 12px;
    border: 1px solid #2b313a;
    border-radius: 8px;
    background-color: #0e1116;
    color: #8b949e;
    font-family: inherit;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: background-color 150ms ease-out, border-color 150ms ease-out, color 150ms ease-out;
  }}

  .tab-btn:hover {{
    color: #e6edf3;
    border-color: #444c56;
  }}

  .tab-btn:focus-visible {{
    outline: 2px solid #58a6ff;
    outline-offset: 2px;
  }}

  .tab-btn[aria-selected="true"] {{
    background-color: #21262d;
    border-color: #58a6ff;
    color: #f0f6fc;
    font-weight: 600;
  }}

  .tab-panel {{
    display: none;
  }}

  .tab-panel.is-active {{
    display: block;
  }}

  .field {{
    margin: 0 0 16px;
  }}

  .field-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin: 0 0 8px;
  }}

  .field-label {{
    font-size: 13px;
    font-weight: 500;
    color: #c9d1d9;
  }}

  .copy-btn {{
    min-height: 32px;
    padding: 4px 12px;
    border: 1px solid #2b313a;
    border-radius: 4px;
    background-color: #21262d;
    color: #e6edf3;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: background-color 150ms ease-out, border-color 150ms ease-out, color 150ms ease-out;
  }}

  .copy-btn:hover {{
    background-color: #2b313a;
    border-color: #444c56;
  }}

  .copy-btn:focus-visible {{
    outline: 2px solid #58a6ff;
    outline-offset: 2px;
  }}

  .copy-btn.is-copied {{
    border-color: #3fb950;
    color: #3fb950;
  }}

  .code-box {{
    margin: 0;
    padding: 12px;
    background-color: #0e1116;
    border: 1px solid #2b313a;
    border-radius: 8px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 13px;
    font-weight: 400;
    line-height: 1.45;
    color: #e6edf3;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }}

  .code-box-pre {{
    white-space: pre;
    word-break: normal;
  }}

  .steps {{
    margin: 16px 0 0;
    padding: 0 0 0 20px;
    color: #c9d1d9;
    font-size: 14px;
  }}

  .steps li {{
    margin: 0 0 8px;
  }}

  .steps li:last-child {{
    margin-bottom: 0;
  }}

  .connector-actions {{
    margin: 16px 0 0;
  }}

  .connector-link {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 36px;
    padding: 6px 14px;
    border: 1px solid #58a6ff;
    border-radius: 6px;
    background-color: #21262d;
    color: #58a6ff;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    text-decoration: none;
    transition: background-color 150ms ease-out, border-color 150ms ease-out, color 150ms ease-out;
  }}

  .connector-link:hover {{
    background-color: #2b313a;
    color: #f0f6fc;
  }}

  .connector-link:focus-visible {{
    outline: 2px solid #58a6ff;
    outline-offset: 2px;
  }}

  .token-section {{
    margin: 24px 0 0;
    padding: 20px 0 0;
    border-top: 1px solid #2b313a;
  }}

  @media (min-width: 520px) {{
    .page {{
      padding: 40px 24px 64px;
    }}
    .card {{
      padding: 24px;
    }}
  }}
</style>
</head>
<body>
  <main class="page">
    <section class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;flex-wrap:wrap;">
        <h1 id="node-heading" style="margin:0;">Узел {esc_node}</h1>
        <select id="connect-lang" aria-label="Language" style="background:#0e1116;color:#e6edf3;border:1px solid #2b313a;border-radius:6px;padding:6px 10px;font-size:13px;cursor:pointer;">
          <option value="en">English</option>
          <option value="de">Deutsch</option>
          <option value="ru" selected>Русский</option>
          <option value="fr">Français</option>
          <option value="es">Español</option>
          <option value="ar">العربية</option>
          <option value="zh">中文</option>
        </select>
      </div>

      <div class="status-bar" id="status-bar" aria-live="polite">
        <div class="status-state">
          <span id="status-dot" class="{initial_dot_class}"></span>
          <span id="status-text">{initial_status_text}</span>
        </div>
        <div class="status-meta" id="status-expires">Действует до {esc_expires}</div>
      </div>

      <div class="tabs" role="tablist" aria-label="Клиенты MCP">
        <button type="button" class="tab-btn" role="tab" id="tab-gemini" aria-controls="panel-gemini" aria-selected="true" data-tab="gemini">Gemini</button>
        <button type="button" class="tab-btn" role="tab" id="tab-chatgpt" aria-controls="panel-chatgpt" aria-selected="false" data-tab="chatgpt">ChatGPT</button>
        <button type="button" class="tab-btn" role="tab" id="tab-claude" aria-controls="panel-claude" aria-selected="false" data-tab="claude">Claude</button>
      </div>

      <div class="tab-panel is-active" id="panel-gemini" role="tabpanel" aria-labelledby="tab-gemini">
        <div class="field">
          <div class="field-header">
            <span class="field-label" data-i18n="connect_gemini_url">SSE URL для Gemini</span>
            <button type="button" class="copy-btn" data-copy="gemini-sse" data-i18n="btn_copy">Копировать</button>
          </div>
          <pre class="code-box">{esc_sse_url}</pre>
        </div>
        <ol class="steps">
          <li data-i18n="connect_gemini_step1">Откройте настройки подключений MCP в Gemini.</li>
          <li data-i18n="connect_gemini_step2">Добавьте сервер и вставьте SSE URL выше.</li>
          <li data-i18n="connect_gemini_step3">Сохраните подключение и проверьте список инструментов.</li>
        </ol>
        <div class="connector-actions">
          <a class="connector-link" href="https://gemini.google.com/customize/connectors" target="_blank" rel="noopener noreferrer" data-i18n="connect_gemini_link">Открыть коннекторы Gemini &rarr;</a>
        </div>
      </div>

      <div class="tab-panel" id="panel-chatgpt" role="tabpanel" aria-labelledby="tab-chatgpt">
        <div class="field">
          <div class="field-header">
            <span class="field-label" data-i18n="connect_chatgpt_url">MCP URL для ChatGPT</span>
            <button type="button" class="copy-btn" data-copy="chatgpt-mcp" data-i18n="btn_copy">Копировать</button>
          </div>
          <pre class="code-box">{esc_mcp_url}</pre>
        </div>
        <ol class="steps">
          <li data-i18n="connect_chatgpt_step1">Откройте «Настройки» &rarr; «Приложения и коннекторы» &rarr; «Создать коннектор».</li>
          <li data-i18n="connect_chatgpt_step2">Вставьте MCP URL в поле адреса сервера.</li>
          <li data-i18n="connect_chatgpt_step3">Выберите режим без дополнительной аутентификации (токен передаётся в URL) и сохраните.</li>
        </ol>
        <div class="connector-actions">
          <a class="connector-link" href="https://chatgpt.com/#settings/Connectors" target="_blank" rel="noopener noreferrer" data-i18n="connect_chatgpt_link">Открыть коннекторы ChatGPT &rarr;</a>
        </div>
      </div>

      <div class="tab-panel" id="panel-claude" role="tabpanel" aria-labelledby="tab-claude">
        <div class="field">
          <div class="field-header">
            <span class="field-label" data-i18n="connect_claude_url">MCP URL для Claude</span>
            <button type="button" class="copy-btn" data-copy="claude-mcp" data-i18n="btn_copy">Копировать</button>
          </div>
          <pre class="code-box">{esc_mcp_url}</pre>
        </div>
        <div class="field">
          <div class="field-header">
            <span class="field-label" data-i18n="connect_claude_json">Конфигурация mcpServers (JSON)</span>
            <button type="button" class="copy-btn" data-copy="claude-json" data-i18n="btn_copy">Копировать</button>
          </div>
          <pre class="code-box code-box-pre">{esc_claude_json}</pre>
        </div>
        <div class="field">
          <div class="field-header">
            <span class="field-label" data-i18n="connect_claude_cli">Команда Claude Code</span>
            <button type="button" class="copy-btn" data-copy="claude-cli" data-i18n="btn_copy">Копировать</button>
          </div>
          <pre class="code-box">{esc_claude_cli}</pre>
        </div>
        <ol class="steps">
          <li data-i18n="connect_claude_step1">В Claude.ai добавьте MCP URL в разделе «Настройки» &rarr; «Интеграции».</li>
          <li data-i18n="connect_claude_step2">В Claude Desktop добавьте блок mcpServers в файл claude_desktop_config.json.</li>
          <li data-i18n="connect_claude_step3">В Claude Code выполните команду claude mcp add в терминале.</li>
        </ol>
        <div class="connector-actions">
          <a class="connector-link" href="https://claude.ai/settings/connectors" target="_blank" rel="noopener noreferrer" data-i18n="connect_claude_link">Открыть коннекторы Claude &rarr;</a>
        </div>
      </div>

      <div class="token-section">
        <div class="field">
          <div class="field-header">
            <span class="field-label" data-i18n="connect_token_label">Токен доступа (link_key)</span>
            <button type="button" class="copy-btn" data-copy="raw-token" data-i18n="btn_copy">Копировать</button>
          </div>
          <pre class="code-box">{esc_link_key}</pre>
        </div>
      </div>
    </section>
  </main>

  {script_open}
    (function () {{
      var cfg = {js_config};
      var i18n = cfg.i18n || {{}};
      var lang = "en";
      var lastStatusKey = cfg.online ? "connect_status_online" : "connect_status_offline";

      function t(key) {{
        var b = i18n[lang] || i18n.en || i18n.ru || {{}};
        return b[key] || (i18n.en && i18n.en[key]) || key;
      }}

      function applyLang(code, persist) {{
        lang = i18n[code] ? code : "en";
        var meta = (i18n[lang] && i18n[lang]._meta) || {{ code: lang, dir: lang === "ar" ? "rtl" : "ltr" }};
        document.documentElement.lang = meta.code || lang;
        document.documentElement.dir = meta.dir === "rtl" ? "rtl" : "ltr";
        if (persist) {{
          try {{ window.localStorage.setItem("rhm_liveusb_lang", lang); }} catch (e) {{}}
        }}
        var sel = document.getElementById("connect-lang");
        if (sel && sel.value !== lang) sel.value = lang;
        document.title = t("connect_title").replace("{{node}}", cfg.nodeId);
        var h = document.getElementById("node-heading");
        if (h) h.textContent = t("connect_node_heading").replace("{{node}}", cfg.nodeId);
        document.querySelectorAll("[data-i18n]").forEach(function (el) {{
          var k = el.getAttribute("data-i18n");
          if (k) el.textContent = t(k);
        }});
        var tEl = document.getElementById("status-text");
        if (tEl) tEl.textContent = t(lastStatusKey);
        if (expiresEl) expiresEl.textContent = formatExpires(cfg.expiresAt);
      }}

      function formatExpires(isoStr) {{
        if (!isoStr) return "";
        var prefix = t("connect_expires_prefix") + " ";
        try {{
          var d = new Date(isoStr);
          if (!isNaN(d.getTime())) {{
            return prefix + d.toLocaleString(lang, {{
              day: "2-digit",
              month: "2-digit",
              year: "numeric",
              hour: "2-digit",
              minute: "2-digit"
            }});
          }}
        }} catch (e) {{}}
        return prefix + isoStr;
      }}

      var expiresEl = document.getElementById("status-expires");
      var langSel = document.getElementById("connect-lang");
      if (langSel) {{
        langSel.addEventListener("change", function () {{ applyLang(langSel.value, true); }});
      }}
      var initLang = "en";
      try {{
        var q = new URLSearchParams(window.location.search).get("lang");
        var stored = window.localStorage.getItem("rhm_liveusb_lang");
        if (q && i18n[q]) initLang = q;
        else if (stored && i18n[stored]) initLang = stored;
      }} catch (e) {{}}
      applyLang(initLang, false);

      var tabButtons = document.querySelectorAll(".tab-btn");
      var tabPanels = document.querySelectorAll(".tab-panel");
      tabButtons.forEach(function (btn) {{
        btn.addEventListener("click", function () {{
          var target = btn.getAttribute("data-tab");
          tabButtons.forEach(function (b) {{
            b.setAttribute("aria-selected", b === btn ? "true" : "false");
          }});
          tabPanels.forEach(function (panel) {{
            panel.classList.toggle("is-active", panel.id === "panel-" + target);
          }});
        }});
      }});

      function fallbackCopy(text) {{
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try {{
          document.execCommand("copy");
        }} catch (e) {{}}
        document.body.removeChild(ta);
      }}

      document.querySelectorAll(".copy-btn").forEach(function (btn) {{
        btn.addEventListener("click", function () {{
          var key = btn.getAttribute("data-copy");
          var text = cfg.copyTargets[key];
          if (!text) return;
          var done = function () {{
            btn.textContent = t("btn_copied");
            btn.classList.add("is-copied");
            window.setTimeout(function () {{
              btn.textContent = t("btn_copy");
              btn.classList.remove("is-copied");
            }}, 1500);
          }};
          if (navigator.clipboard && navigator.clipboard.writeText) {{
            navigator.clipboard.writeText(text).then(done, function () {{
              fallbackCopy(text);
              done();
            }});
          }} else {{
            fallbackCopy(text);
            done();
          }}
        }});
      }});

      var dotEl = document.getElementById("status-dot");
      var textEl = document.getElementById("status-text");

      function applyStatus(state, labelKey, expiresIso) {{
        lastStatusKey = labelKey;
        if (dotEl) {{
          dotEl.className = "status-dot status-dot-" + state;
        }}
        if (textEl) {{
          textEl.textContent = t(labelKey);
        }}
        if (expiresEl && expiresIso) {{
          expiresEl.textContent = formatExpires(expiresIso);
        }}
      }}

      function pollStatus() {{
        var url = cfg.standalone
          ? "/health"
          : ("/v1/liveusb/status/" + encodeURIComponent(cfg.sessionCode));
        var controller = typeof AbortController === "function" ? new AbortController() : null;
        var timerId = controller
          ? window.setTimeout(function () {{ controller.abort(); }}, 4000)
          : null;
        var fetchOpts = {{ cache: "no-store" }};
        if (controller) {{
          fetchOpts.signal = controller.signal;
        }}
        fetch(url, fetchOpts)
          .then(function (resp) {{
            if (timerId !== null) window.clearTimeout(timerId);
            if (resp.status === 404 || resp.status === 410) {{
              applyStatus("error", "connect_status_expired", "");
              return null;
            }}
            if (!resp.ok) {{
              applyStatus("offline", "connect_status_reconnecting", "");
              return null;
            }}
            return resp.json();
          }})
          .then(function (data) {{
            if (!data) return;
            if (cfg.standalone) {{
              if (data.status === "healthy") {{
                applyStatus("online", "connect_status_online", "");
              }} else {{
                applyStatus("offline", "connect_status_offline", "");
              }}
              return;
            }}
            if (data.revoked) {{
              applyStatus("error", "connect_status_revoked", data.expires_at);
            }} else if (data.online) {{
              applyStatus("online", "connect_status_online", data.expires_at);
            }} else {{
              applyStatus("offline", "connect_status_offline", data.expires_at);
            }}
          }})
          .catch(function () {{
            if (timerId !== null) window.clearTimeout(timerId);
            applyStatus("offline", "connect_status_reconnecting", "");
          }});
      }}

      pollStatus();
      window.setInterval(pollStatus, 5000);
    }})();
  </script>
</body>
</html>
"""
