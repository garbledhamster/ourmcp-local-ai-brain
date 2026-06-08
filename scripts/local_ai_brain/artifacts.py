from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m local_ai_brain.artifacts",
        description="Publish local AI Brain public artifacts into docs.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(default_project_root()),
        help="Repository root containing docs output. Defaults to the repository root for this package.",
    )
    parser.add_argument(
        "--project-root",
        default=str(default_project_root()),
        help="Project root containing artifacts/public. Defaults to the repository root for this package.",
    )
    parser.add_argument("--what-if", action="store_true", help="Show what would happen without copying files.")
    return parser.parse_args(argv)


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_project_root() -> Path:
    return default_repo_root()


def publish(repo_root: Path, project_root: Path, what_if: bool) -> int:
    source_root = project_root / "artifacts" / "public"
    destination_root = repo_root / "docs"
    if not source_root.is_dir():
        raise RuntimeError(f"Missing artifact source folder: {source_root}")
    files = [file for file in source_root.rglob("*") if file.is_file()]
    if not files:
        raise RuntimeError(f"No public artifacts found under: {source_root}")
    for file in files:
        relative = file.relative_to(source_root)
        target = destination_root / relative
        if what_if:
            print(f"Would publish {relative} -> {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        print(f"Published {relative} -> {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
    return publish(repo_root=repo_root, project_root=project_root, what_if=args.what_if)


if __name__ == "__main__":
    raise SystemExit(main())
