from __future__ import annotations

import re
from pathlib import Path
from typing import Any


PATH_PATTERN = re.compile(
    r"(?:(?:[A-Za-z]:\\|/|\.\.?[/\\])[\w .\-()[\]{}@#+=,;:!$%^&~`'\\/]+|[\w.-]+[/\\][\w .\-()[\]{}@#+=,;:!$%^&~`'\\/]+)"
)


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;\n]", value) if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()] if str(value).strip() else []


def derive_related_files(text: str, provided: Any = None) -> list[str]:
    found = normalize_list(provided)
    for match in PATH_PATTERN.findall(text):
        candidate = match.strip().rstrip(".,)")
        if len(candidate) > 2 and candidate not in found:
            found.append(candidate)
    return found[:100]


def derive_tags(payload: dict[str, Any], text: str) -> list[str]:
    tags = normalize_list(payload.get("tags"))
    lower = text.lower()
    vocab = {
        "ticket": ["ticket", "acceptance checks", "owner pair"],
        "failure-note": ["failure", "risk", "blocked"],
        "verification": ["test", "check", "pass", "fail", "smoke"],
        "ui": ["frontend", "mobile", "layout", "responsive", "component"],
        "database": ["sqlite", "schema", "fts", "query"],
        "install": ["install", "what-if", "bootstrap", "health"],
    }
    for tag, needles in vocab.items():
        if tag not in tags and any(needle in lower for needle in needles):
            tags.append(tag)
    return tags[:50]


def classify_payload(payload: dict[str, Any], scrubbed_text: str, distilled_text: str) -> dict[str, Any]:
    combined = "\n".join([scrubbed_text, distilled_text])
    artifact_path = payload.get("artifact_path") or ""
    artifact_type = payload.get("artifact_type") or "artifact"
    if not payload.get("artifact_type") and artifact_path:
        suffix = Path(artifact_path).suffix.lower()
        artifact_type = {
            ".md": "markdown",
            ".json": "json",
            ".jsonl": "jsonl",
            ".txt": "text",
        }.get(suffix, "artifact")
    related_files = derive_related_files(combined, payload.get("related_files"))
    tags = derive_tags(payload, combined)
    return {
        "artifact_type": artifact_type,
        "repo_path": payload.get("repo_path") or "",
        "target_surface": payload.get("target_surface") or "",
        "ticket_title": payload.get("ticket_title") or payload.get("title") or "",
        "status": payload.get("status") or "active",
        "summary": payload.get("summary") or first_nonempty_line(distilled_text),
        "tags": tags,
        "related_files": related_files,
    }


def first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip(" -#")
        if stripped:
            return stripped[:500]
    return ""
