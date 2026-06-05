from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MIN_PYTHON = (3, 10)
TOOL_NAME = "local-ai-brain"


@dataclass(frozen=True)
class InstallPlan:
    platform_name: str
    python_executable: Path
    source_dir: Path
    agents_home: Path
    destination_dir: Path
    data_dir: Path
    in_place: bool
    wrapper_command: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install Local AI Brain into this user's .agents folder.",
    )
    parser.add_argument("--what-if", action="store_true", help="Show what would happen without changing files.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing destination tool folder.")
    parser.add_argument("--no-doctor", action="store_true", help="Run init but skip the doctor check.")
    parser.add_argument("--agents-home", default="", help="Override the target .agents folder.")
    parser.add_argument(
        "--platform",
        choices=["windows", "macos", "linux"],
        default="",
        help="Override platform detection for dry-run/testing.",
    )
    args = parser.parse_args(argv)

    try:
        require_python()
        plan = build_plan(args)
        return run_plan(plan, what_if=args.what_if, force=args.force, no_doctor=args.no_doctor)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def require_python() -> None:
    if sys.version_info < MIN_PYTHON:
        required = ".".join(str(part) for part in MIN_PYTHON)
        current = platform.python_version()
        raise RuntimeError(f"Python {required}+ is required; found {current}")


def build_plan(args: argparse.Namespace) -> InstallPlan:
    source_dir = Path(__file__).resolve().parent
    agents_home = Path(args.agents_home or os.environ.get("AGENTS_HOME", Path.home() / ".agents")).expanduser().resolve()
    destination_dir = (agents_home / "tools" / TOOL_NAME).resolve()
    data_dir = (agents_home / "data" / TOOL_NAME).resolve()
    platform_name = args.platform or detect_platform()
    in_place = same_path(source_dir, destination_dir)
    return InstallPlan(
        platform_name=platform_name,
        python_executable=Path(sys.executable).resolve(),
        source_dir=source_dir,
        agents_home=agents_home,
        destination_dir=destination_dir,
        data_dir=data_dir,
        in_place=in_place,
        wrapper_command=wrapper_command(platform_name, destination_dir),
    )


def detect_platform() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def wrapper_command(platform_name: str, destination_dir: Path) -> str:
    if platform_name == "windows":
        return str(destination_dir / "local-ai-brain.ps1")
    return str(destination_dir / "local-ai-brain.sh")


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except FileNotFoundError:
        return left == right


def run_plan(plan: InstallPlan, what_if: bool, force: bool, no_doctor: bool) -> int:
    print_plan(plan, what_if=what_if, force=force, no_doctor=no_doctor)
    if what_if:
        return 0

    if not plan.in_place:
        copy_tool(plan, force=force)
    plan.data_dir.mkdir(parents=True, exist_ok=True)
    run_brain_command(plan, ["init"])
    if not no_doctor:
        run_brain_command(plan, ["doctor"])
    print_next_steps(plan)
    return 0


def print_plan(plan: InstallPlan, what_if: bool, force: bool, no_doctor: bool) -> None:
    mode = "WhatIf" if what_if else "Install"
    print(f"{mode}: Local AI Brain")
    print(f"platform: {plan.platform_name}")
    print(f"python: {plan.python_executable}")
    print(f"source: {plan.source_dir}")
    print(f"agents_home: {plan.agents_home}")
    print(f"destination: {plan.destination_dir}")
    print(f"data: {plan.data_dir}")
    print(f"in_place: {plan.in_place}")
    print(f"force: {force}")
    print(f"doctor: {not no_doctor}")
    operations = []
    operations.append("copy tool folder" if not plan.in_place else "use installed tool folder")
    operations.append("create runtime data folders")
    operations.append("run python -m local_ai_brain init")
    if not no_doctor:
        operations.append("run python -m local_ai_brain doctor")
    for operation in operations:
        prefix = "WOULD" if what_if else "DO"
        print(f"{prefix} {operation}")


def copy_tool(plan: InstallPlan, force: bool) -> None:
    if plan.destination_dir.exists():
        if not force:
            raise RuntimeError(f"destination already exists; re-run with --force to update: {plan.destination_dir}")
        assert_safe_destination(plan)
        shutil.rmtree(plan.destination_dir)
    plan.destination_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plan.source_dir, plan.destination_dir, ignore=copy_ignore)


def copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", ".pytest_cache", ".opencode", ".git"}
    ignored.update(name for name in names if name.endswith(".pyc") or name.endswith(".pyo"))
    return ignored


def assert_safe_destination(plan: InstallPlan) -> None:
    tools_root = (plan.agents_home / "tools").resolve()
    destination = plan.destination_dir.resolve()
    if destination == tools_root or tools_root not in destination.parents:
        raise RuntimeError(f"refusing to remove unsafe destination: {destination}")


def run_brain_command(plan: InstallPlan, args: list[str]) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(plan.destination_dir)
    env.setdefault("AGENTS_HOME", str(plan.agents_home))
    subprocess.run(
        [str(plan.python_executable), "-m", "local_ai_brain", *args],
        cwd=plan.destination_dir,
        env=env,
        check=True,
    )


def print_next_steps(plan: InstallPlan) -> None:
    print("")
    print("Next:")
    if plan.platform_name == "windows":
        print(f"  {plan.wrapper_command} doctor")
        print(f"  {plan.wrapper_command} context-pack --query \"your topic\"")
    else:
        print(f"  chmod +x {plan.wrapper_command}")
        print(f"  {plan.wrapper_command} doctor")
        print(f"  {plan.wrapper_command} context-pack --query \"your topic\"")
    print("")
    print("Direct Python always works:")
    print(f"  cd {plan.destination_dir}")
    print("  python -m local_ai_brain doctor")


if __name__ == "__main__":
    raise SystemExit(main())
