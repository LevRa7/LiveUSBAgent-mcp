#!/usr/bin/env python3
"""Pure-Python .deb builder for LiveUSBAgent Creator (Linux GUI).

Builds ``dist/liveusb-agent-creator_<version>_all.deb`` (+ ``.sha256``)
using only the Python 3 standard library (works on Windows, Linux, and macOS).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent


def _ar_entry(name: str, payload: bytes, mtime: int) -> bytes:
    header = (
        f"{name:<16}"
        f"{mtime:<12}"
        f"{0:<6}"
        f"{0:<6}"
        f"{'100644':<8}"
        f"{len(payload):<10}"
        "`\n"
    ).encode("ascii")
    if len(header) != 60:
        raise ValueError(f"Invalid ar header length for {name}: {len(header)}")
    pad = b"\n" if (len(payload) % 2 == 1) else b""
    return header + payload + pad


def _build_tar_gz(entries: List[Tuple[str, bytes, int]], mtime: int) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=mtime) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tf:
            seen_dirs = set()
            for rel_path, content, mode in entries:
                norm = rel_path.lstrip("/")
                parts = norm.split("/")
                for i in range(1, len(parts)):
                    d = "./" + "/".join(parts[:i]) + "/"
                    if d not in seen_dirs:
                        seen_dirs.add(d)
                        di = tarfile.TarInfo(name=d)
                        di.type = tarfile.DIRTYPE
                        di.mode = 0o755
                        di.uid = 0
                        di.gid = 0
                        di.uname = "root"
                        di.gname = "root"
                        di.mtime = mtime
                        tf.addfile(di)
                ti = tarfile.TarInfo(name="./" + norm)
                ti.size = len(content)
                ti.mode = mode
                ti.uid = 0
                ti.gid = 0
                ti.uname = "root"
                ti.gname = "root"
                ti.mtime = mtime
                tf.addfile(ti, io.BytesIO(content))
    return buf.getvalue()


def resolve_version() -> str:
    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    ver = str(pkg["version"]).strip()
    sys.path.insert(0, str(REPO_ROOT))
    from core import version as core_version

    if ver != core_version.__version__:
        raise RuntimeError(
            f"Version mismatch: package.json={ver} != core/version.py={core_version.__version__}"
        )
    return ver


def build_deb(output_dir: Path) -> Tuple[Path, Path]:
    version = resolve_version()
    gui_src = (REPO_ROOT / "tools" / "liveusb-creator-gui.py").read_bytes().replace(b"\r\n", b"\n")
    desktop_entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=LiveUSBAgent Creator\n"
        "Comment=Download and write LiveUSBAgent-mcp bootable USB flash drives\n"
        "Exec=/usr/bin/liveusb-agent-creator\n"
        "Icon=drive-removable-media-usb\n"
        "Terminal=false\n"
        "Categories=System;Utility;\n"
    ).encode("utf-8")

    installed_kb = max(16, (len(gui_src) + len(desktop_entry) + 1023) // 1024)
    control_text = (
        "Package: liveusb-agent-creator\n"
        f"Version: {version}\n"
        "Section: utils\n"
        "Priority: optional\n"
        "Architecture: all\n"
        "Depends: python3, python3-tk, coreutils, util-linux, policykit-1 | pkexec\n"
        f"Installed-Size: {installed_kb}\n"
        "Maintainer: LevRa7 <noreply@github.com>\n"
        "Homepage: https://github.com/LevRa7/LiveUSBAgent-mcp\n"
        "Description: Bootable LiveUSB Creator GUI for LiveUSBAgent-mcp\n"
        " Downloads official LiveUSBAgent-mcp ISO images from GitHub Releases\n"
        " (or opens a local .iso) and writes them raw to a removable USB drive.\n"
    ).encode("utf-8")

    mtime = int(os.environ.get("SOURCE_DATE_EPOCH") or time.time())
    debian_binary = b"2.0\n"
    control_tar_gz = _build_tar_gz([("control", control_text, 0o644)], mtime)
    data_tar_gz = _build_tar_gz(
        [
            ("usr/bin/liveusb-agent-creator", gui_src, 0o755),
            ("usr/share/applications/liveusb-agent-creator.desktop", desktop_entry, 0o644),
        ],
        mtime,
    )

    deb_bytes = (
        b"!<arch>\n"
        + _ar_entry("debian-binary", debian_binary, mtime)
        + _ar_entry("control.tar.gz", control_tar_gz, mtime)
        + _ar_entry("data.tar.gz", data_tar_gz, mtime)
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    deb_name = f"liveusb-agent-creator_{version}_all.deb"
    deb_path = output_dir / deb_name
    deb_path.write_bytes(deb_bytes)

    digest = hashlib.sha256(deb_bytes).hexdigest()
    sha_path = output_dir / f"{deb_name}.sha256"
    sha_path.write_text(f"{digest}  {deb_name}\n", encoding="ascii")
    print(f"Built: {deb_path} ({len(deb_bytes)} bytes, sha256={digest})")
    return deb_path, sha_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build liveusb-agent-creator .deb package")
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "dist"),
        help="Destination directory for .deb and .sha256",
    )
    args = parser.parse_args(argv)
    build_deb(Path(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
