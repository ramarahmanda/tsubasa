"""Secret redaction at write time.

The graph stores knowledge ABOUT secrets (refs: name, where it lives),
never values. Every event passes through redact_event() before hitting
disk; `tsubasa doctor` remains the second line of defense.
"""

from __future__ import annotations

import re

PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]+)?"), "jwt"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-key"),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC )?PRIVATE KEY-----"), "private-key"),
    (re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\b(\s*[:=]\s*)(\"[^\"]{8,}\"|'[^']{8,}'|\S{8,})"), "credential"),
]


def redact_text(text: str) -> str:
    if not text:
        return text
    for rex, kind in PATTERNS:
        if kind == "credential":
            text = rex.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:{kind}]", text)
        else:
            text = rex.sub(f"[REDACTED:{kind}]", text)
    return text


def redact_event(event) -> None:
    """Scrub all free-text fields in place before persisting."""
    event.title = redact_text(event.title)
    event.summary = redact_text(event.summary)
    event.body = redact_text(event.body)
    for ed in event.derived_entities:
        for key in ("description", "name"):
            if key in ed:
                ed[key] = redact_text(ed[key])
        profile = ed.get("profile")
        if isinstance(profile, dict):
            if "summary" in profile:
                profile["summary"] = redact_text(profile["summary"])
            if "key_facts" in profile:
                profile["key_facts"] = [redact_text(f) for f in profile["key_facts"]]
