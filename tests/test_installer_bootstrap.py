"""The installer's bootstrap list must name every module the node imports.

``install.sh`` is often the only thing a new node has: when it runs without the code
beside it, it *fetches a list of files* rather than a tree. That list was written by
hand and stopped at the modules that existed at the time, which is how a fresh phone
install ended up advertising ``device_info`` while answering "the device layer
(core/device.py) is missing from this checkout" - the tool was there, its module was
not. The same holds for a Windows node bootstrapped from the domain.

0.5.0 shipped with the platform layer (``core/platforms/<os>/...``) missing from both
lists: it is a package, the lists were flat, and a freshly installed node died on
``from core import platforms`` before it could report a version. The checks below
walk ``core/`` recursively *and* import the tree the bootstrap would produce, so a
nested package cannot slip through again.
"""

import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILE_PATTERN = r"core/[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*\.py"


def _read(relative):
    with open(os.path.join(REPO, relative), "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def bootstrap_list() -> str:
    """The file list inside install.sh's bootstrap loop (the one with __init__.py)."""
    text = _read("install.sh")
    match = re.search(r"for f in ([^;]*core/__init__\.py[^;]*); do", text)
    assert match, "install.sh no longer has the bootstrap loop with core/__init__.py"
    return match.group(1)


def windows_bootstrap_list() -> str:
    """The ``$files = @( ... )`` array inside install.ps1."""
    text = _read("install.ps1")
    match = re.search(r"\$files = @\(([\s\S]*?)\n    \)", text)
    assert match, "install.ps1 no longer has the $files bootstrap array"
    return match.group(1)


def shipped_modules() -> set:
    """Every module the node can import, as a repository-relative POSIX path."""
    found = set()
    for current, directories, files in os.walk(os.path.join(REPO, "core")):
        directories[:] = [name for name in directories if name != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            absolute = os.path.join(current, name)
            found.add(os.path.relpath(absolute, REPO).replace(os.sep, "/"))
    return found


def _listed(text: str) -> set:
    return set(re.findall(FILE_PATTERN, text))


def test_every_core_module_is_bootstrapped():
    listed = _listed(bootstrap_list())
    missing = sorted(shipped_modules() - listed)
    assert missing == [], (
        "install.sh would not fetch these modules on a fresh node: %s - add them to the "
        "bootstrap list (and to the payload the gateway serves)" % missing)


def test_the_windows_installer_fetches_the_same_modules():
    """Both installers must agree: a module missing from one breaks that platform."""
    shell_list = _listed(bootstrap_list())
    windows_list = _listed(windows_bootstrap_list())
    missing_in_windows = sorted(shell_list - windows_list)
    assert missing_in_windows == [], (
        "install.ps1 does not fetch %s, so a Windows node bootstrapped from the domain "
        "would fail to import it" % missing_in_windows)
    assert sorted(shipped_modules() - windows_list) == [], (
        "install.ps1 is missing the modules listed above")


def test_the_orchestration_skill_is_bootstrapped():
    assert "skills/orchestrator.md" in bootstrap_list(), (
        "get_orchestration_skill reads skills/orchestrator.md, so a bootstrapped node "
        "must fetch it")
    assert "skills/orchestrator.md" in windows_bootstrap_list()


def test_the_bootstrap_list_names_only_files_that_exist():
    for relative in _listed(bootstrap_list()):
        assert os.path.isfile(os.path.join(REPO, relative)), (
            "install.sh fetches %s, which is not in this tree" % relative)
    for relative in _listed(windows_bootstrap_list()):
        assert os.path.isfile(os.path.join(REPO, relative)), (
            "install.ps1 fetches %s, which is not in this tree" % relative)


def test_a_bootstrapped_tree_can_import_the_node(tmp_path):
    """The strongest guard: build exactly what the bootstrap fetches, then import it.

    Everything the installer would download is copied into a temporary tree and
    ``core.mcp_tools`` is imported from it - the import that failed on 0.5.0 because
    ``core/platforms`` was never fetched.
    """
    for relative in _listed(bootstrap_list()):
        source = os.path.join(REPO, relative)
        target = tmp_path / relative.replace("/", os.sep)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(open(source, "rb").read())

    result = subprocess.run(
        [sys.executable, "-c", "import core.mcp_tools, core.agent; print(len(core.mcp_tools.TOOLS))"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, (
        "a bootstrapped node cannot start: the installer fetches a tree that does not "
        "import:\n%s" % (result.stderr or result.stdout))


@pytest.mark.skipif(os.name == "nt", reason="the bootstrap loop is POSIX shell")
def test_the_bootstrap_list_parses_as_shell(tmp_path):
    """A line-continuation mistake here would silently fetch nothing."""
    result = pytest.importorskip("subprocess").run(
        ["bash", "-n", os.path.join(REPO, "install.sh")],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
