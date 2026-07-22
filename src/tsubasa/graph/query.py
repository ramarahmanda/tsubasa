"""Graph query: entity matching + N-hop subgraph serialization.

The CLI returns structured, citation-rich context; the judgment (turning
it into an answer) happens in the Captain's session. See DESIGN.md §5.4.
"""

from __future__ import annotations

from ..models import Entity, Event, Relation

STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "why", "what", "when",
             "who", "how", "does", "do", "did", "in", "on", "at", "of", "for",
             "to", "it", "this", "that", "we", "our", "and", "or", "with"}


def match_entities(entities: dict[str, Entity], text: str, limit: int = 5) -> list[Entity]:
    """Rank entities by name/alias/id overlap with the query text."""
    words = {w for w in _tokens(text) if w not in STOPWORDS}
    scored: list[tuple[float, Entity]] = []
    lowered = text.lower()
    for e in entities.values():
        score = 0.0
        for name in [e.id] + e.all_names():
            nl = name.lower()
            if nl in lowered:
                score = max(score, 3.0 + len(nl) / 100)
                continue
            overlap = len(set(_tokens(nl)) & words)
            if overlap:
                score = max(score, overlap * 1.0)
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda p: (-p[0], p[1].id))
    return [e for _, e in scored[:limit]]


def _tokens(text: str) -> list[str]:
    out, buf = [], []
    for ch in text.lower():
        if ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
            buf = []
    if buf:
        out.append("".join(buf))
    return out


def subgraph(relations: list[Relation], centers: set[str], hops: int = 2) -> list[Relation]:
    nodes = set(centers)
    frontier = set(centers)
    picked: list[Relation] = []
    picked_keys: set[tuple] = set()
    for _ in range(hops):
        nxt: set[str] = set()
        for r in relations:
            if (r.source in frontier or r.target in frontier) and r.key() not in picked_keys:
                picked.append(r)
                picked_keys.add(r.key())
                nxt.update((r.source, r.target))
        frontier = nxt - nodes
        nodes |= frontier
        if not frontier:
            break
    return picked


def serialize(
    entities: dict[str, Entity],
    relations: list[Relation],
    events: dict[str, Event],
    matched: list[Entity],
    hops: int = 2,
) -> str:
    """Human/LLM-readable context block with citations."""
    lines: list[str] = []
    centers = {e.id for e in matched}
    sub = subgraph(relations, centers, hops)
    node_ids = centers | {r.source for r in sub} | {r.target for r in sub}

    lines.append("## Matched entities")
    for e in matched:
        status = f" [SUPERSEDED by {e.superseded_by}]" if e.status == "superseded" else ""
        lines.append(f"- {e.id} ({e.type}){status}: {e.description or e.name}")
        if e.summary:
            lines.append(f"  summary: {e.summary}")
        for fact in e.key_facts[:5]:
            lines.append(f"  fact: {fact}")

    if sub:
        lines.append("")
        lines.append(f"## Relations ({hops}-hop)")
        for r in sorted(sub, key=lambda r: r.key()):
            cite = f"  [{r.provenance}]" if r.provenance else ""
            lines.append(f"({r.source}) --[{r.predicate}]--> ({r.target}){cite}")

    cited_events = _relevant_events(events, node_ids, matched)
    if cited_events:
        lines.append("")
        lines.append("## Source events")
        for ev in cited_events:
            flag = " [DISPUTED]" if ev.disputed else ""
            if ev.trust == "low":
                flag += " [trust=low — doc-derived, verify in code]"
            lines.append(f"- {ev.id} ({ev.type}, {ev.ts[:10]}, impact={ev.impact}){flag}: {ev.title}")
            if ev.summary:
                lines.append(f"  {ev.summary}")
            for ref in ev.refs:
                lines.append(f"  ref {ref.kind}: {ref.id}")
    if not sub and not cited_events and not matched:
        lines.append("(no knowledge found)")
    return "\n".join(lines)


def _relevant_events(events: dict[str, Event], node_ids: set[str], matched: list[Entity], cap: int = 12) -> list:
    ids: list[str] = []
    for e in matched:
        for ev_id in reversed(e.source_events):
            if ev_id not in ids:
                ids.append(ev_id)
    for ev_id in node_ids:
        if ev_id in events and ev_id not in ids:
            ids.append(ev_id)
    picked = [events[i] for i in ids if i in events]
    picked.sort(key=lambda ev: ev.ts, reverse=True)
    return picked[:cap]
