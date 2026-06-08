from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
os.environ["PYTHONPATH"] = str(SCRIPTS_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
os.environ["LOCAL_AI_BRAIN_HOME"] = str(PROJECT_ROOT / "brain")
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from local_ai_brain import main_mcp as base_mcp  # noqa: E402


PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "0.1.0"
ACTION_TO_BASE = {
    "doctor": "doctor",
    "context-pack": "context_pack",
    "context_pack": "context_pack",
    "search": "search",
    "record-artifact": "record_artifact",
    "record_artifact": "record_artifact",
    "record-ticket": "record_ticket",
    "record_ticket": "record_ticket",
    "record-event": "record_event",
    "record_event": "record_event",
    "proof-start": "proof_start",
    "proof_start": "proof_start",
    "proof-finish": "proof_finish",
    "proof_finish": "proof_finish",
    "proof-report": "proof_report",
    "proof_report": "proof_report",
    "rebuild-index": "rebuild_index",
    "rebuild_index": "rebuild_index",
    "codex-title-distill": "codex_title_distill",
    "codex_title_distill": "codex_title_distill",
}
BASE_TO_ACTION = {
    "doctor": "doctor",
    "context_pack": "context-pack",
    "search": "search",
    "record_artifact": "record-artifact",
    "record_ticket": "record-ticket",
    "record_event": "record-event",
    "proof_start": "proof-start",
    "proof_finish": "proof-finish",
    "proof_report": "proof-report",
    "rebuild_index": "rebuild-index",
    "codex_title_distill": "codex-title-distill",
}


def project_tool_prefix(project_root: Path) -> str:
    name = project_root.name.strip() or "project"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "project"


TOOL_PREFIX = os.environ.get("LOCAL_AI_BRAIN_MCP_TOOL_PREFIX", project_tool_prefix(PROJECT_ROOT))
SCOPE_DESCRIPTION = os.environ.get("LOCAL_AI_BRAIN_MCP_SCOPE_DESCRIPTION", "Project-local Local AI Brain")


def main() -> int:
    while True:
        message = base_mcp.read_message(sys.stdin.buffer)
        if message is None:
            return 0
        response = handle(message)
        if response is not None:
            base_mcp.write_message(sys.stdout.buffer, response)


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    try:
        if method == "initialize":
            return base_mcp.result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": TOOL_PREFIX, "version": SERVER_VERSION},
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return base_mcp.result(request_id, {})
        if method == "tools/list":
            return base_mcp.result(request_id, {"tools": project_tools_list()})
        if method == "tools/call":
            return base_mcp.result(request_id, call_project_tool(request.get("params")))
        return base_mcp.rpc_error(request_id, -32601, f"Method not found: {method}")
    except Exception as exc:
        return base_mcp.rpc_error(request_id, -32000, str(exc))


def project_tools_list() -> list[dict[str, Any]]:
    tools = []
    for item in base_mcp.tools_list():
        copied = json.loads(json.dumps(item))
        base_name = str(copied.get("name", ""))
        action = BASE_TO_ACTION.get(base_name, base_name.replace("_", "-"))
        copied["name"] = f"{TOOL_PREFIX}.{action}"
        copied["description"] = f"{SCOPE_DESCRIPTION}: {copied.get('description', '')}".strip()
        tools.append(copied)
    return tools


def call_project_tool(params: Any) -> dict[str, Any]:
    record = params if isinstance(params, dict) else {}
    tool_name = str(record.get("name", ""))
    args = record.get("arguments")
    args = dict(args) if isinstance(args, dict) else {}
    action = normalize_action(tool_name)
    base_name = ACTION_TO_BASE.get(action)
    if not base_name:
        raise ValueError(f"unknown project Local AI Brain tool: {tool_name}")
    if base_name in {"context_pack", "search", "proof_start", "proof_report"}:
        args.setdefault("repo", str(PROJECT_ROOT))
    if base_name in {"record_artifact", "record_ticket", "record_event"}:
        payload = args.get("payload")
        if isinstance(payload, dict):
            payload.setdefault("repo_path", str(PROJECT_ROOT))
    return base_mcp.call_tool({"name": base_name, "arguments": args})


def normalize_action(tool_name: str) -> str:
    if tool_name.startswith(TOOL_PREFIX + "."):
        return tool_name[len(TOOL_PREFIX) + 1 :]
    return tool_name


if __name__ == "__main__":
    raise SystemExit(main())
