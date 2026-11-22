"""Parity tests for LiveUSBAgent-mcp bilingual documentation (README.md and README_RU.md).

Verifies that:
* both README.md and README_RU.md exist and are non-empty;
* both cross-link each other near the top;
* both document the core LiveUSB MCP tools, build/test scripts, and Security section;
* README_RU.md contains substantial Russian text and neither file keeps legacy combined headings.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

EN = "README.md"
RU = "README_RU.md"

REQUIRED_TOKENS = (
    "LiveUSBAgent-mcp",
    "rescue_info",
    "rescue_action",
    "list_skills",
    "load_skill",
    "build-iso.sh",
    "test-qemu.sh",
)


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8", errors="replace")


def test_both_language_files_exist_and_non_empty():
    for relative in (EN, RU):
        path = REPO / relative
        assert path.is_file(), f"{relative} is missing"
        assert len(_read(relative).strip()) > 0, f"{relative} is empty"


def test_each_file_links_to_the_other_language():
    english = _read(EN)
    russian = _read(RU)
    assert "](README_RU.md)" in english, "README.md must link to README_RU.md"
    assert "](README.md)" in russian, "README_RU.md must link back to README.md"
    # The language switch belongs near the top, where a reader looks for it.
    assert english.index("](README_RU.md)") < 1000
    assert russian.index("](README.md)") < 1000


def test_titles_name_liveusb_agent_mcp():
    english_first = _read(EN).splitlines()[0]
    russian_first = _read(RU).splitlines()[0]
    assert english_first.startswith("# ") and "LiveUSBAgent-mcp" in english_first
    assert russian_first.startswith("# ") and "LiveUSBAgent-mcp" in russian_first


def test_both_files_document_core_tools_and_scripts():
    for relative in (EN, RU):
        text = _read(relative)
        for token in REQUIRED_TOKENS:
            assert token in text, f"{relative} is missing required reference: {token}"


def test_both_files_have_security_section():
    english = _read(EN)
    russian = _read(RU)
    assert re.search(r"^##\s+.*Security", english, re.M | re.I), (
        "README.md must include a Security section"
    )
    assert re.search(r"^##\s+.*Безопасность", russian, re.M | re.I), (
        "README_RU.md must include a Безопасность (Security) section"
    )


def test_the_combined_russian_heading_is_gone():
    """No file may keep the old in-document combined heading."""
    for relative in (EN, RU):
        text = _read(relative)
        assert "# 🇷🇺 Русская версия" not in text, relative
        assert "](#-русская-версия)" not in text, relative


def test_the_russian_file_is_actually_russian():
    russian = _read(RU)
    assert len(re.findall(r"[А-Яа-я]", russian)) > 2000, (
        "README_RU.md lost its Russian text"
    )
