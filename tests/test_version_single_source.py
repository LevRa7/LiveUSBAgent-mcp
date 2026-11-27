"""The node version is declared once, and everything else agrees with it.

Why this file exists: the updater decides whether to install a release by
comparing the release tag with ``core/version.py``. If the npm package, the
installer or the release workflow carried a second, drifting number, a node
would either never update (it would think it is already newer) or update in a
loop (it would install the same version forever). Both failures are silent, so
the agreement is a test, not a convention.
"""

import json
import os
import re
import subprocess
import sys

import pytest

from core import version as version_module

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Directories that hold generated or vendored copies of the tree.
SKIP_DIRS = {"__pycache__", "node_modules", "live_logs", "docs", "tests", "dist",
             ".win-test-deps", ".pytest-tmp", ".verify-temp", ".verify-payload"}


def _read(relative):
    with open(os.path.join(REPO, relative), "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def test_the_version_is_a_documented_shape():
    assert re.fullmatch(version_module.VERSION_PATTERN, version_module.__version__), (
        "core/version.py must declare MAJOR.MINOR.PATCH[-pre], got %r"
        % version_module.__version__)


def test_version_file_agrees_with_the_node():
    marker = _read("VERSION").strip()
    assert marker == version_module.__version__, (
        "VERSION (%s) and core/version.py (%s) disagree"
        % (marker, version_module.__version__))


def test_every_other_declaration_of_the_version_is_derived():
    """No second literal: a Python file that repeats the version is a drift risk."""
    literal = version_module.__version__
    offenders = []
    for current, directories, files in os.walk(REPO):
        directories[:] = [name for name in directories
                          if name not in SKIP_DIRS and not name.startswith(".")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(current, name)
            relative = os.path.relpath(path, REPO)
            if relative == os.path.join("core", "version.py"):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            for line in text.splitlines():
                if "__version__" in line and literal in line:
                    offenders.append("%s: %s" % (relative, line.strip()))
    assert offenders == [], "the version is declared outside core/version.py: %s" % offenders


#: The line the README carries so a reader can see which release they are on.
#: Kept in the README on purpose: the number a reader copies must be the number
#: ``core/version.py`` declares, and this pattern is the only place it is read.
README_VERSION_LINE = re.compile(
    r"^\*\*Version:\*\*\s+(\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?)", re.M)


def test_the_docs_name_the_current_version():
    """The README's stated version must match the shipped node.

    Upstream guarded this with the ``RemoteHarnessMCP-Setup-<version>.exe``
    name. The LiveUSB fork is a bootable Debian ISO and does **not** ship that
    Windows installer, so the executable-name assertion is deliberately dropped
    (there is no assert on ``*-Setup-*.exe`` here, by design). The invariant it
    protected is kept: the README states one version, in a real sentence, and it
    must equal ``core/version.py`` - no second literal can drift unnoticed.
    """
    readme = _read("README.md")
    mentioned = set(README_VERSION_LINE.findall(readme))
    assert mentioned, (
        "the README should state the release it documents on a "
        "'**Version:** X.Y.Z' line")
    assert mentioned == {version_module.__version__}, (
        "the README advertises %s while the node is %s"
        % (sorted(mentioned), version_module.__version__))


def test_the_release_notes_file_exists_for_this_version():
    assert os.path.isfile(os.path.join(REPO, "docs", "releases", "v%s.md" % version_module.__version__)), (
        "the release workflow takes the notes from docs/releases/v<version>.md")


def test_the_build_refuses_a_mismatch():
    """build-creator-exe.ps1 is the gate that catches a bad bump locally."""
    build = _read("build-creator-exe.ps1")
    assert "core\\version.py" in build
    assert "version mismatch" in build


@pytest.mark.skipif(os.name != "nt", reason="the gate runs in Windows PowerShell")
def test_the_build_gate_actually_stops_a_mismatch(tmp_path):
    """Copy the build script's version check into a probe and run it for real.

    The check is what stands between "package.json bumped by hand" and a release
    whose asset the node refuses to install, so it is verified, not just present.
    """
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        "$packageVersion = '9.9.9'\n"
        "$moduleVersion = (Select-String -LiteralPath '%s' -Pattern "
        "'^__version__\\s*=\\s*\"([^\"]+)\"' | Select-Object -First 1).Matches[0].Groups[1].Value\n"
        "if ($moduleVersion -ne $packageVersion) { Write-Host ('mismatch ' + $moduleVersion); exit 1 }\n"
        "exit 0\n" % os.path.join(REPO, "core", "version.py").replace("'", "''"),
        encoding="ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 1
    assert version_module.__version__ in result.stdout


def test_python_can_import_every_shipped_module():
    """A syntax error in a shipped module must fail here, not on a client's node."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import core.agent, core.mcp_tools, core.server, core.version"],
        cwd=REPO, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
