from __future__ import annotations

import os
from pathlib import Path


def agent_home() -> Path:
    return Path(os.environ.get("AGENTS_HOME", Path.home() / ".agents")).expanduser()


def brain_home() -> Path:
    return Path(os.environ.get("LOCAL_AI_BRAIN_HOME", agent_home() / "data" / "local-ai-brain")).expanduser()


def db_path() -> Path:
    return brain_home() / "brain.db"


def artifact_root() -> Path:
    return brain_home() / "artifacts"


def tool_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_runtime_dirs() -> None:
    for path in (
        brain_home(),
        artifact_root(),
        artifact_root() / "raw",
        artifact_root() / "scrubbed",
        artifact_root() / "distilled",
        artifact_root() / "context-packs",
    ):
        path.mkdir(parents=True, exist_ok=True)
