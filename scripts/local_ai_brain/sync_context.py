from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .capture import now_iso
from .classify import classify_payload
from .db import (
    connect,
    get_source_file,
    init_db,
    insert_event,
    json_dumps,
    upsert_memory_record_by_uid,
    upsert_source_file,
)
from .paths import agent_home, artifact_root, db_path, ensure_runtime_dirs
from .scrub import scrub_text


DEFAULT_MAX_BYTES = 256 * 1024
DEFAULT_SAMPLE_SIZE = 100
MEMORY_TEXT_LIMIT = 12000
DISTILLED_TEXT_LIMIT = 3000
PATH_HASH_BYTES = 16

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".jsonl", ".toml", ".yaml", ".yml"}
HARD_EXCLUDE_SUFFIXES = {
    ".sqlite",
    ".sqlite3",
    ".db",
    ".db3",
    ".log",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".zip",
    ".7z",
    ".tar",
    ".gz",
    ".rar",
    ".exe",
    ".dll",
    ".pyd",
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".crt",
}
HARD_EXCLUDE_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".cache",
    ".turbo",
    ".next",
    "dist",
    "build",
    "generated_images",
    "attachments",
}
SECRET_NAME_PATTERNS = ("*.env", ".env.*", "*credential*", "*secret*", "*token*", "*cookie*", "auth.json")
RELEVANCE_KEYWORDS = (
    "agent",
    "brain",
    "context",
    "decision",
    "failure",
    "fix",
    "guardrail",
    "instruction",
    "memory",
    "plan",
    "repo",
    "skill",
    "sync",
    "test",
    "verify",
    "workflow",
)


@dataclass
class Candidate:
    source_key: str
    path_key: str
    display_path: str
    text: str
    title: str
    artifact_type: str = "context-sync"
    repo_path: str = ""
    target_surface: str = ""
    tags: list[str] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)
    mtime_ns: int = 0
    size_bytes: int = 0
    raw_sha256: str = ""


@dataclass
class SyncOptions:
    apply: bool
    source: str
    agents_home: Path
    codex_home: Path
    repo_roots: list[Path]
    max_bytes: int
    sample_size: int
    limit: int
    allow_warnings: bool
    refresh: bool
    json_output: bool


def add_sync_context_command(sub) -> None:
    command = sub.add_parser("sync-context")
    command.add_argument("--apply", action="store_true", help="Write indexed records. Default is dry-run.")
    command.add_argument(
        "--source",
        default="safe-core",
        choices=[
            "safe-core",
            "agents-home",
            "agents-skills",
            "agents-tools-docs",
            "codex-memory",
            "codex-thread-metadata",
            "codex-session-index",
            "codex-rollouts",
            "repo-docs",
            "all",
        ],
    )
    command.add_argument("--agents-home", default=str(agent_home()))
    command.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    command.add_argument("--repo-root", action="append", default=[])
    command.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    command.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    command.add_argument("--limit", type=int, default=0, help="Debug cap per source. 0 means no extra cap.")
    command.add_argument("--allow-warnings", action="store_true", help="Index scrub-warning records instead of quarantining.")
    command.add_argument("--refresh", action="store_true", help="Rebuild indexed artifacts even when source bytes are unchanged.")
    command.add_argument("--json", dest="sync_json", action="store_true")
    command.set_defaults(func=cmd_sync_context)


def cmd_sync_context(args) -> int:
    options = SyncOptions(
        apply=bool(args.apply),
        source=args.source,
        agents_home=Path(args.agents_home).expanduser(),
        codex_home=Path(args.codex_home).expanduser(),
        repo_roots=[Path(value).expanduser() for value in args.repo_root],
        max_bytes=max(1024, int(args.max_bytes)),
        sample_size=max(1, int(args.sample_size)),
        limit=max(0, int(args.limit)),
        allow_warnings=bool(args.allow_warnings),
        refresh=bool(args.refresh),
        json_output=bool(getattr(args, "json_output", False) or getattr(args, "sync_json", False)),
    )
    result = run_sync_context(options)
    if options.json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_human_summary(result)
    return 0 if result["ok"] else 1


def run_sync_context(options: SyncOptions) -> dict[str, Any]:
    ensure_runtime_dirs()
    run_uid = str(uuid.uuid4())
    started_at = now_iso()
    sources = expand_sources(options.source)
    results = []
    with connect(db_path()) as con:
        init_db(con)
        for source_key in sources:
            results.append(sync_source(con, options, run_uid, source_key))
        if options.apply:
            insert_event(
                con,
                {
                    "event_uid": str(uuid.uuid4()),
                    "run_uid": run_uid,
                    "created_at": now_iso(),
                    "event_type": "brain_sync_context",
                    "repo_path": "",
                    "target_surface": "",
                    "summary": "sync-context imported existing user and agent context",
                    "body": json.dumps({"sources": results}, indent=2, ensure_ascii=False),
                    "tags_json": json_dumps(["sync-context", "local-ai-brain", "deterministic"]),
                    "related_files_json": json_dumps(["plan.md"]),
                },
            )
    totals = summarize_totals(results)
    return {
        "ok": totals["errors"] == 0,
        "dry_run": not options.apply,
        "run_uid": run_uid,
        "started_at": started_at,
        "sources": results,
        "totals": totals,
    }


def sync_source(con: sqlite3.Connection, options: SyncOptions, run_uid: str, source_key: str) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "source": source_key,
        "scanned": 0,
        "new": 0,
        "changed": 0,
        "unchanged": 0,
        "skipped": 0,
        "quarantined": 0,
        "errors": [],
    }
    for candidate in limited(iter_source_candidates(options, source_key), options.limit):
        stats["scanned"] += 1
        try:
            outcome = process_candidate(con, options, run_uid, candidate)
            stats[outcome] = stats.get(outcome, 0) + 1
        except Exception as exc:
            stats["errors"].append({"path": candidate.display_path, "error": str(exc)})
    return stats


def process_candidate(con: sqlite3.Connection, options: SyncOptions, run_uid: str, candidate: Candidate) -> str:
    if not candidate.raw_sha256:
        candidate.raw_sha256 = sha256_text(candidate.text)
    existing = get_source_file(con, candidate.source_key, candidate.path_key)
    if existing and existing["raw_sha256"] == candidate.raw_sha256 and existing["status"] == "ingested" and not options.refresh:
        return "unchanged"

    scrubbed = scrub_text(candidate.text)
    scrubbed_sha = sha256_text(scrubbed.text)
    warnings = scrubbed.warnings
    first_seen = existing["first_seen_at"] if existing else now_iso()
    record_uid = stable_uid(candidate.source_key, candidate.path_key)
    summary = summarize_candidate(candidate, scrubbed.text)
    status = "changed" if existing else "new"

    if warnings and not options.allow_warnings:
        if options.apply:
            quarantine_path = write_quarantine(run_uid, record_uid, candidate, scrubbed.text, warnings)
            upsert_source_file(
                con,
                source_entry(
                    candidate,
                    scrubbed_sha,
                    record_uid,
                    "quarantined",
                    warnings,
                    f"quarantined: {quarantine_path}",
                    first_seen,
                ),
            )
        return "quarantined"

    if not is_good_memory(scrubbed.text, candidate, allow_redactions=options.allow_warnings):
        if options.apply:
            upsert_source_file(
                con,
                source_entry(candidate, scrubbed_sha, record_uid, "skipped:low-quality", warnings, summary, first_seen),
            )
        return "skipped"

    if not options.apply:
        return status

    record = build_memory_record(run_uid, record_uid, candidate, scrubbed.text, scrubbed_sha, warnings, summary, first_seen)
    upsert_memory_record_by_uid(con, record)
    upsert_source_file(con, source_entry(candidate, scrubbed_sha, record_uid, "ingested", warnings, summary, first_seen))
    return status


def build_memory_record(
    run_uid: str,
    record_uid: str,
    candidate: Candidate,
    scrubbed_text: str,
    scrubbed_sha: str,
    warnings: list[str],
    summary: str,
    created_at: str,
) -> dict[str, Any]:
    raw_path = artifact_root() / "raw" / "context-sync" / f"{record_uid}.md"
    scrubbed_path = artifact_root() / "scrubbed" / "context-sync" / f"{record_uid}.txt"
    distilled_path = artifact_root() / "distilled" / "context-sync" / f"{record_uid}.txt"
    for path in (raw_path, scrubbed_path, distilled_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(candidate.text, encoding="utf-8")
    scrubbed_path.write_text(scrubbed_text, encoding="utf-8")
    distilled_text = deterministic_distill(candidate, scrubbed_text, summary)
    distilled_path.write_text(distilled_text, encoding="utf-8")
    classification_text = "\n".join([candidate.title, summary, *candidate.related_files, " ".join(candidate.tags)])
    classified = classify_payload(
        {
            "artifact_type": candidate.artifact_type,
            "repo_path": candidate.repo_path,
            "target_surface": candidate.target_surface,
            "ticket_title": candidate.title,
            "summary": summary,
            "tags": candidate.tags,
            "related_files": candidate.related_files,
        },
        classification_text,
        classification_text,
    )
    tags = sorted(set(classified["tags"] + ["context-sync", candidate.source_key]))
    related_files = sorted(set(classified["related_files"] + candidate.related_files))
    search_text = "\n".join(
        [
            candidate.title,
            summary,
            " ".join(tags),
            " ".join(related_files),
            distilled_text,
            scrubbed_text[:MEMORY_TEXT_LIMIT],
        ]
    ).strip()
    timestamp = now_iso()
    path_hash = short_hash(candidate.path_key)
    return {
        "record_uid": record_uid,
        "run_uid": run_uid,
        "artifact_path": str(raw_path),
        "raw_path": str(raw_path),
        "scrubbed_path": str(scrubbed_path),
        "distilled_path": str(distilled_path),
        "artifact_type": candidate.artifact_type,
        "repo_path": candidate.repo_path,
        "target_surface": candidate.target_surface,
        "ticket_title": candidate.title,
        "status": "active",
        "summary": summary,
        "tags_json": json_dumps(tags),
        "tags_text": " ".join(tags),
        "related_files_json": json_dumps(related_files),
        "related_files_text": " ".join(related_files),
        "search_text": search_text,
        "scrub_status": "scrubbed",
        "scrub_warnings_json": json_dumps(warnings),
        "distill_status": "deterministic",
        "classifier_json": json_dumps(classified),
        "content_sha256": scrubbed_sha,
        "source": f"context-sync:{candidate.source_key}:{path_hash}",
        "created_at": created_at,
        "updated_at": timestamp,
    }


def source_entry(
    candidate: Candidate,
    scrubbed_sha: str,
    record_uid: str,
    status: str,
    warnings: list[str],
    summary: str,
    first_seen: str,
) -> dict[str, Any]:
    return {
        "source_key": candidate.source_key,
        "path_key": candidate.path_key,
        "display_path": candidate.display_path,
        "mtime_ns": candidate.mtime_ns,
        "size_bytes": candidate.size_bytes,
        "raw_sha256": candidate.raw_sha256,
        "scrubbed_sha256": scrubbed_sha,
        "record_uid": record_uid,
        "status": status,
        "warnings_json": json_dumps(warnings),
        "summary": summary,
        "first_seen_at": first_seen,
        "last_seen_at": now_iso(),
    }


def expand_sources(source: str) -> list[str]:
    safe = ["agents-home", "agents-skills", "agents-tools-docs", "codex-memory", "codex-thread-metadata", "codex-session-index", "repo-docs"]
    if source == "safe-core":
        return safe
    if source == "all":
        return safe + ["codex-rollouts"]
    return [source]


def iter_source_candidates(options: SyncOptions, source_key: str) -> Iterable[Candidate]:
    if source_key == "agents-home":
        yield from iter_agents_home(options)
    elif source_key == "agents-skills":
        yield from iter_agents_skills(options)
    elif source_key == "agents-tools-docs":
        yield from iter_agents_tools_docs(options)
    elif source_key == "codex-memory":
        yield from iter_codex_memory(options)
    elif source_key == "codex-thread-metadata":
        yield from iter_codex_thread_metadata(options)
    elif source_key == "codex-session-index":
        yield from iter_codex_session_index(options)
    elif source_key == "codex-rollouts":
        yield from iter_codex_rollouts(options)
    elif source_key == "repo-docs":
        yield from iter_repo_docs(options)


def iter_agents_home(options: SyncOptions) -> Iterable[Candidate]:
    root = options.agents_home
    seen: set[str] = set()
    for path in [root / "AGENTS.md", *sorted(root.glob("*.md"))]:
        key = normalize_path_key(path)
        if key in seen:
            continue
        seen.add(key)
        yield from file_candidate(path, "agents-home", options)


def iter_agents_skills(options: SyncOptions) -> Iterable[Candidate]:
    root = options.agents_home / "skills"
    for path in sorted(root.glob("*/SKILL.md")):
        yield from file_candidate(path, "agents-skills", options)
    for path in sorted(root.glob("*/README.md")):
        yield from file_candidate(path, "agents-skills", options)
    for path in sorted(root.glob("*/references/**/*.md")):
        yield from file_candidate(path, "agents-skills", options)


def iter_agents_tools_docs(options: SyncOptions) -> Iterable[Candidate]:
    root = options.agents_home / "tools"
    patterns = ["*/README.md", "*/CLAUDE_START_HERE.md", "*/PUBLISHING_LOCAL.md", "*/docs/**/*.md"]
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            yield from file_candidate(path, "agents-tools-docs", options)
    yield from file_candidate(Path(__file__).resolve().parents[1] / "plan.md", "agents-tools-docs", options)


def iter_codex_memory(options: SyncOptions) -> Iterable[Candidate]:
    root = options.codex_home / "memories"
    for path in [root / "memory_summary.md", root / "MEMORY.md"]:
        yield from file_candidate(path, "codex-memory", options)
    for path in newest_files(root / "rollout_summaries", "*.md", options.sample_size):
        yield from file_candidate(path, "codex-memory", options)


def iter_codex_thread_metadata(options: SyncOptions) -> Iterable[Candidate]:
    db_file = options.codex_home / "state_5.sqlite"
    if not db_file.exists():
        return
    uri = f"file:{db_file.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT id, title, cwd, rollout_path, first_user_message, preview, updated_at, created_at
            FROM threads
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (options.sample_size,),
        ).fetchall()
    finally:
        con.close()
    for row in rows:
        text = build_thread_memory(row)
        path_key = f"codex-thread:{row['id']}"
        yield Candidate(
            source_key="codex-thread-metadata",
            path_key=path_key,
            display_path=path_key,
            text=text,
            title=clean_title(row["title"]) or f"Codex thread {row['id']}",
            artifact_type="codex-thread-metadata",
            repo_path=row["cwd"] or "",
            target_surface="Codex thread metadata",
            tags=["codex", "thread", "metadata", "context-sync"],
            related_files=[value for value in [row["cwd"], row["rollout_path"]] if value],
            size_bytes=len(text.encode("utf-8")),
            raw_sha256=sha256_text(text),
        )


def iter_codex_session_index(options: SyncOptions) -> Iterable[Candidate]:
    path = options.codex_home / "session_index.jsonl"
    if not path.exists() or is_excluded(path):
        return
    rows = read_jsonl_tail(path, options.sample_size)
    for row in rows:
        title = row.get("thread_name") or row.get("title") or row.get("summary") or row.get("id") or "Codex session index row"
        text = "# Codex Session Index\n\n" + json.dumps(compact_mapping(row), indent=2, ensure_ascii=False)
        row_id = str(row.get("id") or sha256_text(text)[:16])
        path_key = f"codex-session-index:{row_id}"
        yield Candidate(
            source_key="codex-session-index",
            path_key=path_key,
            display_path=str(path),
            text=text,
            title=clean_title(str(title)),
            artifact_type="codex-session-index",
            target_surface="Codex session index",
            tags=["codex", "session", "index", "context-sync"],
            related_files=[str(path)],
            mtime_ns=path.stat().st_mtime_ns,
            size_bytes=len(text.encode("utf-8")),
            raw_sha256=sha256_text(text),
        )


def iter_codex_rollouts(options: SyncOptions) -> Iterable[Candidate]:
    root = options.codex_home / "sessions"
    for path in newest_files(root, "*.jsonl", options.sample_size):
        if is_excluded(path):
            continue
        text = build_rollout_sample(path)
        if not text.strip():
            continue
        yield Candidate(
            source_key="codex-rollouts",
            path_key=normalize_path_key(path),
            display_path=str(path),
            text=text,
            title=f"Codex rollout {path.stem}",
            artifact_type="codex-rollout-sample",
            target_surface="Codex rollout sample",
            tags=["codex", "rollout", "sample", "context-sync"],
            related_files=[str(path)],
            mtime_ns=path.stat().st_mtime_ns,
            size_bytes=len(text.encode("utf-8")),
            raw_sha256=sha256_text(text),
        )


def iter_repo_docs(options: SyncOptions) -> Iterable[Candidate]:
    roots = default_repo_roots() + options.repo_roots
    seen: set[str] = set()
    patterns = ["AGENTS.md", "CLAUDE.md", "LLM.md", "README.md", "CONTEXT.md", "APP_LINGO.md", "docs/**/*.md", "docs/adr/**/*.md", ".llm_brain/**/*.md"]
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for pattern in patterns:
            for path in sorted(root.glob(pattern)):
                key = normalize_path_key(path)
                if key in seen:
                    continue
                seen.add(key)
                yield from file_candidate(path, "repo-docs", options, repo_path=str(root))


def file_candidate(path: Path, source_key: str, options: SyncOptions, repo_path: str = "") -> Iterable[Candidate]:
    if not path.exists() or not path.is_file() or is_excluded(path):
        return
    stat = path.stat()
    if stat.st_size > options.max_bytes:
        return
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return
    memory_text = build_file_memory(path, text)
    if not memory_text.strip():
        return
    yield Candidate(
        source_key=source_key,
        path_key=normalize_path_key(path),
        display_path=str(path),
        text=memory_text,
        title=title_from_path(path, text),
        artifact_type="context-file",
        repo_path=repo_path,
        target_surface=source_key,
        tags=[source_key, "context-sync"],
        related_files=[str(path)],
        mtime_ns=stat.st_mtime_ns,
        size_bytes=stat.st_size,
        raw_sha256=sha256_bytes(raw),
    )


def build_file_memory(path: Path, text: str) -> str:
    useful = select_useful_lines(text)
    if len(useful) < 200:
        useful = first_nonempty_text(text)
    useful = useful[:MEMORY_TEXT_LIMIT].strip()
    return f"# Context File: {path.name}\n\n- Source path: {path}\n- Kind: existing user/agent context\n\n## Useful Context\n\n{useful}\n"


def build_thread_memory(row: sqlite3.Row) -> str:
    values = {
        "id": row["id"],
        "title": clean_title(row["title"]),
        "cwd": row["cwd"] or "",
        "rollout_path": Path(row["rollout_path"]).name if row["rollout_path"] else "",
        "first_user_message": compact_text(row["first_user_message"] or "", 1200),
        "preview": compact_text(row["preview"] or "", 1200),
    }
    return "# Codex Thread Metadata\n\n" + json.dumps(values, indent=2, ensure_ascii=False)


def build_rollout_sample(path: Path) -> str:
    session: dict[str, Any] = {}
    user_messages: list[str] = []
    final_messages: list[str] = []
    events: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = item.get("payload") or {}
            if item.get("type") == "session_meta":
                session = {key: payload.get(key) for key in ("id", "cwd", "source", "model_provider")}
            elif item.get("type") == "event_msg" and len(events) < 20:
                message = payload.get("message")
                if message:
                    events.append(compact_text(str(message), 240))
            elif item.get("type") == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                text = message_text(payload)
                if role == "user" and text and len(user_messages) < 5:
                    user_messages.append(compact_text(text, 700))
                elif role == "assistant" and text:
                    final_messages.append(compact_text(text, 1000))
                    final_messages = final_messages[-3:]
    sample = {
        "session": session,
        "source_file": str(path),
        "user_messages_sample": user_messages,
        "assistant_final_sample": final_messages,
        "events_sample": events,
    }
    return "# Codex Rollout Sample\n\n" + json.dumps(sample, indent=2, ensure_ascii=False)


def deterministic_distill(candidate: Candidate, scrubbed_text: str, summary: str) -> str:
    lines = [
        f"Summary: {summary}",
        f"Source: {candidate.source_key}",
        f"Title: {candidate.title}",
        "Tags: " + ", ".join(candidate.tags),
        "Related files: " + ", ".join(candidate.related_files[:20]),
        "",
        "Search text:",
        compact_text(scrubbed_text, DISTILLED_TEXT_LIMIT),
    ]
    return "\n".join(lines).strip()


def summarize_candidate(candidate: Candidate, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip(" -#\t")
        if stripped and not stripped.lower().startswith("source path:"):
            return compact_text(stripped, 300)
    return compact_text(candidate.title, 300)


def select_useful_lines(text: str) -> str:
    selected: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if selected and selected[-1] != "":
                selected.append("")
            continue
        lower = stripped.lower()
        useful = (
            stripped.startswith("#")
            or stripped.startswith(("-", "*", "1."))
            or "`" in stripped
            or "\\" in stripped
            or "/" in stripped
            or any(keyword in lower for keyword in RELEVANCE_KEYWORDS)
        )
        if useful:
            selected.append(stripped)
        if sum(len(item) + 1 for item in selected) >= MEMORY_TEXT_LIMIT:
            break
    return "\n".join(selected)


def first_nonempty_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[:MEMORY_TEXT_LIMIT]


def is_good_memory(text: str, candidate: Candidate, allow_redactions: bool = False) -> bool:
    compact = text.strip()
    if len(compact) < 60:
        return False
    lower = compact.lower()
    if compact.count("[REDACTED:") > 0 and not allow_redactions:
        return False
    if candidate.source_key in {"agents-home", "agents-skills", "agents-tools-docs", "codex-memory", "codex-thread-metadata", "repo-docs"}:
        return True
    return any(keyword in lower for keyword in RELEVANCE_KEYWORDS)


def write_quarantine(run_uid: str, record_uid: str, candidate: Candidate, scrubbed_text: str, warnings: list[str]) -> Path:
    path = artifact_root() / "quarantine" / "context-sync" / run_uid / f"{record_uid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        f"# Quarantined Context Candidate: {candidate.title}",
        "",
        f"- Source: {candidate.source_key}",
        f"- Path: {candidate.display_path}",
        f"- Warnings: {', '.join(warnings)}",
        "",
        "## Scrubbed Preview",
        "",
        scrubbed_text[:MEMORY_TEXT_LIMIT],
    ]
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def is_excluded(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if parts.intersection(HARD_EXCLUDE_PARTS):
        return True
    lower_name = path.name.lower()
    if path.suffix.lower() in HARD_EXCLUDE_SUFFIXES:
        return True
    if path.suffix.lower() and path.suffix.lower() not in TEXT_SUFFIXES:
        return True
    return any(fnmatch.fnmatch(lower_name, pattern.lower()) for pattern in SECRET_NAME_PATTERNS)


def newest_files(root: Path, pattern: str, limit: int) -> list[Path]:
    if not root.exists():
        return []
    files = [path for path in root.rglob(pattern) if path.is_file() and not is_excluded(path)]
    files.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
    return files[:limit]


def default_repo_roots() -> list[Path]:
    candidates = [
        Path(r"C:\Codex\build your own brain"),
        Path(r"C:\Github\ourstuff.space"),
        Path(r"C:\Github\resumes.ourstuff.space"),
        Path(r"C:\Codex\codex_utility_belt"),
    ]
    return [path for path in candidates if path.exists()]


def read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows[-limit:]


def compact_mapping(row: dict[str, Any]) -> dict[str, Any]:
    keep = {}
    for key in ("id", "thread_name", "title", "summary", "updated_at", "path", "rollout_path", "session_path"):
        if row.get(key):
            keep[key] = row[key]
    return keep


def message_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in payload.get("content") or []:
        if isinstance(item, dict):
            text = item.get("text")
            if text:
                parts.append(str(text))
    return "\n".join(parts)


def clean_title(value: Any) -> str:
    text = compact_text(str(value or ""), 180)
    return text.replace("\n", " ").strip()


def title_from_path(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return clean_title(stripped.strip("# "))
    return path.name


def compact_text(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    return value[:limit].rstrip()


def normalize_path_key(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()
    return os.path.normcase(str(resolved))


def stable_uid(source_key: str, path_key: str) -> str:
    return hashlib.sha256(f"{source_key}\n{path_key}".encode("utf-8")).hexdigest()[:32]


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:PATH_HASH_BYTES]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def limited(items: Iterable[Candidate], limit: int) -> Iterable[Candidate]:
    for index, item in enumerate(items):
        if limit and index >= limit:
            break
        yield item


def summarize_totals(results: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("scanned", "new", "changed", "unchanged", "skipped", "quarantined")
    totals = {key: sum(int(result.get(key, 0)) for result in results) for key in keys}
    totals["errors"] = sum(len(result.get("errors", [])) for result in results)
    return totals


def print_human_summary(result: dict[str, Any]) -> None:
    mode = "DRY-RUN" if result["dry_run"] else "APPLY"
    totals = result["totals"]
    print(f"sync-context {mode}: scanned={totals['scanned']} new={totals['new']} changed={totals['changed']} unchanged={totals['unchanged']} skipped={totals['skipped']} quarantined={totals['quarantined']} errors={totals['errors']}")
    for source in result["sources"]:
        print(
            f"- {source['source']}: scanned={source['scanned']} new={source['new']} changed={source['changed']} unchanged={source['unchanged']} skipped={source['skipped']} quarantined={source['quarantined']} errors={len(source['errors'])}"
        )
