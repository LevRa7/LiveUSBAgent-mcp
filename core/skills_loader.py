#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/skills_loader.py - Progressive SKILL.md discovery, frontmatter parsing, and MCP tools.

Designed to follow the exact contract of :mod:`core.rescue` and :mod:`core.mcp_tools`:
* Standard library only (Python 3.8+, zero third-party dependencies);
* Never raises unhandled exceptions — every failure is returned as ``{"error": "..."}``;
* Progressive disclosure: ``list_skills`` returns compact metadata only (tiny token
  footprint), while ``load_skill`` loads the full ``SKILL.md`` body or a validated
  file inside the skill's ``references/`` directory with strict path-traversal protection.
"""

from __future__ import annotations

import os
import re
import stat
from typing import Any, Dict, List, Optional, Tuple, Union

#: Default Linux LiveUSB SquashFS skills directory (included when present on disk).
DEFAULT_OPT_SKILLS_DIR = "/opt/remote-harness-mcp/skills"

#: Directories skipped while recursively discovering skills.
_SKIP_DIR_NAMES = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv", "references"})

#: Flat markdown filenames ignored when scanning directories without a SKILL.md.
_IGNORED_FLAT_MD = frozenset({"readme.md", "license.md", "changelog.md"})

#: Read-only MCP tool annotations matching ``tests/test_tool_annotations.py``.
_READ_ONLY_ANNOTATIONS: Dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def _schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        out["required"] = list(required)
    return out


SKILLS_TOOLS_SPEC: List[Dict[str, Any]] = [
    {
        "name": "list_skills",
        "title": "List Available Skills",
        "description": (
            "List locally installed operational skills (SKILL.md packages and flat .md guides) "
            "returning compact metadata only (name, description, category, tags, os, risk_level, "
            "has_references). Supports optional filtering by category, query, and os."
        ),
        "inputSchema": _schema(
            {
                "category": {
                    "type": "string",
                    "description": "Optional category filter (case-insensitive exact match, e.g. 'recovery', 'sysadmin').",
                },
                "query": {
                    "type": "string",
                    "description": "Optional search substring matched against name, description, category, and tags.",
                },
                "os": {
                    "type": "string",
                    "description": "Optional target OS filter (e.g. 'linux', 'windows', 'macos').",
                },
            }
        ),
        "annotations": dict(_READ_ONLY_ANNOTATIONS),
    },
    {
        "name": "load_skill",
        "title": "Load Skill Instructions or Reference File",
        "description": (
            "Load the full SKILL.md body and list of available reference files for a named skill, "
            "or load a specific relative file from the skill's references/ directory."
        ),
        "inputSchema": _schema(
            {
                "name": {
                    "type": "string",
                    "description": "Skill name as returned by list_skills.",
                },
                "reference_file": {
                    "type": "string",
                    "description": (
                        "Optional relative path inside the skill's references/ directory "
                        "(path traversal such as '..' or absolute paths is rejected)."
                    ),
                },
            },
            ["name"],
        ),
        "annotations": dict(_READ_ONLY_ANNOTATIONS),
    },
]


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_skills_dirs() -> List[str]:
    """Return ordered, deduplicated skill root directories that currently exist.

    When ``MESH_SKILLS_DIRS`` is set and non-empty, its ``os.pathsep``-separated
    entries override the default directories. Otherwise defaults to
    ``<repo_root>/skills`` and ``/opt/remote-harness-mcp/skills`` (if present).
    """
    env_val = os.environ.get("MESH_SKILLS_DIRS", "").strip()
    if env_val:
        candidates = [p.strip() for p in env_val.split(os.pathsep) if p.strip()]
    else:
        candidates = [
            os.path.join(_repo_root(), "skills"),
            DEFAULT_OPT_SKILLS_DIR,
        ]

    resolved: List[str] = []
    seen = set()
    for raw_path in candidates:
        try:
            abs_path = os.path.abspath(os.path.expanduser(raw_path))
            real_path = os.path.realpath(abs_path)
            if real_path not in seen and os.path.isdir(abs_path):
                seen.add(real_path)
                resolved.append(abs_path)
        except Exception:
            continue
    return resolved


def _strip_quotes(value: str) -> str:
    val = value.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        return val[1:-1]
    return val


def _strip_inline_comment(value: str) -> str:
    """Strip an unquoted ``# ...`` YAML comment from a scalar value."""
    in_single = False
    in_double = False
    for idx, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or value[idx - 1].isspace():
                return value[:idx].rstrip()
    return value.rstrip()


def _parse_inline_list(raw: str) -> List[str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    items: List[str] = []
    buf: List[str] = []
    in_single = False
    in_double = False
    for ch in inner:
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
        elif ch == "," and not in_single and not in_double:
            token = _strip_quotes("".join(buf).strip())
            if token:
                items.append(token)
            buf = []
        else:
            buf.append(ch)
    tail = _strip_quotes("".join(buf).strip())
    if tail:
        items.append(tail)
    return items


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter (``--- ... ---``) without PyYAML.

    Supports ``name``, ``description``, ``category``, ``tags`` (inline or block list),
    ``os`` (scalar or list), ``risk_level``, plus multiline folded/literal scalars
    (``>`` / ``|``). Falls back gracefully to ``({}, text)`` when absent or malformed.
    """
    if not isinstance(text, str):
        return {}, ""

    cleaned = text.lstrip("\ufeff")
    normalized = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    if not lines or lines[0].strip() != "---":
        return {}, normalized

    end_idx: Optional[int] = None
    for i in range(1, len(lines)):
        stripped = lines[i].strip()
        if stripped in ("---", "..."):
            end_idx = i
            break

    if end_idx is None:
        return {}, normalized

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")

    meta: Dict[str, Any] = {}
    i = 0
    while i < len(fm_lines):
        line = fm_lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        # Top-level key: value
        match = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not match:
            i += 1
            continue

        key = match.group(1).strip().lower()
        raw_val = match.group(2).strip()

        # Multiline block scalar (> or |)
        if raw_val in (">", "|", ">-", "|-"):
            fold = raw_val.startswith(">")
            block_lines: List[str] = []
            i += 1
            while i < len(fm_lines):
                sub = fm_lines[i]
                if sub.strip() == "":
                    block_lines.append("")
                    i += 1
                elif sub.startswith((" ", "\t")):
                    block_lines.append(sub.strip())
                    i += 1
                else:
                    break
            if fold:
                meta[key] = " ".join(part for part in block_lines if part).strip()
            else:
                meta[key] = "\n".join(block_lines).strip()
            continue

        # Block list following an empty value (e.g. tags:\n  - a\n  - b)
        if raw_val == "":
            items: List[str] = []
            i += 1
            while i < len(fm_lines):
                sub = fm_lines[i]
                sub_stripped = sub.strip()
                if not sub_stripped or sub_stripped.startswith("#"):
                    i += 1
                    continue
                list_match = re.match(r"^\s*-\s+(.*)$", sub)
                if list_match:
                    item_val = _strip_quotes(_strip_inline_comment(list_match.group(1)))
                    if item_val:
                        items.append(item_val)
                    i += 1
                else:
                    break
            meta[key] = items
            continue

        # Inline flow list: [a, b, c]
        val_no_comment = _strip_inline_comment(raw_val)
        if val_no_comment.startswith("[") and val_no_comment.endswith("]"):
            meta[key] = _parse_inline_list(val_no_comment)
        else:
            meta[key] = _strip_quotes(val_no_comment)
        i += 1

    return meta, body


def _fallback_description(body: str) -> str:
    """Extract a short summary line from markdown body when frontmatter lacks one."""
    heading_fallback = ""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if not heading_fallback:
                heading_fallback = stripped.lstrip("#").strip()
            continue
        if stripped.startswith(("```", "---", "***")):
            continue
        desc = stripped.lstrip(">*- ").strip()
        if desc:
            return desc[:160]
    return heading_fallback[:160]


def _normalize_tags(raw_tags: Any) -> List[str]:
    if isinstance(raw_tags, list):
        return [str(t).strip() for t in raw_tags if str(t).strip()]
    if isinstance(raw_tags, str) and raw_tags.strip():
        return [part.strip() for part in raw_tags.split(",") if part.strip()]
    return []


def _normalize_os(raw_os: Any) -> Union[str, List[str]]:
    if isinstance(raw_os, list):
        vals = [str(v).strip() for v in raw_os if str(v).strip()]
        if len(vals) == 1:
            return vals[0]
        return vals
    if isinstance(raw_os, str):
        return raw_os.strip()
    return ""


def _list_reference_files(skill_dir: Optional[str]) -> List[str]:
    """Return sorted POSIX-style relative paths of all files inside ``references/``."""
    if not skill_dir:
        return []
    refs_dir = os.path.join(skill_dir, "references")
    if not os.path.isdir(refs_dir):
        return []

    found: List[str] = []
    try:
        refs_real = os.path.realpath(refs_dir)
        for root, dirs, files in os.walk(refs_dir):
            dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__"))
            for fname in sorted(files):
                full_path = os.path.join(root, fname)
                real_file = os.path.realpath(full_path)
                try:
                    if os.path.commonpath([refs_real, real_file]) != refs_real:
                        continue
                except ValueError:
                    continue
                if os.path.isfile(full_path):
                    rel = os.path.relpath(full_path, refs_dir).replace(os.sep, "/")
                    found.append(rel)
    except Exception:
        return []
    return found


def _safe_read_regular_file(
    file_path: str,
    allowed_root: str,
    max_bytes: int = 512 * 1024,
) -> Tuple[Optional[str], Optional[str]]:
    """Safely read a regular UTF-8 file contained within *allowed_root* up to *max_bytes*."""
    try:
        root_real = os.path.realpath(allowed_root)
        target_real = os.path.realpath(file_path)
        try:
            common = os.path.commonpath([root_real, target_real])
        except ValueError:
            return None, "path escapes allowed root"
        if common != root_real or target_real == root_real:
            return None, "path escapes allowed root"
        st = os.stat(target_real)
        if not stat.S_ISREG(st.st_mode):
            return None, "target is not a regular file"
        if st.st_size > max_bytes:
            return None, f"file exceeds maximum allowed size ({max_bytes} bytes)"
        with open(target_real, "rb") as fh:
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None, f"file exceeds maximum allowed size ({max_bytes} bytes)"
        return data.decode("utf-8", errors="replace"), None
    except Exception as exc:
        return None, str(exc)


def _load_skill_entry(
    file_path: str,
    is_package: bool,
    root_dir: str,
) -> Optional[Dict[str, Any]]:
    raw_text, read_err = _safe_read_regular_file(file_path, root_dir)
    if read_err is not None or raw_text is None:
        return None

    meta, body = parse_frontmatter(raw_text)

    if is_package:
        skill_dir: Optional[str] = os.path.dirname(file_path)
        default_name = os.path.basename(skill_dir)
    else:
        skill_dir = None
        default_name = os.path.splitext(os.path.basename(file_path))[0]

    name = str(meta.get("name") or "").strip() or default_name
    description = str(meta.get("description") or "").strip() or _fallback_description(body)

    # Derive a sensible default category from parent directory when nested
    default_category = "general"
    try:
        rel_parent = os.path.relpath(os.path.dirname(file_path), root_dir)
        parts = [p for p in rel_parent.split(os.sep) if p and p != "."]
        if is_package and len(parts) >= 2:
            default_category = parts[-2]
        elif not is_package and len(parts) >= 1:
            default_category = parts[-1]
    except Exception:
        pass

    category = str(meta.get("category") or "").strip() or default_category
    tags = _normalize_tags(meta.get("tags"))
    os_val = _normalize_os(meta.get("os"))
    risk_level = str(meta.get("risk_level") or "").strip() or "unknown"

    references = _list_reference_files(skill_dir)

    return {
        "name": name,
        "description": description,
        "category": category,
        "tags": tags,
        "os": os_val,
        "risk_level": risk_level,
        "has_references": bool(references),
        "_file_path": file_path,
        "_skill_dir": skill_dir,
        "_body": body,
        "_references": references,
    }


def discover_skills(skills_dirs: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Recursively discover ``SKILL.md`` packages and flat ``.md`` skills."""
    roots = skills_dirs if skills_dirs is not None else get_skills_dirs()
    discovered: List[Dict[str, Any]] = []
    seen_names = set()

    for root_dir in roots:
        if not os.path.isdir(root_dir):
            continue
        try:
            for current_dir, dirs, files in os.walk(root_dir):
                dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIR_NAMES)
                files_sorted = sorted(files)

                if "SKILL.md" in files_sorted:
                    entry = _load_skill_entry(
                        os.path.join(current_dir, "SKILL.md"),
                        is_package=True,
                        root_dir=root_dir,
                    )
                    if entry is not None:
                        key = entry["name"].lower()
                        if key not in seen_names:
                            seen_names.add(key)
                            discovered.append(entry)
                else:
                    for fname in files_sorted:
                        lower_name = fname.lower()
                        if not lower_name.endswith(".md") or lower_name in _IGNORED_FLAT_MD:
                            continue
                        entry = _load_skill_entry(
                            os.path.join(current_dir, fname),
                            is_package=False,
                            root_dir=root_dir,
                        )
                        if entry is not None:
                            key = entry["name"].lower()
                            if key not in seen_names:
                                seen_names.add(key)
                                discovered.append(entry)
        except Exception:
            continue

    discovered.sort(key=lambda item: item["name"].lower())
    return discovered


def _matches_os(skill_os: Union[str, List[str]], target_os: str) -> bool:
    needle = target_os.strip().lower()
    if not needle:
        return True
    if isinstance(skill_os, list):
        return any(str(v).strip().lower() == needle for v in skill_os)
    if isinstance(skill_os, str):
        parts = [p.strip().lower() for p in skill_os.split(",") if p.strip()]
        return needle in parts
    return False


def _matches_query(skill: Dict[str, Any], query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    haystack_parts = [
        str(skill.get("name", "")),
        str(skill.get("description", "")),
        str(skill.get("category", "")),
        " ".join(str(t) for t in skill.get("tags", [])),
    ]
    return needle in " ".join(haystack_parts).lower()


def _compact_metadata(skill: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": skill["name"],
        "description": skill["description"],
        "category": skill["category"],
        "tags": list(skill["tags"]),
        "os": list(skill["os"]) if isinstance(skill["os"], list) else skill["os"],
        "risk_level": skill["risk_level"],
        "has_references": bool(skill["has_references"]),
    }


def _validate_reference_path(reference_file: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate a relative ``reference_file`` path against traversal attacks.

    Returns ``(normalized_posix_path, error_message)``.
    """
    if not isinstance(reference_file, str):
        return None, "Invalid reference_file: must be a string."

    raw = reference_file.strip()
    if not raw:
        return None, None

    if "\x00" in raw:
        return None, "Invalid reference_file: null byte is not allowed."

    # Reject absolute paths (POSIX or Windows) and Windows drive-letter prefixes
    if raw.startswith(("/", "\\")) or os.path.isabs(raw) or re.match(r"^[A-Za-z]:", raw):
        return None, "Invalid reference_file: absolute paths are not allowed."

    # Split on both POSIX and Windows separators to catch traversal regardless of host OS
    segments = re.split(r"[/\\]+", raw)
    clean_segments: List[str] = []
    for seg in segments:
        if seg in ("", "."):
            continue
        if seg == "..":
            return None, "Invalid reference_file: path traversal ('..') is not allowed."
        clean_segments.append(seg)

    if not clean_segments:
        return None, "Invalid reference_file: empty path."

    return "/".join(clean_segments), None


def handle_list_skills(args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """MCP handler for ``list_skills``."""
    try:
        params = args if isinstance(args, dict) else {}
        category_filter = str(params.get("category") or "").strip().lower()
        query_filter = str(params.get("query") or "").strip()
        os_filter = str(params.get("os") or "").strip()

        skills = discover_skills()
        matched: List[Dict[str, Any]] = []
        for skill in skills:
            if category_filter and str(skill.get("category", "")).strip().lower() != category_filter:
                continue
            if os_filter and not _matches_os(skill.get("os", ""), os_filter):
                continue
            if query_filter and not _matches_query(skill, query_filter):
                continue
            matched.append(_compact_metadata(skill))

        return {
            "skills": matched,
            "count": len(matched),
        }
    except Exception as exc:
        return {"error": f"Failed to list skills: {exc}"}


def handle_load_skill(args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """MCP handler for ``load_skill``."""
    try:
        params = args if isinstance(args, dict) else {}
        raw_name = params.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return {"error": "Missing required parameter: name"}

        target_name = raw_name.strip()
        raw_ref = params.get("reference_file")

        # Validate reference_file syntax up front so traversal attempts are always rejected
        normalized_ref: Optional[str] = None
        if raw_ref is not None and raw_ref != "":
            normalized_ref, ref_err = _validate_reference_path(raw_ref)
            if ref_err:
                return {"error": ref_err}

        skills = discover_skills()
        found: Optional[Dict[str, Any]] = None
        for skill in skills:
            if skill["name"] == target_name:
                found = skill
                break
        if found is None:
            lower_target = target_name.lower()
            for skill in skills:
                if skill["name"].lower() == lower_target:
                    found = skill
                    break

        if found is None:
            return {"error": f"Skill not found: {target_name}"}

        available_refs = list(found.get("_references", []))

        if normalized_ref:
            skill_dir = found.get("_skill_dir")
            if not skill_dir:
                return {"error": f"Skill '{found['name']}' has no references directory."}

            refs_root = os.path.realpath(os.path.join(skill_dir, "references"))
            if not os.path.isdir(refs_root):
                return {"error": f"Skill '{found['name']}' has no references directory."}

            candidate_path = os.path.join(refs_root, *normalized_ref.split("/"))
            real_candidate = os.path.realpath(candidate_path)
            try:
                common = os.path.commonpath([refs_root, real_candidate])
            except ValueError:
                return {"error": "Invalid reference_file: path traversal is not allowed."}

            if common != refs_root or real_candidate == refs_root:
                return {"error": "Invalid reference_file: path traversal is not allowed."}

            if not os.path.exists(real_candidate):
                return {
                    "error": f"Reference file not found: {normalized_ref}",
                    "references": available_refs,
                }

            ref_content, read_err = _safe_read_regular_file(candidate_path, refs_root)
            if read_err is not None or ref_content is None:
                return {
                    "error": f"Invalid reference_file: {read_err}",
                    "references": available_refs,
                }

            return {
                "name": found["name"],
                "reference_file": normalized_ref,
                "content": ref_content,
                "references": available_refs,
            }

        return {
            "name": found["name"],
            "description": found["description"],
            "category": found["category"],
            "tags": list(found["tags"]),
            "os": list(found["os"]) if isinstance(found["os"], list) else found["os"],
            "risk_level": found["risk_level"],
            "content": found.get("_body", ""),
            "references": available_refs,
        }
    except Exception as exc:
        return {"error": f"Failed to load skill: {exc}"}
