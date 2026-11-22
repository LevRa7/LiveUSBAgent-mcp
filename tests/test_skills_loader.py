#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for core/skills_loader.py and ops/live-usb/skills-manifest.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core import skills_loader


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture()
def sample_skills_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a realistic multi-level skill tree and point MESH_SKILLS_DIRS at it."""
    root = tmp_path / "skills_root"

    # 1. Nested SKILL.md with inline list tags, scalar os, and references/
    _write(
        root / "liveusb" / "recovery" / "win-boot-repair" / "SKILL.md",
        """---
name: windows-offline-boot-repair
description: "Repair Windows UEFI/ESP, BCD, and BitLocker offline"
category: recovery
tags: [windows, uefi, bcd, bitlocker]
os: windows
risk_level: high
---
# Windows Offline Boot Repair

Step 1: Mount ESP read-only first.
Step 2: Inspect BCD store.
""",
    )
    _write(
        root / "liveusb" / "recovery" / "win-boot-repair" / "references" / "bcd-cheatsheet.md",
        "# BCD Cheatsheet\nUse hivexsh or bcdedit offline.\n",
    )
    _write(
        root / "liveusb" / "recovery" / "win-boot-repair" / "references" / "nested" / "bitlocker.txt",
        "dislocker -V /dev/nvme0n1p3 -p<RECOVERY_KEY> -- /mnt/dislocker\n",
    )
    # Secret file outside references/ to test traversal rejection
    _write(
        root / "liveusb" / "recovery" / "win-boot-repair" / "secret.txt",
        "TOP SECRET OUTSIDE REFERENCES\n",
    )

    # 2. Nested SKILL.md with block list tags and multi-OS list
    _write(
        root / "liveusb" / "sysadmin" / "disk-triage" / "SKILL.md",
        """---
name: linux-disk-triage
description: >
  Inspect SMART attributes, NVMe logs, and LVM/mdadm topology
  without modifying storage media.
category: sysadmin
tags:
  - linux
  - smartctl
  - nvme
os:
  - linux
  - macos
risk_level: low
---
# Disk Triage

Run `lsblk -f` and `smartctl -a`.
""",
    )

    # 3. Flat .md skill without frontmatter (like skills/orchestrator.md)
    _write(
        root / "orchestrator.md",
        """# Remote Harness Orchestrator

Autonomous infrastructure orchestration rules for MCP nodes.
""",
    )

    # 4. Ignored README.md should not show up as a skill
    _write(root / "README.md", "# Skills Directory\nNot a skill.\n")

    monkeypatch.setenv("MESH_SKILLS_DIRS", str(root))
    return root


# ---------------------------------------------------------------------------
# Frontmatter parsing tests
# ---------------------------------------------------------------------------


def test_parse_frontmatter_inline_and_quoted():
    raw = (
        "\ufeff---\r\n"
        "name: btrfs-rescue\r\n"
        'description: "Recover Btrfs: CoW trees safely" # trailing comment\r\n'
        "category: recovery\r\n"
        "tags: [btrfs, 'cow', \"read-only\"]\r\n"
        "os: linux\r\n"
        "risk_level: medium\r\n"
        "---\r\n"
        "# Body heading\r\nContent line.\r\n"
    )
    meta, body = skills_loader.parse_frontmatter(raw)
    assert meta["name"] == "btrfs-rescue"
    assert meta["description"] == "Recover Btrfs: CoW trees safely"
    assert meta["category"] == "recovery"
    assert meta["tags"] == ["btrfs", "cow", "read-only"]
    assert meta["os"] == "linux"
    assert meta["risk_level"] == "medium"
    assert body.startswith("# Body heading")


def test_parse_frontmatter_block_list_and_multiline_scalars():
    raw = (
        "---\n"
        "name: ram-diag\n"
        "description: |\n"
        "  Line one.\n"
        "  Line two.\n"
        "tags:\n"
        "  - ram\n"
        "  - dmidecode # comment\n"
        "---\n"
        "Body text\n"
    )
    meta, body = skills_loader.parse_frontmatter(raw)
    assert meta["name"] == "ram-diag"
    assert meta["description"] == "Line one.\nLine two."
    assert meta["tags"] == ["ram", "dmidecode"]
    assert body == "Body text\n"


def test_parse_frontmatter_absent_and_unclosed_graceful_fallback():
    plain = "# Plain Skill\nNo frontmatter here.\n"
    meta, body = skills_loader.parse_frontmatter(plain)
    assert meta == {}
    assert body == plain

    unclosed = "---\nname: broken\nno closing delimiter"
    meta2, body2 = skills_loader.parse_frontmatter(unclosed)
    assert meta2 == {}
    assert body2 == unclosed


# ---------------------------------------------------------------------------
# Discovery & filtering tests
# ---------------------------------------------------------------------------


def test_recursive_discovery_and_compact_metadata(sample_skills_tree: Path):
    res = skills_loader.handle_list_skills({})
    assert "error" not in res
    assert res["count"] == 3

    by_name = {item["name"]: item for item in res["skills"]}
    assert set(by_name) == {"windows-offline-boot-repair", "linux-disk-triage", "orchestrator"}

    expected_keys = {"name", "description", "category", "tags", "os", "risk_level", "has_references"}
    for item in res["skills"]:
        assert set(item.keys()) == expected_keys

    win = by_name["windows-offline-boot-repair"]
    assert win["category"] == "recovery"
    assert win["os"] == "windows"
    assert win["risk_level"] == "high"
    assert win["has_references"] is True
    assert "bitlocker" in win["tags"]

    triage = by_name["linux-disk-triage"]
    assert triage["os"] == ["linux", "macos"]
    assert triage["has_references"] is False

    orch = by_name["orchestrator"]
    assert orch["category"] == "general"
    assert orch["risk_level"] == "unknown"
    assert orch["has_references"] is False
    assert "Autonomous infrastructure" in orch["description"]


def test_filtering_by_category_query_and_os(sample_skills_tree: Path):
    # Filter by category (case-insensitive)
    cat_res = skills_loader.handle_list_skills({"category": "RECOVERY"})
    assert [s["name"] for s in cat_res["skills"]] == ["windows-offline-boot-repair"]

    # Filter by os (scalar and list)
    win_res = skills_loader.handle_list_skills({"os": "windows"})
    assert [s["name"] for s in win_res["skills"]] == ["windows-offline-boot-repair"]

    mac_res = skills_loader.handle_list_skills({"os": "macos"})
    assert [s["name"] for s in mac_res["skills"]] == ["linux-disk-triage"]

    # Filter by query matching tags and description
    tag_res = skills_loader.handle_list_skills({"query": "SMARTCTL"})
    assert [s["name"] for s in tag_res["skills"]] == ["linux-disk-triage"]

    desc_res = skills_loader.handle_list_skills({"query": "BitLocker"})
    assert [s["name"] for s in desc_res["skills"]] == ["windows-offline-boot-repair"]

    # Combined filters with no match
    none_res = skills_loader.handle_list_skills({"category": "recovery", "os": "linux"})
    assert none_res["count"] == 0
    assert none_res["skills"] == []


def test_mesh_skills_dirs_precedence_and_missing_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    missing = tmp_path / "does_not_exist"

    _write(
        dir_a / "dup" / "SKILL.md",
        "---\nname: shared-skill\ndescription: From A\n---\nBody A\n",
    )
    _write(
        dir_b / "dup" / "SKILL.md",
        "---\nname: shared-skill\ndescription: From B\n---\nBody B\n",
    )
    _write(
        dir_b / "only-b" / "SKILL.md",
        "---\nname: only-b\ndescription: From B only\n---\nBody B only\n",
    )

    monkeypatch.setenv("MESH_SKILLS_DIRS", os.pathsep.join([str(missing), str(dir_a), str(dir_b)]))
    res = skills_loader.handle_list_skills({})
    by_name = {s["name"]: s for s in res["skills"]}
    assert set(by_name) == {"shared-skill", "only-b"}
    assert by_name["shared-skill"]["description"] == "From A"


def test_default_repo_skills_dir_discovers_orchestrator(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MESH_SKILLS_DIRS", raising=False)
    res = skills_loader.handle_list_skills({"query": "orchestrator"})
    assert "error" not in res
    names = [s["name"] for s in res["skills"]]
    assert "orchestrator" in names


# ---------------------------------------------------------------------------
# load_skill, reference_file, and path-traversal tests
# ---------------------------------------------------------------------------


def test_load_skill_success_and_missing(sample_skills_tree: Path):
    res = skills_loader.handle_load_skill({"name": "windows-offline-boot-repair"})
    assert "error" not in res
    assert res["name"] == "windows-offline-boot-repair"
    assert "Step 1: Mount ESP read-only first." in res["content"]
    assert res["references"] == ["bcd-cheatsheet.md", "nested/bitlocker.txt"]

    # Case-insensitive lookup works
    res_ci = skills_loader.handle_load_skill({"name": "ORCHESTRATOR"})
    assert "error" not in res_ci
    assert res_ci["name"] == "orchestrator"
    assert res_ci["references"] == []
    assert "Autonomous infrastructure" in res_ci["content"]

    # Missing skill returns structured error, never raises
    missing = skills_loader.handle_load_skill({"name": "nonexistent-skill"})
    assert "error" in missing
    assert "not found" in missing["error"].lower()

    # Missing/invalid args never raise
    assert "error" in skills_loader.handle_load_skill({})
    assert "error" in skills_loader.handle_load_skill(None)  # type: ignore[arg-type]


def test_load_skill_reference_file_success_and_missing(sample_skills_tree: Path):
    res = skills_loader.handle_load_skill(
        {
            "name": "windows-offline-boot-repair",
            "reference_file": "bcd-cheatsheet.md",
        }
    )
    assert "error" not in res
    assert res["reference_file"] == "bcd-cheatsheet.md"
    assert "hivexsh" in res["content"]
    assert res["references"] == ["bcd-cheatsheet.md", "nested/bitlocker.txt"]

    # Nested reference file
    nested = skills_loader.handle_load_skill(
        {
            "name": "windows-offline-boot-repair",
            "reference_file": "nested/bitlocker.txt",
        }
    )
    assert "error" not in nested
    assert "dislocker" in nested["content"]

    # Missing reference file returns error + available list
    missing_ref = skills_loader.handle_load_skill(
        {
            "name": "windows-offline-boot-repair",
            "reference_file": "does-not-exist.md",
        }
    )
    assert "error" in missing_ref
    assert missing_ref["references"] == ["bcd-cheatsheet.md", "nested/bitlocker.txt"]


@pytest.mark.parametrize(
    "bad_path",
    [
        "../SKILL.md",
        "../secret.txt",
        "../../etc/passwd",
        r"..\secret.txt",
        "nested/../../secret.txt",
        "/etc/passwd",
        r"C:\Windows\win.ini",
        "bcd-cheatsheet.md\x00.txt",
    ],
)
def test_load_skill_rejects_path_traversal(sample_skills_tree: Path, bad_path: str):
    res = skills_loader.handle_load_skill(
        {
            "name": "windows-offline-boot-repair",
            "reference_file": bad_path,
        }
    )
    assert "error" in res
    assert "content" not in res


# ---------------------------------------------------------------------------
# Tool schema & manifest contract tests
# ---------------------------------------------------------------------------


def test_skills_tools_spec_contract():
    by_name = {spec["name"]: spec for spec in skills_loader.SKILLS_TOOLS_SPEC}
    assert set(by_name) == {"list_skills", "load_skill"}

    for spec in by_name.values():
        hints = spec.get("annotations", {})
        assert hints.get("readOnlyHint") is True
        assert hints.get("destructiveHint") is False
        assert hints.get("idempotentHint") is True
        assert hints.get("openWorldHint") is False

    assert by_name["load_skill"]["inputSchema"]["required"] == ["name"]
    assert "reference_file" in by_name["load_skill"]["inputSchema"]["properties"]
    list_props = by_name["list_skills"]["inputSchema"]["properties"]
    assert {"category", "query", "os"}.issubset(set(list_props))


def test_skills_manifest_json_valid():
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "ops" / "live-usb" / "skills-manifest.json"
    assert manifest_path.is_file()

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["destination"] == "skills/liveusb/external"
    repos = {entry["repo"]: entry for entry in data["repositories"]}

    required_repos = {
        "peterbamuhigire/linux-skills",
        "HermeticOrmus/linux-sysadmin-skills",
        "sickn33/agentic-awesome-skills",
        "github/awesome-copilot",
        "mukul975/Anthropic-Cybersecurity-Skills",
        "danielrosehill/Claude-System-Recovery-Mode",
        "0xDarkMatter/claude-mods",
        "crt106/c3rb3r-btrfs-recovery",
        "Tokeii0/memprocfs-skill",
        "yaklang/hack-skills",
    }
    assert required_repos.issubset(set(repos))
    for entry in data["repositories"]:
        assert entry["url"].startswith("https://github.com/")
        assert isinstance(entry["paths"], list) and len(entry["paths"]) >= 1
        assert entry["dest_subdir"]
