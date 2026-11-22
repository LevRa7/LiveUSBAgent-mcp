#!/usr/bin/env bash
# ops/live-usb/fetch-skills.sh
#
# Fetches curated external SKILL.md packages listed in skills-manifest.json into
# skills/liveusb/external/, records resolved commit SHAs in skills-lock.json,
# and runs NVIDIA SkillSpector (--recursive --no-llm) if installed on the host.
#
# Usage:
#   ./ops/live-usb/fetch-skills.sh [--manifest PATH] [--only ID] [--dry-run] [--continue-on-error]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MANIFEST_PATH="${SCRIPT_DIR}/skills-manifest.json"
ONLY_ID=""
DRY_RUN="0"
CONTINUE_ON_ERROR="0"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --manifest)
            MANIFEST_PATH="$2"
            shift 2
            ;;
        --only)
            ONLY_ID="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN="1"
            shift
            ;;
        --continue-on-error)
            CONTINUE_ON_ERROR="1"
            shift
            ;;
        -h|--help)
            sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is required to parse ${MANIFEST_PATH}" >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is required to fetch skill repositories" >&2
    exit 1
fi

if [[ ! -f "${MANIFEST_PATH}" ]]; then
    echo "ERROR: Manifest not found: ${MANIFEST_PATH}" >&2
    exit 1
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rhm-fetch-skills.XXXXXX")"
cleanup() {
    rm -rf "${TMP_ROOT}"
}
trap cleanup EXIT

DEST_ROOT="$(python3 - "${MANIFEST_PATH}" "${REPO_ROOT}" "${TMP_ROOT}" "${ONLY_ID}" "${DRY_RUN}" "${CONTINUE_ON_ERROR}" <<'PYEOF'
import datetime
import json
import os
import shutil
import subprocess
import sys

manifest_path, repo_root, tmp_root, only_id, dry_run_flag, continue_flag = sys.argv[1:7]
dry_run = dry_run_flag == "1"
continue_on_error = continue_flag == "1"

with open(manifest_path, "r", encoding="utf-8") as fh:
    manifest = json.load(fh)

dest_rel = manifest.get("destination", "skills/liveusb/external")
dest_root = os.path.join(repo_root, dest_rel)
lock_rel = manifest.get("lockfile", "ops/live-usb/skills-lock.json")
lock_path = os.path.join(repo_root, lock_rel)
repos = manifest.get("repositories", [])

if only_id:
    repos = [r for r in repos if r.get("id") == only_id]
    if not repos:
        print(f"ERROR: No repository with id '{only_id}' in {manifest_path}", file=sys.stderr)
        sys.exit(1)

if not dry_run:
    os.makedirs(dest_root, exist_ok=True)


def run_git(args, cwd=None):
    res = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def copy_clean(src_path, dst_path):
    if os.path.isdir(src_path):
        if os.path.exists(dst_path):
            shutil.rmtree(dst_path)
        shutil.copytree(
            src_path,
            dst_path,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".DS_Store"),
        )
    elif os.path.isfile(src_path):
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        shutil.copy2(src_path, dst_path)


def discover_skill_dirs(base_dir):
    found = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__", "node_modules"))
        if "SKILL.md" in files:
            found.append(root)
    return found


lock_entries = []
if os.path.isfile(lock_path) and only_id:
    try:
        with open(lock_path, "r", encoding="utf-8") as fh:
            existing = json.load(fh)
            lock_entries = [e for e in existing.get("repositories", []) if e.get("id") != only_id]
    except Exception:
        lock_entries = []

errors = []
for entry in repos:
    repo_id = entry["id"]
    url = entry["url"]
    dest_subdir = entry.get("dest_subdir", repo_id)
    paths = entry.get("paths", ["."])
    use_sparse = paths != ["."]
    clone_dir = os.path.join(tmp_root, repo_id)
    target_base = os.path.join(dest_root, dest_subdir)

    print(f"==> [{repo_id}] {url}", file=sys.stderr)
    if dry_run:
        print(f"    (dry-run) paths={paths} -> {target_base}", file=sys.stderr)
        continue

    if use_sparse:
        rc, _, err = run_git(
            ["clone", "--depth", "1", "--filter=blob:none", "--sparse", url, clone_dir]
        )
        if rc != 0:
            # Fallback for older git servers without partial clone support
            if os.path.exists(clone_dir):
                shutil.rmtree(clone_dir)
            rc, _, err = run_git(["clone", "--depth", "1", url, clone_dir])
        else:
            run_git(["sparse-checkout", "set", "--no-cone"] + paths, cwd=clone_dir)
    else:
        rc, _, err = run_git(["clone", "--depth", "1", url, clone_dir])

    if rc != 0:
        msg = f"[{repo_id}] git clone failed: {err}"
        print(f"ERROR: {msg}", file=sys.stderr)
        errors.append(msg)
        if not continue_on_error:
            sys.exit(1)
        lock_entries.append({
            "id": repo_id,
            "repo": entry.get("repo", ""),
            "url": url,
            "status": "error",
            "error": err,
        })
        continue

    _, commit_sha, _ = run_git(["rev-parse", "HEAD"], cwd=clone_dir)
    copied = []

    if paths == ["."]:
        copy_clean(clone_dir, target_base)
        copied.append(".")
    else:
        os.makedirs(target_base, exist_ok=True)
        for rel_p in paths:
            src_p = os.path.join(clone_dir, rel_p)
            if os.path.exists(src_p):
                leaf = os.path.basename(rel_p.rstrip("/"))
                copy_clean(src_p, os.path.join(target_base, leaf))
                copied.append(rel_p)

        # Fallback: match by keywords or all SKILL.md dirs if repo layout shifted
        if not copied and entry.get("fallback_keywords"):
            keywords = [k.lower() for k in entry["fallback_keywords"]]
            for sdir in discover_skill_dirs(clone_dir):
                rel_s = os.path.relpath(sdir, clone_dir).replace(os.sep, "/")
                if any(kw in rel_s.lower() for kw in keywords):
                    copy_clean(sdir, os.path.join(target_base, os.path.basename(sdir)))
                    copied.append(rel_s)

        if not copied and entry.get("fallback_all_skills"):
            for sdir in discover_skill_dirs(clone_dir):
                rel_s = os.path.relpath(sdir, clone_dir).replace(os.sep, "/")
                copy_clean(sdir, os.path.join(target_base, os.path.basename(sdir)))
                copied.append(rel_s)

    print(f"    commit={commit_sha[:12]} copied={len(copied)} path(s)", file=sys.stderr)
    lock_entries.append({
        "id": repo_id,
        "repo": entry.get("repo", ""),
        "url": url,
        "license": entry.get("license", ""),
        "category": entry.get("category", ""),
        "commit": commit_sha,
        "dest_subdir": dest_subdir,
        "copied_paths": sorted(copied),
        "status": "ok" if copied else "empty",
    })

if not dry_run:
    lock_payload = {
        "schema_version": 1,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "destination": dest_rel,
        "repositories": sorted(lock_entries, key=lambda x: x.get("id", "")),
    }
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as fh:
        json.dump(lock_payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"==> Wrote lockfile: {lock_path}", file=sys.stderr)

print(dest_root)
if errors and not continue_on_error:
    sys.exit(1)
PYEOF
)"

if [[ "${DRY_RUN}" == "1" ]]; then
    echo "==> Dry run complete."
    exit 0
fi

if command -v skillspector >/dev/null 2>&1; then
    echo "==> Running SkillSpector security scan on ${DEST_ROOT}..."
    skillspector --recursive --no-llm "${DEST_ROOT}"
else
    echo "==> Note: 'skillspector' is not installed on this host; skipping offline prompt-injection scan."
    echo "    Install NVIDIA/SkillSpector to enable automated scanning during ISO builds."
fi

echo "==> Done fetching skills into ${DEST_ROOT}."
