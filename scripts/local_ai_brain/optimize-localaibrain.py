from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


MIN_PYTHON = (3, 10)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    require_python()

    script_root = Path(__file__).resolve().parent
    brain_db = find_nearest_brain_db(args.db, script_root)
    tool_root = find_tool_root(script_root)

    if args.print_paths:
        print(f"database: {brain_db}")
        print(f"tool_root: {tool_root}")
        return 0

    command = [
        sys.executable,
        "-m",
        "local_ai_brain.optimize",
        "--db",
        str(brain_db),
        "--lookup-event-retention-days",
        str(args.lookup_event_retention_days),
        "--keep-lookup-events",
        str(args.keep_lookup_events),
        "--source-file-retention-days",
        str(args.source_file_retention_days),
        "--empty-record-retention-days",
        str(args.empty_record_retention_days),
    ]
    if args.apply:
        command.append("--apply")
    if args.drop_missing_artifacts:
        command.append("--drop-missing-artifacts")
    if args.no_vacuum:
        command.append("--no-vacuum")
    if args.no_backup:
        command.append("--no-backup")
    if args.json:
        command.append("--json")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(tool_root) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(command, cwd=script_root, env=env, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="optimize-localaibrain.py",
        description="Cross-platform wrapper for Local AI Brain database optimization.",
    )
    parser.add_argument("--db", "--database-path", default="", help="Path to brain.db. Defaults to the nearest brain.db.")
    parser.add_argument("--apply", action="store_true", help="Delete candidates and compact the database.")
    parser.add_argument("--lookup-event-retention-days", type=int, default=30)
    parser.add_argument("--keep-lookup-events", type=int, default=200)
    parser.add_argument("--source-file-retention-days", type=int, default=30)
    parser.add_argument("--empty-record-retention-days", type=int, default=7)
    parser.add_argument("--drop-missing-artifacts", action="store_true")
    parser.add_argument("--no-vacuum", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable optimizer report.")
    parser.add_argument("--print-paths", action="store_true", help="Show resolved database and tool paths without optimizing.")
    return parser


def require_python() -> None:
    if sys.version_info < MIN_PYTHON:
        required = ".".join(str(part) for part in MIN_PYTHON)
        current = ".".join(str(part) for part in sys.version_info[:3])
        raise RuntimeError(f"Python {required}+ is required; found {current}")


def find_nearest_brain_db(explicit_path: str, script_root: Path) -> Path:
    if explicit_path.strip():
        resolved = Path(explicit_path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"brain database not found: {resolved}")
        return resolved

    seen: set[Path] = set()
    for start in (script_root, Path.cwd()):
        current = start.resolve()
        while current not in seen:
            seen.add(current)
            for candidate in brain_db_candidates(current):
                if candidate.is_file():
                    return candidate.resolve()
            parent = current.parent
            if parent == current:
                break
            current = parent

    local_home = os.environ.get("LOCAL_AI_BRAIN_HOME", "").strip()
    if local_home:
        candidate = Path(local_home).expanduser().resolve() / "brain.db"
        if candidate.is_file():
            return candidate

    raise FileNotFoundError("Could not find a nearby brain.db. Pass --db or run from a project folder.")


def brain_db_candidates(directory: Path) -> list[Path]:
    return [
        directory / "brain.db",
        directory / "brain" / "brain.db",
        directory / ".tools" / "local-ai-brain-data" / "brain.db",
        directory / ".agents" / "data" / "local-ai-brain" / "brain.db",
    ]


def find_tool_root(script_root: Path) -> Path:
    candidates = [
        os.environ.get("LOCAL_AI_BRAIN_TOOL_PATH", ""),
        script_root,
        script_root.parent / "scripts",
        script_root.parent,
        script_root.parent.parent / "tools" / "local-ai-brain",
        Path.home() / ".agents" / "tools" / "local-ai-brain",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        if (path / "local_ai_brain" / "optimize.py").is_file():
            return path
    raise FileNotFoundError(
        "Could not find local_ai_brain.optimize. Set LOCAL_AI_BRAIN_TOOL_PATH to the Local AI Brain tool folder."
    )


if __name__ == "__main__":
    raise SystemExit(main())
