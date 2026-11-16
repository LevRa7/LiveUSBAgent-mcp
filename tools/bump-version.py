"""Bump the release version in every place the repository declares it.

Run from the repository root: tests/test_version_single_source.py is the gate
that proves the result is consistent.
"""

import json
import pathlib
import re

NEW = "0.5.0"
ROOT = pathlib.Path(r"D:\MyProjects\Antigravity-mash")

# 1. core/version.py - the single source of truth.
path = ROOT / "core" / "version.py"
text = path.read_text(encoding="utf-8")
updated = re.sub(r'^__version__\s*=\s*"[^"]+"', '__version__ = "%s"' % NEW,
                 text, count=1, flags=re.M)
assert updated != text, "core/version.py has no __version__ line"
path.write_text(updated, encoding="utf-8", newline="\n")

# 2. package.json - the npm package and the build gate.
path = ROOT / "package.json"
package = json.loads(path.read_text(encoding="utf-8"))
package["version"] = NEW
path.write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8", newline="\n")

# 3. the alias package ships in lockstep (npm/publish-alias.sh refuses a drift).
path = ROOT / "npm" / "gemini-computer-use" / "package.json"
alias = json.loads(path.read_text(encoding="utf-8"))
alias["version"] = NEW
alias["dependencies"]["remote-harness-mcp"] = "^" + NEW
path.write_text(json.dumps(alias, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8", newline="\n")

# 4. the READMEs name the setup executable (test_the_docs_name_the_current_version).
for name in ("README.md", "README_RU.md"):
    path = ROOT / name
    text = path.read_text(encoding="utf-8")
    updated = re.sub(r"RemoteHarnessMCP-Setup-\d+\.\d+\.\d+\.exe",
                     "RemoteHarnessMCP-Setup-%s.exe" % NEW, text)
    if updated != text:
        path.write_text(updated, encoding="utf-8", newline="\n")
    print("%-14s %s" % (name, "updated" if updated != text else "unchanged"))

print("core/version.py :", re.search(r'__version__\s*=\s*"([^"]+)"',
                                     (ROOT / "core" / "version.py").read_text(encoding="utf-8")).group(1))
print("package.json    :", json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"])
print("alias package   :", json.loads((ROOT / "npm" / "gemini-computer-use" /
                                       "package.json").read_text(encoding="utf-8"))["version"])
