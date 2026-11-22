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

    # Verify required surface tokens, focus-visible styles, arrows, and transition discipline
    assert "#0e1116" in html_doc
    assert "#161b22" in html_doc
    assert "#2b313a" in html_doc
    assert "150ms ease-out" in html_doc
    assert ".tab-btn:focus-visible" in html_doc
    assert ".copy-btn:focus-visible" in html_doc
    assert "&rarr;" in html_doc
    assert "-&gt;" not in html_doc


def _register_normal_node(username: str = "normal-box") -> tuple[str, str]:
    device_token = gateway.new_device_token()
    link_key = gateway.new_link_key()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    reg = gateway.load_registry()
    reg[username] = gateway.new_node_entry("owner@example.com", "linux", "x64", now_iso, device_token, link_key)
    gateway.save_registry(reg)
    return username, link_key


def test_liveusb_tools_list_advertises_rescue_and_skills_only_for_liveusb():
    client = _client()
    live = client.post("/v1/liveusb/register").json()
    normal_user, normal_lk = _register_normal_node("normal-box")

    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

    live_resp = client.post(f"/mcp?user={live['node_id']}&token={live['link_key']}", json=list_req)
    assert live_resp.status_code == 200
    live_tools = {t["name"]: t for t in live_resp.json()["result"]["tools"]}
    for tool_name in ("rescue_info", "rescue_action", "list_skills", "load_skill"):
        assert tool_name in live_tools
        assert live_tools[tool_name].get("inputSchema", {}).get("type") == "object"

    assert live_tools["rescue_action"]["annotations"] == gateway._ANNOT_DESTRUCTIVE
    assert live_tools["rescue_info"]["annotations"] == gateway._ANNOT_READ_ONLY
    assert live_tools["list_skills"]["annotations"] == gateway._ANNOT_READ_ONLY
    assert live_tools["load_skill"]["annotations"] == gateway._ANNOT_READ_ONLY

    normal_resp = client.post(f"/mcp?user={normal_user}&token={normal_lk}", json=list_req)
    assert normal_resp.status_code == 200
    normal_names = {t["name"] for t in normal_resp.json()["result"]["tools"]}
    for tool_name in ("rescue_info", "rescue_action", "list_skills", "load_skill"):
        assert tool_name not in normal_names


def test_liveusb_tools_call_dispatches_skills_rescue_and_bypasses_system_guards(monkeypatch):
    client = _client()
    live = client.post("/v1/liveusb/register").json()
    node_id = live["node_id"]
    link_key = live["link_key"]

    forwarded = []

    async def fake_call_remote_tool(user, name, args):
        forwarded.append((user, name, dict(args)))
        if name == "list_skills":
            return {"skills": [{"name": "disk-recovery", "category": "recovery"}]}
        if name == "rescue_info":
            return {"section": args.get("section", "summary"), "disks": ["/dev/nvme0n1"]}
        if name == "bash_exec":
            return {"exit_code": 0, "stdout": "ok", "stderr": ""}
        if name == "run_job":
            return {"job_id": "job-1", "status": "running"}
        if name in ("write_file", "edit_file"):
            return {"path": args.get("path"), "bytes": 4, "replacements": 1, "sha256": "abc"}
        return {"ok": True}

    monkeypatch.setattr(gateway, "call_remote_tool", fake_call_remote_tool)

    # Dispatch list_skills and rescue_info
    r_skills = client.post(
        f"/mcp?user={node_id}&token={link_key}",
        json={"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "list_skills", "arguments": {"category": "recovery"}}},
    )
    assert r_skills.status_code == 200
    assert r_skills.json()["result"]["isError"] is False
    assert "disk-recovery" in r_skills.json()["result"]["content"][0]["text"]

    r_rescue = client.post(
        f"/mcp?user={node_id}&token={link_key}",
        json={"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "rescue_info", "arguments": {"section": "disks"}}},
    )
    assert r_rescue.status_code == 200
    assert r_rescue.json()["result"]["isError"] is False
    assert "/dev/nvme0n1" in r_rescue.json()["result"]["content"][0]["text"]

    # LiveUSB bypasses classify_command() and is_system_path() refusals
    r_bash = client.post(
        f"/mcp?user={node_id}&token={link_key}",
        json={"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "bash_exec", "arguments": {"command": "rm -rf /mnt/rescue/tmpfile"}}},
    )
    assert r_bash.status_code == 200
    assert r_bash.json()["result"]["isError"] is False

    r_job = client.post(
        f"/mcp?user={node_id}&token={link_key}",
        json={"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "run_job", "arguments": {"command": "apt-get install -y gddrescue"}}},
    )
    assert r_job.status_code == 200
    assert r_job.json()["result"]["isError"] is False

    r_write = client.post(
        f"/mcp?user={node_id}&token={link_key}",
        json={"jsonrpc": "2.0", "id": 14, "method": "tools/call", "params": {"name": "write_file", "arguments": {"path": "/etc/fstab", "content": "# ok\n"}}},
    )
    assert r_write.status_code == 200
    assert r_write.json()["result"]["isError"] is False

    r_edit = client.post(
        f"/mcp?user={node_id}&token={link_key}",
        json={"jsonrpc": "2.0", "id": 15, "method": "tools/call", "params": {"name": "edit_file", "arguments": {"path": "/etc/fstab", "old_string": "a", "new_string": "b"}}},
    )
    assert r_edit.status_code == 200
    assert r_edit.json()["result"]["isError"] is False

    dispatched_names = [item[1] for item in forwarded]
    assert dispatched_names == ["list_skills", "rescue_info", "bash_exec", "run_job", "write_file", "edit_file"]


def test_liveusb_registry_expired_entries_pruned_when_ram_sessions_empty():
    client = _client()
    reg_resp = client.post("/v1/liveusb/register").json()
    node_id = reg_resp["node_id"]

    # Simulate gateway restart: RAM dicts are empty, expired entry remains on disk in registry
    reg = _load_disk_registry()
    assert node_id in reg
    reg[node_id]["expires_ts"] = time.time() - 60.0
    gateway.save_registry(reg)

    gateway.liveusb_sessions.clear()
    gateway.liveusb_nodes.clear()
    assert len(gateway.liveusb_sessions) == 0

    pruned = gateway._prune_expired_liveusb_sessions()
    assert pruned == 1
    assert node_id not in _load_disk_registry()


def test_liveusb_register_enforces_max_active_cap(monkeypatch):
    monkeypatch.setattr(gateway, "LIVEUSB_MAX_ACTIVE", 2)
    client = _client()

    r1 = client.post("/v1/liveusb/register")
    assert r1.status_code == 200
    r2 = client.post("/v1/liveusb/register")
    assert r2.status_code == 200

    r3 = client.post("/v1/liveusb/register")
    assert r3.status_code == 429
    assert r3.json()["error"] == "liveusb_capacity_reached"
