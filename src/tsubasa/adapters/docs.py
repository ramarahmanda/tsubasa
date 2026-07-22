"""Generic curated-docs adapter: principles, guidelines, runbooks, RFCs…

Any directory of markdown knowledge that isn't an ADR or a postmortem.
The `kind` option names what these documents are (default "doc") and
prefixes the entity ids: kind=principle -> principle-<slug>.

    tsubasa source add doc docs/principles --kind principle --impact high

High-impact kinds (principles) score into the hot tier, so they shape
every plan the captain makes.
"""

from __future__ import annotations

import hashlib

from ..models import Event, Ref, now_iso, slugify
from .base import Adapter
from .adr import _extract_meta, _first_paragraph


class DocAdapter(Adapter):
    name = "doc"

    def collect(self) -> list[Event]:
        base = (self.root / self.source.path).resolve()
        pattern = self.source.glob or "**/*.md"
        kind = slugify(str(self.source.options.get("kind", "doc"))) or "doc"
        impact = self.source.options.get("impact", "medium")
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
            title, meta = _extract_meta(text)
            if not title:
                seen[rel] = digest
                continue
            summary = _first_paragraph(text)
            doc_id = f"{kind}-{slugify(title)}"
            events.append(Event(
                id=f"evt-{kind}-{slugify(title)}-{digest[:8]}",
                type="note", ts=meta.get("date") or now_iso()[:10],
                title=f"{kind}: {title}",
                summary=summary,
                impact=impact if impact in ("high", "medium", "low") else "medium",
                source=self.name,
                refs=[Ref(kind="doc", id=rel)],
                derived_entities=[{
                    "id": doc_id, "type": "doc", "name": title,
                    "description": summary or title,
                }],
                derived_relations=[{"source": doc_id, "predicate": "documented_in", "target": rel}],
            ))
            seen[rel] = digest
        return events
