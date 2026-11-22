"""Unit tests for core.liveusb_bootstrap and core.agent --ephemeral-liveusb."""

import io
import json
import os
import platform
import stat
import urllib.error

import pytest

@pytest.fixture(autouse=True)
def _restore_mesh_env():
    before = {k: v for k, v in os.environ.items() if k.startswith("MESH_")}
    yield
    for k in list(os.environ):
        if k.startswith("MESH_") and k not in before:
            os.environ.pop(k, None)
    os.environ.update(before)

from core import agent, liveusb_bootstrap


class _FakeResponse:
    """Minimal context-manager mock for urllib.request.urlopen responses."""

    def __init__(self, body: bytes = b"{}", status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(url: str, code: int, body: bytes = b"err") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url=url,
        code=code,
        msg=f"HTTP {code}",
        hdrs={},
        fp=io.BytesIO(body),
    )


# ---------------------------------------------------------------------------
# state_dir_path & export_env_file
# ---------------------------------------------------------------------------

def test_state_dir_path_default_and_overrides(monkeypatch):
    monkeypatch.delenv("MESH_LIVE_STATE_DIR", raising=False)
    assert liveusb_bootstrap.DEFAULT_STATE_DIR == "/run/rhm-live"
    assert liveusb_bootstrap.state_dir_path() == "/run/rhm-live"

    monkeypatch.setenv("MESH_LIVE_STATE_DIR", "/tmp/custom-rhm")
    assert liveusb_bootstrap.state_dir_path() == "/tmp/custom-rhm"
    assert liveusb_bootstrap.state_dir_path("/tmp/explicit") == "/tmp/explicit"


def test_export_env_file_loads_bom_comments_and_quotes(tmp_path, monkeypatch):
    env_file = tmp_path / "agent.env"
    env_file.write_text(
        "\ufeff# comment line\n"
        "MESH_GATEWAY=gw.example.test\n"
        "export MESH_USER=\"live-node-1\"\n"
        "MESH_TOKEN='tok_secret_123'\n"
        "MESH_SCENARIO=live_usb\n",
        encoding="utf-8",
    )
    for k in ("MESH_GATEWAY", "MESH_USER", "MESH_TOKEN", "MESH_SCENARIO"):
        monkeypatch.delenv(k, raising=False)

    exported = liveusb_bootstrap.export_env_file(str(env_file))
    assert exported["MESH_GATEWAY"] == "gw.example.test"
    assert exported["MESH_USER"] == "live-node-1"
    assert exported["MESH_TOKEN"] == "tok_secret_123"
    assert os.environ["MESH_TOKEN"] == "tok_secret_123"
    assert os.environ["MESH_SCENARIO"] == "live_usb"

    # Missing file never raises
    assert liveusb_bootstrap.export_env_file(str(tmp_path / "missing.env")) == {}


# ---------------------------------------------------------------------------
# wait_for_gateway
# ---------------------------------------------------------------------------

def test_wait_for_gateway_succeeds_after_transient_failures(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, timeout))
        if len(calls) == 1:
            raise urllib.error.URLError("network unreachable")
        if len(calls) == 2:
            raise _http_error(req.full_url, 503, b"unavailable")
        return _FakeResponse(b'{"status":"ok"}', status=200)

    monkeypatch.setattr(liveusb_bootstrap.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(liveusb_bootstrap.time, "sleep", lambda _s: None)

    assert liveusb_bootstrap.wait_for_gateway("gw.example.test", timeout_sec=10, poll_interval=0.05) is True
    assert len(calls) == 3
    assert calls[0][0] == "https://gw.example.test/health"


def test_wait_for_gateway_accepts_404_as_reachable(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise _http_error(req.full_url, 404, b"not found")

    monkeypatch.setattr(liveusb_bootstrap.urllib.request, "urlopen", fake_urlopen)
    assert liveusb_bootstrap.wait_for_gateway("https://gw.example.test/", timeout_sec=5, poll_interval=0.05) is True


def test_wait_for_gateway_times_out_cleanly(monkeypatch):
    clock = [0.0]

    def fake_monotonic():
        return clock[0]

    def fake_sleep(sec):
        clock[0] += max(sec, 0.5)

    def fake_urlopen(req, timeout=None):
        clock[0] += 0.5
        raise urllib.error.URLError("down")

    monkeypatch.setattr(liveusb_bootstrap.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(liveusb_bootstrap.time, "sleep", fake_sleep)
    monkeypatch.setattr(liveusb_bootstrap.urllib.request, "urlopen", fake_urlopen)

    assert liveusb_bootstrap.wait_for_gateway("gw.example.test", timeout_sec=2.0, poll_interval=0.5) is False
    assert liveusb_bootstrap.wait_for_gateway("", timeout_sec=1.0) is False


# ---------------------------------------------------------------------------
# register_ephemeral_node
# ---------------------------------------------------------------------------

def test_register_ephemeral_node_writes_agent_env_and_atomic_session_json(tmp_path, monkeypatch):
    captured = {}
    replace_calls = []
    real_replace = os.replace

    def tracking_replace(src, dst):
        replace_calls.append((src, dst))
        return real_replace(src, dst)

    gw_response = {
        "node_id": "live-abc123",
        "device_token": "dtok_live_999",
        "link_key": "lk_live_888",
        "session_code": "ABCD-1234",
        "connect_url": "https://gw.example.test/liveusb/ABCD-1234",
        "sse_url": "https://gw.example.test/sse?user=live-abc123&token=lk_live_888",
        "mcp_url": "https://gw.example.test/mcp?user=live-abc123&token=lk_live_888",
        "expires_at": "2026-11-23T12:00:00Z",
    }

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse(json.dumps(gw_response).encode("utf-8"), status=200)

    monkeypatch.setattr(liveusb_bootstrap.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(liveusb_bootstrap.os, "replace", tracking_replace)

    state_dir = tmp_path / "rhm-live"
    res = liveusb_bootstrap.register_ephemeral_node(
        "https://gw.example.test/",
        hostname="rescue-box",
        state_dir=str(state_dir),
        timeout_sec=12,
    )

    assert res["ok"] is True
    assert captured["url"] == "https://gw.example.test/v1/liveusb/register"
    assert captured["method"] == "POST"
    assert captured["body"] == {"hostname": "rescue-box", "os": "linux", "arch": platform.machine()}
    assert captured["timeout"] == 12.0

    # Verify agent.env contents
    env_path = state_dir / "agent.env"
    assert env_path.is_file()
    env_parsed = liveusb_bootstrap._parse_env_file(str(env_path))
    assert env_parsed == {
        "MESH_GATEWAY": "gw.example.test",
        "MESH_USER": "live-abc123",
        "MESH_TOKEN": "dtok_live_999",
        "MESH_LINK_KEY": "lk_live_888",
        "MESH_SCENARIO": "live_usb",
        "MESH_ACCESS_MODE": "full",
        "MESH_WORKSPACE": "/",
    }
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(env_path).st_mode) == 0o600

    # Verify session.json contents & atomic write
    session_path = state_dir / "session.json"
    assert session_path.is_file()
    session_doc = json.loads(session_path.read_text(encoding="utf-8"))
    for field in (
        "node_id",
        "link_key",
        "session_code",
        "connect_url",
        "sse_url",
        "mcp_url",
        "expires_at",
        "gateway",
        "registered_at",
    ):
        assert field in session_doc and session_doc[field]
    assert session_doc["node_id"] == "live-abc123"
    assert session_doc["link_key"] == "lk_live_888"
    assert session_doc["token"] == "lk_live_888"
    assert session_doc["ready"] is True
    assert session_doc["gateway"] == "https://gw.example.test"
    assert "device_token" not in session_doc

    replaced_targets = [os.path.basename(dst) for _, dst in replace_calls]
    assert "agent.env" in replaced_targets
    assert "session.json" in replaced_targets
    assert not list(state_dir.glob("*.tmp"))


def test_register_ephemeral_node_handles_errors_without_raising(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise _http_error(req.full_url, 500, b"internal error")

    monkeypatch.setattr(liveusb_bootstrap.urllib.request, "urlopen", fake_urlopen)
    res = liveusb_bootstrap.register_ephemeral_node("gw.example.test", state_dir=str(tmp_path))
    assert res["ok"] is False
    assert "500" in res["error"]

    # Missing required fields in gateway JSON
    monkeypatch.setattr(
        liveusb_bootstrap.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResponse(b'{"node_id": "only-id"}'),
    )
    res2 = liveusb_bootstrap.register_ephemeral_node("gw.example.test", state_dir=str(tmp_path))
    assert res2["ok"] is False
    assert "missing" in res2["error"]


# ---------------------------------------------------------------------------
# revoke_ephemeral_node
# ---------------------------------------------------------------------------

def test_revoke_ephemeral_node_posts_device_token_and_cleans_session(tmp_path, monkeypatch):
    state_dir = tmp_path / "rhm-live"
    state_dir.mkdir()
    (state_dir / "agent.env").write_text(
        "MESH_GATEWAY=gw.example.test\n"
        "MESH_USER=live-abc123\n"
        "MESH_TOKEN=dtok_live_999\n",
        encoding="utf-8",
    )
    session_path = state_dir / "session.json"
    session_path.write_text(
        json.dumps({"node_id": "live-abc123", "gateway": "https://gw.example.test"}),
        encoding="utf-8",
    )

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse(b'{"revoked": true}', status=200)

    monkeypatch.setattr(liveusb_bootstrap.urllib.request, "urlopen", fake_urlopen)

    res = liveusb_bootstrap.revoke_ephemeral_node(state_dir=str(state_dir), timeout_sec=6)
    assert res["ok"] is True
    assert res["revoked"] is True
    assert res["cleaned_session"] is True
    assert not session_path.exists()
    assert captured["url"] == "https://gw.example.test/v1/liveusb/revoke"
    assert captured["method"] == "POST"
    assert captured["body"]["device_token"] == "dtok_live_999"
    assert captured["body"]["node_id"] == "live-abc123"
    assert captured["timeout"] == 6.0


def test_revoke_ephemeral_node_cleans_session_even_when_gateway_fails(tmp_path, monkeypatch):
    state_dir = tmp_path / "rhm-live"
    state_dir.mkdir()
    (state_dir / "agent.env").write_text(
        "MESH_GATEWAY=gw.example.test\nMESH_TOKEN=dtok_live_999\n",
        encoding="utf-8",
    )
    session_path = state_dir / "session.json"
    session_path.write_text(json.dumps({"node_id": "live-abc123"}), encoding="utf-8")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(liveusb_bootstrap.urllib.request, "urlopen", fake_urlopen)

    res = liveusb_bootstrap.revoke_ephemeral_node(state_dir=str(state_dir))
    assert res["ok"] is False
    assert res["revoked"] is False
    assert res["cleaned_session"] is True
    assert not session_path.exists()
    assert "error" in res


# ---------------------------------------------------------------------------
# active_uplink_interface (/proc/net/route)
# ---------------------------------------------------------------------------

def test_active_uplink_interface_parses_proc_net_route(tmp_path):
    route_file = tmp_path / "route"
    route_file.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
        "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
        "usb0\t00000000\t012AA8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
        "down0\t00000000\t0100000A\t0000\t0\t0\t10\t00000000\t0\t0\t0\n",
        encoding="utf-8",
    )
    # usb0 has RTF_UP (0x0003) and lower metric (100) than wlan0 (600); down0 is not RTF_UP
    assert liveusb_bootstrap.active_uplink_interface(str(route_file)) == "usb0"


def test_active_uplink_interface_no_default_or_missing_file(tmp_path):
    no_default = tmp_path / "route_no_default"
    no_default.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n",
        encoding="utf-8",
    )
    assert liveusb_bootstrap.active_uplink_interface(str(no_default)) is None
    assert liveusb_bootstrap.active_uplink_interface(str(tmp_path / "nonexistent")) is None


# ---------------------------------------------------------------------------
# core.agent --ephemeral-liveusb integration
# ---------------------------------------------------------------------------

def test_agent_bootstrap_noop_without_flag(monkeypatch):
    monkeypatch.delenv("MESH_EPHEMERAL_LIVEUSB", raising=False)
    monkeypatch.delenv("MESH_ACCESS_MODE", raising=False)
    monkeypatch.delenv("MESH_SCENARIO", raising=False)

    called = []
    monkeypatch.setattr(liveusb_bootstrap, "wait_for_gateway", lambda *a, **k: called.append("wait") or True)
    monkeypatch.setattr(liveusb_bootstrap, "register_ephemeral_node", lambda *a, **k: called.append("reg") or {"ok": True})

    agent._bootstrap_ephemeral_liveusb([])
    assert called == []
    assert "MESH_ACCESS_MODE" not in os.environ
    assert "MESH_SCENARIO" not in os.environ


def test_agent_bootstrap_ephemeral_registers_exports_and_hooks_atexit(tmp_path, monkeypatch):
    state_dir = tmp_path / "live-state"
    env_file = state_dir / "agent.env"

    for k in (
        "MESH_TOKEN",
        "MESH_USER",
        "MESH_GATEWAY",
        "MESH_LINK_KEY",
        "MESH_ACCESS_MODE",
        "MESH_SCENARIO",
        "MESH_CONFIG_FILE",
        "MESH_EPHEMERAL_LIVEUSB",
        "MESH_PUBLIC_URL",
        "AGY_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)

    monkeypatch.setenv("MESH_DOMAIN_FILE", str(tmp_path / "no-domain.env"))
    monkeypatch.setattr(agent, "TOKEN", "")
    monkeypatch.setattr(agent, "USER", "old-user")
    monkeypatch.setattr(agent, "GATEWAY_HOST", "gw.example.test")

    events = []
    atexit_hooks = []

    def fake_wait(gw, **kwargs):
        events.append(("wait", gw))
        return True

    def fake_reg(gw, state_dir=None, **kwargs):
        events.append(("register", gw, state_dir))
        os.makedirs(state_dir, exist_ok=True)
        env_path = os.path.join(state_dir, "agent.env")
        with open(env_path, "w", encoding="utf-8") as handle:
            handle.write(
                "MESH_GATEWAY=gw.example.test\n"
                "MESH_USER=ephem-node-7\n"
                "MESH_TOKEN=dtok_ephem_7\n"
                "MESH_LINK_KEY=lk_ephem_7\n"
                "MESH_SCENARIO=live_usb\n"
                "MESH_ACCESS_MODE=full\n"
                "MESH_WORKSPACE=/\n"
            )
        return {"ok": True, "node_id": "ephem-node-7", "env_file": env_path}

    monkeypatch.setattr(liveusb_bootstrap, "wait_for_gateway", fake_wait)
    monkeypatch.setattr(liveusb_bootstrap, "register_ephemeral_node", fake_reg)
    monkeypatch.setattr(agent.atexit, "register", lambda fn, *a, **k: atexit_hooks.append((fn, a, k)))
    monkeypatch.setattr(agent, "configure_from_env", lambda: events.append(("configure",)))

    agent._bootstrap_ephemeral_liveusb(["--ephemeral-liveusb", f"--state-dir={state_dir}"])

    assert events[0] == ("wait", "gw.example.test")
    assert events[1] == ("register", "gw.example.test", str(state_dir))
    assert ("configure",) in events
    assert os.environ["MESH_ACCESS_MODE"] == "full"
    assert os.environ["MESH_SCENARIO"] == "live_usb"
    assert os.environ["MESH_TOKEN"] == "dtok_ephem_7"
    assert agent.USER == "ephem-node-7"
    assert agent.TOKEN == "dtok_ephem_7"
    assert len(atexit_hooks) == 1
    assert atexit_hooks[0][0] is liveusb_bootstrap.revoke_ephemeral_node
    assert atexit_hooks[0][2] == {"state_dir": str(state_dir)}


def test_configure_from_env_syncs_liveusb_scenario_tools(monkeypatch):
    from core import mcp_tools
    monkeypatch.setenv("MESH_SCENARIO", "live_usb")
    try:
        agent.configure_from_env()
        tool_names = {t["name"] for t in mcp_tools.TOOLS}
        assert "rescue_info" in tool_names
        assert "rescue_action" in tool_names
    finally:
        monkeypatch.delenv("MESH_SCENARIO", raising=False)
        mcp_tools.sync_scenario_tools()


# ---------------------------------------------------------------------------
# Security hardening tests (F-04, F-05, F-08 IPv6, F-13)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_url",
    [
        "http://gw.example.test",
        "http://gw.example.test:8080",
        "ws://gw.example.test/ws",
        "http://192.168.1.10:8000",
        "ftp://gw.example.test",
    ],
)
def test_normalize_gateway_base_rejects_non_loopback_plaintext(bad_url):
    assert liveusb_bootstrap._normalize_gateway_base(bad_url) == ""
    res = liveusb_bootstrap.register_ephemeral_node(bad_url)
    assert res["ok"] is False


@pytest.mark.parametrize(
    "good_url,expected",
    [
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("http://localhost:8080/", "http://localhost:8080"),
        ("http://[::1]:8080", "http://[::1]:8080"),
        ("ws://127.0.0.1:9000", "http://127.0.0.1:9000"),
        ("https://gw.example.test/", "https://gw.example.test"),
        ("wss://gw.example.test", "https://gw.example.test"),
    ],
)
def test_normalize_gateway_base_allows_loopback_and_tls(good_url, expected):
    assert liveusb_bootstrap._normalize_gateway_base(good_url) == expected


def test_revoke_ephemeral_node_unlinks_agent_env_alongside_session(tmp_path, monkeypatch):
    state_dir = tmp_path / "rhm-live"
    state_dir.mkdir()
    env_path = state_dir / "agent.env"
    session_path = state_dir / "session.json"
    env_path.write_text(
        "MESH_GATEWAY=gw.example.test\nMESH_USER=live-1\nMESH_TOKEN=dtok_1\n",
        encoding="utf-8",
    )
    session_path.write_text(json.dumps({"node_id": "live-1"}), encoding="utf-8")

    monkeypatch.setattr(
        liveusb_bootstrap.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResponse(b'{"revoked": true}', status=200),
    )

    res = liveusb_bootstrap.revoke_ephemeral_node(state_dir=str(state_dir))
    assert res["ok"] is True
    assert res["cleaned_session"] is True
    assert res["cleaned_env"] is True
    assert not session_path.exists()
    assert not env_path.exists()


def test_active_uplink_interface_falls_back_to_ipv6_route(tmp_path):
    v4_file = tmp_path / "route"
    v4_file.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n",
        encoding="utf-8",
    )
    v6_file = tmp_path / "ipv6_route"
    v6_file.write_text(
        "00000000000000000000000000000000 00 00000000000000000000000000000000 00 fe800000000000000000000000000001 00000258 00000001 00000000 00000003 wlan0\n"
        "00000000000000000000000000000000 00 00000000000000000000000000000000 00 fe800000000000000000000000000001 00000064 00000001 00000000 00000003 enp0s3\n",
        encoding="utf-8",
    )
    assert liveusb_bootstrap.active_uplink_interface(str(v4_file), ipv6_route_path=str(v6_file)) == "enp0s3"


def test_no_redirect_handler_blocks_3xx():
    handler = liveusb_bootstrap._NoRedirectHandler()
    req = liveusb_bootstrap.urllib.request.Request("https://gw.example.test/v1/liveusb/register")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        handler.redirect_request(req, io.BytesIO(b""), 302, "Found", {}, "https://evil.example/steal")
    assert exc_info.value.code == 302
