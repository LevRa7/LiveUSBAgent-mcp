#!/usr/bin/env python3
"""LiveUSBAgent-mcp - Bootable LiveUSB Creator (Linux GUI).

Downloads prebuilt LiveUSB ISOs from GitHub Releases or uses a local .iso
and writes it raw to a removable USB flash drive using pkexec/sudo + dd.
Uses only Python 3 stdlib + tkinter.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/LevRa7/LiveUSBAgent-mcp/releases/latest"
)
USER_AGENT = "LiveUSBAgent-Creator-Linux/1.0"

ARCH_CHOICES: List[Tuple[str, str]] = [
    ("amd64", "amd64 - Standard 64-bit PC / Laptop (UEFI + BIOS)"),
    ("arm64", "arm64 - ARM64 PC / Mac / Raspberry Pi (UEFI)"),
    ("i386", "i386  - Old 32-bit PC (BIOS, console TUI)"),
]


def format_size_gb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 ** 3):.2f} GB"


def parse_lsblk_usb_disks(raw_json: str) -> List[Dict[str, Any]]:
    """Parse ``lsblk -J -b -o NAME,PATH,SIZE,MODEL,TRAN,RM,TYPE`` output.

    Returns ONLY whole disks that are USB or removable (never internal SATA/NVMe).
    """
    if not raw_json or not raw_json.strip():
        return []
    data = json.loads(raw_json)
    devices = data.get("blockdevices") or []
    usb_disks: List[Dict[str, Any]] = []
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        if str(dev.get("type") or "").lower() != "disk":
            continue
        name = str(dev.get("name") or "").strip()
        if not name or name.startswith(("loop", "ram", "zram", "sr", "dm-")):
            continue
        tran = str(dev.get("tran") or "").strip().lower()
        rm_val = dev.get("rm")
        is_rm = rm_val is True or str(rm_val).strip().lower() in ("1", "true")
        if tran != "usb" and not is_rm:
            continue
        path = str(dev.get("path") or f"/dev/{name}").strip()
        try:
            size_bytes = int(dev.get("size") or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        if size_bytes <= 0:
            continue
        model = str(dev.get("model") or "USB Drive").strip()
        usb_disks.append(
            {
                "name": name,
                "path": path,
                "size": size_bytes,
                "model": model,
                "tran": tran or "removable",
                "label": f"{path} - {model} ({format_size_gb(size_bytes)}, USB)",
            }
        )
    usb_disks.sort(key=lambda item: item["path"])
    return usb_disks


def detect_usb_disks() -> List[Dict[str, Any]]:
    """Query lsblk on Linux for removable/USB whole-disk targets."""
    if not shutil.which("lsblk"):
        return []
    proc = subprocess.run(
        ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,MODEL,TRAN,RM,TYPE"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return parse_lsblk_usb_disks(proc.stdout)


def select_release_iso_asset(
    release_payload: Dict[str, Any], arch: str
) -> Optional[Dict[str, Any]]:
    """Find the matching ``LiveUSBAgent-*-<arch>-*.iso`` asset in a GitHub Release."""
    arch_clean = arch.strip().lower()
    assets = release_payload.get("assets") or []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if (
            name.startswith("LiveUSBAgent-")
            and f"-{arch_clean}-" in name.lower()
            and name.endswith(".iso")
        ):
            return asset
    return None


def fetch_latest_release(timeout: float = 20.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        GITHUB_LATEST_RELEASE_API,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_flash_shell_command(iso_path: str, device_path: str) -> str:
    """Build the privileged bash command that unmounts and flashes a USB disk."""
    q_iso = shlex.quote(iso_path)
    q_dev = shlex.quote(device_path)
    return (
        "set -euo pipefail; "
        f"DEV={q_dev}; ISO={q_iso}; "
        'if [ ! -b "$DEV" ]; then echo "ERROR: Not a block device: $DEV" >&2; exit 2; fi; '
        'if [ ! -f "$ISO" ]; then echo "ERROR: ISO not found: $ISO" >&2; exit 2; fi; '
        'for part in "${DEV}"?*; do [ -b "$part" ] && umount "$part" 2>/dev/null || true; done; '
        'dd if="$ISO" of="$DEV" bs=4M conv=fsync oflag=sync status=progress; '
        "sync; "
        'echo "DONE: Successfully wrote $ISO to $DEV"'
    )


def run_self_test() -> int:
    sample = json.dumps(
        {
            "blockdevices": [
                {"name": "nvme0n1", "path": "/dev/nvme0n1", "size": 512000000000, "model": "NVMe SSD", "tran": "nvme", "rm": False, "type": "disk"},
                {"name": "sdb", "path": "/dev/sdb", "size": 7813988352, "model": "Transcend 8GB", "tran": "usb", "rm": True, "type": "disk"},
                {"name": "loop0", "path": "/dev/loop0", "size": 104857600, "model": "", "tran": None, "rm": False, "type": "loop"},
            ]
        }
    )
    disks = parse_lsblk_usb_disks(sample)
    assert len(disks) == 1 and disks[0]["path"] == "/dev/sdb", f"unexpected disks: {disks}"
    rel = {
        "tag_name": "v0.1.2",
        "assets": [
            {"name": "LiveUSBAgent-0.1.2-amd64-full.iso", "size": 2100000000, "browser_download_url": "https://example.invalid/amd64.iso"},
            {"name": "LiveUSBAgent-0.1.2-amd64-full.iso.sha256", "size": 90, "browser_download_url": "https://example.invalid/amd64.sha256"},
        ],
    }
    asset = select_release_iso_asset(rel, "amd64")
    assert asset is not None and asset["name"] == "LiveUSBAgent-0.1.2-amd64-full.iso"
    cmd = build_flash_shell_command("/tmp/test.iso", "/dev/sdb")
    assert "dd if=" in cmd and "/dev/sdb" in cmd
    print("LiveUSBAgent Creator Linux self-test: OK")
    return 0


def launch_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    downloads_dir = Path.home() / "Downloads"
    if not downloads_dir.is_dir():
        downloads_dir = Path.home()

    root = tk.Tk()
    root.title("LiveUSBAgent-mcp - Bootable LiveUSB Creator")
    root.geometry("740x640")
    root.resizable(False, False)

    usb_disks: List[Dict[str, Any]] = []
    cancel_download = threading.Event()
    is_busy = {"downloading": False, "writing": False}

    def log(msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        txt_log.configure(state="normal")
        txt_log.insert("end", f"[{stamp}] {msg}\n")
        txt_log.see("end")
        txt_log.configure(state="disabled")

    def ui_call(fn, *args) -> None:
        root.after(0, lambda: fn(*args))

    # --- Group 1: ISO Download / Select ---
    grp_iso = ttk.LabelFrame(
        root,
        text="1. LiveUSB ISO Image (Download from GitHub Releases or Select Local .iso)",
        padding=10,
    )
    grp_iso.pack(fill="x", padx=14, pady=(12, 8))

    row1 = ttk.Frame(grp_iso)
    row1.pack(fill="x", pady=(0, 8))
    ttk.Label(row1, text="Target PC:").pack(side="left")
    arch_var = tk.StringVar(value=ARCH_CHOICES[0][1])
    cmb_arch = ttk.Combobox(
        row1,
        textvariable=arch_var,
        values=[label for _, label in ARCH_CHOICES],
        state="readonly",
        width=48,
    )
    cmb_arch.pack(side="left", padx=(8, 10))
    btn_download = ttk.Button(row1, text="Download Latest ISO", width=22)
    btn_download.pack(side="right")

    row2 = ttk.Frame(grp_iso)
    row2.pack(fill="x", pady=(0, 8))
    ttk.Label(row2, text="ISO File:").pack(side="left")
    iso_var = tk.StringVar()
    existing_isos = sorted(
        downloads_dir.glob("LiveUSBAgent-*.iso"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if existing_isos:
        iso_var.set(str(existing_isos[0]))
    ent_iso = ttk.Entry(row2, textvariable=iso_var, width=58)
    ent_iso.pack(side="left", padx=(14, 10), fill="x", expand=True)
    btn_browse = ttk.Button(row2, text="Browse...", width=12)
    btn_browse.pack(side="right")

    progress = ttk.Progressbar(grp_iso, orient="horizontal", mode="determinate", maximum=100)
    progress.pack(fill="x", pady=(0, 6))

    hint_var = tk.StringVar(
        value="Click 'Download Latest ISO' to fetch the official prebuilt image into ~/Downloads, or select a local .iso."
    )
    ttk.Label(grp_iso, textvariable=hint_var, foreground="#555555", wraplength=680).pack(
        anchor="w"
    )

    # --- Group 2: Target USB Flash Drive ---
    grp_usb = ttk.LabelFrame(root, text="2. Target Removable USB Flash Drive", padding=10)
    grp_usb.pack(fill="x", padx=14, pady=8)

    row_usb = ttk.Frame(grp_usb)
    row_usb.pack(fill="x", pady=(0, 8))
    usb_var = tk.StringVar()
    cmb_usb = ttk.Combobox(row_usb, textvariable=usb_var, state="readonly", width=62)
    cmb_usb.pack(side="left", fill="x", expand=True, padx=(0, 10))
    btn_refresh = ttk.Button(row_usb, text="Refresh USB", width=14)
    btn_refresh.pack(side="right")

    tk.Label(
        grp_usb,
        text=(
            "WARNING: Writing the LiveUSB image will FORMAT and PERMANENTLY ERASE ALL DATA "
            "on the selected USB flash drive! Double-check the drive model and size before continuing."
        ),
        fg="#8B0000",
        font=("TkDefaultFont", 9, "bold"),
        wraplength=680,
        justify="left",
    ).pack(anchor="w", pady=(2, 8))

    confirm_var = tk.BooleanVar(value=False)
    chk_confirm = ttk.Checkbutton(
        grp_usb,
        text="I understand that the selected USB drive will be formatted and all existing data will be erased.",
        variable=confirm_var,
    )
    chk_confirm.pack(anchor="w")

    # --- Action & Log ---
    btn_flash = ttk.Button(root, text="Write Bootable LiveUSB Flash Drive", state="disabled")
    btn_flash.pack(fill="x", padx=14, pady=(8, 8), ipady=6)

    log_frame = ttk.Frame(root)
    log_frame.pack(fill="both", expand=True, padx=14, pady=(0, 12))
    txt_log = tk.Text(log_frame, height=9, state="disabled", font=("Monospace", 9))
    scroll = ttk.Scrollbar(log_frame, command=txt_log.yview)
    txt_log.configure(yscrollcommand=scroll.set)
    txt_log.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    def update_flash_state(*_args) -> None:
        has_usb = len(usb_disks) > 0
        iso_path = iso_var.get().strip()
        has_iso = bool(iso_path) and os.path.isfile(iso_path)
        ready = (
            has_usb
            and has_iso
            and confirm_var.get()
            and not is_busy["downloading"]
            and not is_busy["writing"]
        )
        btn_flash.configure(state="normal" if ready else "disabled")

    def refresh_usb() -> None:
        nonlocal usb_disks
        usb_disks = detect_usb_disks()
        if usb_disks:
            cmb_usb.configure(values=[d["label"] for d in usb_disks])
            cmb_usb.current(0)
            log(f"Detected {len(usb_disks)} removable USB drive(s).")
        else:
            cmb_usb.configure(values=["(No removable USB drives detected)"])
            cmb_usb.current(0)
            log("No removable USB drives detected. Insert a USB stick and click Refresh USB.")
        update_flash_state()

    def browse_iso() -> None:
        chosen = filedialog.askopenfilename(
            title="Select LiveUSB ISO Image",
            initialdir=str(downloads_dir),
            filetypes=[("ISO Images", "*.iso"), ("All Files", "*.*")],
        )
        if chosen:
            iso_var.set(chosen)
            log(f"Selected ISO: {chosen}")

    def toggle_download() -> None:
        if is_busy["downloading"]:
            cancel_download.set()
            log("Cancelling download...")
            return

        selected_label = arch_var.get()
        arch_code = selected_label.split()[0].strip()
        cancel_download.clear()
        is_busy["downloading"] = True
        btn_download.configure(text="Cancel Download")
        btn_browse.configure(state="disabled")
        cmb_arch.configure(state="disabled")
        progress["value"] = 0
        update_flash_state()
        log(f"Checking latest GitHub Release for {arch_code} ISO...")

        def worker() -> None:
            try:
                release = fetch_latest_release()
                tag = str(release.get("tag_name") or "latest")
                asset = select_release_iso_asset(release, arch_code)
                if not asset:
                    ui_call(log, f"ERROR: No {arch_code} ISO found in release {tag}.")
                    ui_call(
                        messagebox.showwarning,
                        "ISO Not Found",
                        f"No {arch_code} ISO asset was found in release {tag}.",
                    )
                    return
                name = str(asset["name"])
                url = str(asset["browser_download_url"])
                total = int(asset.get("size") or 0)
                dest = downloads_dir / name
                part_dest = downloads_dir / (name + ".part")
                ui_call(
                    log,
                    f"Downloading {name} ({total / (1024 ** 2):.1f} MB) from {tag}...",
                )
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                downloaded = 0
                with urllib.request.urlopen(req, timeout=30) as resp, open(part_dest, "wb") as out:
                    if not total:
                        try:
                            total = int(resp.headers.get("Content-Length") or 0)
                        except ValueError:
                            total = 0
                    while True:
                        if cancel_download.is_set():
                            out.close()
                            part_dest.unlink(missing_ok=True)
                            ui_call(log, "Download cancelled.")
                            ui_call(hint_var.set, "Download cancelled.")
                            ui_call(progress.configure, {"value": 0})
                            return
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(100.0, downloaded * 100.0 / total)
                            ui_call(progress.configure, {"value": pct})
                            ui_call(
                                hint_var.set,
                                f"Downloading {name}: {downloaded / (1024**2):.1f} MB / {total / (1024**2):.1f} MB ({pct:.0f}%)",
                            )
                os.replace(part_dest, dest)
                ui_call(progress.configure, {"value": 100})
                ui_call(iso_var.set, str(dest))
                ui_call(hint_var.set, f"Downloaded: {dest}")
                ui_call(log, f"Download complete: {dest}")
            except Exception as exc:
                ui_call(log, f"Download error: {exc}")
                ui_call(hint_var.set, "Download failed. Check your connection or select a local .iso.")
                ui_call(progress.configure, {"value": 0})
            finally:
                def finish() -> None:
                    is_busy["downloading"] = False
                    btn_download.configure(text="Download Latest ISO")
                    btn_browse.configure(state="normal")
                    cmb_arch.configure(state="readonly")
                    update_flash_state()

                ui_call(finish)

        threading.Thread(target=worker, daemon=True).start()

    def flash_usb() -> None:
        idx = cmb_usb.current()
        if idx < 0 or idx >= len(usb_disks):
            return
        disk = usb_disks[idx]
        iso_path = iso_var.get().strip()
        if not os.path.isfile(iso_path):
            messagebox.showerror("Error", f"ISO file not found:\n{iso_path}")
            return
        iso_size = os.path.getsize(iso_path)
        if iso_size > disk["size"]:
            messagebox.showerror(
                "Error",
                f"ISO size ({format_size_gb(iso_size)}) exceeds {disk['path']} ({format_size_gb(disk['size'])}).",
            )
            return
        confirmed = messagebox.askyesno(
            "Confirm USB Format & Erase",
            (
                f"FINAL WARNING:\n\n"
                f"ALL DATA on {disk['path']} ({disk['model']}, {format_size_gb(disk['size'])}) "
                f"will be FORMATTED and PERMANENTLY ERASED!\n\n"
                f"ISO: {iso_path}\n\n"
                f"Proceed with writing the bootable LiveUSB?"
            ),
            icon="warning",
        )
        if not confirmed:
            log("USB write cancelled by user.")
            return

        shell_cmd = build_flash_shell_command(iso_path, disk["path"])
        if os.geteuid() == 0:
            argv = ["bash", "-c", shell_cmd]
        elif shutil.which("pkexec"):
            argv = ["pkexec", "bash", "-c", shell_cmd]
        elif shutil.which("sudo"):
            argv = ["sudo", "bash", "-c", shell_cmd]
        else:
            messagebox.showerror(
                "Elevation Required",
                "Neither pkexec nor sudo was found. Run liveusb-agent-creator as root.",
            )
            return

        is_busy["writing"] = True
        update_flash_state()
        log(f"Writing {iso_path} to {disk['path']} (authorization prompt may appear)...")

        def write_worker() -> None:
            try:
                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    clean = line.strip()
                    if clean:
                        ui_call(log, clean)
                rc = proc.wait()
                if rc == 0:
                    ui_call(log, "Bootable LiveUSB flash drive is ready! You can safely eject it.")
                    ui_call(
                        messagebox.showinfo,
                        "Success",
                        f"LiveUSB successfully written to {disk['path']}.\nYou can safely eject the USB drive.",
                    )
                else:
                    ui_call(log, f"ERROR: Flash process exited with code {rc}.")
                    ui_call(
                        messagebox.showerror,
                        "Write Failed",
                        f"Writing to {disk['path']} failed (exit code {rc}). See log for details.",
                    )
            except Exception as exc:
                ui_call(log, f"ERROR: {exc}")
                ui_call(messagebox.showerror, "Write Failed", str(exc))
            finally:
                def finish_write() -> None:
                    is_busy["writing"] = False
                    update_flash_state()

                ui_call(finish_write)

        threading.Thread(target=write_worker, daemon=True).start()

    btn_refresh.configure(command=refresh_usb)
    btn_browse.configure(command=browse_iso)
    btn_download.configure(command=toggle_download)
    btn_flash.configure(command=flash_usb)
    iso_var.trace_add("write", update_flash_state)
    confirm_var.trace_add("write", update_flash_state)

    refresh_usb()
    root.mainloop()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LiveUSBAgent-mcp Bootable LiveUSB Creator")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run non-interactive self-test and exit 0",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    return launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
