from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


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
