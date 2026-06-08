from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import init_db, rebuild_fts
from .paths import db_path

LOOKUP_EVENT_TYPES = (
    "brain_search",
    "brain_context_pack",
    "gui_search",
    "gui_context_pack",
)


@dataclass(frozen=True)
class OptimizeOptions:
    database: Path
    apply: bool = False
    lookup_event_retention_days: int = 30
    keep_lookup_events: int = 200
    source_file_retention_days: int = 30
    empty_record_retention_days: int = 7
    drop_missing_artifacts: bool = False
    vacuum: bool = True
    backup: bool = True


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    options = OptimizeOptions(
        database=Path(args.db).expanduser().resolve() if args.db else db_path().resolve(),
        apply=args.apply,
        lookup_event_retention_days=args.lookup_event_retention_days,
        keep_lookup_events=args.keep_lookup_events,
        source_file_retention_days=args.source_file_retention_days,
        empty_record_retention_days=args.empty_record_retention_days,
        drop_missing_artifacts=args.drop_missing_artifacts,
        vacuum=not args.no_vacuum,
        backup=not args.no_backup,
    )
    report = optimize_brain(options)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m local_ai_brain.optimize",
        description="Deterministically prune transient Local AI Brain rows and compact brain.db.",
    )
    parser.add_argument("--db", default="", help="Path to brain.db. Defaults to LOCAL_AI_BRAIN_HOME/brain.db.")
    parser.add_argument("--apply", action="store_true", help="Delete candidates and compact the database.")
    parser.add_argument("--lookup-event-retention-days", type=int, default=30)
    parser.add_argument("--keep-lookup-events", type=int, default=200)
    parser.add_argument("--source-file-retention-days", type=int, default=30)
    parser.add_argument("--empty-record-retention-days", type=int, default=7)
    parser.add_argument(
        "--drop-missing-artifacts",
        action="store_true",
        help="Also remove context-sync memory rows whose artifact_path no longer exists.",
    )
    parser.add_argument("--no-vacuum", action="store_true", help="Skip VACUUM after cleanup.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create a backup before --apply.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report.")
    return parser


def optimize_brain(options: OptimizeOptions) -> dict[str, Any]:
    if not options.database.is_file():
        raise FileNotFoundError(f"brain database not found: {options.database}")

    before_size = options.database.stat().st_size
    con = sqlite3.connect(str(options.database))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        init_db(con)
        before_counts = table_counts(con)
        candidates = {
            "duplicate_memory_records": duplicate_memory_record_ids(con),
            "empty_memory_records": empty_memory_record_ids(con, options.empty_record_retention_days),
            "missing_artifact_memory_records": missing_artifact_memory_record_ids(con) if options.drop_missing_artifacts else [],
            "old_lookup_events": old_lookup_event_ids(
                con,
                retention_days=options.lookup_event_retention_days,
                keep_recent=options.keep_lookup_events,
            ),
            "stale_source_files": stale_source_file_ids(con, options.source_file_retention_days),
        }
        candidate_counts = {key: len(value) for key, value in candidates.items()}
        backup_path = ""
        deleted: dict[str, int] = {key: 0 for key in candidates}
        maintenance: dict[str, str] = {
            "backup": "skipped",
            "rebuild_fts": "skipped",
            "pragma_optimize": "skipped",
            "wal_checkpoint": "skipped",
            "vacuum": "skipped",
            "integrity_check": "skipped",
        }
        if options.apply:
            if options.backup:
                backup_path = backup_database(options.database)
                maintenance["backup"] = "created"
            deleted = apply_deletes(con, candidates)
            rebuild_fts(con)
            maintenance["rebuild_fts"] = "done"
            con.execute("PRAGMA optimize")
            maintenance["pragma_optimize"] = "done"
            con.commit()
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                maintenance["wal_checkpoint"] = "done"
            except sqlite3.DatabaseError as exc:
                maintenance["wal_checkpoint"] = f"skipped: {exc}"
            if options.vacuum:
                con.execute("VACUUM")
                maintenance["vacuum"] = "done"
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            maintenance["integrity_check"] = str(integrity)
        after_counts = table_counts(con)
    finally:
        con.close()

    after_size = options.database.stat().st_size
    return {
        "ok": True,
        "mode": "apply" if options.apply else "dry-run",
        "database": str(options.database),
        "backup_path": backup_path,
        "before_size_bytes": before_size,
        "after_size_bytes": after_size,
        "size_delta_bytes": after_size - before_size,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "candidate_counts": candidate_counts,
        "deleted_counts": deleted,
        "maintenance": maintenance,
        "options": {
            "lookup_event_retention_days": options.lookup_event_retention_days,
            "keep_lookup_events": options.keep_lookup_events,
            "source_file_retention_days": options.source_file_retention_days,
            "empty_record_retention_days": options.empty_record_retention_days,
            "drop_missing_artifacts": options.drop_missing_artifacts,
            "vacuum": options.vacuum,
            "backup": options.backup,
        },
    }


def table_counts(con: sqlite3.Connection) -> dict[str, int]:
    counts = {}
    for table in ("memory_records", "events", "source_files", "runs"):
        counts[table] = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return counts


def duplicate_memory_record_ids(con: sqlite3.Connection) -> list[int]:
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY content_sha256
                    ORDER BY updated_at DESC, id DESC
                ) AS row_rank,
                COUNT(*) OVER (PARTITION BY content_sha256) AS duplicate_count
            FROM memory_records
            WHERE TRIM(content_sha256) <> ''
        )
        SELECT id
        FROM ranked
        WHERE duplicate_count > 1 AND row_rank > 1
        ORDER BY id
        """
    ).fetchall()
    return [int(row["id"]) for row in rows]


def empty_memory_record_ids(con: sqlite3.Connection, retention_days: int) -> list[int]:
    cutoff = cutoff_iso(retention_days)
    rows = con.execute(
        """
        SELECT id
        FROM memory_records
        WHERE created_at < ?
          AND TRIM(COALESCE(ticket_title, '')) = ''
          AND TRIM(COALESCE(summary, '')) = ''
          AND TRIM(COALESCE(search_text, '')) = ''
        ORDER BY id
        """,
        (cutoff,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def missing_artifact_memory_record_ids(con: sqlite3.Connection) -> list[int]:
    rows = con.execute(
        """
        SELECT id, artifact_path
        FROM memory_records
        WHERE source LIKE 'context-sync:%'
          AND TRIM(COALESCE(artifact_path, '')) <> ''
        ORDER BY id
        """
    ).fetchall()
    missing = []
    for row in rows:
        if not Path(row["artifact_path"]).expanduser().exists():
            missing.append(int(row["id"]))
    return missing


def old_lookup_event_ids(con: sqlite3.Connection, retention_days: int, keep_recent: int) -> list[int]:
    cutoff = cutoff_iso(retention_days)
    placeholders = ",".join("?" for _ in LOOKUP_EVENT_TYPES)
    keep_rows = con.execute(
        f"""
        SELECT id
        FROM events
        WHERE event_type IN ({placeholders})
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (*LOOKUP_EVENT_TYPES, max(0, keep_recent)),
    ).fetchall()
    keep_ids = {int(row["id"]) for row in keep_rows}
    rows = con.execute(
        f"""
        SELECT id
        FROM events
        WHERE event_type IN ({placeholders})
          AND created_at < ?
        ORDER BY id
        """,
        (*LOOKUP_EVENT_TYPES, cutoff),
    ).fetchall()
    return [int(row["id"]) for row in rows if int(row["id"]) not in keep_ids]


def stale_source_file_ids(con: sqlite3.Connection, retention_days: int) -> list[int]:
    cutoff = cutoff_iso(retention_days)
    rows = con.execute(
        """
        SELECT sf.id
        FROM source_files sf
        LEFT JOIN memory_records mr ON mr.record_uid = sf.record_uid
        WHERE sf.last_seen_at < ?
          AND (
            sf.status <> 'ingested'
            OR (TRIM(COALESCE(sf.record_uid, '')) <> '' AND mr.id IS NULL)
          )
        ORDER BY sf.id
        """,
        (cutoff,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def apply_deletes(con: sqlite3.Connection, candidates: dict[str, list[int]]) -> dict[str, int]:
    deleted = {}
    with con:
        deleted["old_lookup_events"] = delete_ids(con, "events", candidates["old_lookup_events"])
        deleted["stale_source_files"] = delete_ids(con, "source_files", candidates["stale_source_files"])
        memory_ids = sorted(
            set(candidates["duplicate_memory_records"])
            | set(candidates["empty_memory_records"])
            | set(candidates["missing_artifact_memory_records"])
        )
        deleted["duplicate_memory_records"] = len(set(candidates["duplicate_memory_records"]) & set(memory_ids))
        deleted["empty_memory_records"] = len(set(candidates["empty_memory_records"]) & set(memory_ids))
        deleted["missing_artifact_memory_records"] = len(set(candidates["missing_artifact_memory_records"]) & set(memory_ids))
        delete_ids(con, "memory_records", memory_ids)
    return deleted


def delete_ids(con: sqlite3.Connection, table: str, ids: list[int]) -> int:
    if not ids:
        return 0
    total = 0
    for batch in chunked(ids, 500):
        placeholders = ",".join("?" for _ in batch)
        cur = con.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", batch)
        total += int(cur.rowcount if cur.rowcount is not None else 0)
    return total


def backup_database(database: Path) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = database.with_name(f"{database.name}.bak-optimize-{timestamp}")
    source = sqlite3.connect(str(database))
    try:
        dest = sqlite3.connect(str(backup_path))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return str(backup_path)


def cutoff_iso(retention_days: int) -> str:
    days = max(0, int(retention_days))
    return (datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=days)).isoformat()


def chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"Local AI Brain optimize {report['mode'].upper()}",
        f"database: {report['database']}",
    ]
    if report["backup_path"]:
        lines.append(f"backup: {report['backup_path']}")
    lines.extend(
        [
            f"size: {format_bytes(report['before_size_bytes'])} -> {format_bytes(report['after_size_bytes'])} ({report['size_delta_bytes']} bytes)",
            "candidates:",
        ]
    )
    for key, value in report["candidate_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.append("deleted:")
    for key, value in report["deleted_counts"].items():
        lines.append(f"- {key}: {value}")
    lines.append("tables:")
    for table, before in report["before_counts"].items():
        after = report["after_counts"].get(table, before)
        lines.append(f"- {table}: {before} -> {after}")
    lines.append("maintenance:")
    for key, value in report["maintenance"].items():
        lines.append(f"- {key}: {value}")
    if report["mode"] == "dry-run":
        lines.append("Run again with --apply to delete candidates and compact the database.")
    return "\n".join(lines)


def format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


if __name__ == "__main__":
    raise SystemExit(main())
