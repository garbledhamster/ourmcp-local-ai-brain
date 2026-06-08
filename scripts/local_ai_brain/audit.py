from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


def iter_codex_session_candidates(codex_home: Path) -> Iterable[dict]:
    index = codex_home / "session_index.jsonl"
    if index.exists():
        for line in index.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            path = parsed.get("path") or parsed.get("rollout_path") or parsed.get("session_path")
            if path:
                yield {
                    "artifact_path": path,
                    "artifact_type": "codex-session",
                    "source": "codex-session-index",
                    "summary": parsed.get("summary", ""),
                    "tags": ["codex", "session"],
                }
