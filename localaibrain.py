from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MIN_PYTHON = (3, 10)
CANONICAL_INSTALLER_NAME = "localaibrain.py"
AGENTS_BLOCK_START = "<!-- LOCAL_AI_BRAIN_PROJECT_START -->"
AGENTS_BLOCK_END = "<!-- LOCAL_AI_BRAIN_PROJECT_END -->"
MCP_REGISTRY_NAME = "local-ai-brain-tools.json"
MCP_ADAPTER_NAME = "project_mcp.py"
WORK_FILE_PATTERN = "NNN_slug.ext"
WORK_FILE_EXAMPLE = "001_file-use-name.md"
WORK_FILE_EXCLUDED_DIRS = ("scripts", "artifacts", "archive", "brain", "mcp", "meetings", "tools")
LOCAL_AI_BRAIN_ACTIONS = [
    ("doctor", "Run Local AI Brain doctor for this project.", "read_only"),
    ("context-pack", "Return a compact project Local AI Brain context pack.", "read_only"),
    ("search", "Search this project's Local AI Brain index.", "read_only"),
    ("record-artifact", "Record a project artifact through Local AI Brain capture.", "write_safe"),
    ("record-ticket", "Record a project ticket through Local AI Brain capture.", "write_safe"),
    ("record-event", "Record a project event in Local AI Brain.", "write_safe"),
    ("proof-start", "Start a project Local AI Brain proof session.", "write_safe"),
    ("proof-finish", "Finish a project Local AI Brain proof session.", "write_safe"),
    ("proof-report", "Report project Local AI Brain proof metrics.", "read_only"),
    ("rebuild-index", "Rebuild this project's Local AI Brain search index.", "write_safe"),
    ("codex-title-distill", "Run deterministic Codex title distill through this project brain.", "write_safe"),
]


@dataclass(frozen=True)
class ProjectInstallPlan:
    platform_name: str
    python_executable: Path
    source_package_dir: Path
    source_installer: Path
    project_root: Path
    scripts_dir: Path
    package_dir: Path
    data_dir: Path
    db_path: Path
    artifacts_dir: Path
    plans_dir: Path
    agents_file: Path
    mcp_dir: Path
    mcp_registry_path: Path
    mcp_adapter_path: Path
    brain_scope: str
    mcp_tool_prefix: str


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if not effective_argv:
        if sys.stdin.isatty():
            return interactive_cli(parser)
        parser.print_help()
        return 0
    command = effective_argv[0].lower()
    if command in {"help", "-h", "--help"}:
        print_commander_help(parser)
        return 0
    if command in {"deploy", "setup", "install"}:
        args = parser.parse_args(effective_argv[1:])
        return run_from_args(args)
    if command in {"run", "cmd", "brain"}:
        return run_local_brain_from_args(effective_argv[1:])
    if command in {"terminal", "shell"}:
        return open_terminal_from_args(effective_argv[1:])
    if command in {"ui", "gui"}:
        return open_ui_from_args(effective_argv[1:])
    if command == "rollback":
        args = build_rollback_parser().parse_args(effective_argv[1:])
        return run_rollback_from_args(args)
    if "--menu" in effective_argv:
        return interactive_cli(parser)

    args = parser.parse_args(effective_argv)
    return run_from_args(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CANONICAL_INSTALLER_NAME,
        description=(
            "Commander for a project-local Local AI Brain. "
            "Use deploy/setup to seed the current folder, run to execute brain commands, "
            "or terminal/shell to open a configured command window."
        ),
    )
    parser.add_argument("--menu", action="store_true", help="Open the interactive terminal menu.")
    parser.add_argument("--project-root", default=".", help="Project root to initialize. Defaults to the current directory.")
    parser.add_argument(
        "--source-dir",
        default="",
        help="Optional source folder containing local_ai_brain, scripts/local_ai_brain, or tools/local_ai_brain.",
    )
    parser.add_argument("--what-if", action="store_true", help="Show what would happen without changing files.")
    parser.add_argument("--force", action="store_true", help="Replace scripts/local_ai_brain from --source-dir.")
    parser.add_argument("--doctor", action="store_true", help="Run doctor after init. This can require Distill.")
    parser.add_argument(
        "--brain-scope",
        choices=["main", "project"],
        default="",
        help="Install as the agents main brain or as a project brain. Interactive runs ask when omitted.",
    )
    parser.add_argument(
        "--platform",
        choices=["windows", "macos", "linux"],
        default="",
        help="Override platform detection for dry-run/testing.",
    )
    return parser


def interactive_cli(parser: argparse.ArgumentParser) -> int:
    print("")
    print("Project Local AI Brain commander")
    print("Choose a mode. Each mode prints the exact direct command before it runs.")
    print("")
    print("Navigation: b=back | h=help | q=quit")
    print("")
    choices = {
        "1": ("Deploy or refresh this folder", ("deploy", [])),
        "2": ("Preview deploy", ("deploy", ["--what-if"])),
        "3": ("Force refresh package", ("deploy", ["--force"])),
        "4": ("Deploy and run doctor", ("deploy", ["--doctor"])),
        "5": ("Run a Local AI Brain command", "run"),
        "6": ("Open configured terminal", ("terminal", [])),
        "7": ("Open Local AI Brain UI", ("ui", [])),
        "8": ("Rollback project-local files", ("rollback", [])),
        "9": ("Custom deploy builder", ("deploy", None)),
        "10": ("Show command help", "help"),
        "11": ("Quit", "quit"),
    }
    while True:
        for key, (label, _argv) in choices.items():
            print(f"{key}. {label}")
        raw_choice = read_input("Select mode [1] (b|h|q): ")
        if raw_choice is None:
            return 0
        choice = raw_choice.strip().lower() or "1"
        if choice in {"b", "back"}:
            continue
        if choice in {"h", "help"}:
            print("")
            print_commander_help(parser)
            print("")
            continue
        if choice in {"q", "quit", "11"}:
            return 0
        if choice not in choices:
            print("Unknown choice.")
            continue
        label, selected = choices[choice]
        if selected == "quit":
            return 0
        if selected == "help":
            print_commander_help(parser)
            print("")
            continue
        mode, selected_argv = selected
        argv = build_custom_argv() if selected_argv is None else list(selected_argv)
        print("")
        print(f"Mode: {label}")
        print("Direct command:")
        print(f"  python {CANONICAL_INSTALLER_NAME} {mode} {' '.join(argv)}".rstrip())
        print("")
        if not prompt_yes_no("Run this now?", default=True):
            print("")
            continue
        if mode == "terminal":
            open_terminal_from_args(argv)
            print("")
            continue
        if mode == "ui":
            open_ui_from_args(argv)
            print("")
            continue
        if mode == "run":
            interactive_run_command()
            print("")
            continue
        if mode == "rollback":
            run_rollback_from_args(build_rollback_parser().parse_args(argv))
            print("")
            continue
        run_from_args(parser.parse_args(argv))
        print("")
        continue


def interactive_run_command() -> int:
    print("")
    print("Examples:")
    print("  context-pack --query \"your topic\" --limit 5")
    print("  search --query \"your topic\" --limit 10")
    print("  doctor")
    while True:
        raw_command = read_input("Local AI Brain command (b/back/h/help/q/quit) [doctor]: ")
        if raw_command is None:
            return 0
        command = raw_command.strip().lower()
        if command in {"b", "back"}:
            return 0
        if command in {"h", "help"}:
            print("")
            print(
                "Enter a local AI Brain command string (for example, `context-pack --query \"your topic\" --limit 5` or `search --query \"topic\"`)."
            )
            print("")
            continue
        if command in {"q", "quit"}:
            return 0
        break
    raw_command = raw_command.strip() or "doctor"
    argv = split_command(raw_command)
    print("")
    print("Direct command:")
    print(f"  python {CANONICAL_INSTALLER_NAME} run {raw_command}".rstrip())
    print("")
    if not prompt_yes_no("Run this now?", default=True):
        return 0
    return run_local_brain_from_args(argv)


def print_commander_help(parser: argparse.ArgumentParser) -> None:
    print("Project Local AI Brain commander")
    print("")
    print("Commands:")
    print(f"  python {CANONICAL_INSTALLER_NAME} deploy [options]")
    print(f"  python {CANONICAL_INSTALLER_NAME} run <local_ai_brain command>")
    print(f"  python {CANONICAL_INSTALLER_NAME} terminal")
    print(f"  python {CANONICAL_INSTALLER_NAME} ui")
    print(f"  python {CANONICAL_INSTALLER_NAME} rollback [--what-if] [--yes]")
    print(f"  python {CANONICAL_INSTALLER_NAME} --menu")
    print("")
    print("Deploy options:")
    parser.print_help()


def build_custom_argv() -> list[str]:
    argv: list[str] = []
    project_root = prompt_text("Project root", ".")
    if project_root != ".":
        argv.extend(["--project-root", project_root])
    source_dir = prompt_text("Source dir containing local_ai_brain/scripts/local_ai_brain/tools/local_ai_brain; blank uses commander folder", "")
    if source_dir:
        argv.extend(["--source-dir", source_dir])
    brain_scope = prompt_choice("Brain scope", ["project", "main"], "project")
    argv.extend(["--brain-scope", brain_scope])
    platform_name = prompt_choice("Platform override", ["auto", "windows", "macos", "linux"], "auto")
    if platform_name != "auto":
        argv.extend(["--platform", platform_name])
    if prompt_yes_no("Preview only (--what-if)?", default=True):
        argv.append("--what-if")
    if prompt_yes_no("Replace scripts/local_ai_brain if it already exists (--force)?", default=False):
        argv.append("--force")
    if prompt_yes_no("Run doctor after init (--doctor)?", default=False):
        argv.append("--doctor")
    return argv


def prompt_text(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    raw_value = read_input(f"{label}{suffix}: ")
    if raw_value is None:
        return default
    value = raw_value.strip()
    return value or default


def prompt_choice(label: str, choices: list[str], default: str) -> str:
    values = "/".join(choices)
    while True:
        raw_value = read_input(f"{label} ({values}) [{default}]: ")
        if raw_value is None:
            return default
        value = raw_value.strip() or default
        if value in choices:
            return value
        print(f"Choose one of: {values}")


def prompt_yes_no(label: str, default: bool) -> bool:
    marker = "Y/n" if default else "y/N"
    while True:
        raw_value = read_input(f"{label} [{marker}]: ")
        if raw_value is None:
            return default
        value = raw_value.strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Answer y or n.")


def read_input(prompt: str) -> str | None:
    try:
        return input(prompt)
    except EOFError:
        print("")
        return None


def split_command(raw_command: str) -> list[str]:
    return shlex.split(raw_command, posix=os.name != "nt")


def build_project_action_parser(program: str) -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(
        prog=program,
        description="Run or launch against this folder's project-local Local AI Brain.",
    )
    command_parser.add_argument("--project-root", default=".", help="Project root. Defaults to the current directory.")
    command_parser.add_argument("--source-dir", default="", help="Source folder for first-time setup. Defaults to commander folder.")
    command_parser.add_argument("--brain-scope", choices=["main", "project"], default="", help="Main brain or project brain.")
    command_parser.add_argument("--force-setup", action="store_true", help="Refresh scripts/local_ai_brain before running.")
    command_parser.add_argument("--no-setup", action="store_true", help="Do not auto-deploy if this folder has no project brain yet.")
    return command_parser


def prepare_or_require_project_plan(plan: ProjectInstallPlan, no_setup: bool, force: bool, doctor: bool) -> None:
    if no_setup:
        require_existing_project_brain(plan)
    else:
        ensure_project_ready(plan, force=force, doctor=doctor)


def run_local_brain_from_args(argv: list[str]) -> int:
    command_parser = build_project_action_parser(f"{CANONICAL_INSTALLER_NAME} run")
    command_parser.add_argument("brain_args", nargs=argparse.REMAINDER)
    args = command_parser.parse_args(argv)
    brain_args = list(args.brain_args)
    if brain_args and brain_args[0] == "--":
        brain_args = brain_args[1:]
    if not brain_args:
        command_parser.print_help()
        return 0
    misplaced_options = {"--project-root", "--source-dir", "--brain-scope", "--force-setup", "--no-setup"}
    if any(option in brain_args for option in misplaced_options):
        print(
            "ERROR: commander options for 'run' must come before the Local AI Brain command. "
            f"Example: python {CANONICAL_INSTALLER_NAME} run --project-root <folder> search --query <topic>",
            file=sys.stderr,
        )
        return 2

    try:
        require_python()
        plan = build_plan(
            argparse.Namespace(
                project_root=args.project_root,
                source_dir=args.source_dir,
                brain_scope=args.brain_scope,
                platform="",
                what_if=False,
                force=args.force_setup,
                doctor=False,
            )
        )
        prepare_or_require_project_plan(plan, args.no_setup, force=args.force_setup, doctor=False)
        return run_brain_command(plan, brain_args, check=False)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def open_terminal_from_args(argv: list[str]) -> int:
    command_parser = build_project_action_parser(f"{CANONICAL_INSTALLER_NAME} terminal")
    args = command_parser.parse_args(argv)
    try:
        require_python()
        plan = build_plan(
            argparse.Namespace(
                project_root=args.project_root,
                source_dir=args.source_dir,
                brain_scope=args.brain_scope,
                platform="",
                what_if=False,
                force=args.force_setup,
                doctor=False,
            )
        )
        prepare_or_require_project_plan(plan, args.no_setup, force=args.force_setup, doctor=False)
        return open_configured_terminal(plan)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def open_ui_from_args(argv: list[str]) -> int:
    command_parser = build_project_action_parser(f"{CANONICAL_INSTALLER_NAME} ui")
    args = command_parser.parse_args(argv)
    try:
        require_python()
        plan = build_plan(
            argparse.Namespace(
                project_root=args.project_root,
                source_dir=args.source_dir,
                brain_scope=args.brain_scope,
                platform="",
                what_if=False,
                force=args.force_setup,
                doctor=False,
            )
        )
        prepare_or_require_project_plan(plan, args.no_setup, force=args.force_setup, doctor=False)
        return open_project_ui(plan)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def open_project_ui(plan: ProjectInstallPlan) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(plan.scripts_dir)
    env["LOCAL_AI_BRAIN_HOME"] = str(plan.data_dir)
    if plan.db_path.is_file():
        env["LOCAL_AI_BRAIN_DB"] = str(plan.db_path)
    return subprocess.run(
        [str(plan.python_executable), "-m", "local_ai_brain.ui"],
        cwd=plan.project_root,
        env=env,
        check=False,
    ).returncode


def require_existing_project_brain(plan: ProjectInstallPlan) -> None:
    if not (plan.package_dir / "__main__.py").is_file():
        raise RuntimeError(f"project brain is not deployed yet: {plan.package_dir}")
    if not plan.db_path.is_file():
        raise RuntimeError(f"project brain database is not initialized yet: {plan.db_path}")


def ensure_project_ready(plan: ProjectInstallPlan, force: bool, doctor: bool) -> None:
    package_missing = not (plan.package_dir / "__main__.py").is_file()
    db_missing = not plan.db_path.is_file()
    mcp_missing = not plan.mcp_registry_path.is_file() or not plan.mcp_adapter_path.is_file()
    agents_missing = not agents_block_exists(plan)
    if not (force or package_missing or db_missing or mcp_missing or agents_missing or doctor):
        return
    if force or package_missing:
        print_plan(plan, what_if=False, force=force, doctor=doctor)
        install_package(plan, force=force)
    plan.data_dir.mkdir(parents=True, exist_ok=True)
    plan.plans_dir.mkdir(parents=True, exist_ok=True)
    if db_missing or force or package_missing:
        run_brain_command(plan, ["init"])
    if doctor:
        run_brain_command(plan, ["doctor"])
    if mcp_missing or force or package_missing:
        write_mcp_registration(plan)
    if agents_missing or force or package_missing:
        write_agents_file(plan)


def agents_block_exists(plan: ProjectInstallPlan) -> bool:
    if not plan.agents_file.is_file():
        return False
    content = plan.agents_file.read_text(encoding="utf-8")
    return AGENTS_BLOCK_START in content and AGENTS_BLOCK_END in content


def open_configured_terminal(plan: ProjectInstallPlan) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(plan.scripts_dir)
    env["LOCAL_AI_BRAIN_HOME"] = str(plan.data_dir)
    if plan.platform_name == "windows":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            raise RuntimeError("PowerShell is required to open a configured terminal on Windows")
        command = "\n".join(
            [
                f"$env:PYTHONPATH = {powershell_quote(str(plan.scripts_dir))}",
                f"$env:LOCAL_AI_BRAIN_HOME = {powershell_quote(str(plan.data_dir))}",
                f"Set-Location -LiteralPath {powershell_quote(str(plan.project_root))}",
                "function localaibrain { python -m local_ai_brain @args }",
                "Write-Host 'Local AI Brain ready. Use: localaibrain doctor'",
            ]
        )
        subprocess.Popen([shell, "-NoExit", "-Command", command], cwd=plan.project_root, env=env)
        print(f"opened Local AI Brain terminal for {plan.project_root}")
        return 0

    shell = os.environ.get("SHELL") or "/bin/sh"
    print("Opening a configured subshell. Use: python -m local_ai_brain doctor")
    return subprocess.run([shell], cwd=plan.project_root, env=env, check=False).returncode


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_from_args(args: argparse.Namespace) -> int:
    try:
        require_python()
        plan = build_plan(args)
        return run_plan(plan, what_if=args.what_if, force=args.force, doctor=args.doctor)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def build_rollback_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{CANONICAL_INSTALLER_NAME} rollback",
        description="Remove project-local Local AI Brain generated files.",
    )
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to the current directory.")
    parser.add_argument("--source-dir", default="", help="Source folder for this installer. Defaults to the commander folder.")
    parser.add_argument("--what-if", action="store_true", help="Show what would happen without removing files.")
    parser.add_argument("--yes", action="store_true", help="Confirm destructive rollback without an interactive prompt.")
    return parser


def run_rollback_from_args(args: argparse.Namespace) -> int:
    try:
        require_python()
        plan = build_plan(
            argparse.Namespace(
                project_root=args.project_root,
                source_dir=args.source_dir,
                brain_scope="project",
                platform="",
                what_if=False,
                force=False,
                doctor=False,
            )
        )
        return run_rollback(plan, what_if=args.what_if, yes=args.yes)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def run_rollback(plan: ProjectInstallPlan, what_if: bool, yes: bool) -> int:
    if not plan.project_root.exists():
        raise RuntimeError(f"project root does not exist: {plan.project_root}")
    remove_target = [(plan.data_dir, "project brain"), (plan.mcp_dir, "MCP registration"), (plan.agents_file, "AGENTS block")]
    if not same_path(plan.source_package_dir, plan.package_dir):
        remove_target.append((plan.package_dir, "generated package"))
    if plan.mcp_adapter_path.is_file():
        remove_target.append((plan.mcp_adapter_path, "legacy MCP wrapper"))
    if not what_if and not confirm_rollback(plan, yes):
        print("Rollback cancelled.")
        return 1
    for target, label in remove_target:
        if target == plan.agents_file:
            continue
        if not is_safe_under_project(plan.project_root, target):
            raise RuntimeError(f"unsafe rollback target: {target}")
        if what_if:
            print(f"Would remove {label}: {target}")
            continue
        if target.is_file():
            print(f"Removing {label}: {target}")
            target.unlink()
        elif target.is_dir():
            print(f"Removing {label}: {target}")
            shutil.rmtree(target)
        else:
            print(f"Already absent: {target}")
    remove_agents_block(plan, what_if=what_if)
    return 0


def confirm_rollback(plan: ProjectInstallPlan, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise RuntimeError("rollback is destructive; re-run with --yes to confirm")
    print("")
    print(f"Rollback will remove project-local Local AI Brain state under: {plan.project_root}")
    return (read_input("Type rollback to continue: ") or "").strip().lower() == "rollback"


def remove_agents_block(plan: ProjectInstallPlan, what_if: bool) -> None:
    path = plan.agents_file
    if not path.is_file():
        return
    if what_if:
        print(f"Would remove AGENTS managed block: {path}")
        return
    content = path.read_text(encoding="utf-8")
    if AGENTS_BLOCK_START not in content or AGENTS_BLOCK_END not in content:
        print(f"No AGENTS managed block found: {path}")
        return
    before, rest = content.split(AGENTS_BLOCK_START, 1)
    _, after = rest.split(AGENTS_BLOCK_END, 1)
    updated = before.rstrip() + "\n\n" + after.lstrip()
    path.write_text(updated, encoding="utf-8")
    print(f"Removed AGENTS managed block: {path}")


def is_safe_under_project(project_root: Path, target: Path) -> bool:
    candidate = target.resolve()
    return candidate == project_root or project_root in candidate.parents


def require_python() -> None:
    if sys.version_info < MIN_PYTHON:
        required = ".".join(str(part) for part in MIN_PYTHON)
        current = platform.python_version()
        raise RuntimeError(f"Python {required}+ is required; found {current}")


def build_plan(args: argparse.Namespace) -> ProjectInstallPlan:
    project_root = Path(args.project_root).expanduser().resolve()
    source_root = Path(args.source_dir).expanduser().resolve() if args.source_dir else Path(__file__).resolve().parent
    brain_scope = resolve_brain_scope(getattr(args, "brain_scope", ""))
    source_package_dir = find_source_package(source_root)
    scripts_dir = (project_root / "scripts").resolve()
    package_dir = (scripts_dir / "local_ai_brain").resolve()
    data_dir = (project_root / "brain").resolve()
    mcp_dir = (project_root / "mcp").resolve()
    return ProjectInstallPlan(
        platform_name=args.platform or detect_platform(),
        python_executable=Path(sys.executable).resolve(),
        source_package_dir=source_package_dir,
        source_installer=(source_root / CANONICAL_INSTALLER_NAME).resolve(),
        project_root=project_root,
        scripts_dir=scripts_dir,
        package_dir=package_dir,
        data_dir=data_dir,
        db_path=data_dir / "brain.db",
        artifacts_dir=data_dir / "artifacts",
        plans_dir=project_root / "plans",
        agents_file=find_agents_file(project_root),
        mcp_dir=mcp_dir,
        mcp_registry_path=mcp_dir / MCP_REGISTRY_NAME,
        mcp_adapter_path=scripts_dir / MCP_ADAPTER_NAME,
        brain_scope=brain_scope,
        mcp_tool_prefix=mcp_tool_prefix(project_root, brain_scope),
    )


def resolve_brain_scope(value: str) -> str:
    if value:
        return value
    if sys.stdin.isatty():
        return prompt_choice("Install as", ["project", "main"], "project")
    return "project"


def find_source_package(source_root: Path) -> Path:
    candidates = [
        source_root / "local_ai_brain",
        source_root / "scripts" / "local_ai_brain",
        source_root / "tools" / "local_ai_brain",
        source_root / "tools" / "local-ai-brain" / "local_ai_brain",
    ]
    for candidate in candidates:
        if (candidate / "__main__.py").is_file():
            return candidate.resolve()
    raise RuntimeError(f"source is missing local_ai_brain package: {source_root}")


def find_agents_file(project_root: Path) -> Path:
    for name in ("agents.md", "AGENTS.md"):
        candidate = project_root / name
        if candidate.exists():
            return candidate.resolve()
    return (project_root / "agents.md").resolve()


def detect_platform() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def run_plan(plan: ProjectInstallPlan, what_if: bool, force: bool, doctor: bool) -> int:
    print_plan(plan, what_if=what_if, force=force, doctor=doctor)
    if what_if:
        return 0

    plan.project_root.mkdir(parents=True, exist_ok=True)
    install_package(plan, force=force)
    plan.data_dir.mkdir(parents=True, exist_ok=True)
    plan.plans_dir.mkdir(parents=True, exist_ok=True)
    run_brain_command(plan, ["init"])
    if doctor:
        run_brain_command(plan, ["doctor"])
    write_mcp_registration(plan)
    write_agents_file(plan)
    print_next_steps(plan)
    return 0


def print_plan(plan: ProjectInstallPlan, what_if: bool, force: bool, doctor: bool) -> None:
    mode = "WhatIf" if what_if else "Install"
    print(f"{mode}: {plan.brain_scope} Local AI Brain")
    print(f"platform: {plan.platform_name}")
    print(f"python: {plan.python_executable}")
    print(f"brain_scope: {plan.brain_scope}")
    print(f"project: {plan.project_root}")
    print(f"source_package: {plan.source_package_dir}")
    print(f"scripts: {plan.scripts_dir}")
    print(f"package: {plan.package_dir}")
    print(f"data: {plan.data_dir}")
    print(f"database: {plan.db_path}")
    print(f"plans: {plan.plans_dir}")
    print(f"work_file_naming: {WORK_FILE_PATTERN} (example: {WORK_FILE_EXAMPLE})")
    print(f"agents_file: {plan.agents_file}")
    print(f"mcp_registry: {plan.mcp_registry_path}")
    print(f"mcp_adapter: {plan.mcp_adapter_path}")
    print(f"mcp_name: {plan.mcp_tool_prefix}")
    print(f"force: {force}")
    print(f"doctor: {doctor}")
    operations = [
        "ensure scripts/local_ai_brain exists",
        "create project-local brain runtime folder",
        "run python -m local_ai_brain init with LOCAL_AI_BRAIN_HOME set to the project brain folder",
        f"ensure plans folder exists and document readable work filenames like {WORK_FILE_EXAMPLE}",
        "create/update local MCP adapter and registration manifest",
        "append or refresh one Local AI Brain block in agents.md",
    ]
    if not same_path(plan.source_package_dir, plan.package_dir):
        operations[0] = "copy local_ai_brain package into scripts/local_ai_brain"
    if doctor:
        operations.append("run python -m local_ai_brain doctor")
    for operation in operations:
        prefix = "WOULD" if what_if else "DO"
        print(f"{prefix} {operation}")


def install_package(plan: ProjectInstallPlan, force: bool) -> None:
    validate_source_package(plan.source_package_dir)
    if same_path(plan.source_package_dir, plan.package_dir):
        return
    if plan.package_dir.exists():
        if not force:
            raise RuntimeError(f"package already exists; re-run with --force to update: {plan.package_dir}")
        assert_safe_package_destination(plan)
        shutil.rmtree(plan.package_dir)
    plan.package_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plan.source_package_dir, plan.package_dir, ignore=copy_ignore)
    installer_destination = plan.project_root / CANONICAL_INSTALLER_NAME
    if plan.source_installer.is_file() and not same_path(plan.source_installer, installer_destination):
        shutil.copy2(plan.source_installer, installer_destination)


def validate_source_package(source_package_dir: Path) -> None:
    if not (source_package_dir / "__main__.py").is_file():
        raise RuntimeError(f"source package is missing __main__.py: {source_package_dir}")


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except FileNotFoundError:
        return left.resolve() == right.resolve()


def copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", ".pytest_cache", ".opencode", ".git"}
    ignored.update(name for name in names if name.endswith(".pyc") or name.endswith(".pyo"))
    return ignored


def assert_safe_package_destination(plan: ProjectInstallPlan) -> None:
    scripts_dir = plan.scripts_dir.resolve()
    package_dir = plan.package_dir.resolve()
    if package_dir == scripts_dir or scripts_dir not in package_dir.parents:
        raise RuntimeError(f"refusing to remove unsafe package destination: {package_dir}")


def run_brain_command(plan: ProjectInstallPlan, args: list[str], check: bool = True) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(plan.scripts_dir)
    env["LOCAL_AI_BRAIN_HOME"] = str(plan.data_dir)
    result = subprocess.run(
        [str(plan.python_executable), "-m", "local_ai_brain", *args],
        cwd=plan.project_root,
        env=env,
        check=check,
    )
    return result.returncode


def write_mcp_registration(plan: ProjectInstallPlan) -> None:
    plan.mcp_dir.mkdir(parents=True, exist_ok=True)
    plan.mcp_adapter_path.parent.mkdir(parents=True, exist_ok=True)
    plan.mcp_adapter_path.write_text(mcp_adapter_source(plan), encoding="utf-8")
    plan.mcp_registry_path.write_text(json.dumps(mcp_registry(plan), indent=2) + "\n", encoding="utf-8")


def mcp_registry(plan: ProjectInstallPlan) -> dict[str, object]:
    return {
        "schema": f"ourstuff.local-mcp.{plan.brain_scope}-localaibrain.v1",
        "brain_scope": plan.brain_scope,
        "project": plan.project_root.name,
        "project_root": str(plan.project_root),
        "plan_naming": {
            "directory": str(plan.plans_dir),
            "pattern": WORK_FILE_PATTERN,
            "example": WORK_FILE_EXAMPLE,
            "rule": "Use the next 3-digit number, an underscore, and a short lowercase hyphenated slug. Preserve the file extension.",
        },
        "work_file_naming": {
            "pattern": WORK_FILE_PATTERN,
            "example": WORK_FILE_EXAMPLE,
            "excluded_directories": list(WORK_FILE_EXCLUDED_DIRS),
        },
        "server": {
            "name": plan.mcp_tool_prefix,
            "transport": "stdio",
            "command": str(plan.python_executable),
            "args": [str(plan.mcp_adapter_path)],
            "cwd": str(plan.project_root),
            "env": {
                "PYTHONPATH": str(plan.scripts_dir),
                "LOCAL_AI_BRAIN_HOME": str(plan.data_dir),
            },
        },
        "tools": [
            {
                "name": mcp_tool_name(plan, action),
                "action": action,
                "description": description,
                "permission": permission,
                "project_root": str(plan.project_root),
                "pythonpath": str(plan.scripts_dir),
                "local_ai_brain_home": str(plan.data_dir),
                "database": str(plan.db_path),
            }
            for action, description, permission in LOCAL_AI_BRAIN_ACTIONS
        ],
    }


def mcp_tool_name(plan: ProjectInstallPlan, action: str) -> str:
    return f"{plan.mcp_tool_prefix}.{action}"


def mcp_tool_prefix(project_root: Path, brain_scope: str) -> str:
    if brain_scope == "main":
        return "localaibrain"
    return f"{project_tool_prefix(project_root)}.localaibrain"


def project_tool_prefix(project_root: Path) -> str:
    name = project_root.name.strip() or "project"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "project"


def write_agents_file(plan: ProjectInstallPlan) -> None:
    plan.agents_file.parent.mkdir(parents=True, exist_ok=True)
    existing = plan.agents_file.read_text(encoding="utf-8") if plan.agents_file.exists() else "# Agent Instructions\n"
    block = agents_block(plan)
    if AGENTS_BLOCK_START in existing and AGENTS_BLOCK_END in existing:
        before, rest = existing.split(AGENTS_BLOCK_START, 1)
        _old, after = rest.split(AGENTS_BLOCK_END, 1)
        updated = before.rstrip() + "\n\n" + block + "\n" + after.lstrip()
        print(f"agents.md Local AI Brain instructions refreshed: {plan.agents_file}")
    elif AGENTS_BLOCK_START in existing:
        print(f"agents.md already contains an incomplete Local AI Brain marker; leaving file unchanged: {plan.agents_file}")
        return
    else:
        updated = existing.rstrip() + "\n\n" + block + "\n"
    plan.agents_file.write_text(updated, encoding="utf-8")


def agents_block(plan: ProjectInstallPlan) -> str:
    project = command_path(plan.project_root)
    commander = command_path(plan.project_root / CANONICAL_INSTALLER_NAME)
    scripts = command_path(plan.scripts_dir)
    package = command_path(plan.package_dir)
    data = command_path(plan.data_dir)
    database = command_path(plan.db_path)
    artifacts = command_path(plan.artifacts_dir)
    plans = command_path(plan.plans_dir)
    registry = command_path(plan.mcp_registry_path)
    adapter = command_path(plan.mcp_adapter_path)
    tool_names = "\n".join(f"- `{mcp_tool_name(plan, action)}`" for action, _description, _permission in LOCAL_AI_BRAIN_ACTIONS)
    scope_label = "Main" if plan.brain_scope == "main" else "Project"
    scope_sentence = (
        "This is the agents main Local AI Brain. Register MCP as `localaibrain` without a project prefix."
        if plan.brain_scope == "main"
        else "This project has its own Local AI Brain. Register MCP with this project's prefixed tool namespace."
    )
    return f"""{AGENTS_BLOCK_START}
## {scope_label} Local AI Brain

{scope_sentence}
Use it before non-trivial repo, debugging, UI, deployment, planning, or multi-agent work that may depend on relevant history.

- Project root: `{project}`
- Brain scope: `{plan.brain_scope}`
- MCP name: `{plan.mcp_tool_prefix}`
- Commander: `{commander}`
- Python module path: `{scripts}`
- Local AI Brain package: `{package}`
- Runtime data path: `{data}`
- SQLite database path: `{database}`
- Artifact path: `{artifacts}`
- Plans path: `{plans}`
- Local MCP registration: `{registry}`
- Local MCP stdio adapter: `{adapter}`

Use Python only. Do not write SQLite directly.

Project commander:

```powershell
python "{commander}" deploy --brain-scope {plan.brain_scope}
python "{commander}" run --brain-scope {plan.brain_scope} context-pack --repo "{project}" --query "<topic>" --limit 5
python "{commander}" run --brain-scope {plan.brain_scope} search --repo "{project}" --query "<topic>" --limit 10
python "{commander}" run --brain-scope {plan.brain_scope} doctor
python "{commander}" terminal --brain-scope {plan.brain_scope}
```

Project work file naming:

- Keep project plans in `{plans}`.
- Name plan, phase, task, milestone, and similar work-tracking files as `{WORK_FILE_PATTERN}`, for example `{WORK_FILE_EXAMPLE}`.
- Use the next available 3-digit number, an underscore, and a short lowercase hyphenated slug that reads like the file topic.
- Preserve the file extension, such as `.md` or `.txt`.
- Do not apply this naming rule inside `{", ".join(WORK_FILE_EXCLUDED_DIRS)}`.

PowerShell:

```powershell
$env:PYTHONPATH = "{scripts}"
$env:LOCAL_AI_BRAIN_HOME = "{data}"
python -m local_ai_brain context-pack --repo "{project}" --query "<topic>" --limit 5
python -m local_ai_brain search --repo "{project}" --query "<topic>" --limit 10
python -m local_ai_brain proof-start --repo "{project}" --surface "<surface>" --summary "<scrubbed task>" --json
python -m local_ai_brain proof-finish --session "<proof-session-uid>" --json-file ".local\\proof-finish.json"
python -m local_ai_brain proof-report --repo "{project}" --json
python -m local_ai_brain optimize --apply
```

macOS/Linux:

```sh
PYTHONPATH="{scripts}" LOCAL_AI_BRAIN_HOME="{data}" python -m local_ai_brain context-pack --repo "{project}" --query "<topic>" --limit 5
PYTHONPATH="{scripts}" LOCAL_AI_BRAIN_HOME="{data}" python -m local_ai_brain search --repo "{project}" --query "<topic>" --limit 10
PYTHONPATH="{scripts}" LOCAL_AI_BRAIN_HOME="{data}" python -m local_ai_brain proof-start --repo "{project}" --surface "<surface>" --summary "<scrubbed task>" --json
PYTHONPATH="{scripts}" LOCAL_AI_BRAIN_HOME="{data}" python -m local_ai_brain proof-finish --session "<proof-session-uid>" --json-file ".local/proof-finish.json"
PYTHONPATH="{scripts}" LOCAL_AI_BRAIN_HOME="{data}" python -m local_ai_brain proof-report --repo "{project}" --json
PYTHONPATH="{scripts}" LOCAL_AI_BRAIN_HOME="{data}" python -m local_ai_brain optimize --apply
```

For token conservation, prefer `context-pack --limit 5` with a specific `--query` and `--surface` before broad scans. Use `search --limit 10` when you need more matches.
For proof that the brain helped, start a proof session before the first lookup, set `LOCAL_AI_BRAIN_PROOF_SESSION` to the returned id, classify useful/stale/irrelevant hits in `proof-finish`, and check `proof-report` before making speed or token-savings claims.
For deterministic maintenance, run `python -m local_ai_brain optimize --apply`; it finds the project `brain.db`, backs it up, removes transient cleanup candidates, rebuilds FTS, and compacts SQLite.

Local MCP tool names registered for this project:

{tool_names}
{AGENTS_BLOCK_END}"""


def command_path(path: Path) -> str:
    return str(path.resolve())


def print_next_steps(plan: ProjectInstallPlan) -> None:
    print("")
    print("Next:")
    print(f"  commander: {plan.project_root / CANONICAL_INSTALLER_NAME}")
    print(f"  agents.md: {plan.agents_file}")
    print(f"  database: {plan.db_path}")
    print(f"  local MCP registry: {plan.mcp_registry_path}")
    print(f"  local MCP adapter: {plan.mcp_adapter_path}")
    print("")
    print("Commander:")
    print(f'  python "{plan.project_root / CANONICAL_INSTALLER_NAME}" run context-pack --query "your topic"')
    print(f'  python "{plan.project_root / CANONICAL_INSTALLER_NAME}" terminal')
    print("")
    print("Direct Python:")
    if plan.platform_name == "windows":
        print(f'  $env:PYTHONPATH = "{plan.scripts_dir}"')
        print(f'  $env:LOCAL_AI_BRAIN_HOME = "{plan.data_dir}"')
        print(f'  python -m local_ai_brain context-pack --repo "{plan.project_root}" --query "your topic"')
    else:
        print(
            f'  PYTHONPATH="{plan.scripts_dir}" LOCAL_AI_BRAIN_HOME="{plan.data_dir}" '
            f'python -m local_ai_brain context-pack --repo "{plan.project_root}" --query "your topic"'
        )


def mcp_adapter_source(plan: ProjectInstallPlan) -> str:
    tool_prefix = json.dumps(plan.mcp_tool_prefix)
    scope_description = "Main Local AI Brain" if plan.brain_scope == "main" else "Project-local Local AI Brain"
    scope_description_literal = json.dumps(scope_description)
    template = r'''from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
os.environ["PYTHONPATH"] = str(SCRIPTS_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
os.environ["LOCAL_AI_BRAIN_HOME"] = str(PROJECT_ROOT / "brain")
os.environ["LOCAL_AI_BRAIN_MCP_TOOL_PREFIX"] = __TOOL_PREFIX__
os.environ["LOCAL_AI_BRAIN_MCP_SCOPE_DESCRIPTION"] = __SCOPE_DESCRIPTION__
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from local_ai_brain.project_mcp import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
'''
    return template.replace("__TOOL_PREFIX__", tool_prefix).replace("__SCOPE_DESCRIPTION__", scope_description_literal)


if __name__ == "__main__":
    raise SystemExit(main())
