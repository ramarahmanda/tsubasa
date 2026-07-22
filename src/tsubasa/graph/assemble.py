"""Graph assembly: replay Events into Entities + Relations.

Events are the only source of truth. Each event may carry `derived`
entities/relations (produced by its adapter or by the Captain at capture
time); assembly upserts them, so `tsubasa rebuild` can always reconstruct
the graph from the event log alone.
"""

from __future__ import annotations

import re

from ..models import Entity, Event, Relation
from ..storage import Store
from .reconcile import reconcile_event

IMPACT_RANK = {"low": 0, "medium": 1, "high": 2}
_ID_PREFIX_RE = re.compile(
    r"^(svc-|feat-|ext-|env-|team-|secret-|goal-|adr-|task-|inc-|evt-|doc-|principle-|module-|person-|PR-)"
)


def _id_like(endpoint: str, entities: dict[str, "Entity"]) -> bool:
    """A relation endpoint is legitimate if it's a known entity, follows the
    id conventions (may be defined by a later event), or is a file/ref path."""
    return endpoint in entities or bool(_ID_PREFIX_RE.match(endpoint)) or "/" in endpoint


def upsert_entity(entities: dict[str, Entity], new: Entity, event: Event) -> Entity:
    cur = entities.get(new.id)
    if cur is None:
        cur = new
        entities[new.id] = cur
    else:
        cur.description = new.description or cur.description
        cur.summary = new.summary or cur.summary
        cur.aliases = sorted(set(cur.aliases) | set(new.aliases) | ({new.name} if new.name != cur.name else set()))
        cur.domains = sorted(set(cur.domains) | set(new.domains))
        for fact in new.key_facts:
            if fact not in cur.key_facts:
                cur.key_facts.append(fact)
        if new.status != "active":  # explicit resolution (superseded/achieved/dropped) propagates
            cur.status = new.status
            cur.superseded_by = new.superseded_by or cur.superseded_by
        elif event.type == "plan" and cur.status in ("achieved", "dropped"):
            cur.status = "active"  # a plan event may explicitly reopen a goal
    touch(cur, event)
    return cur


def touch(entity: Entity, event: Event) -> None:
    """An event referencing an entity re-heats it and records provenance."""
    if event.id not in entity.source_events:
        entity.source_events.append(event.id)
    if event.ts > entity.last_touched:
        entity.last_touched = event.ts
    if IMPACT_RANK.get(event.impact, 0) > IMPACT_RANK.get(entity.impact, 0):
        entity.impact = event.impact
    new_domains = set(event.domains) - set(entity.domains)
    if new_domains:
        entity.domains = sorted(set(entity.domains) | new_domains)


def apply_event(entities: dict[str, Entity], relations: list[Relation], event: Event,
                aliases: dict[str, str] | None = None) -> list[str]:
    """Apply one event to the graph; returns reconciliation notes.

    `aliases` (from `tsubasa resolve`) folds duplicate entity ids into their
    canonical id at assembly time, so the event log stays untouched while the
    derived graph is deduplicated."""
    aliases = aliases or {}

    def canon(eid: str) -> str:
        return aliases.get(eid, eid)

    for ed in event.derived_entities:
        e = Entity.from_dict(ed)
        if e.id in aliases:
            canonical_id = aliases[e.id]
            e.aliases = sorted(set(e.aliases) | {e.id, e.name})
            e.id = canonical_id
        upsert_entity(entities, e, event)
    rel_keys = {r.key() for r in relations}
    for rd in event.derived_relations:
        rel = Relation.from_dict(rd)
        rel.source, rel.target = canon(rel.source), canon(rel.target)
        if rel.source == rel.target:
            continue
        # LLM-derived relations sometimes carry free-text endpoints ("GELF",
        # "Quarkus OIDC") instead of ids. Keep the knowledge but not the junk
        # edge: it becomes a key fact on the known side.
        src_ok = _id_like(rel.source, entities)
        tgt_ok = _id_like(rel.target, entities)
        if not (src_ok and tgt_ok):
            holder = entities.get(rel.source if src_ok else rel.target)
            if holder is not None:
                fact = (f"{rel.predicate}: {rel.target}" if src_ok
                        else f"{rel.source} {rel.predicate} this")
                if fact not in holder.key_facts:
                    holder.key_facts.append(fact)
            continue
        rel.ts = rel.ts or event.ts
        rel.provenance = rel.provenance or event.id
        if rel.key() not in rel_keys:
            relations.append(rel)
            rel_keys.add(rel.key())
        for end in (rel.source, rel.target):
            if end in entities:
                touch(entities[end], event)
    for ref in event.refs:
        if ref.kind == "entity" and canon(ref.id) in entities:
            touch(entities[canon(ref.id)], event)
    return reconcile_event(entities, relations, event)


def apply_profiles(entities: dict[str, Entity], profiles: dict[str, dict]) -> None:
    """Overlay LLM-generated hub profiles (from `tsubasa profile`)."""
    for eid, prof in profiles.items():
        e = entities.get(eid)
        if e is None:
            continue
        e.summary = prof.get("summary", "") or e.summary
        for fact in prof.get("key_facts", []):
            if fact not in e.key_facts:
                e.key_facts.append(fact)


def replay(store: Store, as_of: str = "") -> tuple[dict[str, Entity], list[Relation], list[str]]:
    """Rebuild the whole graph from the event log.

    `as_of` (ISO date) replays only events up to that date — the graph as the
    Captain would have known it then. Temporal recall comes free from the
    append-only log."""
    aliases = store.load_aliases()
    entities: dict[str, Entity] = {}
    relations: list[Relation] = []
    notes: list[str] = []
    for event in store.load_events():
        if as_of and event.ts[:10] > as_of:
            continue
        notes.extend(apply_event(entities, relations, event, aliases))
    apply_profiles(entities, store.load_profiles())
    return entities, relations, notes
