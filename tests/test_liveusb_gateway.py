"""Tests for ephemeral LiveUSB gateway sessions and the Russian connect page."""

from __future__ import annotations

import datetime
import json
import re
import time

import pytest
from starlette.testclient import TestClient

import gateway
from core.liveusb_connect_page import render_connect_page


@pytest.fixture(autouse=True)
def _clean_liveusb_state(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    gateway._REGISTRY_CACHE["key"] = None
    gateway._REGISTRY_CACHE["data"] = None
    gateway._rate_windows.clear()
    gateway.pending_pairs.clear()
    gateway.liveusb_sessions.clear()
    gateway.liveusb_nodes.clear()
    gateway.liveusb_revoked_sessions.clear()
    gateway.active_tunnels.clear()
    gateway.active_sse_subscribers.clear()
    yield
    gateway._rate_windows.clear()
    gateway.pending_pairs.clear()
    gateway.liveusb_sessions.clear()
    gateway.liveusb_nodes.clear()
    gateway.liveusb_revoked_sessions.clear()
    gateway.active_tunnels.clear()
    gateway.active_sse_subscribers.clear()


def _client() -> TestClient:
    return TestClient(gateway.app)


def _load_disk_registry() -> dict:
    try:
        with open(gateway.REGISTRY_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def test_liveusb_register_issues_ephemeral_credentials_and_accepts_mcp_and_ws():
    client = _client()
    resp = client.post("/v1/liveusb/register", json={"os": "linux", "arch": "x64"})
    assert resp.status_code == 200
    data = resp.json()

    node_id = data["node_id"]
    device_token = data["device_token"]
    link_key = data["link_key"]
    session_code = data["session_code"]
    expires_at = data["expires_at"]

    assert re.fullmatch(r"live-[0-9a-f]{8}", node_id)
    assert re.fullmatch(r"[0-9a-f]{32}", device_token)
    assert link_key.startswith("lk_")
    assert len(session_code) >= 12
    assert data["connect_url"].endswith(f"/connect/{session_code}")
    assert f"/sse?user={node_id}&token={link_key}" in data["sse_url"]
    assert f"/mcp?user={node_id}&token={link_key}" in data["mcp_url"]

    exp_dt = datetime.datetime.fromisoformat(expires_at)
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    delta_seconds = (exp_dt - now_dt).total_seconds()
    assert 23 * 3600 < delta_seconds <= 24 * 3600 + 60

    reg = _load_disk_registry()
    assert node_id in reg
    assert reg[node_id]["token_sha256"] == gateway.hash_secret(device_token)
    assert reg[node_id]["link_key_sha256"] == gateway.hash_secret(link_key)
    assert "token" not in reg[node_id]
    assert "link_key" not in reg[node_id]

    # /mcp accepts link_key immediately (initialize request)
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    }
    mcp_resp = client.post(f"/mcp?user={node_id}&token={link_key}", json=init_body)
    assert mcp_resp.status_code == 200
    assert mcp_resp.json()["result"]["serverInfo"]["name"]

    # /sse accepts link_key immediately
    sse_resp = client.post(f"/sse?user={node_id}&token={link_key}", json=init_body)
    assert sse_resp.status_code == 200

    # /ws/agent accepts device_token immediately and sets online=True
    with client.websocket_connect(
        f"/ws/agent?user={node_id}",
        headers={"Authorization": f"Bearer {device_token}"},
    ) as ws:
        hello = ws.receive_json()
        assert hello["user"] == node_id
        status_online = client.get(f"/v1/liveusb/status/{session_code}")
        assert status_online.status_code == 200
        assert status_online.json()["online"] is True


def test_liveusb_status_unknown_and_offline():
    client = _client()
    assert client.get("/v1/liveusb/status/nonexistent-code-123").status_code == 404

    reg = client.post("/v1/liveusb/register").json()
    status = client.get(f"/v1/liveusb/status/{reg['session_code']}")
    assert status.status_code == 200
    body = status.json()
    assert body["node_id"] == reg["node_id"]
    assert body["online"] is False
    assert body["expires_at"] == reg["expires_at"]
    assert body["revoked"] is False


def test_liveusb_revoke_auth_and_immediate_cleanup():
    client = _client()
    reg = client.post("/v1/liveusb/register").json()
    node_id = reg["node_id"]
    device_token = reg["device_token"]
    link_key = reg["link_key"]
    session_code = reg["session_code"]

    # Missing token -> 401
    assert client.post("/v1/liveusb/revoke").status_code == 401
    # Link key cannot revoke -> 403
    assert client.post(
        "/v1/liveusb/revoke",
        headers={"Authorization": f"Bearer {link_key}"},
    ).status_code == 403
    # Wrong token -> 403
    assert client.post(
        "/v1/liveusb/revoke",
        headers={"Authorization": "Bearer " + "0" * 32},
    ).status_code == 403

    # Valid Bearer device_token -> 200
    rev = client.post(
        "/v1/liveusb/revoke",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert rev.status_code == 200
    assert rev.json()["status"] == "revoked"
    assert rev.json()["node_id"] == node_id

    # Immediately removed from active registry & sessions
    assert node_id not in _load_disk_registry()
    assert session_code not in gateway.liveusb_sessions
    assert node_id not in gateway.liveusb_nodes

    # /mcp rejects revoked link_key immediately
    mcp_after = client.post(
        f"/mcp?user={node_id}&token={link_key}",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert mcp_after.status_code in (401, 403)

    # /connect returns 410 Gone
    assert client.get(f"/connect/{session_code}").status_code == 410

    # /v1/liveusb/status reports revoked=True, online=False
    st = client.get(f"/v1/liveusb/status/{session_code}")
    assert st.status_code == 200
    assert st.json()["revoked"] is True
    assert st.json()["online"] is False


def test_liveusb_revoke_with_json_body_device_token():
    client = _client()
    reg = client.post("/v1/liveusb/register").json()
    rev = client.post("/v1/liveusb/revoke", json={"device_token": reg["device_token"]})
    assert rev.status_code == 200
    assert rev.json()["node_id"] == reg["node_id"]
    assert reg["session_code"] not in gateway.liveusb_sessions


def test_liveusb_ttl_expiration_prunes_session_and_rejects_access():
    client = _client()
    reg = client.post("/v1/liveusb/register").json()
    node_id = reg["node_id"]
    link_key = reg["link_key"]
    session_code = reg["session_code"]

    # Force expiration in the past
    gateway.liveusb_sessions[session_code]["expires_ts"] = time.time() - 5.0

    assert client.get(f"/v1/liveusb/status/{session_code}").status_code == 404
    assert client.get(f"/connect/{session_code}").status_code == 404
    assert session_code not in gateway.liveusb_sessions
    assert node_id not in _load_disk_registry()

    mcp_after = client.post(
        f"/mcp?user={node_id}&token={link_key}",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert mcp_after.status_code in (401, 403)


def test_liveusb_connect_page_html_rendering_and_tabs():
    client = _client()
    reg = client.post("/v1/liveusb/register").json()
    session_code = reg["session_code"]
    node_id = reg["node_id"]
    link_key = reg["link_key"]

    resp = client.get(f"/connect/{session_code}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert resp.headers.get("cache-control") == "no-store"

    html_doc = resp.text
    assert 'lang="ru"' in html_doc
    assert "Gemini" in html_doc
    assert "ChatGPT" in html_doc
    assert "Claude" in html_doc
    assert f"/sse?user={node_id}&amp;token={link_key}" in html_doc
    assert f"/mcp?user={node_id}&amp;token={link_key}" in html_doc
    assert "mcpServers" in html_doc
    assert "claude mcp add" in html_doc
    assert link_key in html_doc
    assert "/v1/liveusb/status/" in html_doc

    assert client.get("/connect/unknown-session-code").status_code == 404


def test_liveusb_connect_page_zero_unslop_ui_surface_tells():
    html_doc = render_connect_page(
        session_code="abc123def456",
        node_id="live-1a2b3c4d",
        link_key="lk_sampleTokenValue123456",
        host="smart-server.online",
        expires_at="2026-11-23T12:00:00+00:00",
        online=False,
    )

    forbidden_patterns = [
        r"animate-pulse",
        r"animate-bounce",
        r"transition\s*:\s*all",
        r"linear-gradient",
        r"radial-gradient",
        r"conic-gradient",
        r"hover:scale-",
        r"transform\s*:\s*scale",
        r"font-weight\s*:\s*(700|800|900|bold|extrabold|black)",
        r"font-extrabold",
        r"font-black",
        r"01\s*//",
        r"[\U0001F300-\U0001FAFF\u2600-\u27BF]",
    ]
    for pattern in forbidden_patterns:
        match = re.search(pattern, html_doc, flags=re.IGNORECASE)
        assert match is None, f"Forbidden unslop-ui tell matched {pattern!r}: {match.group(0)!r}"

    # Verify required surface tokens and transition discipline
    assert "#0e1116" in html_doc
    assert "#161b22" in html_doc
    assert "#2b313a" in html_doc
    assert "150ms ease-out" in html_doc
