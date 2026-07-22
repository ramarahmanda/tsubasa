"""Reconciliation: self-correcting knowledge (DESIGN.md §5.5).

Runs on every write, from v0.1. The deterministic core handles:
  - supersession (new event explicitly replaces an entity)
  - trust arbitration (low-trust claims against high-trust knowledge
    are recorded as disputed instead of overwriting)
  - alias collisions (same name pointing at two entity ids)

Anything it cannot settle becomes an open question queued for the Captain
to raise with the user at the next natural moment (never as spam).
"""

from __future__ import annotations

from ..models import Entity, Event, Relation
from ..storage import Store
from .. import toon

TRUST_RANK = {"low": 0, "normal": 1, "high": 2}


def reconcile_event(entities: dict[str, Entity], relations: list[Relation], event: Event) -> list[str]:
    """Reconcile one event against the graph, mutating in place.

    Returns human-readable notes: "superseded:", "disputed:", "question:".
    """
    notes: list[str] = []
    primary = event.derived_entities[0]["id"] if event.derived_entities else event.id

    for target_id in event.supersedes:
        old = entities.get(target_id)
        if old is None:
            notes.append(f"question: {event.id} supersedes unknown entity '{target_id}' — typo or missing knowledge?")
            continue
        old_trust = _entity_trust(old, entities)
        if TRUST_RANK.get(event.trust, 1) < TRUST_RANK.get(old_trust, 1):
            event.disputed = True
            notes.append(
                f"disputed: {event.id} (trust={event.trust}) tried to supersede "
                f"'{target_id}' (trust={old_trust}) — kept old, recorded dispute"
            )
            continue
        if old.status != "superseded":
            old.status = "superseded"
            old.superseded_by = primary
            rel = Relation(source=primary, predicate="supersedes", target=target_id,
                           ts=event.ts, provenance=event.id)
            if rel.key() not in {r.key() for r in relations}:
                relations.append(rel)
            notes.append(f"superseded: '{target_id}' by '{primary}' ({event.id})")

    notes.extend(_alias_collisions(entities, event))
    return notes


def _entity_trust(entity: Entity, entities: dict[str, Entity]) -> str:
    # v0.1: entities inherit "normal" trust; a future pass will aggregate
    # trust from their source events.
    return "normal"


def _alias_collisions(entities: dict[str, Entity], event: Event) -> list[str]:
    notes = []
    involved = {d["id"] for d in event.derived_entities if "id" in d}
    name_owner: dict[str, str] = {}
    for e in entities.values():
        if e.status == "superseded":
            continue
        for n in e.all_names():
            key = n.lower()
            owner = name_owner.get(key)
            if owner and owner != e.id and (e.id in involved or owner in involved):
                notes.append(
                    f"question: name '{n}' is claimed by both '{owner}' and '{e.id}' — same thing? "
                    f"(surfaced by {event.id})"
                )
            else:
                name_owner.setdefault(key, e.id)
    return notes


# ------------------------------------------------------------ open questions

def load_questions(store: Store) -> list[dict]:
    path = store.base / "questions.toon"
    if not path.is_file():
        return []
    return toon.decode(path.read_text()).get("questions", [])


def queue_questions(store: Store, notes: list[str], ts: str) -> int:
    """Persist unresolved reconciliation notes for the Captain to raise."""
    open_notes = [n for n in notes if n.startswith(("question:", "disputed:"))]
    if not open_notes:
        return 0
    existing = load_questions(store)
    seen = {q["text"] for q in existing}
    for n in open_notes:
        if n not in seen:
            existing.append({"ts": ts, "text": n, "status": "open"})
            seen.add(n)
    (store.base / "questions.toon").write_text(toon.encode({"questions": existing}))
    return len(open_notes)
