from __future__ import annotations

import re
from dataclasses import dataclass, field


SECRET_PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("api_key", re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[^'\"\s]{8,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)")),
]


@dataclass
class ScrubResult:
    text: str
    warnings: list[str] = field(default_factory=list)


def scrub_text(text: str) -> ScrubResult:
    warnings: list[str] = []
    scrubbed = text
    for label, pattern in SECRET_PATTERNS:
        scrubbed, count = pattern.subn(f"[REDACTED:{label}]", scrubbed)
        if count:
            warnings.append(f"{label}:{count}")
    return ScrubResult(text=scrubbed, warnings=warnings)
