"""Bump the release version in every place the repository declares it.

Run from anywhere: ``python tools/bump-version.py 0.1.0``. Without an argument
it keeps the current ``core/version.py`` value, which is only useful to
re-derive the other files. tests/test_version_single_source.py is the gate.
"""

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
current = re.search(r'__version__\s*=\s*"([^"]+)"',
                    (ROOT / "core" / "version.py").read_text(encoding="utf-8")).group(1)
NEW = sys.argv[1].strip() if len(sys.argv) > 1 else current
if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?", NEW):
    raise SystemExit("not a MAJOR.MINOR.PATCH version: %r" % NEW)

# 1. core/version.py - the single source of truth.
path = ROOT / "core" / "version.py"
text = path.read_text(encoding="utf-8")
updated = re.sub(r'^__version__\s*=\s*"[^"]+"', '__version__ = "%s"' % NEW,
                 text, count=1, flags=re.M)
assert updated != text, "core/version.py has no __version__ line"
path.write_text(updated, encoding="utf-8", newline="\n")

# 2. VERSION marker (used by LiveUSB ISO & Creator builds).
(ROOT / "VERSION").write_text(NEW + "\n", encoding="utf-8", newline="\n")

# 3. server.json MCP Registry manifest (if present).
srv = ROOT / "server.json"
if srv.is_file():
    data = json.loads(srv.read_text(encoding="utf-8"))
    data["version"] = NEW
    srv.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8", newline="\n")

# 4. READMEs (**Version:** X.Y.Z line checked by test_version_single_source.py).
for name in ("README.md", "README_RU.md"):
    path = ROOT / name
    text = path.read_text(encoding="utf-8")
    updated = re.sub(r"^(\*\*Version:\*\*\s+)\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?",
                     r"\g<1>%s" % NEW, text, count=1, flags=re.M)
    if updated != text:
        path.write_text(updated, encoding="utf-8", newline="\n")
    print("%-14s %s" % (name, "updated" if updated != text else "unchanged"))

print("core/version.py :", re.search(r'__version__\s*=\s*"([^"]+)"',
                                     (ROOT / "core" / "version.py").read_text(encoding="utf-8")).group(1))
print("VERSION         :", (ROOT / "VERSION").read_text(encoding="utf-8").strip())
