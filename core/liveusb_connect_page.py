from __future__ import annotations

import html
import json


def _safe_json_for_script(value: object) -> str:
    """Serialize a Python object for safe embedding inside a <script> block."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
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
) -> str:
    """Render the mobile-friendly Russian LiveUSB connect page.

    Uses Python 3.8+ stdlib only. Produces a self-contained HTML document
    with Gemini, ChatGPT, and Claude tabs, one-click copy controls, and
    live status polling against /v1/liveusb/status/<session_code>.
    """
    base = (public_base_url or "").strip().rstrip("/")
    if not base:
        clean_host = (host or "").strip().rstrip("/")
        if clean_host.startswith("http://") or clean_host.startswith("https://"):
            base = clean_host
        else:
            base = f"https://{clean_host}" if clean_host else ""

    sse_url = f"{base}/sse?user={node_id}&token={link_key}"
    mcp_url = f"{base}/mcp?user={node_id}&token={link_key}"

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
    claude_cli_cmd = f'claude mcp add --transport http {node_id} "{mcp_url}"'

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
            "copyTargets": {
                "gemini-sse": sse_url,
                "chatgpt-mcp": mcp_url,
                "claude-mcp": mcp_url,
                "claude-json": claude_json,
                "claude-cli": claude_cli_cmd,
                "raw-token": link_key,
            },
        }
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Подключение {esc_node}</title>
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
      <h1>Узел {esc_node}</h1>

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
            <span class="field-label">SSE URL для Gemini</span>
            <button type="button" class="copy-btn" data-copy="gemini-sse">Копировать</button>
          </div>
          <pre class="code-box">{esc_sse_url}</pre>
        </div>
        <ol class="steps">
          <li>Откройте настройки подключений MCP в Gemini.</li>
          <li>Добавьте сервер и вставьте SSE URL выше.</li>
          <li>Сохраните подключение и проверьте список инструментов.</li>
        </ol>
      </div>

      <div class="tab-panel" id="panel-chatgpt" role="tabpanel" aria-labelledby="tab-chatgpt">
        <div class="field">
          <div class="field-header">
            <span class="field-label">MCP URL для ChatGPT</span>
            <button type="button" class="copy-btn" data-copy="chatgpt-mcp">Копировать</button>
          </div>
          <pre class="code-box">{esc_mcp_url}</pre>
        </div>
        <ol class="steps">
          <li>Откройте «Настройки» &rarr; «Приложения и коннекторы» &rarr; «Создать коннектор».</li>
          <li>Вставьте MCP URL в поле адреса сервера.</li>
          <li>Выберите режим без дополнительной аутентификации (токен передаётся в URL) и сохраните.</li>
        </ol>
      </div>

      <div class="tab-panel" id="panel-claude" role="tabpanel" aria-labelledby="tab-claude">
        <div class="field">
          <div class="field-header">
            <span class="field-label">MCP URL для Claude</span>
            <button type="button" class="copy-btn" data-copy="claude-mcp">Копировать</button>
          </div>
          <pre class="code-box">{esc_mcp_url}</pre>
        </div>
        <div class="field">
          <div class="field-header">
            <span class="field-label">Конфигурация mcpServers (JSON)</span>
            <button type="button" class="copy-btn" data-copy="claude-json">Копировать</button>
          </div>
          <pre class="code-box code-box-pre">{esc_claude_json}</pre>
        </div>
        <div class="field">
          <div class="field-header">
            <span class="field-label">Команда Claude Code</span>
            <button type="button" class="copy-btn" data-copy="claude-cli">Копировать</button>
          </div>
          <pre class="code-box">{esc_claude_cli}</pre>
        </div>
        <ol class="steps">
          <li>В Claude.ai добавьте MCP URL в разделе «Настройки» &rarr; «Интеграции».</li>
          <li>В Claude Desktop добавьте блок mcpServers в файл claude_desktop_config.json.</li>
          <li>В Claude Code выполните команду claude mcp add в терминале.</li>
        </ol>
      </div>

      <div class="token-section">
        <div class="field">
          <div class="field-header">
            <span class="field-label">Токен доступа (link_key)</span>
            <button type="button" class="copy-btn" data-copy="raw-token">Копировать</button>
          </div>
          <pre class="code-box">{esc_link_key}</pre>
        </div>
      </div>
    </section>
  </main>

  <script>
    (function () {{
      var cfg = {js_config};

      function formatExpires(isoStr) {{
        if (!isoStr) return "";
        try {{
          var d = new Date(isoStr);
          if (!isNaN(d.getTime())) {{
            return "Действует до " + d.toLocaleString("ru-RU", {{
              day: "2-digit",
              month: "2-digit",
              year: "numeric",
              hour: "2-digit",
              minute: "2-digit"
            }});
          }}
        }} catch (e) {{}}
        return "Действует до " + isoStr;
      }}

      var expiresEl = document.getElementById("status-expires");
      if (expiresEl) {{
        expiresEl.textContent = formatExpires(cfg.expiresAt);
      }}

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
            btn.textContent = "Скопировано";
            btn.classList.add("is-copied");
            window.setTimeout(function () {{
              btn.textContent = "Копировать";
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

      function applyStatus(state, label, expiresIso) {{
        if (dotEl) {{
          dotEl.className = "status-dot status-dot-" + state;
        }}
        if (textEl) {{
          textEl.textContent = label;
        }}
        if (expiresEl && expiresIso) {{
          expiresEl.textContent = formatExpires(expiresIso);
        }}
      }}

      function pollStatus() {{
        var url = "/v1/liveusb/status/" + encodeURIComponent(cfg.sessionCode);
        fetch(url, {{ cache: "no-store" }})
          .then(function (resp) {{
            if (resp.status === 404 || resp.status === 410) {{
              applyStatus("error", "Сессия истекла", "");
              return null;
            }}
            if (!resp.ok) return null;
            return resp.json();
          }})
          .then(function (data) {{
            if (!data) return;
            if (data.revoked) {{
              applyStatus("error", "Сессия отозвана", data.expires_at);
            }} else if (data.online) {{
              applyStatus("online", "Туннель в сети", data.expires_at);
            }} else {{
              applyStatus("offline", "Туннель не в сети", data.expires_at);
            }}
          }})
          .catch(function () {{}});
      }}

      pollStatus();
      window.setInterval(pollStatus, 5000);
    }})();
  </script>
</body>
</html>
"""
