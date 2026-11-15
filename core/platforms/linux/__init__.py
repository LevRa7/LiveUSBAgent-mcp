"""core/platforms/linux - the Linux backend.

Linux is the platform the node was written on, so it is the reference for what
"the full set of tools" means.  This backend keeps that behaviour but removes
its last external dependencies: telemetry reads ``/proc`` instead of running
``df``/``free``/``ps``/``uptime``, and process control is a syscall instead of
``ps``/``kill``.

There is deliberately **no screenshot backend here.**  Linux has no portable
screen API: an X server needs a client library the node does not ship, a Wayland
session refuses to hand one client another's pixels, and a server has no display
at all.  Guessing at one of those was rejected by the operator, so the platform
answers ``None`` and ``screen_capture`` reports that instead of pretending.
Capture from a Windows or macOS node.

macOS lives in :mod:`core.platforms.darwin`: it is POSIX with the same shell, but
none of the ``/proc`` interface this module reads.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, List, Optional, Tuple

from core.platforms import common, encoding, shell
from core.platforms.base import PlatformBackend
from core.platforms.linux import process as _process
from core.platforms.linux import telemetry as _telemetry
from core.platforms.linux.procfs import ProcFS


class LinuxBackend(PlatformBackend):
    scenario = "linux"
    label = "Linux"

    def __init__(self) -> None:
        self._shell_cache: Optional[Tuple[str, List[str]]] = None
        self._encodings: Optional[List[str]] = None

    # -- command execution -------------------------------------------------

    def shell(self) -> Tuple[str, List[str]]:
        if self._shell_cache is None:
            self._shell_cache = shell.resolve(
                is_windows=False, which=shutil.which, environ=os.environ,
                isfile=os.path.isfile)
        return self._shell_cache

    def child_env(self) -> Dict[str, str]:
        return dict(os.environ)

    def output_encodings(self) -> List[str]:
        if self._encodings is None:
            self._encodings = encoding.candidate_encodings(is_windows=False)
        return self._encodings

    # -- telemetry ---------------------------------------------------------

    def uptime_seconds(self) -> Optional[float]:
        return ProcFS().uptime()

    def vitals(self) -> Dict[str, object]:
        return _telemetry.vitals()

    def info_blocks(self) -> Dict[str, str]:
        return _telemetry.info_blocks()

    def wallpaper(self) -> str:
        """KDE and GNOME store the current wallpaper in a file, not a syscall."""
        kde = os.path.expanduser("~/.config/plasma-org.kde.plasma.desktop-appletsrc")
        if os.path.isfile(kde):
            for line in common.read_text(kde, 262144).splitlines():
                if line.startswith("Image="):
                    return line.strip()[len("Image="):].replace("file://", "")
        gsettings = shutil.which("gsettings")
        if gsettings:
            out, _err, code, _dur = common.run_argv(
                [gsettings, "get", "org.gnome.desktop.background", "picture-uri"], timeout=3.0)
            if code == 0 and "file://" in out:
                return out.split("file://", 1)[1].strip().strip("'\"")
        return ""

    # -- processes ---------------------------------------------------------

    def pid_alive(self, pid: int, start_time: Optional[int] = None) -> bool:
        return _process.alive(pid, start_time)

    def pid_start_time(self, pid: int) -> Optional[int]:
        return _process.start_time_of(pid)

    def kill_tree(self, pid: int) -> None:
        _process.kill_tree(pid)

    # -- display -----------------------------------------------------------

    def screenshot_png(self) -> Optional[bytes]:
        """Always ``None``: Linux has no portable screen API for this node.

        A headless server has no display, a Wayland session refuses cross-client
        pixel access, and an X server would need a client library the node does
        not ship.  The base class already answers ``None``; the override exists
        so the reason is written down where the next reader looks.
        """
        return None

    def capabilities(self) -> Dict[str, bool]:
        return {
            "shell": True,
            "vitals": os.path.isdir("/proc"),
            "info_blocks": os.path.isdir("/proc"),
            "process_tree": os.path.isdir("/proc"),
            "screenshot": False,
            "clipboard": bool(shutil.which("xclip") or shutil.which("xsel")),
        }


__all__ = ["LinuxBackend"]
