import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

from google.antigravity import types
from google.antigravity.hooks import hooks

AUDIT_LOG_PATH = "live_logs/mcp_audit.jsonl"

DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+/"),
    re.compile(r"\bmkfs(\.|\s)"),
    re.compile(r"\bdd\s+if=/dev/zero\s+of=/dev/sd"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    re.compile(r"\b(shutdown|reboot|poweroff)\b"),
    re.compile(r"remove-item\b.*-recurse"),
    re.compile(r"\bformat\s+[a-z]:"),
    re.compile(r"\b(curl|wget)\b[^\n]*\|\s*(ba)?sh\b"),
    re.compile(r"\bchmod\s+[0-7]{3,4}\s+/(bin|usr|etc|boot)\b"),
    re.compile(r"\bmv\b[^\n]*(agy-gdrive-runner|remote-harness-mcp)"),
]

#: Values that are obviously placeholders, not commands.
_QUEUE_MARKERS = ("rhm_tasks.json", "rhm_queue")


def _normalise(value: object) -> str:
    """Quotes and backslashes removed, whitespace collapsed.

    ``cat rhm_""tasks.json`` and ``rm  -rf  /`` are the same command as their
    plain spellings; matching the raw JSON string let both through.
    """
    text = str(value)
    for char in ('"', "'", "\\", "\u2018", "\u2019", "\u201c", "\u201d"):
        text = text.replace(char, "")
    return re.sub(r"\s+", " ", text).strip().lower()


@hooks.pre_tool_call_decide
async def mesh_pre_tool_call(data: types.ToolCall) -> types.HookResult:
    args = data.args or {}
    raw_args = json.dumps(args)
    normalised_args = _normalise(raw_args)

    # 1. Block Google Drive Task Queues
    if any(marker in normalised_args for marker in _QUEUE_MARKERS):
        return types.HookResult(
            allow=False,
            message="Zero Drive Queue Policy Violation: Task queues on Google Drive are permanently disabled."
        )

    # 2. Block dangerous destructive commands
    for key, val in args.items():
        if not isinstance(val, str):
            continue
        normalised = _normalise(val)
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(normalised):
                return types.HookResult(
                    allow=False,
                    message=f"Dangerous command blocked: matched pattern '{pattern.pattern}' in argument '{key}'"
                )

    return types.HookResult(allow=True)


@hooks.on_tool_error
async def mesh_on_tool_error(data: Exception) -> Optional[str]:
    return f"[HARD ERROR HALT] Tool execution failed: {data}. Execution halted. Report to user immediately and wait for explicit instructions."


@hooks.post_tool_call
async def mesh_post_tool_call(data: types.ToolResult) -> None:
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "name": str(data.name),
        "id": data.id,
        "status": "error" if (data.error or data.exception) else "success",
        "error": str(data.error) if data.error else (str(data.exception) if data.exception else None),
        "server_name": data.server_name,
    }
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def get_mesh_hooks():
    return [mesh_pre_tool_call, mesh_on_tool_error, mesh_post_tool_call]
