"""The data structure layer: Event, Entity, Relation, Task.

Events are facts (immutable, append-only). Entities are things (derived,
upsertable). Relations are meaning (triples with provenance). Tasks are
stateful and ADR-linked. See DESIGN.md §3.2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

EVENT_TYPES = {"incident", "adr", "pr_merged", "deploy", "config_change", "note", "release", "decision", "task_update", "plan"}
ENTITY_TYPES = {"service", "module", "adr", "incident", "feature", "task", "env", "team", "secret-ref", "external", "person", "goal", "doc"}
ENTITY_STATUSES = {"active", "superseded", "achieved", "dropped"}
TASK_STATES = {"draft", "todo", "in_progress", "in_review", "done", "abandoned"}
IMPACT_LEVELS = {"high", "medium", "low"}
TRUST_LEVELS = {"high", "normal", "low"}

ADR_ID_RE = re.compile(r"\badr-[a-z0-9][a-z0-9-]*[a-z0-9]\b")
SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 48) -> str:
    slug = SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts: str) -> datetime:
    """Parse an ISO-ish timestamp (date-only allowed) to an aware datetime."""
    ts = ts.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%Y-%m"):
        try:
            dt = datetime.strptime(ts, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unparseable timestamp: {ts!r}")


def _clean(d: dict) -> dict:
    """Drop empty/None values so serialized files stay minimal."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


@dataclass
class Ref:
    kind: str  # pr | adr | file | url | commit | event | entity | doc
    id: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id}

    @classmethod
    def from_dict(cls, d: dict) -> "Ref":
        return cls(kind=str(d["kind"]), id=str(d["id"]))


@dataclass
class Event:
    id: str
    type: str
    ts: str
    title: str
    summary: str = ""
    impact: str = "low"
    domains: list[str] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)
    trust: str = "normal"
    refs: list[Ref] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)  # entity ids this event supersedes
    body: str = ""
    source: str = "manual"  # adapter name
    disputed: bool = False
    # derived graph objects carried by the event so `rebuild` can replay
    derived_entities: list[dict] = field(default_factory=list)
    derived_relations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _clean({
            "id": self.id, "type": self.type, "ts": self.ts, "title": self.title,
            "summary": self.summary,
            "criticality": _clean({"impact": self.impact, "domains": self.domains}),
            "actors": self.actors, "trust": self.trust,
            "refs": [r.to_dict() for r in self.refs],
            "supersedes": self.supersedes, "body": self.body, "source": self.source,
            "disputed": self.disputed or None,
            "derived": _clean({"entities": self.derived_entities, "relations": self.derived_relations}),
        })

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        crit = d.get("criticality", {}) or {}
        derived = d.get("derived", {}) or {}
        return cls(
            id=d["id"], type=d["type"], ts=str(d["ts"]), title=d.get("title", ""),
            summary=d.get("summary", ""),
            impact=crit.get("impact", "low"), domains=list(crit.get("domains", [])),
            actors=list(d.get("actors", [])), trust=d.get("trust", "normal"),
            refs=[Ref.from_dict(r) for r in d.get("refs", [])],
            supersedes=list(d.get("supersedes", [])), body=d.get("body", ""),
            source=d.get("source", "manual"), disputed=bool(d.get("disputed", False)),
            derived_entities=list(derived.get("entities", [])),
            derived_relations=list(derived.get("relations", [])),
        )


@dataclass
class Entity:
    id: str
    type: str
    name: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    status: str = "active"  # active | superseded
    superseded_by: str = ""
    summary: str = ""
    key_facts: list[str] = field(default_factory=list)
    source_events: list[str] = field(default_factory=list)
    last_touched: str = ""  # ts of most recent referencing event
    impact: str = "low"     # max impact across referencing events

    def to_dict(self) -> dict:
        return _clean({
            "id": self.id, "type": self.type, "name": self.name,
            "description": self.description, "aliases": self.aliases,
            "domains": self.domains, "status": self.status if self.status != "active" else None,
            "superseded_by": self.superseded_by,
            "profile": _clean({"summary": self.summary, "key_facts": self.key_facts}),
            "source_events": self.source_events, "last_touched": self.last_touched,
            "impact": self.impact if self.impact != "low" else None,
        })

    @classmethod
    def from_dict(cls, d: dict) -> "Entity":
        profile = d.get("profile", {}) or {}
        return cls(
            id=d["id"], type=d["type"], name=d.get("name", d["id"]),
            description=d.get("description", ""), aliases=list(d.get("aliases", [])),
            domains=list(d.get("domains", [])), status=d.get("status", "active"),
            superseded_by=d.get("superseded_by", ""),
            summary=profile.get("summary", ""), key_facts=list(profile.get("key_facts", [])),
            source_events=list(d.get("source_events", [])),
            last_touched=str(d.get("last_touched", "")), impact=d.get("impact", "low"),
        )

    def all_names(self) -> list[str]:
        return [self.name] + self.aliases


@dataclass
class Relation:
    source: str
    predicate: str
    target: str
    ts: str = ""
    provenance: str = ""  # event id

    def key(self) -> tuple:
        return (self.source, self.predicate, self.target)

    def to_dict(self) -> dict:
        return {"source": self.source, "predicate": self.predicate, "target": self.target,
                "ts": self.ts, "provenance": self.provenance}

    @classmethod
    def from_dict(cls, d: dict) -> "Relation":
        return cls(source=d["source"], predicate=d["predicate"], target=d["target"],
                   ts=str(d.get("ts", "")), provenance=d.get("provenance", ""))


@dataclass
class TaskHistoryEntry:
    ts: str
    state: str
    by: str  # user | captain | adapter:<name>
    evidence: str = ""

    def to_dict(self) -> dict:
        return _clean({"ts": self.ts, "state": self.state, "by": self.by, "evidence": self.evidence})

    @classmethod
    def from_dict(cls, d: dict) -> "TaskHistoryEntry":
        return cls(ts=str(d["ts"]), state=d["state"], by=d.get("by", ""), evidence=d.get("evidence", ""))


@dataclass
class Task:
    id: str
    title: str
    state: str = "todo"
    adr: str = ""
    branch: str = ""
    prs: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    history: list[TaskHistoryEntry] = field(default_factory=list)

    def transition(self, state: str, by: str, evidence: str = "", ts: str | None = None) -> bool:
        """Move to `state` with evidence; returns False if already there."""
        if state not in TASK_STATES:
            raise ValueError(f"unknown task state: {state}")
        if state == self.state:
            return False
        ts = ts or now_iso()
        self.state = state
        self.updated = ts
        self.history.append(TaskHistoryEntry(ts=ts, state=state, by=by, evidence=evidence))
        return True

    def to_dict(self) -> dict:
        return _clean({
            "id": self.id, "title": self.title, "state": self.state, "adr": self.adr,
            "branch": self.branch, "prs": self.prs, "domains": self.domains,
            "created": self.created, "updated": self.updated,
            "history": [h.to_dict() for h in self.history],
        })

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            id=d["id"], title=d.get("title", ""), state=d.get("state", "todo"),
            adr=d.get("adr", ""), branch=d.get("branch", ""), prs=[str(p) for p in d.get("prs", [])],
            domains=list(d.get("domains", [])), created=str(d.get("created", "")),
            updated=str(d.get("updated", "")),
            history=[TaskHistoryEntry.from_dict(h) for h in d.get("history", [])],
        )
