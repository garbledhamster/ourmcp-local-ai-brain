# Local AI Brain MCP

Local AI Brain now ships a stdio MCP server:

```bash
python -m local_ai_brain.mcp_server
```

The server calls the existing Python CLI instead of reimplementing behavior.

## Runtime Rule

Local AI Brain is Python-first.

Before install or use, the MCP server must check for `python3` or `python`.
If Python 3.10+ is missing, return a blocked result:

```json
{
  "ok": false,
  "blocked": true,
  "reason": "Python 3.10+ required"
}
```

The host agent can then install Python or use a host-provided Python runtime.

## Install Commands

From the cloned `local-ai-brain` folder:

```bash
python install.py --what-if
python install.py --force
python install.py --no-doctor
```

## Tool Commands

After install, the MCP server dispatches to:

```bash
python -m local_ai_brain doctor
python -m local_ai_brain context-pack --query "..."
python -m local_ai_brain search --query "..."
python -m local_ai_brain record-artifact --json-file payload.json
python -m local_ai_brain record-ticket --json-file payload.json
python -m local_ai_brain record-event --json-file payload.json
python -m local_ai_brain rebuild-index
python -m local_ai_brain codex-title-distill --date YYYY-MM-DD
```

MCP tool names use underscores:

```text
doctor
context_pack
search
record_artifact
record_ticket
record_event
rebuild_index
codex_title_distill
```

## Agent Setup Guidance

An MCP install flow should tell Claude, Codex, Cowork, or Code:

- call `context-pack` before non-trivial repo, debugging, UI, deployment, or multi-agent work,
- call `record-artifact`, `record-ticket`, or `record-event` after important decisions and artifacts,
- never write SQL directly,
- treat files as source of truth and SQLite as the searchable index.

## Data Layout

Installed tool code:

```text
~/.agents/tools/local-ai-brain/
```

User memory data:

```text
~/.agents/data/local-ai-brain/
```

The MCP server must not overwrite the data folder during upgrades.
