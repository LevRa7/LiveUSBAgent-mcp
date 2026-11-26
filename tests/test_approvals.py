"""Access modes after v0.7.2: node-side approval prompts were removed.

Confirmation is the MCP client's job; the node keeps two modes (``read_only``
and ``full``) and maps the legacy ``approval`` name to ``full`` so old scripts
and tray entries keep working.
"""

import os

import pytest

from core import control, mcp_tools, settings


@pytest.fixture(autouse=True)
def configured_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_CONTROL_DIR", str(tmp_path / "control"))
    mcp_tools.configure(
        workspace=str(tmp_path),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=20000,
    )
    control.set_access_mode("full")
    yield
    control.set_access_mode("full")
    mcp_tools.configure(
        workspace=os.getcwd(),
        jobs_dir=os.path.expanduser("~/.cache/remote-harness-mcp/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=50000,
    )


def test_legacy_approval_mode_maps_to_full_access(tmp_path):
    applied = settings.apply({"action": "mode", "mode": "approval"}, actor="cli")
    assert "error" not in applied
    assert control.access_mode() == "full"

    target = tmp_path / "written.txt"
    result = mcp_tools.call_tool("write_file", {"path": str(target), "content": "ok"})
    assert "error" not in result, result
    assert target.read_text(encoding="utf-8") == "ok"


def test_read_only_mode_blocks_writes_and_allows_reads(tmp_path):
    settings.apply({"action": "mode", "mode": "read_only"}, actor="cli")
    target = tmp_path / "blocked.txt"

    blocked = mcp_tools.call_tool("write_file", {"path": str(target), "content": "x"})
    assert "error" in blocked
    assert not target.exists()

    assert "error" not in mcp_tools.call_tool("list_dir", {})
    assert "error" not in mcp_tools.call_tool("mesh_status", {})


def test_the_cabinet_can_lift_read_only_to_full():
    """The logged-in owner clicking "full" is the decision, not a request."""
    settings.apply({"action": "mode", "mode": "read_only"}, actor="cli")
    result = mcp_tools.call_tool(
        "node_control", {"action": "mode", "mode": "full", "_origin": "web"})
    assert "error" not in result
    assert control.access_mode() == "full"


def test_a_model_cannot_lift_read_only_to_full():
    settings.apply({"action": "mode", "mode": "read_only"}, actor="cli")
    result = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "full"})
    assert "read_only" in result["error"]
    assert control.access_mode() == "read_only"


def test_a_model_can_throttle_full_to_read_only():
    settings.apply({"action": "mode", "mode": "full"}, actor="cli")
    result = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "read_only"})
    assert "error" not in result
    assert control.access_mode() == "read_only"
