from __future__ import annotations

import os
import json
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class DistillResult:
    ok: bool
    text: str
    error: str = ""


def distill_command() -> list[str] | None:
    configured_json = os.environ.get("LOCAL_AI_BRAIN_DISTILL_CMD_JSON")
    if configured_json:
        parsed = json.loads(configured_json)
        if not isinstance(parsed, list) or not all(isinstance(part, str) for part in parsed):
            raise ValueError("LOCAL_AI_BRAIN_DISTILL_CMD_JSON must be a JSON array of strings")
        return parsed
    configured = os.environ.get("LOCAL_AI_BRAIN_DISTILL_CMD")
    if configured:
        import shlex

        return shlex.split(configured, posix=os.name != "nt")
    executable = shutil.which("distill")
    if executable:
        return [executable]
    return None


def health() -> DistillResult:
    command = distill_command()
    if not command:
        return DistillResult(False, "", "distill executable not found")
    try:
        proc = subprocess.run(command + ["--help"], text=True, capture_output=True, timeout=20)
    except Exception as exc:  # pragma: no cover - platform dependent
        return DistillResult(False, "", str(exc))
    if proc.returncode != 0:
        return DistillResult(False, proc.stdout.strip(), proc.stderr.strip() or f"exit {proc.returncode}")
    return DistillResult(True, proc.stdout.strip())


def distill_text(text: str, prompt: str | None = None) -> DistillResult:
    command = distill_command()
    if not command:
        return DistillResult(False, "", "distill executable not found")
    prompt = prompt or (
        "Return compact Local AI Brain context. Include: Summary, Tags, Related files, "
        "Status if present, and Search text. Do not include secrets or raw PII."
    )
    try:
        proc = subprocess.run(command + [prompt], input=text, text=True, capture_output=True, timeout=90)
    except Exception as exc:
        return DistillResult(False, "", str(exc))
    output = proc.stdout.strip()
    if proc.returncode != 0:
        return DistillResult(False, output, proc.stderr.strip() or f"exit {proc.returncode}")
    if not output:
        return DistillResult(False, "", "distill returned empty output")
    return DistillResult(True, output)
