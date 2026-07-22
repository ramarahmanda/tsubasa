"""ADR adapter: docs/adr/*.md → adr entities + events.

Understands MADR-ish markdown: title from the first heading, optional
`Status:`/`Date:`/`Superseded by:` lines or YAML frontmatter. The ADR id
is derived from the filename (adr-<slug> convention enforced).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from ..models import Event, Ref, slugify
from .base import Adapter

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
FIELD_RES = {
    "status": re.compile(r"^(?:[-*]\s*)?status\s*[:–]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "date": re.compile(r"^(?:[-*]\s*)?date\s*[:–]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "supersedes": re.compile(r"^(?:[-*]\s*)?supersedes\s*[:–]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
ADR_REF_RE = re.compile(r"\badr-[a-z0-9][a-z0-9-]*[a-z0-9]\b", re.IGNORECASE)


class AdrAdapter(Adapter):
    name = "adr"

    def collect(self) -> list[Event]:
        base = (self.root / self.source.path).resolve()
        pattern = self.source.glob or "**/*.md"
        seen: dict = self.state.setdefault("seen", {})
        events: list[Event] = []
        if not base.is_dir():
            return events
        for path in sorted(base.glob(pattern)):
            if not path.is_file() or path.name.lower() == "readme.md":
                continue
            rel = str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)
            digest = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
            if seen.get(rel) == digest:  # content cursor: moves/re-checkouts don't refire
                continue
            ev = self._parse(path, rel)
            if ev is not None:
                events.append(ev)
                seen[rel] = digest
        return events

    def _parse(self, path, rel: str) -> Event | None:
        text = path.read_text(errors="replace")
        title, meta = _extract_meta(text)
        if not title:
            return None
        adr_id = _adr_id(path.stem, title)
        date = meta.get("date") or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        status = (meta.get("status") or "accepted").lower()
        supersedes = [s.lower() for s in ADR_REF_RE.findall(meta.get("supersedes", ""))]

        summary = _first_paragraph(text)
        # event id is content-derived, not date-derived: moving/re-checking-out
        # the file (new mtime) must not mint a duplicate event; editing the
        # content legitimately does
        content_hash = hashlib.sha1(text.encode()).hexdigest()[:8]
        # Trust hierarchy: real ADRs are decisions (high trust); other docs
        # are claims about the code and rot silently — low trust, verify in
        # code before relying on them.
        is_real_adr = any(p in ("adr", "adrs", "decisions") for p in Path(rel).parts) \
            or _adr_id(path.stem, title).startswith("adr-") and re.match(r"^(adr|\d{3,5})-", path.stem)
        event = Event(
            id=f"evt-adr-{slugify(title)}-{content_hash}",
            type="adr",
            ts=date,
            title=f"ADR: {title}",
            summary=summary,
            impact="medium",
            trust="high" if is_real_adr else "low",
            source=self.name,
            refs=[Ref(kind="doc", id=rel), Ref(kind="adr", id=adr_id)],
            supersedes=supersedes,
            derived_entities=[{
                "id": adr_id, "type": "adr", "name": title,
                "description": summary or title,
                "status": "superseded" if status in ("superseded", "deprecated", "rejected") else "active",
            }],
            derived_relations=[
                {"source": adr_id, "predicate": "documented_in", "target": rel},
            ],
        )
        return event


def _extract_meta(text: str) -> tuple[str, dict]:
    meta: dict = {}
    fm = FRONTMATTER_RE.match(text)
    if fm:
        for line in fm.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip("\"'")
        text = text[fm.end():]
    for key, rex in FIELD_RES.items():
        if key not in meta:
            m = rex.search(text)
            if m:
                meta[key] = m.group(1).strip()
    if "date" in meta:
        m = DATE_RE.search(meta["date"])
        meta["date"] = m.group(0) if m else ""
    heading = HEADING_RE.search(text)
    title = meta.get("title") or (heading.group(1).strip() if heading else "")
    title = re.sub(r"^(?:ADR[-\s]?\d*\s*[:.\-]\s*)", "", title, flags=re.IGNORECASE)
    return title, meta


def _adr_id(stem: str, title: str) -> str:
    stem_slug = slugify(stem)
    if stem_slug.startswith("adr-"):
        return stem_slug
    m = re.match(r"^(\d{3,5})-(.+)$", stem_slug)
    if m:
        return f"adr-{m.group(2)}"
    return f"adr-{slugify(title)}"


def _first_paragraph(text: str) -> str:
    text = FRONTMATTER_RE.sub("", text)
    for block in text.split("\n\n"):
        block = block.strip()
        if block and not block.startswith("#") and len(block) > 40:
            return re.sub(r"\s+", " ", block)[:300]
    return ""
