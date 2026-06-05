#!/usr/bin/env python3
"""Deterministic Codex chat title distiller.

Reads Codex local thread metadata plus rollout JSONL logs and proposes
date-prefixed accomplishment titles without using an LLM.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ACTION_WORDS = {
    "added",
    "built",
    "completed",
    "created",
    "documented",
    "fixed",
    "found",
    "identified",
    "implemented",
    "improved",
    "investigated",
    "moved",
    "planned",
    "proposed",
    "removed",
    "renamed",
    "reviewed",
    "switched",
    "updated",
    "verified",
}

WEAK_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "so",
    "that",
    "the",
    "this",
    "to",
    "with",
}

TITLE_CASE_OVERRIDES = {
    "ai": "AI",
    "api": "API",
    "cli": "CLI",
    "codex": "Codex",
    "css": "CSS",
    "db": "DB",
    "docx": "DOCX",
    "html": "HTML",
    "json": "JSON",
    "jsonl": "JSONL",
    "llm": "LLM",
    "mcp": "MCP",
    "openrouter": "OpenRouter",
    "sqlite": "SQLite",
    "ui": "UI",
}

TYPO_FIXES = {
    "dashbaord": "dashboard",
    "dleeted": "deleted",
    "fuzy": "fuzzy",
    "hroziaotnal": "horizontal",
    "reutnr": "return",
    "retun": "return",
    "sqllite": "sqlite",
    "verticle": "vertical",
}


@dataclass
class ThreadRow:
    id: str
    title: str
    created_at: int
    rollout_path: str
    first_user_message: str
    thread_source: str
    agent_nickname: str


@dataclass
class Candidate:
    text: str
    score: int
    source: str


@dataclass
class Proposal:
    id: str
    old_title: str
    new_title: str
    source: str
    reason: str
    skipped: bool = False


def local_day_bounds(day: str) -> tuple[int, int]:
    parsed = dt.datetime.strptime(day, "%Y-%m-%d").date()
    start = dt.datetime.combine(parsed, dt.time.min).astimezone()
    end = start + dt.timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def default_codex_home() -> Path:
    return Path.home() / ".codex"


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def strip_markup(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s)]+", " ", text)
    text = re.sub(r"[/\\][\w .-]+[/\\][^\s)]+", " ", text)
    text = text.replace("_", " ").replace("-", " ")
    return normalize_space(text)


def fix_typos(text: str) -> str:
    words = []
    for word in text.split():
        key = re.sub(r"[^a-z]", "", word.lower())
        words.append(TYPO_FIXES.get(key, word))
    return " ".join(words)


def useful_words(text: str, max_words: int) -> list[str]:
    cleaned = re.sub(r"[^A-Za-z0-9.+# ]+", " ", text)
    words = []
    for word in cleaned.split():
        low = word.lower().strip(".")
        if not low or low in WEAK_WORDS:
            continue
        if len(low) == 1 and not low.isdigit():
            continue
        words.append(word.strip("."))
        if len(words) >= max_words:
            break
    return words


def title_case(words: Iterable[str]) -> str:
    output = []
    for word in words:
        clean = word.strip()
        if not clean:
            continue
        low = clean.lower()
        if low in TITLE_CASE_OVERRIDES:
            output.append(TITLE_CASE_OVERRIDES[low])
        elif re.fullmatch(r"[A-Z0-9]{2,}", clean):
            output.append(clean)
        else:
            output.append(low[:1].upper() + low[1:])
    return " ".join(output)


def split_phrases(text: str) -> list[str]:
    text = strip_markup(text)
    text = re.sub(r"\b[SCDROPNAFUX]{1,3}\s+", " ", text)
    raw_parts = re.split(r"(?:\.\s+|\n+|;|\|| - |\u2022)", text)
    parts = []
    for part in raw_parts:
        part = normalize_space(part)
        part = re.sub(r"^(changed|tracked changes|verification|verified|checks|outcome|result):\s*", "", part, flags=re.I)
        if len(part) >= 12:
            parts.append(part)
    return parts


def action_phrase(text: str) -> str | None:
    fixed = fix_typos(text)
    match = re.search(
        r"\b("
        + "|".join(sorted(ACTION_WORDS))
        + r")\b\s+(?:the|a|an|this|that)?\s*(.+)",
        fixed,
        flags=re.I,
    )
    if not match:
        return None
    verb = match.group(1)
    rest = re.split(r"(?: because | when | so | and then | but |, then )", match.group(2), maxsplit=1, flags=re.I)[0]
    words = useful_words(rest, 6)
    if len(words) < 2:
        return None
    return title_case([verb] + words)


def keyword_phrase(text: str, max_words: int = 6) -> str:
    fixed = fix_typos(text)
    words = useful_words(fixed, max_words)
    if not words:
        return "Untitled Chat"
    return title_case(words)


def read_rollout_signals(path: Path) -> dict[str, object]:
    signals: dict[str, object] = {
        "final_assistant": [],
        "assistant": [],
        "user": [],
        "calls": [],
        "parse_errors": 0,
    }
    if not path.exists():
        return signals

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                signals["parse_errors"] = int(signals["parse_errors"]) + 1
                continue
            if item.get("type") != "response_item":
                continue
            payload = item.get("payload") or {}
            kind = payload.get("type")
            if kind == "function_call":
                name = payload.get("name")
                if name:
                    signals["calls"].append(name)
                continue
            if kind != "message":
                continue
            role = payload.get("role")
            text = " ".join(
                block.get("text", "")
                for block in payload.get("content", [])
                if block.get("type") == "output_text"
            )
            text = normalize_space(text)
            if not text:
                continue
            if role == "assistant":
                signals["assistant"].append(text)
                if payload.get("phase") == "final" or item.get("phase") == "final":
                    signals["final_assistant"].append(text)
            elif role == "user":
                signals["user"].append(text)
    return signals


def candidate_from_thread(thread: ThreadRow) -> tuple[str, str]:
    rollout = read_rollout_signals(Path(thread.rollout_path))
    candidates: list[Candidate] = []

    existing_title = normalize_space(thread.title)
    if existing_title and "\n" not in thread.title and len(existing_title) <= 90 and len(existing_title.split()) <= 9:
        candidates.append(Candidate(existing_title, 125, "existing concise title"))

    for text in rollout["final_assistant"][-3:]:
        for phrase in split_phrases(text):
            score = 90
            if re.search(r"\b(implemented|fixed|added|removed|created|updated|verified|proposed|found|identified)\b", phrase, re.I):
                score += 30
            candidates.append(Candidate(phrase, score, "final assistant"))

    for text in rollout["assistant"][-5:]:
        for phrase in split_phrases(text):
            score = 45
            if re.search(r"\b(implemented|fixed|added|removed|created|updated|verified|proposed|found|identified)\b", phrase, re.I):
                score += 25
            candidates.append(Candidate(phrase, score, "assistant"))

    for text in [thread.first_user_message, thread.title] + list(rollout["user"][-2:]):
        for phrase in split_phrases(text):
            score = 25
            if re.search(r"\b(fix|add|remove|rename|update|create|build|plan|review|test|delete)\b", phrase, re.I):
                score += 15
            candidates.append(Candidate(phrase, score, "user prompt"))

    if thread.thread_source == "subagent" and thread.agent_nickname:
        candidates.append(Candidate(f"{thread.agent_nickname} Findings", 20, "subagent"))

    if not candidates:
        return "Empty Compaction Thread", "empty"

    best_title = ""
    best_reason = ""
    best_score = -1
    for candidate in candidates:
        phrase = action_phrase(candidate.text) or keyword_phrase(candidate.text)
        if phrase.lower() in {"implemented", "fixed", "updated", "changed"}:
            continue
        score = candidate.score + min(len(phrase.split()), 8)
        if len(phrase.split()) > 8:
            score -= 5
        if score > best_score:
            best_title = phrase
            best_reason = candidate.source
            best_score = score

    if not best_title:
        best_title = keyword_phrase(candidates[0].text)
        best_reason = candidates[0].source

    return best_title, best_reason


def title_for_date(day: str, phrase: str, max_words: int) -> str:
    words = phrase.split()[:max_words]
    return f"{day} {' '.join(words)}".strip()


def console_safe(text: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")


def safe_repr(text: str) -> str:
    return console_safe(repr(text))


def read_threads(db_path: Path, day: str, limit: int | None) -> list[ThreadRow]:
    start, end = local_day_bounds(day)
    sql = """
        select id, title, created_at, rollout_path, first_user_message,
               thread_source, coalesce(agent_nickname, '') as agent_nickname
        from threads
        where created_at >= ? and created_at < ?
        order by created_at
    """
    if limit is not None:
        sql += " limit ?"
        params: tuple[object, ...] = (start, end, limit)
    else:
        params = (start, end)

    con = sqlite3.connect(str(db_path))
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    return [
        ThreadRow(
            id=row["id"],
            title=row["title"] or "",
            created_at=int(row["created_at"]),
            rollout_path=row["rollout_path"],
            first_user_message=row["first_user_message"] or "",
            thread_source=row["thread_source"] or "",
            agent_nickname=row["agent_nickname"] or "",
        )
        for row in rows
    ]


def make_proposals(threads: list[ThreadRow], day: str, force_retitle: bool, max_words: int) -> list[Proposal]:
    proposals: list[Proposal] = []
    dated_prefix = re.compile(r"^\d{4}-\d{2}-\d{2}\s+")
    for thread in threads:
        date_match = dated_prefix.match(thread.title)
        if date_match and not force_retitle:
            proposals.append(
                Proposal(thread.id, thread.title, thread.title, "existing", "already date-prefixed", skipped=True)
            )
            continue
        if date_match and force_retitle:
            phrase = dated_prefix.sub("", thread.title).strip()
            reason = "existing date title"
        else:
            phrase, reason = candidate_from_thread(thread)
        proposals.append(
            Proposal(
                id=thread.id,
                old_title=thread.title,
                new_title=title_for_date(day, phrase, max_words),
                source=reason,
                reason="candidate scoring",
            )
        )
    return proposals


def safe_backup_sqlite(db_path: Path, backup_path: Path) -> None:
    source = sqlite3.connect(str(db_path))
    try:
        dest = sqlite3.connect(str(backup_path))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def append_session_index(index_path: Path, proposals: list[Proposal]) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    with index_path.open("a", encoding="utf-8") as handle:
        for proposal in proposals:
            if proposal.skipped:
                continue
            handle.write(
                json.dumps(
                    {"id": proposal.id, "thread_name": proposal.new_title, "updated_at": now},
                    separators=(",", ":"),
                )
                + "\n"
            )


def write_audit(codex_home: Path, proposals: list[Proposal], applied: bool, backup_path: Path | None) -> Path:
    audit_dir = codex_home / "title-distill-audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    audit_path = audit_dir / f"codex-title-distill-{stamp}.jsonl"
    with audit_path.open("w", encoding="utf-8") as handle:
        for proposal in proposals:
            handle.write(
                json.dumps(
                    {
                        "id": proposal.id,
                        "old_title": proposal.old_title,
                        "new_title": proposal.new_title,
                        "source": proposal.source,
                        "reason": proposal.reason,
                        "skipped": proposal.skipped,
                        "applied": applied and not proposal.skipped,
                        "backup_path": str(backup_path) if backup_path else None,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    return audit_path


def apply_titles(codex_home: Path, db_path: Path, proposals: list[Proposal], update_index: bool) -> tuple[Path, Path | None]:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    db_backup = db_path.with_name(f"{db_path.name}.bak-codex-title-distill-{stamp}")
    if db_backup.exists():
        raise RuntimeError(f"Refusing to overwrite existing backup: {db_backup}")
    safe_backup_sqlite(db_path, db_backup)

    index_backup: Path | None = None
    index_path = codex_home / "session_index.jsonl"
    if update_index and index_path.exists():
        index_backup = index_path.with_name(f"{index_path.name}.bak-codex-title-distill-{stamp}")
        if index_backup.exists():
            raise RuntimeError(f"Refusing to overwrite existing backup: {index_backup}")
        shutil.copy2(index_path, index_backup)

    pending = [proposal for proposal in proposals if not proposal.skipped]
    con = sqlite3.connect(str(db_path))
    try:
        try:
            con.execute("begin")
            for proposal in pending:
                cursor = con.execute("update threads set title = ? where id = ?", (proposal.new_title, proposal.id))
                if cursor.rowcount != 1:
                    raise RuntimeError(f"Expected to update 1 row for {proposal.id}, got {cursor.rowcount}")
            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()

    if update_index and index_path.exists():
        append_session_index(index_path, proposals)
    return db_backup, index_backup


def parse_args(argv: list[str]) -> argparse.Namespace:
    today = dt.datetime.now().strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(description="Deterministically rename Codex chats by date and accomplishment.")
    parser.add_argument("--date", default=today, help="Local date to process, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--codex-home", default=str(default_codex_home()), help="Codex home directory. Defaults to ~/.codex.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of threads processed.")
    parser.add_argument("--max-title-words", type=int, default=7, help="Maximum words after date prefix.")
    parser.add_argument("--force-retitle", action="store_true", help="Retitle chats that already start with YYYY-MM-DD.")
    parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation when using --apply.")
    parser.add_argument("--no-session-index", action="store_true", help="Do not append compatibility records to session_index.jsonl.")
    parser.add_argument("--json", action="store_true", help="Emit JSONL proposals instead of text table.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        dt.datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print("ERROR: --date must be YYYY-MM-DD", file=sys.stderr)
        return 2

    codex_home = Path(args.codex_home).expanduser().resolve()
    db_path = (codex_home / "state_5.sqlite").resolve()
    if not db_path.exists():
        print(f"ERROR: Codex state DB not found: {db_path}", file=sys.stderr)
        return 2
    if codex_home not in db_path.parents:
        print(f"ERROR: Refusing DB outside codex home: {db_path}", file=sys.stderr)
        return 2

    threads = read_threads(db_path, args.date, args.limit)
    proposals = make_proposals(threads, args.date, args.force_retitle, args.max_title_words)

    for proposal in proposals:
        if args.json:
            print(json.dumps(proposal.__dict__, ensure_ascii=False, separators=(",", ":")))
        else:
            marker = "SKIP" if proposal.skipped else ("APPLY" if args.apply else "DRY")
            print(
                console_safe(
                    f"{marker} {proposal.id} | {safe_repr(proposal.old_title)} -> "
                    f"{safe_repr(proposal.new_title)} [{proposal.source}]"
                )
            )

    if not args.apply:
        audit_path = write_audit(codex_home, proposals, applied=False, backup_path=None)
        print(f"DRY-RUN only. Audit: {audit_path}")
        return 0

    pending_count = len([proposal for proposal in proposals if not proposal.skipped])
    if pending_count == 0:
        audit_path = write_audit(codex_home, proposals, applied=False, backup_path=None)
        print(f"No pending title changes. Audit: {audit_path}")
        return 0

    if not args.yes:
        answer = input(f"Apply {pending_count} Codex title updates for {args.date}? Type YES: ").strip()
        if answer != "YES":
            print("Aborted.")
            return 1

    try:
        db_backup, index_backup = apply_titles(codex_home, db_path, proposals, not args.no_session_index)
        audit_path = write_audit(codex_home, proposals, applied=True, backup_path=db_backup)
    except Exception as exc:
        print(f"ERROR: apply failed: {exc}", file=sys.stderr)
        return 1

    print(f"Applied {pending_count} title updates.")
    print(f"DB backup: {db_backup}")
    if index_backup:
        print(f"session_index backup: {index_backup}")
    print(f"Audit: {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
