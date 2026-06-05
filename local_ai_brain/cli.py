from __future__ import annotations

import argparse
import json
import platform
import sys
import uuid
from pathlib import Path

from . import __version__
from .audit import iter_codex_session_candidates
from .capture import capture_record, now_iso, read_payload
from .context import rows_to_context_pack, write_context_pack
from .db import connect, init_db, insert_event, insert_memory_record, json_dumps, rebuild_fts, search_records
from .distill_adapter import health as distill_health
from .paths import artifact_root, brain_home, db_path, ensure_runtime_dirs
from .title_distill import main as title_distill_main


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        if getattr(args, "json_output", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-ai-brain")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Return machine-readable JSON where supported.")
    sub = parser.add_subparsers(required=True)
    add_command(sub, "init", cmd_init)
    add_command(sub, "health", cmd_health)
    add_command(sub, "doctor", cmd_health)
    install = add_command(sub, "install", cmd_install)
    install.add_argument("--what-if", action="store_true")
    install.add_argument("--platform", choices=["windows", "macos", "linux"], default=current_platform())
    start = add_command(sub, "start-run", cmd_start_run)
    start.add_argument("--repo", default="")
    start.add_argument("--surface", default="")
    start.add_argument("--goal", default="")
    start.add_argument("--status", default="active")
    for name, func in (
        ("record-ticket", cmd_record_ticket),
        ("record-artifact", cmd_record_artifact),
        ("record-event", cmd_record_event),
    ):
        command = add_command(sub, name, func)
        command.add_argument("--json-file", "--json", dest="json_file", required=True)
    search = add_command(sub, "search", cmd_search)
    search.add_argument("--repo", default="")
    search.add_argument("--surface", default="")
    search.add_argument("--query", default="")
    search.add_argument("--limit", type=int, default=10)
    context = add_command(sub, "context-pack", cmd_context_pack)
    context.add_argument("--repo", default="")
    context.add_argument("--surface", default="")
    context.add_argument("--query", default="")
    context.add_argument("--limit", type=int, default=5)
    context.add_argument("--output", default="")
    audit = add_command(sub, "audit", cmd_audit)
    audit.add_argument("--source", choices=["codex-sessions"], required=True)
    audit.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    audit.add_argument("--limit", type=int, default=25)
    audit.add_argument("--ingest", action="store_true")
    title = add_command(sub, "codex-title-distill", cmd_codex_title_distill)
    title.add_argument("--date", default="")
    title.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    title.add_argument("--limit", type=int, default=None)
    title.add_argument("--max-title-words", type=int, default=7)
    title.add_argument("--force-retitle", action="store_true")
    title.add_argument("--apply", action="store_true")
    title.add_argument("--yes", action="store_true")
    title.add_argument("--no-session-index", action="store_true")
    title.add_argument("--json", action="store_true")
    add_command(sub, "rebuild-index", cmd_rebuild_index)
    return parser


def add_command(sub, name: str, func):
    command = sub.add_parser(name)
    command.set_defaults(func=func)
    return command


def cmd_init(args) -> int:
    ensure_runtime_dirs()
    with connect(db_path()) as con:
        init_db(con)
    print(f"initialized {db_path()}")
    return 0


def cmd_health(args) -> int:
    ensure_runtime_dirs()
    sqlite_ok = False
    fts_ok = False
    sqlite_version = ""
    with connect(db_path()) as con:
        init_db(con)
        sqlite_version = con.execute("SELECT sqlite_version()").fetchone()[0]
        try:
            con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS health_fts USING fts5(body)")
            con.execute("DROP TABLE IF EXISTS health_fts")
            fts_ok = True
        except Exception:
            fts_ok = False
        sqlite_ok = True
    distill = distill_health()
    result = {
        "ok": sqlite_ok and fts_ok and distill.ok,
        "version": __version__,
        "platform": current_platform(),
        "brain_home": str(brain_home()),
        "db_path": str(db_path()),
        "sqlite_version": sqlite_version,
        "sqlite_fts5": fts_ok,
        "distill": {"ok": distill.ok, "error": distill.error},
    }
    if args.json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
    return 0 if result["ok"] else 1


def cmd_install(args) -> int:
    preflight = install_preflight(args.platform)
    operations = [
        f"create {brain_home()}",
        f"create {artifact_root()}",
        f"initialize SQLite schema at {db_path()}",
        "verify FTS5 support",
        "verify Distill CLI",
        "prepare agent instruction shims",
    ]
    platform_note = {
        "windows": "use Python sqlite3; no sqlite3.exe bundle required",
        "macos": "use Python sqlite3; no sqlite3 binary required",
        "linux": "use Python sqlite3; no sqlite3 binary required",
    }[args.platform]
    if args.what_if:
        print(f"WhatIf install for {args.platform}: {platform_note}")
        for check in preflight:
            status = "PASS" if check["ok"] else "FAIL"
            print(f"CHECK {status} {check['name']}: {check['detail']}")
        for operation in operations:
            print(f"WOULD {operation}")
        return 0 if all(check["ok"] for check in preflight) else 1
    cmd_init(args)
    return cmd_health(args)


def cmd_start_run(args) -> int:
    ensure_runtime_dirs()
    timestamp = now_iso()
    run_uid = str(uuid.uuid4())
    with connect(db_path()) as con:
        init_db(con)
        con.execute(
            "INSERT INTO runs(run_uid, created_at, updated_at, repo_path, target_surface, goal, status) VALUES(?,?,?,?,?,?,?)",
            (run_uid, timestamp, timestamp, args.repo, args.surface, args.goal, args.status),
        )
        con.commit()
    print(run_uid)
    return 0


def cmd_record_ticket(args) -> int:
    payload = read_payload(args.json_file)
    record = capture_record(payload, artifact_type="ticket")
    with connect(db_path()) as con:
        init_db(con)
        rowid = insert_memory_record(con, record)
    print(json.dumps({"ok": True, "id": rowid, "record_uid": record["record_uid"], "artifact_path": record["artifact_path"]}, ensure_ascii=False))
    return 0


def cmd_record_artifact(args) -> int:
    payload = read_payload(args.json_file)
    record = capture_record(payload, artifact_type=payload.get("artifact_type", "artifact"))
    with connect(db_path()) as con:
        init_db(con)
        rowid = insert_memory_record(con, record)
    print(json.dumps({"ok": True, "id": rowid, "record_uid": record["record_uid"], "artifact_path": record["artifact_path"]}, ensure_ascii=False))
    return 0


def cmd_record_event(args) -> int:
    payload = read_payload(args.json_file)
    timestamp = now_iso()
    event = {
        "event_uid": payload.get("event_uid") or str(uuid.uuid4()),
        "record_uid": payload.get("record_uid", ""),
        "run_uid": payload.get("run_uid", ""),
        "created_at": timestamp,
        "event_type": payload.get("event_type", "event"),
        "repo_path": payload.get("repo_path", ""),
        "target_surface": payload.get("target_surface", ""),
        "summary": payload.get("summary", ""),
        "body": payload.get("body", ""),
        "tags_json": json_dumps(payload.get("tags", [])),
        "related_files_json": json_dumps(payload.get("related_files", [])),
    }
    with connect(db_path()) as con:
        init_db(con)
        rowid = insert_event(con, event)
    print(json.dumps({"ok": True, "id": rowid, "event_uid": event["event_uid"]}, ensure_ascii=False))
    return 0


def cmd_search(args) -> int:
    with connect(db_path()) as con:
        init_db(con)
        rows = search_records(con, query=args.query, repo_path=args.repo, target_surface=args.surface, limit=args.limit)
        record_lookup_event(con, args, rows, "brain_search")
    print(rows_to_json(rows))
    return 0


def cmd_context_pack(args) -> int:
    with connect(db_path()) as con:
        init_db(con)
        rows = search_records(con, query=args.query, repo_path=args.repo, target_surface=args.surface, limit=args.limit)
        record_lookup_event(con, args, rows, "brain_context_pack")
    text = rows_to_context_pack(rows, repo_path=args.repo, target_surface=args.surface, query=args.query)
    output_path = Path(args.output).expanduser() if args.output else None
    write_context_pack(text, output_path)
    print(text, end="")
    return 0


def cmd_audit(args) -> int:
    candidates = []
    if args.source == "codex-sessions":
        candidates = list(iter_codex_session_candidates(Path(args.codex_home)))[: args.limit]
    ingested = 0
    if args.ingest:
        with connect(db_path()) as con:
            init_db(con)
            for candidate in candidates:
                path = Path(candidate["artifact_path"]).expanduser()
                if not path.exists():
                    continue
                record = capture_record(candidate, artifact_type=candidate.get("artifact_type", "artifact"))
                insert_memory_record(con, record)
                ingested += 1
    print(json.dumps({"ok": True, "source": args.source, "candidates": len(candidates), "ingested": ingested, "items": candidates}, indent=2, ensure_ascii=False))
    return 0


def cmd_codex_title_distill(args) -> int:
    argv = ["--codex-home", args.codex_home, "--max-title-words", str(args.max_title_words)]
    if args.date:
        argv.extend(["--date", args.date])
    if args.limit is not None:
        argv.extend(["--limit", str(args.limit)])
    if args.force_retitle:
        argv.append("--force-retitle")
    if args.apply:
        argv.append("--apply")
    if args.yes:
        argv.append("--yes")
    if args.no_session_index:
        argv.append("--no-session-index")
    if args.json:
        argv.append("--json")
    return title_distill_main(argv)


def cmd_rebuild_index(args) -> int:
    with connect(db_path()) as con:
        init_db(con)
        rebuild_fts(con)
    print("rebuilt FTS index")
    return 0


def rows_to_json(rows) -> str:
    result = []
    for row in rows:
        item = dict(row)
        for key in ("tags_json", "related_files_json", "scrub_warnings_json"):
            try:
                item[key[:-5] if key.endswith("_json") else key] = json.loads(item[key])
            except Exception:
                pass
        result.append(item)
    return json.dumps(result, indent=2, ensure_ascii=False)


def record_lookup_event(con, args, rows, event_type: str) -> None:
    try:
        results = []
        related_files = []
        for row in rows:
            files = []
            try:
                files = json.loads(row["related_files_json"] or "[]")
            except Exception:
                files = []
            related_files.extend(str(file) for file in files)
            results.append(
                {
                    "record_uid": row["record_uid"],
                    "artifact_type": row["artifact_type"],
                    "ticket_title": row["ticket_title"],
                    "summary": row["summary"],
                    "artifact_path": row["artifact_path"],
                    "scrubbed_path": row["scrubbed_path"],
                    "distilled_path": row["distilled_path"],
                    "tags": safe_json_list(row["tags_json"]),
                    "related_files": files,
                }
            )
        body = json.dumps(
            {
                "query": args.query,
                "repo": args.repo,
                "surface": args.surface,
                "limit": args.limit,
                "result_count": len(rows),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
        insert_event(
            con,
            {
                "event_uid": str(uuid.uuid4()),
                "run_uid": "",
                "created_at": now_iso(),
                "event_type": event_type,
                "repo_path": args.repo,
                "target_surface": args.surface,
                "summary": f"{event_type}: {len(rows)} result(s) for {args.query or 'latest'}",
                "body": body,
                "tags_json": json_dumps(["lookup", event_type]),
                "related_files_json": json_dumps(sorted(set(related_files))),
            },
        )
    except Exception:
        # Lookup tracking must not break retrieval.
        return


def safe_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def current_platform() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def install_preflight(target_platform: str) -> list[dict[str, object]]:
    distill = distill_health()
    return [
        {
            "name": "python",
            "ok": sys.version_info >= (3, 10),
            "detail": sys.version.split()[0],
        },
        {
            "name": "python-sqlite3",
            "ok": True,
            "detail": "available",
        },
        {
            "name": "distill",
            "ok": distill.ok,
            "detail": "available" if distill.ok else distill.error,
        },
        {
            "name": "python-executable",
            "ok": Path(sys.executable).exists(),
            "detail": sys.executable,
        },
    ]
