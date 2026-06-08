from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .classify import classify_payload
from .db import json_dumps
from .distill_adapter import distill_text
from .paths import artifact_root, ensure_runtime_dirs
from .scrub import scrub_text


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_payload(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def build_ticket_markdown(payload: dict[str, Any]) -> str:
    title = payload.get("ticket_title") or payload.get("title") or "Untitled ticket"
    fields = {
        "Repo": payload.get("repo_path", ""),
        "Target surface": payload.get("target_surface", ""),
        "Status": payload.get("status", "open"),
        "Tags": ", ".join(payload.get("tags", []) if isinstance(payload.get("tags"), list) else [str(payload.get("tags", ""))]),
        "Related files": ", ".join(payload.get("related_files", []) if isinstance(payload.get("related_files"), list) else [str(payload.get("related_files", ""))]),
    }
    body = payload.get("body") or payload.get("task") or payload.get("summary") or ""
    header = "\n".join(f"- {key}: {value}" for key, value in fields.items() if value)
    return f"# {title}\n\n{header}\n\n## Summary\n\n{payload.get('summary', '')}\n\n## Body\n\n{body}\n"


def materialize_raw_artifact(payload: dict[str, Any], artifact_type: str = "artifact") -> tuple[Path, str]:
    ensure_runtime_dirs()
    given_path = payload.get("artifact_path")
    if given_path:
        path = Path(given_path).expanduser()
        text = path.read_text(encoding="utf-8")
        return path, text
    uid = payload.get("record_uid") or str(uuid.uuid4())
    suffix = ".md" if artifact_type in {"ticket", "markdown", "artifact"} else ".txt"
    raw_path = artifact_root() / "raw" / f"{uid}{suffix}"
    raw_text = build_ticket_markdown(payload) if artifact_type == "ticket" else str(payload.get("body") or payload.get("summary") or "")
    raw_path.write_text(raw_text, encoding="utf-8")
    payload["artifact_path"] = str(raw_path)
    return raw_path, raw_text


def capture_record(payload: dict[str, Any], artifact_type: str = "artifact") -> dict[str, Any]:
    ensure_runtime_dirs()
    record_uid = payload.get("record_uid") or str(uuid.uuid4())
    timestamp = now_iso()
    raw_path, raw_text = materialize_raw_artifact(payload, artifact_type=artifact_type)
    scrubbed = scrub_text(raw_text)
    content_hash = hashlib.sha256(scrubbed.text.encode("utf-8")).hexdigest()
    record_uid = payload.get("record_uid") or content_hash[:16]
    scrubbed_path = artifact_root() / "scrubbed" / f"{record_uid}.txt"
    scrubbed_path.write_text(scrubbed.text, encoding="utf-8")
    distilled = distill_text(scrubbed.text)
    if not distilled.ok:
        raise RuntimeError(f"distill failed: {distilled.error}")
    distilled_path = artifact_root() / "distilled" / f"{record_uid}.txt"
    distilled_path.write_text(distilled.text, encoding="utf-8")
    classified = classify_payload({**payload, "artifact_type": artifact_type}, scrubbed.text, distilled.text)
    tags = classified["tags"]
    related_files = classified["related_files"]
    search_text = "\n".join(
        [
            classified.get("ticket_title", ""),
            classified.get("summary", ""),
            " ".join(tags),
            " ".join(related_files),
            distilled.text,
        ]
    ).strip()
    return {
        "record_uid": record_uid,
        "run_uid": payload.get("run_uid", ""),
        "artifact_path": str(raw_path),
        "raw_path": str(raw_path),
        "scrubbed_path": str(scrubbed_path),
        "distilled_path": str(distilled_path),
        "artifact_type": classified["artifact_type"],
        "repo_path": classified["repo_path"],
        "target_surface": classified["target_surface"],
        "ticket_title": classified["ticket_title"],
        "status": classified["status"],
        "summary": classified["summary"],
        "tags_json": json_dumps(tags),
        "tags_text": " ".join(tags),
        "related_files_json": json_dumps(related_files),
        "related_files_text": " ".join(related_files),
        "search_text": search_text,
        "scrub_status": "scrubbed",
        "scrub_warnings_json": json_dumps(scrubbed.warnings),
        "distill_status": "distilled",
        "classifier_json": json_dumps(classified),
        "content_sha256": content_hash,
        "source": payload.get("source", "cli"),
        "created_at": payload.get("created_at", timestamp),
        "updated_at": timestamp,
    }
