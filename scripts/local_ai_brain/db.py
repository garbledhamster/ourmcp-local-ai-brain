from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3


class BrainConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, factory=BrainConnection)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        con.execute("PRAGMA journal_mode = DELETE")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY,
            run_uid TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            repo_path TEXT NOT NULL DEFAULT '',
            target_surface TEXT NOT NULL DEFAULT '',
            goal TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            summary TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS memory_records (
            id INTEGER PRIMARY KEY,
            record_uid TEXT NOT NULL UNIQUE,
            run_uid TEXT NOT NULL DEFAULT '',
            artifact_path TEXT NOT NULL UNIQUE,
            raw_path TEXT NOT NULL DEFAULT '',
            scrubbed_path TEXT NOT NULL DEFAULT '',
            distilled_path TEXT NOT NULL DEFAULT '',
            artifact_type TEXT NOT NULL DEFAULT 'artifact',
            repo_path TEXT NOT NULL DEFAULT '',
            target_surface TEXT NOT NULL DEFAULT '',
            ticket_title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            summary TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            tags_text TEXT NOT NULL DEFAULT '',
            related_files_json TEXT NOT NULL DEFAULT '[]',
            related_files_text TEXT NOT NULL DEFAULT '',
            search_text TEXT NOT NULL DEFAULT '',
            scrub_status TEXT NOT NULL DEFAULT 'unknown',
            scrub_warnings_json TEXT NOT NULL DEFAULT '[]',
            distill_status TEXT NOT NULL DEFAULT 'unknown',
            classifier_json TEXT NOT NULL DEFAULT '{}',
            content_sha256 TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_records_hash_source
        ON memory_records(content_sha256, source);

        CREATE INDEX IF NOT EXISTS idx_memory_records_repo_surface
        ON memory_records(repo_path, target_surface, updated_at);

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            event_uid TEXT NOT NULL UNIQUE,
            record_uid TEXT NOT NULL DEFAULT '',
            run_uid TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            repo_path TEXT NOT NULL DEFAULT '',
            target_surface TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            related_files_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS source_files (
            id INTEGER PRIMARY KEY,
            source_key TEXT NOT NULL,
            path_key TEXT NOT NULL,
            display_path TEXT NOT NULL DEFAULT '',
            mtime_ns INTEGER NOT NULL DEFAULT 0,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            raw_sha256 TEXT NOT NULL DEFAULT '',
            scrubbed_sha256 TEXT NOT NULL DEFAULT '',
            record_uid TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            UNIQUE(source_key, path_key)
        );

        CREATE INDEX IF NOT EXISTS idx_source_files_status
        ON source_files(source_key, status, last_seen_at);

        CREATE TABLE IF NOT EXISTS proof_sessions (
            id INTEGER PRIMARY KEY,
            proof_session_uid TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            repo_path TEXT NOT NULL DEFAULT '',
            target_surface TEXT NOT NULL DEFAULT '',
            task_class TEXT NOT NULL DEFAULT 'unknown',
            summary TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            outcome TEXT NOT NULL DEFAULT '',
            outcome_summary TEXT NOT NULL DEFAULT '',
            checks_json TEXT NOT NULL DEFAULT '[]',
            estimates_json TEXT NOT NULL DEFAULT '{}',
            classifications_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE INDEX IF NOT EXISTS idx_proof_sessions_repo_surface
        ON proof_sessions(repo_path, target_surface, created_at);

        CREATE TABLE IF NOT EXISTS proof_lookups (
            id INTEGER PRIMARY KEY,
            proof_lookup_uid TEXT NOT NULL UNIQUE,
            proof_session_uid TEXT NOT NULL DEFAULT '',
            event_uid TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            lookup_type TEXT NOT NULL DEFAULT '',
            repo_path TEXT NOT NULL DEFAULT '',
            target_surface TEXT NOT NULL DEFAULT '',
            query TEXT NOT NULL DEFAULT '',
            limit_requested INTEGER NOT NULL DEFAULT 0,
            result_count INTEGER NOT NULL DEFAULT 0,
            duration_ms REAL NOT NULL DEFAULT 0,
            returned_chars INTEGER NOT NULL DEFAULT 0,
            result_record_uids_json TEXT NOT NULL DEFAULT '[]',
            classifications_json TEXT NOT NULL DEFAULT '[]',
            used_count INTEGER NOT NULL DEFAULT 0,
            stale_count INTEGER NOT NULL DEFAULT 0,
            irrelevant_count INTEGER NOT NULL DEFAULT 0,
            not_used_count INTEGER NOT NULL DEFAULT 0,
            unknown_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_proof_lookups_session
        ON proof_lookups(proof_session_uid, lookup_type, created_at);

        CREATE INDEX IF NOT EXISTS idx_proof_lookups_event
        ON proof_lookups(event_uid);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_proof_lookups_session_event_unique
        ON proof_lookups(proof_session_uid, event_uid)
        WHERE event_uid <> '';

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_records_fts USING fts5(
            ticket_title,
            summary,
            tags_text,
            related_files_text,
            search_text,
            content='memory_records',
            content_rowid='id'
        );

        CREATE TRIGGER IF NOT EXISTS memory_records_ai AFTER INSERT ON memory_records BEGIN
            INSERT INTO memory_records_fts(rowid, ticket_title, summary, tags_text, related_files_text, search_text)
            VALUES (new.id, new.ticket_title, new.summary, new.tags_text, new.related_files_text, new.search_text);
        END;

        CREATE TRIGGER IF NOT EXISTS memory_records_ad AFTER DELETE ON memory_records BEGIN
            INSERT INTO memory_records_fts(memory_records_fts, rowid, ticket_title, summary, tags_text, related_files_text, search_text)
            VALUES('delete', old.id, old.ticket_title, old.summary, old.tags_text, old.related_files_text, old.search_text);
        END;

        CREATE TRIGGER IF NOT EXISTS memory_records_au AFTER UPDATE ON memory_records BEGIN
            INSERT INTO memory_records_fts(memory_records_fts, rowid, ticket_title, summary, tags_text, related_files_text, search_text)
            VALUES('delete', old.id, old.ticket_title, old.summary, old.tags_text, old.related_files_text, old.search_text);
            INSERT INTO memory_records_fts(rowid, ticket_title, summary, tags_text, related_files_text, search_text)
            VALUES (new.id, new.ticket_title, new.summary, new.tags_text, new.related_files_text, new.search_text);
        END;
        """
    )
    con.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    con.commit()


def insert_memory_record(con: sqlite3.Connection, record: dict[str, Any]) -> int:
    columns = [
        "record_uid",
        "run_uid",
        "artifact_path",
        "raw_path",
        "scrubbed_path",
        "distilled_path",
        "artifact_type",
        "repo_path",
        "target_surface",
        "ticket_title",
        "status",
        "summary",
        "tags_json",
        "tags_text",
        "related_files_json",
        "related_files_text",
        "search_text",
        "scrub_status",
        "scrub_warnings_json",
        "distill_status",
        "classifier_json",
        "content_sha256",
        "source",
        "created_at",
        "updated_at",
    ]
    values = [record.get(column, "") for column in columns]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{column}=excluded.{column}" for column in columns if column not in {"record_uid", "created_at"})
    sql = (
        f"INSERT INTO memory_records({','.join(columns)}) VALUES({placeholders}) "
        f"ON CONFLICT(content_sha256, source) DO UPDATE SET {updates}"
    )
    cur = con.execute(sql, values)
    con.commit()
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = con.execute(
        "SELECT id FROM memory_records WHERE content_sha256 = ? AND source = ?",
        (record["content_sha256"], record["source"]),
    ).fetchone()
    return int(row["id"])


def upsert_memory_record_by_uid(con: sqlite3.Connection, record: dict[str, Any]) -> int:
    columns = memory_record_columns()
    values = [record.get(column, "") for column in columns]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{column}=excluded.{column}" for column in columns if column not in {"record_uid", "created_at"})
    sql = (
        f"INSERT INTO memory_records({','.join(columns)}) VALUES({placeholders}) "
        f"ON CONFLICT(record_uid) DO UPDATE SET {updates}"
    )
    cur = con.execute(sql, values)
    con.commit()
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = con.execute("SELECT id FROM memory_records WHERE record_uid = ?", (record["record_uid"],)).fetchone()
    return int(row["id"])


def memory_record_columns() -> list[str]:
    return [
        "record_uid",
        "run_uid",
        "artifact_path",
        "raw_path",
        "scrubbed_path",
        "distilled_path",
        "artifact_type",
        "repo_path",
        "target_surface",
        "ticket_title",
        "status",
        "summary",
        "tags_json",
        "tags_text",
        "related_files_json",
        "related_files_text",
        "search_text",
        "scrub_status",
        "scrub_warnings_json",
        "distill_status",
        "classifier_json",
        "content_sha256",
        "source",
        "created_at",
        "updated_at",
    ]


def get_source_file(con: sqlite3.Connection, source_key: str, path_key: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM source_files WHERE source_key = ? AND path_key = ?",
        (source_key, path_key),
    ).fetchone()


def upsert_source_file(con: sqlite3.Connection, entry: dict[str, Any]) -> int:
    columns = [
        "source_key",
        "path_key",
        "display_path",
        "mtime_ns",
        "size_bytes",
        "raw_sha256",
        "scrubbed_sha256",
        "record_uid",
        "status",
        "warnings_json",
        "summary",
        "first_seen_at",
        "last_seen_at",
    ]
    values = [entry.get(column, "") for column in columns]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{column}=excluded.{column}" for column in columns if column not in {"source_key", "path_key", "first_seen_at"})
    sql = (
        f"INSERT INTO source_files({','.join(columns)}) VALUES({placeholders}) "
        f"ON CONFLICT(source_key, path_key) DO UPDATE SET {updates}"
    )
    cur = con.execute(sql, values)
    con.commit()
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = con.execute(
        "SELECT id FROM source_files WHERE source_key = ? AND path_key = ?",
        (entry["source_key"], entry["path_key"]),
    ).fetchone()
    return int(row["id"])


def insert_event(con: sqlite3.Connection, event: dict[str, Any]) -> int:
    columns = [
        "event_uid",
        "record_uid",
        "run_uid",
        "created_at",
        "event_type",
        "repo_path",
        "target_surface",
        "summary",
        "body",
        "tags_json",
        "related_files_json",
    ]
    values = [event.get(column, "") for column in columns]
    placeholders = ",".join("?" for _ in columns)
    cur = con.execute(f"INSERT INTO events({','.join(columns)}) VALUES({placeholders})", values)
    con.commit()
    return int(cur.lastrowid)


def insert_proof_session(con: sqlite3.Connection, session: dict[str, Any]) -> int:
    columns = [
        "proof_session_uid",
        "created_at",
        "updated_at",
        "repo_path",
        "target_surface",
        "task_class",
        "summary",
        "status",
        "outcome",
        "outcome_summary",
        "checks_json",
        "estimates_json",
        "classifications_json",
    ]
    values = [session.get(column, "") for column in columns]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(
        f"{column}=excluded.{column}"
        for column in columns
        if column not in {"proof_session_uid", "created_at", "status", "outcome", "outcome_summary", "checks_json", "estimates_json", "classifications_json"}
    )
    cur = con.execute(
        f"INSERT INTO proof_sessions({','.join(columns)}) VALUES({placeholders}) "
        f"ON CONFLICT(proof_session_uid) DO UPDATE SET {updates}",
        values,
    )
    con.commit()
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = get_proof_session(con, str(session["proof_session_uid"]))
    return int(row["id"])


def get_proof_session(con: sqlite3.Connection, proof_session_uid: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM proof_sessions WHERE proof_session_uid = ?",
        (proof_session_uid,),
    ).fetchone()


def insert_proof_lookup(con: sqlite3.Connection, lookup: dict[str, Any]) -> int:
    columns = [
        "proof_lookup_uid",
        "proof_session_uid",
        "event_uid",
        "created_at",
        "lookup_type",
        "repo_path",
        "target_surface",
        "query",
        "limit_requested",
        "result_count",
        "duration_ms",
        "returned_chars",
        "result_record_uids_json",
        "classifications_json",
        "used_count",
        "stale_count",
        "irrelevant_count",
        "not_used_count",
        "unknown_count",
    ]
    values = [lookup.get(column, "") for column in columns]
    placeholders = ",".join("?" for _ in columns)
    cur = con.execute(f"INSERT INTO proof_lookups({','.join(columns)}) VALUES({placeholders})", values)
    con.commit()
    return int(cur.lastrowid)


def finish_proof_session(con: sqlite3.Connection, proof_session_uid: str, payload: dict[str, Any], updated_at: str) -> dict[str, Any]:
    if get_proof_session(con, proof_session_uid) is None:
        raise ValueError(f"proof session not found: {proof_session_uid}")
    classifications = payload.get("classifications", [])
    if not isinstance(classifications, list):
        classifications = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    skipped = 0
    for item in classifications:
        if not isinstance(item, dict):
            skipped += 1
            continue
        event_uid = str(item.get("event_uid", ""))
        if not event_uid:
            skipped += 1
            continue
        grouped.setdefault(event_uid, []).append(
            {
                "record_uid": str(item.get("record_uid", "")),
                "classification": normalize_lookup_classification(item.get("classification", "unknown")),
                "note": str(item.get("note", ""))[:500],
            }
        )
    updated_lookups = 0
    for event_uid, items in grouped.items():
        counts = classification_counts(items)
        cur = con.execute(
            """
            UPDATE proof_lookups
            SET classifications_json = ?,
                used_count = ?,
                stale_count = ?,
                irrelevant_count = ?,
                not_used_count = ?,
                unknown_count = ?
            WHERE proof_session_uid = ? AND event_uid = ?
            """,
            (
                json_dumps(items),
                counts["used"],
                counts["stale"],
                counts["irrelevant"],
                counts["not_used"],
                counts["unknown"],
                proof_session_uid,
                event_uid,
            ),
        )
        updated_lookups += int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)
        if not cur.rowcount:
            skipped += len(items)
    checks = payload.get("checks", [])
    if not isinstance(checks, list):
        checks = []
    estimates = payload.get("estimates", {})
    if not isinstance(estimates, dict):
        estimates = {}
    con.execute(
        """
        UPDATE proof_sessions
        SET updated_at = ?,
            status = ?,
            outcome = ?,
            outcome_summary = ?,
            checks_json = ?,
            estimates_json = ?,
            classifications_json = ?
        WHERE proof_session_uid = ?
        """,
        (
            updated_at,
            str(payload.get("status", "finished") or "finished"),
            str(payload.get("outcome", ""))[:100],
            str(payload.get("outcome_summary", payload.get("summary", "")))[:2000],
            json_dumps(checks),
            json_dumps(estimates),
            json_dumps(classifications),
            proof_session_uid,
        ),
    )
    con.commit()
    return {"updated_lookups": updated_lookups, "skipped_classifications": skipped}


def proof_report(
    con: sqlite3.Connection,
    repo_path: str = "",
    target_surface: str = "",
    limit: int = 30,
) -> dict[str, Any]:
    filters = []
    params: list[Any] = []
    if repo_path:
        filters.append("repo_path = ?")
        params.append(repo_path)
    if target_surface:
        filters.append("target_surface = ?")
        params.append(target_surface)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    session_rows = list(
        con.execute(
            f"SELECT * FROM proof_sessions {where} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        )
    )
    session_uids = [row["proof_session_uid"] for row in session_rows]
    lookup_rows: list[sqlite3.Row] = []
    if session_uids:
        placeholders = ",".join("?" for _ in session_uids)
        lookup_rows = list(
            con.execute(
                f"SELECT * FROM proof_lookups WHERE proof_session_uid IN ({placeholders}) ORDER BY created_at DESC",
                session_uids,
            )
        )
    durations = [float(row["duration_ms"] or 0) for row in lookup_rows]
    total_duration = sum(durations)
    result_count = sum(int(row["result_count"] or 0) for row in lookup_rows)
    useful_hits = sum(int(row["used_count"] or 0) for row in lookup_rows)
    stale_hits = sum(int(row["stale_count"] or 0) for row in lookup_rows)
    irrelevant_hits = sum(int(row["irrelevant_count"] or 0) for row in lookup_rows)
    not_used_hits = sum(int(row["not_used_count"] or 0) for row in lookup_rows)
    unknown_hits = sum(int(row["unknown_count"] or 0) for row in lookup_rows)
    outcomes: dict[str, int] = {}
    confidence: dict[str, int] = {}
    for row in session_rows:
        outcome = str(row["outcome"] or "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        try:
            estimates = json.loads(row["estimates_json"] or "{}")
        except Exception:
            estimates = {}
        label = str(estimates.get("confidence", "inconclusive") or "inconclusive") if isinstance(estimates, dict) else "inconclusive"
        confidence[label] = confidence.get(label, 0) + 1
    return {
        "ok": True,
        "repo": repo_path,
        "surface": target_surface,
        "limit": limit,
        "sessions": len(session_rows),
        "finished_sessions": sum(1 for row in session_rows if row["status"] == "finished"),
        "active_sessions": sum(1 for row in session_rows if row["status"] == "active"),
        "lookups": len(lookup_rows),
        "zero_result_lookups": sum(1 for row in lookup_rows if int(row["result_count"] or 0) == 0),
        "result_count": result_count,
        "lookup_duration_ms_total": round(total_duration, 3),
        "lookup_duration_ms_avg": round(total_duration / len(durations), 3) if durations else 0,
        "lookup_duration_ms_p50": round(percentile(durations, 50), 3) if durations else 0,
        "lookup_duration_ms_p90": round(percentile(durations, 90), 3) if durations else 0,
        "useful_hits": useful_hits,
        "stale_hits": stale_hits,
        "irrelevant_hits": irrelevant_hits,
        "not_used_hits": not_used_hits,
        "unknown_hits": unknown_hits,
        "useful_hit_rate": round(useful_hits / result_count, 4) if result_count else 0,
        "miss_rate": round(sum(1 for row in lookup_rows if int(row["result_count"] or 0) == 0) / len(lookup_rows), 4) if lookup_rows else 0,
        "outcomes": outcomes,
        "confidence_mix": confidence,
    }


def normalize_lookup_classification(value: Any) -> str:
    text = str(value or "unknown").strip().lower().replace("-", "_")
    if text in {"used", "useful", "relevant"}:
        return "used"
    if text in {"stale", "outdated"}:
        return "stale"
    if text in {"irrelevant", "wrong"}:
        return "irrelevant"
    if text in {"not_used", "unused", "notuseful", "not_useful"}:
        return "not_used"
    return "unknown"


def classification_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"used": 0, "stale": 0, "irrelevant": 0, "not_used": 0, "unknown": 0}
    for item in items:
        counts[normalize_lookup_classification(item.get("classification", "unknown"))] += 1
    return counts


def percentile(values: list[float], percent: int) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = (len(ordered) - 1) * (percent / 100)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def search_records(
    con: sqlite3.Connection,
    query: str = "",
    repo_path: str = "",
    target_surface: str = "",
    limit: int = 10,
) -> list[sqlite3.Row]:
    filters = []
    params: list[Any] = []
    if repo_path:
        filters.append("m.repo_path = ?")
        params.append(repo_path)
    if target_surface:
        filters.append("m.target_surface = ?")
        params.append(target_surface)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    if query.strip():
        fts = fts_query(query)
        if fts:
            prefix = " AND " if where else "WHERE "
            sql = (
                "SELECT m.* FROM memory_records_fts f "
                "JOIN memory_records m ON m.id = f.rowid "
                f"{where}{prefix}memory_records_fts MATCH ? "
                "ORDER BY bm25(memory_records_fts) LIMIT ?"
            )
            return list(con.execute(sql, params + [fts, limit]))
        like = f"%{query}%"
        prefix = " AND " if where else "WHERE "
        sql = (
            f"SELECT m.* FROM memory_records m {where}{prefix}"
            "(m.search_text LIKE ? OR m.summary LIKE ? OR m.ticket_title LIKE ?) "
            "ORDER BY m.updated_at DESC LIMIT ?"
        )
        return list(con.execute(sql, params + [like, like, like, limit]))
    sql = f"SELECT m.* FROM memory_records m {where} ORDER BY m.updated_at DESC LIMIT ?"
    return list(con.execute(sql, params + [limit]))


def rebuild_fts(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO memory_records_fts(memory_records_fts) VALUES('rebuild')")
    con.commit()


def fts_query(query: str) -> str:
    import re

    tokens = re.findall(r"[A-Za-z0-9_./:\\-]+", query)
    return " AND ".join(f'"{token.replace(chr(34), chr(34) + chr(34))}"' for token in tokens[:12])


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
