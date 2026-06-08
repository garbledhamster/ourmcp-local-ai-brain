from __future__ import annotations

import json
from pathlib import Path


def rows_to_context_pack(rows, repo_path: str = "", target_surface: str = "", query: str = "") -> str:
    lines = [
        "# Local AI Brain Context Pack",
        "",
        f"- Repo: {repo_path}" if repo_path else "- Repo: any",
        f"- Target surface: {target_surface}" if target_surface else "- Target surface: any",
        f"- Query: {query}" if query else "- Query: latest",
        f"- Results: {len(rows)}",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        tags = safe_json(row["tags_json"])
        related = safe_json(row["related_files_json"])
        lines.extend(
            [
                f"## {index}. {row['ticket_title'] or row['artifact_type']}",
                "",
                f"- Status: {row['status']}",
                f"- Summary: {row['summary']}",
                f"- Artifact: {row['artifact_path']}",
                f"- Scrubbed: {row['scrubbed_path']}",
                f"- Distilled: {row['distilled_path']}",
                f"- Tags: {', '.join(tags)}",
                f"- Related files: {', '.join(related)}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def safe_json(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def write_context_pack(text: str, output_path: Path | None = None) -> Path | None:
    if not output_path:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return output_path
