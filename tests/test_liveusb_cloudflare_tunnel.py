"""Unit and contract tests for LiveUSB Cloudflare Quick Tunnel & standalone /connect."""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
from pathlib import Path

import pytest

from core import liveusb_tunnel, mcp_tools, server


def test_parse_trycloudflare_url_ignores_api_subdomain():
    assert liveusb_tunnel.parse_trycloudflare_url("ERR https://api.trycloudflare.com/tunnel failed") is None
    assert (
        liveusb_tunnel.parse_trycloudflare_url(
            "INF |  https://calm-river-1234.trycloudflare.com  |"
        )
        == "https://calm-river-1234.trycloudflare.com"
    )
    assert server.parse_quick_tunnel_url("https://api.trycloudflare.com/v1") is None
    assert (
        server.parse_quick_tunnel_url("https://bright-star-99.trycloudflare.com")
        == "https://bright-star-99.trycloudflare.com"
    )


def test_cloudflared_asset_names():
    assert liveusb_tunnel.cloudflared_asset_name("Linux", "x86_64") == "cloudflared-linux-amd64"
    assert liveusb_tunnel.cloudflared_asset_name("Linux", "aarch64") == "cloudflared-linux-arm64"
    assert liveusb_tunnel.cloudflared_asset_name("Linux", "i686") == "cloudflared-linux-386"
    assert liveusb_tunnel.cloudflared_asset_name("Windows", "AMD64") == "cloudflared-windows-amd64.exe"
    assert liveusb_tunnel.cloudflared_asset_name("Windows", "x86") == "cloudflared-windows-386.exe"
    assert liveusb_tunnel.cloudflared_asset_name("Darwin", "arm64") == "cloudflared-darwin-arm64.tgz"


def test_write_liveusb_session_cloudflare_fields(tmp_path: Path):
    doc = liveusb_tunnel.write_liveusb_session(
        str(tmp_path),
        public_url="https://demo-node-42.trycloudflare.com/",
        token="tok-abc-123",
        node_id="rescue-box",
    )
    assert doc["ready"] is True
    assert doc["tunnel_mode"] == "cloudflare"
    assert doc["tunnel_status"] == "connected"
    assert doc["mcp_url"] == "https://demo-node-42.trycloudflare.com/mcp/tok-abc-123"
    assert doc["sse_url"] == "https://demo-node-42.trycloudflare.com/sse?token=tok-abc-123"
    assert doc["connect_url"] == "https://demo-node-42.trycloudflare.com/connect?token=tok-abc-123"

    saved = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert saved["mcp_url"] == doc["mcp_url"]
    assert saved["connect_url"] == doc["connect_url"]


def test_standalone_path_token_connect_page_and_liveusb_tools(monkeypatch):
    monkeypatch.setenv("MESH_SCENARIO", "live_usb")
    monkeypatch.setenv("MESH_ACCESS_MODE", "full")
    mcp_tools.sync_scenario_tools()

    token = "liveusb-secret-token-xyz"
    mcp = server.create_mcp_server()
    server.StandaloneMCPHandler.server_mcp = mcp
    server.StandaloneMCPHandler.auth_token = token
    server.StandaloneMCPHandler.oauth_store = server.StandaloneOAuthStore()
    server.StandaloneMCPHandler.bind_host = "127.0.0.1"

    httpd = server._StandaloneHTTPServer(("127.0.0.1", 0), server.StandaloneMCPHandler)
    port = httpd.server_address[1]
    server.StandaloneMCPHandler.bind_port = port
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        # 1. Path-token auth POST /mcp/<token> (easy-mcp style one-paste link)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        conn.request(
            "POST",
            f"/mcp/{token}",
            body=body,
            headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{port}"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
        tool_names = {t["name"] for t in payload["result"]["tools"]}
        # New v0.7.2 core tools + LiveUSB rescue/skills tools must all be present
        for expected in (
            "batch_read",
            "batch_edit",
            "fetch_url",
            "fs_index",
            "git_status",
            "git_diff",
            "read_media",
            "run_script",
            "rescue_info",
            "rescue_action",
            "list_skills",
            "load_skill",
        ):
            assert expected in tool_names, f"missing tool: {expected}"
        conn.close()

        # 2. Wrong path token is rejected with 401
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/mcp/wrong-token",
            body=body,
            headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{port}"},
        )
        resp = conn.getresponse()
        assert resp.status == 401
        resp.read()
        conn.close()

        # 3. GET /connect?token=<token> serves mobile Russian connect page with /mcp/<token>
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "GET",
            f"/connect?token={token}",
            headers={"Host": f"127.0.0.1:{port}", "Accept": "text/html"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        html_body = resp.read().decode("utf-8")
        assert f"/mcp/{token}" in html_body
        assert '"/health"' in html_body
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        monkeypatch.delenv("MESH_SCENARIO", raising=False)
        mcp_tools.sync_scenario_tools()


def test_supervisor_parses_url_and_writes_session(tmp_path: Path, monkeypatch):
    class _FakeProc:
        def __init__(self, *args, **kwargs):
            self.returncode = 0
            self._stopped = threading.Event()
            self.stdout = self._gen()

        def _gen(self):
            yield "INF Requesting tunnel at https://api.trycloudflare.com/tunnel\n"
            yield "INF +------------------------------------------------------------+\n"
            yield "INF |  https://fake-liveusb-99.trycloudflare.com                 |\n"
            self._stopped.wait(timeout=5.0)

        def wait(self, timeout=None):
            self._stopped.wait(timeout=timeout)
            return 0

        def terminate(self):
            self._stopped.set()

        def kill(self):
            self._stopped.set()

    fake_bin = tmp_path / "cloudflared"
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(liveusb_tunnel.subprocess, "Popen", _FakeProc)

    seen_urls = []
    sup = liveusb_tunnel.CloudflareQuickTunnelSupervisor(
        port=8096,
        token="sup-token",
        state_dir=str(tmp_path),
        node_id="liveusb-test",
        cloudflared_bin=str(fake_bin),
        on_public_url=lambda u: seen_urls.append(u),
        auto_download=False,
        restart_delay=0.5,
    )
    sup.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not seen_urls:
            time.sleep(0.05)
        assert seen_urls == ["https://fake-liveusb-99.trycloudflare.com"]
        session = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
        assert session["ready"] is True
        assert session["mcp_url"] == "https://fake-liveusb-99.trycloudflare.com/mcp/sup-token"
    finally:
        sup.stop()


def test_apply_tunnel_public_url_updates_shares_and_cleans_old_trycloudflare_host(
    tmp_path: Path, monkeypatch
):
    web_dir = tmp_path / "web_shares"
    sample_file = tmp_path / "hello.txt"
    sample_file.write_text("hello liveusb", encoding="utf-8")
    sample_dir = tmp_path / "site"
    sample_dir.mkdir()
    (sample_dir / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")

    monkeypatch.setenv("MESH_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MESH_WEB_DIR", str(web_dir))
    monkeypatch.setenv("MESH_READ_ONLY", "0")
    monkeypatch.delenv("MESH_PUBLIC_URL", raising=False)
    monkeypatch.delenv("AGY_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("MESH_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(server, "_STANDALONE_PORT", 8096)

    server.configure_tools_from_env()
    try:
        res0 = mcp_tools._tool_share_file({"path": str(sample_file), "name": "f0"})
        assert res0.get("url", "").startswith("http://localhost:8096/"), res0

        # Preserve static Tailscale host while rotating ephemeral trycloudflare hosts
        monkeypatch.setenv("MESH_ALLOWED_HOSTS", "static-box.tailnet.ts.net")

        applied1 = server._apply_tunnel_public_url("https://first-slug.trycloudflare.com/")
        assert applied1 == "https://first-slug.trycloudflare.com"
        assert os.environ["MESH_PUBLIC_URL"] == "https://first-slug.trycloudflare.com"
        assert server.allowed_public_hosts() == frozenset(
            {"static-box.tailnet.ts.net", "first-slug.trycloudflare.com"}
        )

        res1_file = mcp_tools._tool_share_file(
            {"path": str(sample_file), "name": "f1", "overwrite": True}
        )
        res1_dir = mcp_tools._tool_serve_dir({"path": str(sample_dir), "name": "d1"})
        res1_list = mcp_tools._tool_share_list({})
        assert res1_file["url"].startswith("https://first-slug.trycloudflare.com/"), res1_file
        assert res1_dir["url"].startswith("https://first-slug.trycloudflare.com/"), res1_dir
        assert res1_list["shares"], res1_list
        assert all(
            item["url"].startswith("https://first-slug.trycloudflare.com/")
            for item in res1_list["shares"]
        )

        applied2 = server._apply_tunnel_public_url(
            "https://second-slug.trycloudflare.com/",
            previous_url=applied1,
        )
        assert applied2 == "https://second-slug.trycloudflare.com"
        assert os.environ["MESH_PUBLIC_URL"] == "https://second-slug.trycloudflare.com"
        hosts_after = server.allowed_public_hosts()
        assert "first-slug.trycloudflare.com" not in hosts_after
        assert "second-slug.trycloudflare.com" in hosts_after
        assert "static-box.tailnet.ts.net" in hosts_after

        res2_file = mcp_tools._tool_share_file(
            {"path": str(sample_file), "name": "f2", "overwrite": True}
        )
        res2_dir = mcp_tools._tool_serve_dir({"path": str(sample_dir), "name": "d2"})
        res2_list = mcp_tools._tool_share_list({})
        assert res2_file["url"].startswith("https://second-slug.trycloudflare.com/"), res2_file
        assert res2_dir["url"].startswith("https://second-slug.trycloudflare.com/"), res2_dir
        assert all(
            item["url"].startswith("https://second-slug.trycloudflare.com/")
            for item in res2_list["shares"]
        )
    finally:
        for share_name in ("f0", "f1", "d1", "f2", "d2"):
            try:
                mcp_tools._tool_unshare({"name": share_name})
            except Exception:
                pass


def test_supervisor_watchdog_recovers_from_network_drop(tmp_path: Path, monkeypatch):
    urls = [
        "https://first-drop-11.trycloudflare.com",
        "https://second-recover-22.trycloudflare.com",
    ]
    procs = []

    class _FakeProc:
        def __init__(self, *args, **kwargs):
            self.index = len(procs)
            procs.append(self)
            self.returncode = 0
            self.terminated = threading.Event()
            self.stdout = self._gen()

        def _gen(self):
            url = urls[min(self.index, len(urls) - 1)]
            yield f"INF |  {url}  |\n"
            self.terminated.wait(timeout=10.0)

        def wait(self, timeout=None):
            self.terminated.wait(timeout=timeout)
            return self.returncode

        def terminate(self):
            self.returncode = -15
            self.terminated.set()

        def kill(self):
            self.returncode = -9
            self.terminated.set()

    fake_bin = tmp_path / "cloudflared"
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(liveusb_tunnel.subprocess, "Popen", _FakeProc)

    net_state = {"iface": "wlan0", "online": True}
    seen_urls = []

    sup = liveusb_tunnel.CloudflareQuickTunnelSupervisor(
        port=8096,
        token="wd-token",
        state_dir=str(tmp_path),
        node_id="liveusb-wd",
        cloudflared_bin=str(fake_bin),
        on_public_url=lambda u: seen_urls.append(u),
        auto_download=False,
        restart_delay=0.05,
        watchdog_interval=0.05,
        network_wait_interval=0.05,
        uplink_probe=lambda: net_state["iface"],
        connectivity_check=lambda: net_state["online"],
    )
    sup.start()
    try:
        # 1. Wait for initial tunnel URL and ready=True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(seen_urls) < 1:
            time.sleep(0.02)
        assert seen_urls == [urls[0]]
        session1 = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
        assert session1["ready"] is True
        assert session1["tunnel_status"] == "connected"

        # 2. Drop network -> watchdog kills hung proc and marks session offline/reconnecting
        net_state["iface"] = None
        net_state["online"] = False

        assert procs[0].terminated.wait(timeout=3.0), "watchdog did not terminate hung cloudflared"
        deadline = time.monotonic() + 3.0
        session_down = {}
        while time.monotonic() < deadline:
            session_down = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
            if session_down.get("ready") is False and session_down.get("tunnel_status") in (
                "offline",
                "reconnecting",
            ):
                break
            time.sleep(0.02)
        assert session_down["ready"] is False
        assert session_down["tunnel_status"] in ("offline", "reconnecting")
        assert session_down["public_url"] == ""
        assert session_down["connect_url"] == ""
        assert len(procs) == 1, "supervisor must wait for network before spawning new cloudflared"

        # 3. Restore network -> supervisor launches new cloudflared and updates session.json
        net_state["iface"] = "wlan0"
        net_state["online"] = True

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(seen_urls) < 2:
            time.sleep(0.02)
        assert seen_urls == [urls[0], urls[1]]

        deadline = time.monotonic() + 3.0
        session2 = {}
        while time.monotonic() < deadline:
            session2 = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
            if session2.get("ready") is True and session2.get("public_url") == urls[1]:
                break
            time.sleep(0.02)
        assert session2["ready"] is True
        assert session2["tunnel_status"] == "connected"
        assert session2["connect_url"] == f"{urls[1]}/connect?token=wd-token"
    finally:
        sup.stop()

