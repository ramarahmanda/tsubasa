"""Incident adapter: postmortem markdown files → incident events.

Looks for severity/impact/date in frontmatter or inline fields; falls back
to file mtime. Incidents default to high impact — they're the knowledge
you least want to lose.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from ..models import Event, Ref, slugify
from .base import Adapter
from .adr import FRONTMATTER_RE, HEADING_RE, DATE_RE

SEV_RE = re.compile(r"^(?:[-*]\s*)?(?:severity|sev|impact)\s*[:–]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
DATE_FIELD_RE = re.compile(r"^(?:[-*]\s*)?date\s*[:–]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
HIGH_WORDS = {"sev1", "sev-1", "p1", "critical", "high", "outage"}
MED_WORDS = {"sev2", "sev-2", "p2", "medium", "degraded"}


class IncidentAdapter(Adapter):
    name = "incident"

    def collect(self) -> list[Event]:
        base = (self.root / self.source.path).resolve()
        pattern = self.source.glob or "**/*.md"
        seen: dict = self.state.setdefault("seen", {})
        events: list[Event] = []
        if not base.is_dir():
            return events
        for path in sorted(base.glob(pattern)):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)
            text = path.read_text(errors="replace")
            digest = hashlib.sha1(text.encode()).hexdigest()[:12]
            if seen.get(rel) == digest:
                continue
            heading = HEADING_RE.search(FRONTMATTER_RE.sub("", text))
            title = heading.group(1).strip() if heading else path.stem
            date = _find_date(text) or datetime.fromtimestamp(int(path.stat().st_mtime), tz=timezone.utc).strftime("%Y-%m-%d")
            impact = _find_impact(text)
            inc_id = f"inc-{date.replace('-', '')}-{slugify(title)}"
            events.append(Event(
                id=f"evt-incident-{slugify(title)}-{digest[:8]}",
                type="incident", ts=date, title=title,
                summary=_first_lines(text), impact=impact, source=self.name,
                refs=[Ref(kind="doc", id=rel)],
                derived_entities=[{
                    "id": inc_id, "type": "incident", "name": title,
                    "description": f"Incident on {date}: {title}",
                }],
                derived_relations=[{"source": inc_id, "predicate": "documented_in", "target": rel}],
            ))
            seen[rel] = digest
        return events


def _find_date(text: str) -> str:
    m = DATE_FIELD_RE.search(text)
    if m:
        d = DATE_RE.search(m.group(1))
        if d:
            return d.group(0)
    d = DATE_RE.search(text[:500])
    return d.group(0) if d else ""


def _find_impact(text: str) -> str:
    m = SEV_RE.search(text)
    hay = (m.group(1) if m else text[:300]).lower()
    if any(w in hay for w in HIGH_WORDS):
        return "high"
    if any(w in hay for w in MED_WORDS):
        return "medium"
    return "medium"  # an incident is never 'low' by default


def _first_lines(text: str) -> str:
    text = FRONTMATTER_RE.sub("", text)
    for block in text.split("\n\n"):
        block = block.strip()
        if block and not block.startswith("#"):
            return re.sub(r"\s+", " ", block)[:300]
    return ""
