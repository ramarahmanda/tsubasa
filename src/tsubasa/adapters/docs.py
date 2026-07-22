"""Generic curated-docs adapter: principles, guidelines, runbooks, RFCs…

Any directory of markdown knowledge that isn't an ADR or a postmortem.
The `kind` option names what these documents are (default "doc") and
prefixes the entity ids: kind=principle -> principle-<slug>.

    tsubasa source add doc docs/principles --kind principle --impact high

High-impact kinds (principles) score into the hot tier, so they shape
every plan the captain makes.

`.toon` files are treated as structured metadata (e.g. db schema dumps)
rather than prose: one entity per `table`, with column detail folded into
`key_facts` so it's queryable by table name directly — no file re-read
needed at query time. See `_parse_structured` for the expected shape.
"""

from __future__ import annotations

import hashlib

from .. import toon
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
        impact = impact if impact in ("high", "medium", "low") else "medium"
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
            if path.suffix == ".toon":
                ev = self._structured_event(text, rel, kind, impact, digest)
            else:
                ev = self._prose_event(text, rel, kind, impact, digest)
            seen[rel] = digest
            if ev is not None:
                events.append(ev)
        return events

    def _prose_event(self, text: str, rel: str, kind: str, impact: str, digest: str) -> Event | None:
        title, meta = _extract_meta(text)
        if not title:
            return None
        summary = _first_paragraph(text)
        doc_id = f"{kind}-{slugify(title)}"
        return Event(
            id=f"evt-{kind}-{slugify(title)}-{digest[:8]}",
            type="note", ts=meta.get("date") or now_iso()[:10],
            title=f"{kind}: {title}",
            summary=summary,
            impact=impact,
            source=self.name,
            refs=[Ref(kind="doc", id=rel)],
            derived_entities=[{
                "id": doc_id, "type": "doc", "name": title,
                "description": summary or title,
            }],
            derived_relations=[{"source": doc_id, "predicate": "documented_in", "target": rel}],
        )

    def _structured_event(self, text: str, rel: str, kind: str, impact: str, digest: str) -> Event | None:
        tables = _parse_structured(text)
        if not tables:
            return None
        derived_entities = []
        derived_relations = []
        for t in tables:
            name = t["name"]
            doc_id = f"{kind}-{slugify(name)}"
            facts = [_column_fact(c) for c in t["columns"]]
            derived_entities.append({
                "id": doc_id, "type": "doc", "name": name,
                "description": t["description"] or f"Database table {name}",
                "profile": {"key_facts": facts},
            })
            derived_relations.append({"source": doc_id, "predicate": "documented_in", "target": rel})
        title = tables[0]["name"] if len(tables) == 1 else f"{len(tables)} tables"
        return Event(
            id=f"evt-{kind}-{slugify(title)}-{digest[:8]}",
            type="note", ts=now_iso()[:10],
            title=f"{kind}: {title}",
            summary=f"Schema metadata for {', '.join(t['name'] for t in tables)}",
            impact=impact,
            source=self.name,
            refs=[Ref(kind="doc", id=rel)],
            derived_entities=derived_entities,
            derived_relations=derived_relations,
        )


def _parse_structured(text: str) -> list[dict] | None:
    """A `.toon` doc describing one or more db tables:

        table: orders
        description: Orders placed by customers.
        columns[2]{name,type,cardinality}:
          email,string,0.1
          status,string,0.05

    or multiple tables under a `tables[n]:` list, same per-item shape.
    Returns [] / None (not a list of {"name", "description", "columns"}) if
    the file doesn't match — callers fall back to skipping it, same as a
    prose doc with no title.
    """
    try:
        doc = toon.decode(text)
    except toon.ToonError:
        return None
    if not isinstance(doc, dict):
        return None
    raw_tables = doc["tables"] if isinstance(doc.get("tables"), list) else [doc]
    tables = []
    for raw in raw_tables:
        if not isinstance(raw, dict):
            continue
        name = raw.get("table") or raw.get("name")
        if not name:
            continue
        columns = [c for c in raw.get("columns", []) if isinstance(c, dict) and c.get("name")]
        tables.append({"name": str(name), "description": str(raw.get("description", "")), "columns": columns})
    return tables or None


def _column_fact(col: dict) -> str:
    extra = ", ".join(f"{k}={v}" for k, v in col.items() if k != "name")
    return f"column {col['name']}: {extra}" if extra else f"column {col['name']}"
