"""Console menu (``rhm``): endpoint, mode switch, log, JSON snapshot."""

import io
import json
import os

import pytest

from core import control, menu


def test_build_url_from_agent_env(monkeypatch):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {
        "MESH_USER": "desktop-01",
        "MESH_LINK_KEY": "lk_" + "a" * 32,
    })
    url = menu.build_mcp_url()
    assert "/sse?user=desktop-01&token=lk_" in url


def test_qr_lines_render_when_encoder_is_available():
    pytest.importorskip("qrcode")
    lines = menu.qr_lines("https://example.test/sse?user=n&token=lk_x")
    assert lines
    assert any("\u2588" in line or "\u2584" in line or "\u2580" in line for line in lines)


def test_missing_link_key_has_no_url(monkeypatch):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {"MESH_USER": "desktop-01"})
    assert menu.build_mcp_url() == ""


def test_mode_cycles_read_only_approval_full():
    assert control.next_access_mode("read_only") == "approval"
    assert control.next_access_mode("approval") == "full"
    assert control.next_access_mode("full") == "read_only"


def test_render_contains_mode_endpoint_and_log(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {
        "MESH_USER": "desktop-01",
        "MESH_LINK_KEY": "lk_" + "b" * 32,
    })
    monkeypatch.setattr(menu, "agent_log_path", lambda: str(tmp_path / "agent.log"))
    (tmp_path / "agent.log").write_text("agent line\n", encoding="utf-8")
    control.set_access_mode("approval")

    text = menu.render(menu.read_agent_env())
    assert "MCP endpoint" in text
    assert "desktop-01" in text
    assert "Access on request" in text
    assert "agent line" in text


def test_json_snapshot_is_machine_readable(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {"MESH_USER": "n", "MESH_LINK_KEY": "lk_x"})
    monkeypatch.setattr(menu, "agent_log_path", lambda: str(tmp_path / "agent.log"))
    menu.main(["--json"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["mode"] in control.ACCESS_MODES
    assert "url" in payload
    assert "log" in payload


# ---------------------------------------------------------------------------
# One-shot subcommands: the same switches as the menu, without a terminal
# ---------------------------------------------------------------------------

def test_mode_subcommand_changes_the_mode(capsys):
    assert menu.main(["mode", "read_only"]) == 0
    assert control.access_mode() == "read_only"
    assert "read_only" in capsys.readouterr().out


def test_cd_subcommand_sets_the_workspace(tmp_path, capsys):
    target = tmp_path / "project"
    target.mkdir()
    assert menu.main(["cd", str(target)]) == 0
    assert control.workspace() == os.path.realpath(str(target))


def test_status_subcommand_prints_every_switch(capsys):
    assert menu.main(["status"]) == 0
    text = capsys.readouterr().out
    for line in ("mode:", "workspace:", "isolation:", "remote:", "paused:", "approvals:"):
        assert line in text


def test_status_json_is_machine_readable(capsys):
    assert menu.main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert "remote_control" in payload
    assert "isolation" in payload


def test_remote_control_can_be_closed_and_reopened_from_the_command_line(capsys):
    assert menu.main(["remote-control", "off"]) == 0
    assert control.remote_control_enabled() is False
    assert "only" in capsys.readouterr().out.lower()

    assert menu.main(["remote-control", "on"]) == 0
    assert control.remote_control_enabled() is True


def test_a_bad_value_is_refused_with_exit_code_2(capsys):
    assert menu.main(["mode", "root"]) == 2
    assert "mode must be one of" in capsys.readouterr().err
    assert menu.main(["isolation", "maybe"]) == 2


def test_pause_and_resume_subcommands(capsys):
    assert menu.main(["pause"]) == 0
    assert control.paused() is True
    assert menu.main(["resume"]) == 0
    assert control.paused() is False


def test_goal_commands_are_disabled_by_default(capsys):
    """A node-wide objective leaks between chats, so it is opt-in."""
    assert menu.main(["goal", "finish the MCP update"]) == 2
    assert "disabled" in capsys.readouterr().err
    assert menu.main(["goal"]) == 2
    assert "disabled" in capsys.readouterr().err


def test_goal_clear_still_works_when_disabled(capsys):
    assert menu.main(["goal", "clear"]) == 0
    assert "cleared" in capsys.readouterr().out


def test_approvals_command_allows_and_revokes(capsys):
    assert menu.main(["approvals"]) == 0
    assert "ask on every" in capsys.readouterr().out

    assert menu.main(["approvals", "allow"]) == 0
    assert "ALL allowed" in capsys.readouterr().out
    assert control.grant_status()["active"] is True

    assert menu.main(["approvals", "off"]) == 0
    assert control.grant_status()["active"] is False
    assert "ask on every" in capsys.readouterr().out


def test_approvals_allow_for_a_limited_time(capsys):
    assert menu.main(["approvals", "allow", "30"]) == 0
    status = control.grant_status()
    assert status["active"] is True
    assert 1500 < status["expires_in"] <= 1800
    assert "min" in capsys.readouterr().out


def test_render_shows_the_approval_state(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {"MESH_USER": "n"})
    monkeypatch.setattr(menu, "agent_log_path", lambda: str(tmp_path / "agent.log"))
    menu.main(["approvals", "allow"])
    text = menu.render(menu.read_agent_env())
    assert "Approvals: ALL allowed" in text
    menu.main(["approvals", "off"])


def test_render_hides_the_disabled_goal(monkeypatch, tmp_path):
    monkeypatch.delenv("MESH_GOAL", raising=False)
    monkeypatch.setattr(menu, "read_agent_env", lambda: {"MESH_USER": "n"})
    monkeypatch.setattr(menu, "agent_log_path", lambda: str(tmp_path / "agent.log"))
    text = menu.render(menu.read_agent_env())
    assert "Goal:" not in text


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_posix_launcher_is_executable():
    launcher = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "rhm")
    assert os.path.exists(launcher)


def test_keys_is_a_context_manager(monkeypatch):
    """`rhm` used to crash with TypeError: Keys has no __exit__."""
    restored = []
    monkeypatch.setattr(menu.Keys, "_enable_ansi", lambda self: None)
    monkeypatch.setattr(menu.Keys, "restore", lambda self: restored.append(True))
    with menu.Keys() as keys:
        assert isinstance(keys, menu.Keys)
    assert restored == [True]


def test_render_keeps_the_full_text_for_scripts(monkeypatch):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {
        "MESH_USER": "node-one", "MESH_LINK_KEY": "lk_" + "a" * 32})
    text = menu.render(menu.read_agent_env())
    assert "MCP endpoint:" in text
    assert "Agent log (" in text


def test_interactive_frame_fits_the_window(monkeypatch):
    env = {"MESH_USER": "node-one", "MESH_LINK_KEY": "lk_" + "a" * 32,
           "MESH_GATEWAY": "gw.test"}
    frame = menu._frame(env, columns=60, rows=12)
    lines = frame.splitlines()
    assert len(lines) <= 12, "a frame taller than the window scrolls the screen"
    assert all(len(line) <= 60 for line in lines), "wrapped lines smear the menu"
    assert "q quit" in frame


def test_draw_repaints_only_when_the_frame_changes(monkeypatch):
    env = {"MESH_USER": "node-one", "MESH_LINK_KEY": "lk_" + "a" * 32,
           "MESH_GATEWAY": "gw.test"}
    painted = []
    monkeypatch.setattr(menu, "_terminal_size", lambda: (80, 24))
    monkeypatch.setattr(menu, "_paint", painted.append)
    menu.draw(env)
    menu.draw(env)
    assert len(painted) == 1, "an unchanged frame must not be repainted"
    menu.draw(env, force=True)
    assert len(painted) == 2
    assert "MCP endpoint:" not in painted[-1], "the QR/endpoint block belongs to `e`"


def test_paint_falls_back_to_ansi(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(menu, "_ANSI", True)
    monkeypatch.setattr(menu, "_WIN_CONSOLE", False)
    monkeypatch.setattr(menu.sys, "stdout", out)
    menu._paint("hello")
    written = out.getvalue()
    assert written.startswith("\x1b[H")
    assert written.endswith("\x1b[J")
