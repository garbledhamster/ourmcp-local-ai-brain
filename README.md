# Local AI Brain V0

Local AI Brain is a small local memory kit for people who use coding agents.
It gives agents a safe place to write down useful project context and find it later.

- Files are the source of truth.
- Python's built-in `sqlite3` is the index and retrieval layer.
- Capture is deterministic: raw -> scrubbed -> distilled -> classified -> indexed.
- Agents call the CLI; agents do not write SQL.
- No external `sqlite3` program is required.
- No PowerShell is required on macOS or Linux.
- Portable helper utilities that belong with the brain live in this folder, including Codex chat title distill.

## Install

Requirements:

- Python 3.10 or newer.
- Distill CLI available as `distill`.

Python is the only required runtime. If a user does not have Python, an MCP or coding agent can still detect that and explain the prerequisite, but it cannot run this tool locally until a Python runtime is available.

From the unpacked kit folder, run:

```bash
python install.py
```

The installer detects Windows, macOS, or Linux.
It copies this tool into `~/.agents/tools/local-ai-brain` if needed, creates the local data folder, initializes SQLite through Python, and runs `doctor`.

Preview the install without changing files:

```bash
python install.py --what-if
```

If Distill still needs to be installed, initialize the tool without running `doctor`:

```bash
python install.py --no-doctor
```

## Basic Use

These commands work on Windows, macOS, and Linux:

```bash
python -m local_ai_brain doctor
python -m local_ai_brain init
python -m local_ai_brain search --query "pair failure"
python -m local_ai_brain context-pack --query "mobile overflow"
```

Lower-level commands are available for agents and automation:

```bash
python -m local_ai_brain init
python -m local_ai_brain doctor
python -m local_ai_brain install --what-if --platform windows
python -m local_ai_brain install --what-if --platform macos
python -m local_ai_brain record-ticket --json-file payload.json
python -m local_ai_brain search --repo "C:\Repo" --query "pair failure"
python -m local_ai_brain context-pack --repo "C:\Repo" --surface "Settings" --query "mobile overflow"
python -m local_ai_brain rebuild-index
python -m local_ai_brain codex-title-distill --date 2026-06-05
```

Optional Windows wrapper:

```powershell
C:\Users\jrice\.agents\tools\local-ai-brain\local-ai-brain.ps1 doctor
```

Optional macOS/Linux wrapper:

```sh
~/.agents/tools/local-ai-brain/local-ai-brain.sh doctor
```

The Python commands are the primary cross-platform interface. The wrapper scripts are conveniences only.

## Deploy A Shareable Git Copy

Use `deploy-to-git.ps1` when you want to copy only the files that should be shared with other users into a separate Git working tree.

Default beta deploy:

```powershell
.\deploy-to-git.ps1
```

On Windows, you can also double-click:

```text
deploy-to-git.cmd
```

By default, this deploys the `local-ai-brain` folder into `C:\Github\tools\local-ai-brain` and cleans the target first while preserving a target `.git` folder if present. Set `LOCAL_AI_BRAIN_GIT_ROOT` to use a different default beta Git root.

Preview a deploy:

```powershell
.\deploy-to-git.ps1 -TargetRoot "C:\path\to\share-repo" -DryRun
```

Copy the clean shareable tree:

```powershell
.\deploy-to-git.ps1 -TargetRoot "C:\path\to\share-repo" -Clean
```

`-TargetRoot` creates or refreshes a `local-ai-brain` folder inside the target root. Use `-TargetPath` only when you need to specify the final `local-ai-brain` folder directly.

The deploy script excludes local runtime and cache folders such as `.opencode`, `.git`, `__pycache__`, `.pytest_cache`, and compiled Python files. If the target already has a `.git` folder, `-Clean` preserves it.

## Portable Bundle Layout

Keep the portable Local AI Brain kit together:

```text
local-ai-brain/
  install.py
  deploy-to-git.ps1
  local-ai-brain.ps1
  local-ai-brain.sh
  pyproject.toml
  README.md
  local_ai_brain/
  tests/
```

Runtime data is created outside the shipped tool folder:

```text
~/.agents/data/local-ai-brain/
```

That split is intentional. The Git/MCP package can be replaced or updated without overwriting a user's local memory database and artifacts.

## MCP Install Flow

Local AI Brain includes a dependency-free stdio MCP server:

```bash
python -m local_ai_brain.mcp_server
```

The server wraps the existing CLI instead of reimplementing behavior. It exposes:

```text
doctor
context-pack --query "..."
search --query "..."
record-artifact --json-file payload.json
record-ticket --json-file payload.json
record-event --json-file payload.json
rebuild-index
codex-title-distill --date YYYY-MM-DD
```

Installer-oriented MCP wrappers such as ourMCP/myMCP should:

- clone or download this folder from Git,
- check whether `python` or `python3` is available,
- run `python install.py --what-if`,
- run `python install.py --force` only after the what-if pass is acceptable,
- configure a host MCP client to run `python -m local_ai_brain.mcp_server` with `PYTHONPATH` pointed at the installed tool folder,
- tell Claude/Cowork/Code to call `~/.agents/tools/local-ai-brain/local-ai-brain.* context-pack` before non-trivial work,
- never write directly to SQLite.

If Python is missing, the MCP command should return a clear blocked result: `Python 3.10+ required`. It can ask the host agent to install Python or use a host-provided Python runtime, but Local AI Brain itself remains Python-first for portability.

## Codex Title Distill

Codex title organization is now part of Local AI Brain:

```bash
python -m local_ai_brain codex-title-distill --date 2026-06-05
python -m local_ai_brain codex-title-distill --date 2026-06-05 --apply --yes
```

It reads Codex's local `state_5.sqlite` plus rollout JSONL logs and proposes `YYYY-MM-DD Accomplishment` titles without LLM calls. Dry-run is the default, and apply mode creates backups and audit logs.

## Agent Rule

Before non-trivial repo, skill, debugging, UI, deployment, or multi-agent work, query Local AI Brain with `context-pack`.
Also query it for simple questions that may depend on prior project context.
After creating important artifacts, tickets, checks, failures, or decisions, record them through the Local AI Brain CLI.
Do not write SQL directly.
Store full content as files; use SQLite for indexing and retrieval.
Capture must pass scrub, Distill, and classify before it is indexed.
