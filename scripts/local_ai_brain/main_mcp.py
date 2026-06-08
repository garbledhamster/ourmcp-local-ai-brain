from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "0.1.0"


def main() -> int:
    server = McpServer()
    while True:
        message = read_message(sys.stdin.buffer)
        if message is None:
            return 0
        response = server.handle(message)
        if response is not None:
            write_message(sys.stdout.buffer, response)


class McpServer:
    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        try:
            if method == "initialize":
                return result(
                    request_id,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "local-ai-brain", "version": SERVER_VERSION},
                    },
                )
            if method == "notifications/initialized":
                return None
            if method == "ping":
                return result(request_id, {})
            if method == "tools/list":
                return result(request_id, {"tools": tools_list()})
            if method == "tools/call":
                return result(request_id, call_tool(request.get("params")))
            return rpc_error(request_id, -32601, f"Method not found: {method}")
        except Exception as exc:
            return rpc_error(request_id, -32000, str(exc))


def tools_list() -> list[dict[str, Any]]:
    text_arg = {"type": "string"}
    return [
        {
            "name": "doctor",
            "title": "Check Local AI Brain health",
            "description": "Run Local AI Brain doctor and return JSON health output.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "context_pack",
            "title": "Build a context pack",
            "description": "Return a compact context pack for a repo, surface, and query.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": text_arg,
                    "surface": text_arg,
                    "query": text_arg,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                    "proofSession": text_arg,
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "search",
            "title": "Search Local AI Brain",
            "description": "Search indexed Local AI Brain records.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": text_arg,
                    "surface": text_arg,
                    "query": text_arg,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "proofSession": text_arg,
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        record_tool("record_artifact", "Record an artifact", "Record an artifact through scrub/distill/classify capture."),
        record_tool("record_ticket", "Record a ticket", "Record a ticket through scrub/distill/classify capture."),
        record_tool("record_event", "Record an event", "Record an event in the Local AI Brain event log."),
        {
            "name": "proof_start",
            "title": "Start proof tracking",
            "description": "Start a Local AI Brain proof session for a live call.",
            "inputSchema": {
                "type": "object",
                "properties": {"repo": text_arg, "surface": text_arg, "summary": text_arg, "taskClass": text_arg},
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        },
        {
            "name": "proof_finish",
            "title": "Finish proof tracking",
            "description": "Finish a proof session and record outcome, checks, estimates, and lookup classifications.",
            "inputSchema": {
                "type": "object",
                "properties": {"session": text_arg, "payload": {"type": "object", "additionalProperties": True}},
                "required": ["session", "payload"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        },
        {
            "name": "proof_report",
            "title": "Report proof metrics",
            "description": "Summarize Local AI Brain proof sessions and lookup metrics.",
            "inputSchema": {
                "type": "object",
                "properties": {"repo": text_arg, "surface": text_arg, "limit": {"type": "integer", "minimum": 1, "maximum": 500}},
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "rebuild_index",
            "title": "Rebuild search index",
            "description": "Rebuild the Local AI Brain SQLite FTS index.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "codex_title_distill",
            "title": "Distill Codex chat titles",
            "description": "Preview or apply deterministic Codex chat title distillation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "date": text_arg,
                    "codexHome": text_arg,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "apply": {"type": "boolean"},
                    "yes": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        },
    ]


def record_tool(name: str, title: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "payload": {
                    "type": "object",
                    "description": "JSON payload accepted by the matching local-ai-brain record-* CLI command.",
                    "additionalProperties": True,
                }
            },
            "required": ["payload"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    }


def call_tool(params: Any) -> dict[str, Any]:
    record = params if isinstance(params, dict) else {}
    name = str(record.get("name", ""))
    args = record.get("arguments")
    args = args if isinstance(args, dict) else {}

    if name == "doctor":
        return text_content(run_cli(["--json", "doctor"]))
    if name == "context_pack":
        command = [
            "context-pack",
            "--repo",
            str(args.get("repo", "")),
            "--surface",
            str(args.get("surface", "")),
            "--query",
            str(args.get("query", "")),
            "--limit",
            str(int(args.get("limit", 5) or 5)),
        ]
        if args.get("proofSession"):
            command.extend(["--proof-session", str(args["proofSession"])])
        return text_content(
            run_cli(command)
        )
    if name == "search":
        command = [
            "search",
            "--repo",
            str(args.get("repo", "")),
            "--surface",
            str(args.get("surface", "")),
            "--query",
            str(args.get("query", "")),
            "--limit",
            str(int(args.get("limit", 10) or 10)),
        ]
        if args.get("proofSession"):
            command.extend(["--proof-session", str(args["proofSession"])])
        return text_content(
            run_cli(command)
        )
    if name in {"record_artifact", "record_ticket", "record_event"}:
        payload = args.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        command = name.replace("_", "-")
        return text_content(run_record_command(command, payload))
    if name == "rebuild_index":
        return text_content(run_cli(["rebuild-index"]))
    if name == "proof_start":
        return text_content(
            run_cli(
                [
                    "proof-start",
                    "--repo",
                    str(args.get("repo", "")),
                    "--surface",
                    str(args.get("surface", "")),
                    "--summary",
                    str(args.get("summary", "")),
                    "--task-class",
                    str(args.get("taskClass", "unknown") or "unknown"),
                    "--json",
                ]
            )
        )
    if name == "proof_finish":
        payload = args.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        session = str(args.get("session", ""))
        if not session:
            raise ValueError("session is required")
        return text_content(run_record_command_with_args("proof-finish", ["--session", session], payload))
    if name == "proof_report":
        return text_content(
            run_cli(
                [
                    "proof-report",
                    "--repo",
                    str(args.get("repo", "")),
                    "--surface",
                    str(args.get("surface", "")),
                    "--limit",
                    str(int(args.get("limit", 30) or 30)),
                    "--json",
                ]
            )
        )
    if name == "codex_title_distill":
        command = ["codex-title-distill"]
        if args.get("date"):
            command.extend(["--date", str(args["date"])])
        if args.get("codexHome"):
            command.extend(["--codex-home", str(args["codexHome"])])
        if args.get("limit"):
            command.extend(["--limit", str(int(args["limit"]))])
        if args.get("apply"):
            command.append("--apply")
        if args.get("yes"):
            command.append("--yes")
        return text_content(run_cli(command))
    raise ValueError(f"unknown tool: {name}")


def run_record_command(command: str, payload: dict[str, Any]) -> str:
    return run_record_command_with_args(command, [], payload)


def run_record_command_with_args(command: str, extra_args: list[str], payload: dict[str, Any]) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False)
        payload_path = handle.name
    try:
        return run_cli([command, *extra_args, "--json-file", payload_path])
    finally:
        try:
            Path(payload_path).unlink()
        except OSError:
            pass


def run_cli(args: list[str]) -> str:
    env = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "local_ai_brain", *args],
        cwd=package_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    output = completed.stdout.strip()
    if completed.returncode != 0:
        detail = completed.stderr.strip() or output or f"exit {completed.returncode}"
        raise RuntimeError(detail)
    return output


def read_message(stream) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        line_text = line.decode("ascii", errors="replace").strip()
        if line_text == "":
            break
        key, _, value = line_text.partition(":")
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = stream.read(length)
    return json.loads(body.decode("utf-8"))


def write_message(stream, message: dict[str, Any]) -> None:
    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    stream.write(body)
    stream.flush()


def text_content(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def result(request_id: Any, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    raise SystemExit(main())
