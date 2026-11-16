"""Simulate a fresh `curl ... | bash` bootstrap against the public gateway.

Downloads the served install.sh, parses the file list it would fetch, pulls every
file over HTTPS, and then imports the node from that tree - exactly the check
that would have caught 0.5.0's missing core/platforms package.
"""

import os
import re
import subprocess
import sys
import tempfile
import urllib.request

DOMAIN = "https://racknerd-5a24bf9.merino-carob.ts.net"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def main() -> int:
    script = fetch(DOMAIN + "/install.sh").decode("utf-8", "replace")
    match = re.search(r"for f in ([^;]*core/__init__\.py[^;]*); do", script)
    if not match:
        print("FAIL: the served install.sh has no bootstrap loop")
        return 1
    files = re.findall(r"(core/[A-Za-z0-9_/]+\.py|skills/[A-Za-z0-9_.-]+)", match.group(1))
    print("served install.sh lists %d files" % len(files))

    root = tempfile.mkdtemp(prefix="rhm-curl-")
    failed = []
    for relative in files:
        target = os.path.join(root, relative.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            data = fetch("%s/%s" % (DOMAIN, relative))
        except Exception as exc:
            failed.append("%s: %s" % (relative, exc))
            continue
        with open(target, "wb") as handle:
            handle.write(data)
    if failed:
        print("FAIL: %d file(s) not served:" % len(failed))
        for line in failed[:5]:
            print("  " + line)
        return 1

    version = ""
    with open(os.path.join(root, "core", "version.py"), encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("__version__"):
                version = line.split("=", 1)[1].strip().strip('"')
    print("downloaded version: %s" % version)

    result = subprocess.run(
        [sys.executable, "-c", "import core.mcp_tools; print('tools:', len(core.mcp_tools.TOOLS))"],
        cwd=root, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        print("FAIL: the bootstrapped tree does not import")
        print((result.stderr or result.stdout)[-800:])
        return 2
    print("bootstrap OK:", result.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
