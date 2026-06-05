# Claude Start Here

You are helping a user install Local AI Brain from this unpacked folder.

Local AI Brain is a local, file-first memory kit for coding agents. Tool code installs to the user's `.agents/tools/local-ai-brain` folder. User memory data is stored separately in `.agents/data/local-ai-brain` and must not be overwritten during upgrades.

## Install Steps

1. Find Python 3.10 or newer.

Windows:

```powershell
python --version
```

macOS or Linux:

```sh
python3 --version || python --version
```

If Python 3.10+ is missing, stop and tell the user: `Python 3.10+ is required before Local AI Brain can run locally.`

2. Preview the install from this folder.

```sh
python install.py --what-if
```

Use `python3` instead of `python` if that is the working Python command.

3. Install or update the tool.

```sh
python install.py --force
```

If `doctor` fails only because Distill is not installed, run:

```sh
python install.py --force --no-doctor
```

Then report that Local AI Brain was installed but Distill still needs to be installed for full capture quality checks.

4. Verify the installed command.

Windows:

```powershell
~\.agents\tools\local-ai-brain\local-ai-brain.ps1 doctor
~\.agents\tools\local-ai-brain\local-ai-brain.ps1 context-pack --query "install check"
```

macOS or Linux:

```sh
chmod +x ~/.agents/tools/local-ai-brain/local-ai-brain.sh
~/.agents/tools/local-ai-brain/local-ai-brain.sh doctor
~/.agents/tools/local-ai-brain/local-ai-brain.sh context-pack --query "install check"
```

## Add Agent Habit

Add this rule to the user's global agent instructions, such as `~/.claude/CLAUDE.md`, if the user approves:

````markdown
## Local AI Brain

Before non-trivial repo, debugging, UI, deployment, database, API, or multi-agent work, query Local AI Brain:

```sh
~/.agents/tools/local-ai-brain/local-ai-brain.sh context-pack --query "short task summary"
```

On Windows, use:

```powershell
~\.agents\tools\local-ai-brain\local-ai-brain.ps1 context-pack --query "short task summary"
```

After creating important artifacts, tickets, checks, failures, or decisions, record them through the Local AI Brain CLI. Do not write SQL directly. Store full content as files; SQLite is only the index.
````

## Do Not Package Or Copy

Do not copy user memory data from another machine.

Do not overwrite:

```text
~/.agents/data/local-ai-brain/
```
