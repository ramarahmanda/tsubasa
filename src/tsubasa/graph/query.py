"""Graph query: entity matching + N-hop subgraph serialization.

The CLI returns structured, citation-rich context; the judgment (turning
it into an answer) happens in the Captain's session. See DESIGN.md §5.4.

`serialize` answers "what is true"; `timeline` answers "what happened when".
The event log is temporal, so the second is the native question here, and a
snapshot cannot express it: "added" and "reverted" are two points on a line.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace

from ..models import Entity, Event, Relation
from . import assemble

STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "why", "what", "when",
             "who", "how", "does", "do", "did", "in", "on", "at", "of", "for",
             "to", "it", "this", "that", "we", "our", "and", "or", "with"}


MAX_PHRASE = 16  # longest name, in tokens, still eligible for a phrase match


def match_entities(entities: dict[str, Entity], text: str, limit: int = 5) -> list[Entity]:
    """Rank entities by name/alias/id overlap with the query text.

    Matching is word-boundary aware: names are compared token-for-token, never
    as raw substrings, so the alias "doc" cannot match inside "dockershim".
    Since `_tokens` splits on every non-alphanumeric, `-`, `_`, `.`, `/` and
    whitespace are all boundaries, which is what entity ids are built from.
    """
    qtokens = _tokens(text)
    words = {w for w in qtokens if w not in STOPWORDS}
    # every contiguous run of query tokens, so a whole name can be matched as a
    # phrase in one set lookup instead of a per-entity regex
    phrases = {
        tuple(qtokens[i:i + n])
        for n in range(1, min(len(qtokens), MAX_PHRASE) + 1)
        for i in range(len(qtokens) - n + 1)
    }
    # pass 1: tokenize every name once, keep the candidates, and count how many
    # entities each query token reaches
    candidates: list[tuple[Entity, float, list[tuple[set[str], int]]]] = []
    doc_freq: dict[str, int] = {}
    for e in entities.values():
        phrase = 0.0
        overlaps: list[tuple[set[str], int]] = []
        reached: set[str] = set()
        for name in [e.id] + e.all_names():
            nl = name.lower()
            ntokens = _tokens(nl)
            if tuple(ntokens) in phrases:
                phrase = max(phrase, 3.0 + len(nl) / 100)
                continue
            nset = {t for t in ntokens if t not in STOPWORDS}
            hit = nset & words
            if hit:
                overlaps.append((hit, len(nset)))
                reached |= hit
        if phrase or overlaps:
            candidates.append((e, phrase, overlaps))
            for t in reached:
                doc_freq[t] = doc_freq.get(t, 0) + 1

    # a token half the graph carries ("kep", "status") must not outweigh one that
    # picks out a single entity ("dockershim"), so weight each hit by its inverse
    # document frequency, normalised into (0, 1]
    total = max(len(entities), 1)
    scale = math.log(1 + total)
    weight = {t: math.log(1 + total / df) / scale for t, df in doc_freq.items()}

    scored: list[tuple[float, Entity]] = []
    for e, phrase, overlaps in candidates:
        score = phrase
        for hit, size in overlaps:
            # coverage of the name breaks ties only, hence the /100
            score = max(score, sum(weight[t] for t in hit) + len(hit) / size / 100)
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda p: (-p[0], p[1].id))
    return [e for _, e in scored[:limit]]


def vocabulary(entities: dict[str, Entity], events: dict[str, Event]) -> dict[str, int]:
    """Token -> frequency over every name the matchers score against.

    Built with the same `_tokens` as matching, so every listed token is one a
    query can actually hit: `walbufmappinglock` stays whole here because it
    stays whole in `match_entities`, and a camelCase split would advertise
    tokens that match nothing. Covers entity ids/names/aliases (the
    `match_entities` surface) and event titles (the `--timeline` title-match
    surface).
    """
    counts: dict[str, int] = {}
    for e in entities.values():
        for name in [e.id] + e.all_names():
            for t in _tokens(name):
                if len(t) >= 3 and t not in STOPWORDS:
                    counts[t] = counts.get(t, 0) + 1
    for ev in events.values():
        for t in _tokens(ev.title):
            if len(t) >= 3 and t not in STOPWORDS:
                counts[t] = counts.get(t, 0) + 1
    return counts


def vocab_hint(vocab: dict[str, int], text: str, token_cap: int = 20) -> str:
    """Bridge for an empty entity match: graph tokens near the query's wording.

    `match_entities` is lexical, so a question phrased in words the graph never
    uses returns nothing; without this line the caller reads that as "the graph
    is silent" about something it records. Takes the `vocabulary()` map, which
    the query path computes once. Query tokens are matched as stems into
    vocabulary tokens, the same relation `tsubasa vocab` uses, and rare tokens
    lead because a frequency-1 token picks out one record. Returns "" when
    nothing is near the wording.
    """
    qtokens = {t for t in _tokens(text) if len(t) >= 3 and t not in STOPWORDS}
    if not qtokens:
        return ""
    hits = {t: c for t, c in vocab.items() if any(q in t for q in qtokens)}
    if not hits:
        return ""
    shown = sorted(hits.items(), key=lambda p: (p[1], p[0]))[:token_cap]
    return ("no direct entity match; graph tokens near your wording: "
            + " ".join(f"{t}({c})" for t, c in shown))


def title_events(events: dict[str, Event], text: str, freq: dict[str, int],
                 exclude: set[str] = frozenset(), cap: int = 5) -> list[Event]:
    """Events whose titles carry the query's wording, rarest overlap first.

    Partial overlap by design, where the timeline's `_title_matches` is strict
    AND: a deterministic adapter attaches an event only to its repo entity, so
    entity walking cannot reach a revert however plainly its title answers the
    question, and requiring every query word would miss it too. Query tokens
    match as stems into title tokens, the same relation `vocab_hint` uses, so
    a whole-identifier token like `walbufmappinglock` is reachable from its
    parts. Two stem hits required whenever the query has two meaningful
    tokens: one common word alone would drag unrelated titles into every
    query. Ranking weights each hit by 1/frequency in `freq` (the
    `vocabulary()` counts): a wide query carries common stems, and under raw
    hit counts three hits on error/buffer/return outrank two on tokens that
    each pick out one record, flooding the answer out of the cap.
    """
    scored = [(s, ev) for s, _, ev in _scored_titles(events, text, freq)
              if ev.id not in exclude]
    scored.sort(key=lambda p: (p[0], p[1].ts, p[1].id), reverse=True)
    return [ev for _, ev in scored[:cap]]


def _scored_titles(events: dict[str, Event], text: str,
                   freq: dict[str, int]) -> list[tuple[float, bool, Event]]:
    """(rarity score, has discriminating hit, event) for every event passing
    the stem gate. The one title matcher; `title_events` (display),
    `titles_discriminate` (escalation trigger) and `_title_matches` (timeline
    seeds) all read from it rather than re-deciding what a match is.

    Ranking rarity uses `freq` (the full vocabulary). The discriminating test
    counts event TITLES only: a token five entities carry can still pick out
    the one title that records the topic (the G3 repo-attached-revert shape),
    while a stem spread across many titles discriminates nothing however rare
    it is elsewhere in the graph.
    """
    qtokens = {t for t in _tokens(text) if len(t) >= 3 and t not in STOPWORDS}
    if not qtokens:
        return []
    need = min(2, len(qtokens))
    title_freq: dict[str, int] = {}
    for ev in events.values():
        for t in set(_tokens(ev.title)):
            if len(t) >= 3 and t not in STOPWORDS:
                title_freq[t] = title_freq.get(t, 0) + 1
    out: list[tuple[float, bool, Event]] = []
    for ev in events.values():
        ttokens = _tokens(ev.title)
        weights: list[float] = []
        rare = False
        for q in qtokens:
            # per query token, the rarest title token it stems into
            hit = [t for t in ttokens if q in t]
            if hit:
                weights.append(max(1 / freq.get(t, 1) for t in hit))
                rare = rare or any(title_freq.get(t, 1) <= DISCRIMINATING_FREQ for t in hit)
        if len(weights) >= need:
            out.append((sum(weights), rare, ev))
    return out


# A title hit is evidence only if it discriminates: a token carried by at most
# this many event TITLES picks the answer out of the record, while a stem
# spread across many titles (error, buffer, return) is the flood class by
# definition — it fires on nearly any wording, so such a hit says nothing
# about whether the graph records the topic, and it must not stop the
# escalation ladder or seed a timeline the way a real hit does. Counted over
# titles, not the whole vocabulary: entities repeating a topic's name must not
# make the one title that records it look common (the G3 shape).
DISCRIMINATING_FREQ = 3


def titles_discriminate(events: dict[str, Event], text: str, freq: dict[str, int]) -> bool:
    """Does any title-matched event owe its match to a discriminating token?

    Same gate as `title_events` (two stem hits when the query has two
    meaningful tokens); strong means at least one hit title token appears in
    at most DISCRIMINATING_FREQ titles. False also covers the no-match-at-all
    case, so the caller has one weakness test.
    """
    return any(rare for _, rare, _ev in _scored_titles(events, text, freq))


def strong_title_events(events: dict[str, Event], text: str, freq: dict[str, int],
                        cap: int = 12) -> list[Event]:
    """The events behind a True `titles_discriminate`, rarest overlap first.

    Same gate, evidence instead of a boolean: only hits riding a
    discriminating token survive, so near-misses on common stems are dropped
    rather than printed. `--unanchored` shows these and nothing else.
    """
    scored = [(s, ev) for s, rare, ev in _scored_titles(events, text, freq) if rare]
    scored.sort(key=lambda p: (p[0], p[1].ts, p[1].id), reverse=True)
    return [ev for _, ev in scored[:cap]]


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


# A node every entity in a repo hangs off — `svc-<repo>`, `ext-kubernetes` — is a
# container, not a connection. Expanding the frontier THROUGH one reaches the
# whole corpus in a single step, so the second hop stops meaning "related" and
# starts meaning "in the same repository".
#
# Measured on the reference corpus: "backup retention policy" matched 5 entities
# and 5 relations at one hop, and 638 at two, of which 636 arrived via
# `svc-cloudnative-pg` (361 edges) or `svc-postgres` (295). The section was 91%
# of a 91KB query result, and the answers it dragged in were things like
# `adr-collation-not-setlocale`: real edges, no bearing on backups. The captain
# had started piping `tsubasa query | head -100` to escape it.
#
# Relative, never a constant: a fixed cap is wrong on both a 50-entity workspace
# and a 50,000-entity one. `HUB_DEGREE_FACTOR x median degree` finds the four
# container nodes on the reference corpus with no tuning.
HUB_DEGREE_FACTOR = 20
HUB_DEGREE_FLOOR = 25


def hub_nodes(relations: list[Relation]) -> set[str]:
    """Nodes too well connected to be worth traversing through."""
    degree: dict[str, int] = {}
    for r in relations:
        degree[r.source] = degree.get(r.source, 0) + 1
        degree[r.target] = degree.get(r.target, 0) + 1
    if not degree:
        return set()
    ordered = sorted(degree.values())
    median = ordered[len(ordered) // 2]
    cap = max(HUB_DEGREE_FACTOR * median, HUB_DEGREE_FLOOR)
    return {node for node, d in degree.items() if d > cap}


def subgraph(relations: list[Relation], centers: set[str], hops: int = 2,
             through_hubs: bool = False) -> list[Relation]:
    """Relations within `hops` of `centers`, not expanding through hub nodes.

    A hub still appears as the ENDPOINT of a relation that touches a matched
    entity: "this ADR changed svc-postgres" is the useful half. What it no
    longer does is put every other neighbour of svc-postgres into the result.
    `through_hubs=True` restores the old behaviour.
    """
    hubs: set[str] = set() if through_hubs else hub_nodes(relations)
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
        frontier = (nxt - nodes) - hubs
        nodes |= frontier
        if not frontier:
            break
    return picked


# Output budget: a query answer a reader pipes through `head -100` must keep
# its verdict inside the pipe. Signal first, everything else capped and marked.
MAX_RELATIONS = 12
MAX_SOURCE_EVENTS = 5
MAX_REFS = 3
_REF_ORDER = {"commit": 0, "pr": 1, "file": 2}


def serialize(
    entities: dict[str, Entity],
    relations: list[Relation],
    events: dict[str, Event],
    matched: list[Entity],
    hops: int = 2,
    text: str = "",
    vocab: dict[str, int] | None = None,
) -> str:
    """Human/LLM-readable context block with citations.

    `text` is the query itself; when given, events whose titles carry its
    wording print right after the matched entities, because entity walking
    cannot reach a repo-attached event (a revert, typically) that answers the
    question, and a verdict below line ~100 is one a `| head` never sees.
    Deduped against the source events shown below it. `vocab` is the
    `vocabulary()` map when the caller already computed it; title ranking
    needs its frequencies. Every section is hard-capped with an explicit
    elision marker: silent truncation is how a reader concludes "the graph
    does not have it" about something the graph has.
    """
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

    cited_events = _relevant_events(events, node_ids, matched)
    shown_events = cited_events[:MAX_SOURCE_EVENTS]
    titled = title_events(events, text, vocab if vocab is not None else vocabulary(entities, events),
                          exclude={ev.id for ev in shown_events}) if text else []
    if titled:
        lines.append("")
        lines.append("## Events matched by title (entity walking cannot reach "
                     "these; `--timeline` shows the sequence)")
        for ev in titled:
            if _reverts(ev) is not None:
                # verdict first, as the timeline states it: the reverted thing
                # is absent, and a reader who stops at the title must know that
                lines.append(f"- NOT PRESENT (reverted {ev.ts[:10]}): "
                             f"{ev.title}{_reason_note(ev)}  [{_cite(ev)}]")
            else:
                lines.append(f"- {ev.id} ({ev.type}, {ev.ts[:10]}): {ev.title}")

    if sub:
        lines.append("")
        lines.append(f"## Relations ({hops}-hop)")
        ordered = sorted(sub, key=lambda r: r.key())
        for r in ordered[:MAX_RELATIONS]:
            cite = f"  [{r.provenance}]" if r.provenance else ""
            lines.append(f"({r.source}) --[{r.predicate}]--> ({r.target}){cite}")
        if len(ordered) > MAX_RELATIONS:
            lines.append(f"  ... +{len(ordered) - MAX_RELATIONS} more relations")
        held = len(subgraph(relations, centers, hops, through_hubs=True)) - len(sub)
        if held > 0:
            hubs = ", ".join(sorted(hub_nodes(relations) & node_ids)) or "hub nodes"
            lines.append(f"  ... {held} further relations reachable only through "
                         f"{hubs} (hub) not expanded; they connect the whole repo, "
                         f"not this topic")

    if cited_events:
        lines.append("")
        lines.append("## Source events")
        for ev in shown_events:
            flag = " [DISPUTED]" if ev.disputed else ""
            if ev.trust == "low":
                flag += " [trust=low — doc-derived, verify in code]"
            lines.append(f"- {ev.id} ({ev.type}, {ev.ts[:10]}, impact={ev.impact}){flag}: {ev.title}")
            if ev.summary:
                lines.append(f"  {ev.summary}")
            # commits first: they are the citations an answer can carry
            refs = sorted(ev.refs, key=lambda r: _REF_ORDER.get(r.kind, 9))
            for ref in refs[:MAX_REFS]:
                lines.append(f"  ref {ref.kind}: {ref.id}")
            if len(refs) > MAX_REFS:
                lines.append(f"  ... +{len(refs) - MAX_REFS} more refs")
        if len(cited_events) > MAX_SOURCE_EVENTS:
            lines.append(f"  ... +{len(cited_events) - MAX_SOURCE_EVENTS} more source events")
    if not sub and not cited_events and not matched and not titled:
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


# ------------------------------------------------------------------ timeline

# Predicates that order things in time instead of describing structure. Walking
# them is what puts a revert on the row after what it undid, rather than leaving
# it an unrelated entry the reader has to connect.
TEMPORAL_PREDICATES = {"reverts", "supersedes", "superseded_by", "caused_by"}
# ref kinds that name a thing another event can also name; a `url` is not one
NODE_REF_KINDS = {"commit", "pr", "adr", "entity", "event", "file", "doc"}
# every action `_classify` mints for a change of state. Anything else is the bare
# event type, which means nothing changed: context, and capped as such.
TRANSITIONS = {"added", "status", "SUPERSEDED", "REVERTED", "supersedes", "caused_by"}
MAX_CONTEXT = 4
DETAIL_WIDTH = 62


def timeline(store, text: str, hops: int = 2, limit: int = 5, as_of: str = "") -> str:
    """Matched entities' events as an ascending sequence, verdict stated first.

    State reconstruction is `assemble`'s, not a second implementation of it:
    `replay` gives the resulting state, and `apply_event` stepped one event at a
    time gives the transitions between, which a single snapshot cannot show.
    Title seeding uses the plain path's discriminating standard
    (`_title_matches`): partial stem overlap qualifies only when a hit rides a
    rare token, so extra query words cannot unmatch the topic.
    """
    entities, relations, _ = assemble.replay(store, as_of=as_of)
    events = [ev for ev in store.load_events() if not (as_of and ev.ts[:10] > as_of)]
    matched = match_entities(entities, text, limit=limit)
    titled = _title_matches(events, text, vocabulary(entities, {ev.id: ev for ev in events}))
    seq = _temporal_events(events, relations, matched, titled, hops)
    if not seq:
        return "(no knowledge found)"

    tracked = {e.id for e in matched}
    created, changed = _transitions(seq, store.load_aliases(), tracked)
    lines = [f"# timeline: {text}" + (f" (as of {as_of})" if as_of else "")]
    lines += _verdict(entities, matched, seq, created, changed, {ev.id for ev in titled})
    lines.append("")
    if len(seq) == 1:
        lines.append("one recorded event for this topic, so there is no sequence:")
    ctx = {
        "tracked": tracked, "created": created, "changed": changed,
        "shown": {_cite(ev) for ev in seq},           # citations already on a row
        "undone": {u for ev in seq if (u := _reverts(ev))},
    }
    lines += _render([_row(ev, ctx) for ev in seq])
    return "\n".join(lines)


def _temporal_events(events: list[Event], relations: list[Relation], matched: list[Entity],
                     titled: list[Event], hops: int) -> list[Event]:
    """Events on the matched subgraph, grown along temporal edges.

    Rounds alternate event -> ref -> temporal edge -> event, which is how a
    revert reaches the commit it undid: the adding event names a sha, the
    `reverts` edge names the same sha, so the pair lands in one sequence.

    `titled` seeds the sequence alongside the entities, always, not only when no
    entity matched. A deterministic adapter can attach an event only to what it
    can name without judgement, i.e. the repo: `Revert "Add redo LSN to pgstats
    files"` derives svc-postgres, never feat-pgstats-*, so entity walking cannot
    reach it however well the title matches. Title matching is the primary path
    for deterministically extracted facts, not a fallback for a broken one.
    """
    nodes = {e.id for e in matched}
    for e in matched:
        nodes.update(e.source_events)
    picked: dict[str, Event] = {ev.id: ev for ev in titled}
    nodes |= set(picked)
    expanded: set[str] = set()
    for _ in range(max(hops, 1) + 1):
        grew = False
        for ev in events:
            # a seeded event still needs expanding: its own refs are how the
            # revert reaches the commit it undid
            if ev.id in expanded or (ev.id not in picked and not _touches(ev, nodes)):
                continue
            picked[ev.id] = ev
            expanded.add(ev.id)
            nodes.add(ev.id)
            nodes.update(r.id for r in ev.refs if r.kind in NODE_REF_KINDS)
            grew = True
        for r in relations:
            if r.predicate in TEMPORAL_PREDICATES and (r.source in nodes) != (r.target in nodes):
                nodes.add(r.target if r.source in nodes else r.source)
                grew = True
        if not grew:
            break
    return sorted(picked.values(), key=lambda ev: (ev.ts, ev.id))


def _touches(ev: Event, nodes: set[str]) -> bool:
    if ev.id in nodes or any(r.id in nodes for r in ev.refs if r.kind in NODE_REF_KINDS):
        return True
    # only temporal edges pull an event in: `changed_by` hangs every commit off
    # the repo entity, so honouring it would drag a whole history into a topic
    for pred, src, tgt in _edges(ev):
        if pred in TEMPORAL_PREDICATES and (src in nodes or tgt in nodes):
            return True
    return any(t in nodes for t in ev.supersedes)


def _title_matches(events: list[Event], text: str, freq: dict[str, int],
                   cap: int = 5) -> list[Event]:
    """Timeline seeds: titles passing the discriminating standard, rarest first.

    The same standard as the plain path (`_scored_titles`): at least two query
    stems hit (one for a single-token query) AND at least one hit rides a
    discriminating token. Strict AND over every query token was the old rule;
    any extra query word then failed the whole match, so only single-token
    queries ever seeded. The wrong-topic worry the AND guarded against is
    carried by the discriminating requirement instead: overlap on common
    stems alone seeds nothing.
    """
    scored = [(s, ev) for s, rare, ev in
              _scored_titles({ev.id: ev for ev in events}, text, freq) if rare]
    scored.sort(key=lambda p: (p[0], p[1].ts, p[1].id), reverse=True)
    return [ev for _, ev in scored[:cap]]


def _transitions(seq: list[Event], aliases: dict[str, str],
                 tracked: set[str]) -> tuple[dict[str, str], dict[str, tuple[str, str, str]]]:
    """`replay`'s machinery evaluated across event boundaries.

    Returns (entity id -> event that first derived it, event id -> status change).
    Events are copied because reconciliation writes `disputed` back onto them.
    """
    ents: dict[str, Entity] = {}
    rels: list[Relation] = []
    created: dict[str, str] = {}
    changed: dict[str, tuple[str, str, str]] = {}
    for ev in seq:
        before = {eid: e.status for eid, e in ents.items()}
        assemble.apply_event(ents, rels, replace(ev), aliases)
        for eid, e in ents.items():
            if eid not in before:
                created.setdefault(eid, ev.id)
            elif before[eid] != e.status and eid in tracked and ev.id not in changed:
                changed[ev.id] = (eid, before[eid], e.status)
    return created, changed


def _row(ev: Event, ctx: dict) -> tuple[str, str, str, str]:
    """(date, action, detail, citation) for one event.

    A REVERTED or SUPERSEDED row whose event records no rationale carries the
    marker after the citation, outside the clipped detail column, so the
    render width cannot truncate it away.
    """
    action, detail = _classify(ev, ctx)
    cite = _cite(ev)
    if action in ("REVERTED", "SUPERSEDED"):
        cite += _reason_note(ev)
    return (ev.ts[:10], action, detail, cite)


def _classify(ev: Event, ctx: dict) -> tuple[str, str]:
    tracked, created, changed = ctx["tracked"], ctx["created"], ctx["changed"]
    undone = _reverts(ev)
    if undone is not None:
        # the undone commit is named only when its own row is not right above
        return "REVERTED", _reason(ev, "" if undone in ctx["shown"] else undone)
    if any(r.kind == "commit" and r.id in ctx["undone"] for r in ev.refs):
        return "added", ev.title  # this is the thing a later row undid
    # being superseded outranks superseding something else: one ADR event records
    # both, and the row belongs to the topic that was asked about
    for pred, src, tgt in _edges(ev):
        if pred == "supersedes" and tgt in tracked and src != tgt:
            at_birth = " (at creation)" if created.get(tgt) == ev.id else ""
            return "SUPERSEDED", f"by {src}{at_birth}: {ev.title}"
        if pred == "superseded_by" and src in tracked and src != tgt:
            return "SUPERSEDED", f"by {tgt}: {ev.title}"
    for tgt in ev.supersedes:
        if tgt in tracked:
            return "SUPERSEDED", f"by {_primary(ev)}: {ev.title}"
    for pred, src, tgt in _edges(ev):
        if pred == "supersedes" and src in tracked:
            return "supersedes", f"{tgt}: {ev.title}"
        if pred == "caused_by" and (src in tracked or tgt in tracked):
            return "caused_by", f"{src} caused by {tgt}: {ev.title}"
    if ev.id in changed:
        eid, old, new = changed[ev.id]
        return "status", f"{eid}: {old} -> {new}"
    if any(created.get(eid) == ev.id for eid in tracked):
        return "added", ev.title
    eid, label = _status_note(ev)
    if eid:
        return "status", f"{eid} = {label}"
    return ev.type, ev.title


def _edges(ev: Event):
    for rd in ev.derived_relations:
        yield rd.get("predicate"), rd.get("source", ""), rd.get("target", "")


def _reverts(ev: Event) -> str | None:
    """The sha this event undid, "" if it reverted something it did not name."""
    for rd in ev.derived_relations:
        if rd.get("predicate") == "reverts":
            return str(rd.get("target", ""))
    return None


REVERT_BOILERPLATE = re.compile(r"^(?:Reverts|This reverts commit)\s+[0-9a-f]{7,40}[,.]?\s*",
                                re.IGNORECASE)

# Mechanical rule for "the record states why": body+summary, minus revert
# boilerplate, minus words built purely from the title's own tokens, must
# carry at least this many characters. Restating the title is not a reason,
# and a sha is not a reason; but a minimal clause ("breaks pg_upgrade control
# file") is one, so the floor sits below real one-line reasons.
NO_REASON_LEN = 20
NO_REASON_NOTE = " — reason: not recorded"


def _reason_recorded(ev: Event) -> bool:
    """Does the event record a rationale beyond its own title?"""
    text = f"{ev.summary}\n{ev.body}".strip()
    while (m := REVERT_BOILERPLATE.match(text)):
        text = text[m.end():]
    title_words = set(_tokens(ev.title))
    kept = [w for w in text.split() if set(_tokens(w)) - title_words]
    return len(" ".join(kept)) >= NO_REASON_LEN


def _reason_note(ev: Event) -> str:
    """The marker every verdict surface carries when the record has no why:
    stated as data, an unrecorded reason cannot be silently replaced by an
    invented one downstream."""
    return "" if _reason_recorded(ev) else NO_REASON_NOTE


def _reason(ev: Event, undone: str) -> str:
    """What was undone, then git's own stated reason, carried verbatim.

    The body opens with machine boilerplate twice over, once from the adapter's
    lead and once from git's own wording; strip both. What was undone leads
    because the reverted commit often has no event of its own to name it, and
    because the body's first clause frequently continues from the subject.
    """
    text = (ev.summary or ev.body).strip()
    while (m := REVERT_BOILERPLATE.match(text)):
        text = text[m.end():]
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    detail = ": ".join(p for p in (_reverted_what(ev), first) if p) or ev.title
    return detail + (f" (undoes {undone})" if undone else "")


def _reverted_what(ev: Event) -> str:
    """The subject of the reverted commit, as the revert itself states it."""
    m = re.search(r"\breverted\s+(.+)$", ev.title, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _status_note(ev: Event) -> tuple[str, str]:
    """(entity id, upstream status word) if this event is a status observation.

    The ADR adapter splits a mutable status off into its own dated event so the
    change is visible; a row labelled with the event type would hide it again.
    """
    for d in ev.derived_entities:
        facts = (d.get("profile") or {}).get("key_facts", [])
        fact = next((f for f in facts if f.startswith("status=")), "")
        if fact:
            return str(d.get("id", "")), fact[len("status="):].split(" as of ")[0]
        if d.get("status"):
            return str(d.get("id", "")), str(d["status"])
    return "", ""


def _primary(ev: Event) -> str:
    return str(ev.derived_entities[0].get("id", ev.id)) if ev.derived_entities else ev.id


def _cite(ev: Event) -> str:
    """Every row keeps a citation: a sha where there is one, else the event id."""
    for kind in ("commit", "pr"):
        for ref in ev.refs:
            if ref.kind == kind:
                return ref.id
    return ev.id


def _verdict(entities: dict[str, Entity], matched: list[Entity], seq: list[Event],
             created: dict[str, str], changed: dict[str, tuple[str, str, str]],
             titled: set[str]) -> list[str]:
    """The resulting state, first, so an answer that stops after one line is
    still correct. The sequence below it is the evidence, forward in time."""
    lines: list[str] = []
    attributed: set[str] = set()
    for e in matched:
        cur = entities.get(e.id, e)
        # the citation has to be an event about THIS entity: the last row of a
        # multi-entity sequence usually belongs to something else
        theirs = set(cur.source_events)
        own = [ev for ev in seq if ev.id in theirs] or [seq[-1]]
        moved = next((ev for ev in reversed(seq) if changed.get(ev.id, ("",))[0] == cur.id), None)
        rev = _revert_of(seq, created.get(cur.id, ""))
        if cur.status == "superseded":
            at = _superseder_event(seq, cur.id, cur.superseded_by) or moved or own[-1]
            lines.append(f"{cur.id}: SUPERSEDED by {cur.superseded_by or 'a later decision'} "
                         f"({at.ts[:10]}){_reason_note(at)}  [{_cite(at)}]")
        elif rev is not None:
            attributed.add(rev.id)
            lines.append(f"{cur.id}: NOT PRESENT (reverted {rev.ts[:10]})"
                         f"{_reason_note(rev)}  [{_cite(rev)}]")
        else:
            at = moved or own[-1]
            lines.append(f"{cur.id}: {cur.status.upper()} (last recorded {at.ts[:10]})  [{_cite(at)}]")
    # A revert names the sha it undid, never the feature that sha belonged to, so
    # no matched entity can own it. The topic was still undone, and that is the
    # answer: state it, but only for a revert whose own subject is the topic.
    rev = next((ev for ev in reversed(seq)
                if ev.id in titled and _reverts(ev) is not None and ev.id not in attributed), None)
    if rev is not None:
        what = _clip(_reverted_what(rev) or rev.title)
        lines.insert(0, f"NOT PRESENT (reverted {rev.ts[:10]}): {what}"
                        f"{_reason_note(rev)}  [{_cite(rev)}]")
    if not lines:
        lines.append(f"state not recorded as an entity; last event {seq[-1].ts[:10]}  [{_cite(seq[-1])}]")
    return lines


def _clip(text: str, width: int = DETAIL_WIDTH) -> str:
    return text if len(text) <= width else text[:width - 3] + "..."


def _revert_of(seq: list[Event], created_by: str) -> Event | None:
    """The event that reverted the commit which introduced this entity."""
    if not created_by:
        return None
    origin = next((ev for ev in seq if ev.id == created_by), None)
    shas = {r.id for r in origin.refs if r.kind == "commit"} if origin else set()
    # exactly one, so the event IS that commit. A chunk digest names dozens and
    # derives several entities; one reverted commit inside it does not make any of
    # them absent, and saying so would be the invention this whole surface exists
    # to prevent. Such a revert is reported against the topic, not the entity.
    if len(shas) != 1:
        return None
    for ev in reversed(seq):
        undone = _reverts(ev)
        if undone and any(undone.startswith(s) or s.startswith(undone) for s in shas):
            return ev
    return None


def _superseder_event(seq: list[Event], eid: str, by: str) -> Event | None:
    """The event that recorded the supersession the verdict names.

    Several later decisions may supersede the same thing; the entity keeps the
    first one, so citing the most recent event would attribute the verdict to a
    successor it does not name."""
    hits = [ev for ev in seq if _supersedes(ev, eid, "")]
    named = [ev for ev in hits if by and _supersedes(ev, eid, by)]
    return (named or hits or [None])[0]


def _supersedes(ev: Event, eid: str, by: str) -> bool:
    """Does this event record `eid` being superseded (by `by`, when given)?"""
    if eid in ev.supersedes and (not by or _primary(ev) == by):
        return True
    for pred, src, tgt in _edges(ev):
        if pred == "supersedes" and tgt == eid and (not by or src == by):
            return True
        if pred == "superseded_by" and src == eid and (not by or tgt == by):
            return True
    return False


def _render(rows: list[tuple[str, str, str, str]]) -> list[str]:
    """Ascending rows, capped by kind rather than by recency.

    A transition is the timeline, so it is never dropped however long the
    sequence runs. Everything else is context, and there is a lot of it: a
    `study` chunk digest names many entities at once, so a single focused topic
    accumulates dozens of events that only mention it. Capping by recency alone
    elided the one SUPERSEDED row a reader needed and kept thirty digests.
    """
    context = [i for i, (_, action, _, _) in enumerate(rows) if action not in TRANSITIONS]
    dropped = set(context[:max(len(context) - MAX_CONTEXT, 0)])  # oldest context goes first
    act_w = max((len(r[1]) for i, r in enumerate(rows) if i not in dropped), default=0)
    out: list[str] = []
    noted = False
    for i, (ts, action, detail, cite) in enumerate(rows):
        if i in dropped:
            if not noted:  # once, at the earliest omission, so the gap reads in place
                out.append(f"  ... {len(dropped)} earlier context events omitted "
                           "(no recorded state change) ...")
                noted = True
            continue
        detail = detail.replace("\n", " ")
        if len(detail) > DETAIL_WIDTH:
            detail = detail[:DETAIL_WIDTH - 3] + "..."
        out.append(f"  {ts}  {action:<{act_w}}  {detail:<{DETAIL_WIDTH}}  {cite}".rstrip())
    return out
