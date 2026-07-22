"""Git-is-the-database storage for the knowledge graph.

Layout under <root>/.tsubasa/:
    captain.toml
    graph/entities.toon        one document: entities[N]: - ...
    graph/relations.toon       tabular: relations[N]{source,predicate,target,ts,provenance}:
    graph/events/YYYY/MM/<event-id>.toon
    tasks/<task-id>.toon
    memory/                    generated tiers (hot.md, index.md, domains/)
    state.toon                 adapter cursors (last ingest points)
"""

from __future__ import annotations

from pathlib import Path

from . import toon
from .config import TSUBASA_DIR
from .models import Entity, Event, Relation, Task, parse_ts
from .redact import redact_event


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.base = self.root / TSUBASA_DIR
        self.graph_dir = self.base / "graph"
        self.events_dir = self.graph_dir / "events"
        self.tasks_dir = self.base / "tasks"
        self.memory_dir = self.base / "memory"

    # ------------------------------------------------------------ entities

    def load_entities(self) -> dict[str, Entity]:
        path = self.graph_dir / "entities.toon"
        if not path.is_file():
            return {}
        doc = toon.decode(path.read_text())
        return {e["id"]: Entity.from_dict(e) for e in doc.get("entities", [])}

    def save_entities(self, entities: dict[str, Entity]) -> None:
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        ordered = sorted(entities.values(), key=lambda e: (e.type, e.id))
        doc = {"entities": [e.to_dict() for e in ordered]}
        (self.graph_dir / "entities.toon").write_text(toon.encode(doc))

    # ------------------------------------------------------------ relations

    def load_relations(self) -> list[Relation]:
        path = self.graph_dir / "relations.toon"
        if not path.is_file():
            return []
        doc = toon.decode(path.read_text())
        return [Relation.from_dict(r) for r in doc.get("relations", [])]

    def save_relations(self, relations: list[Relation]) -> None:
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        seen: dict[tuple, Relation] = {}
        for r in relations:
            seen.setdefault(r.key(), r)
        ordered = sorted(seen.values(), key=lambda r: r.key())
        doc = {"relations": [r.to_dict() for r in ordered]}
        (self.graph_dir / "relations.toon").write_text(toon.encode(doc))

    # ------------------------------------------------------------ events
    #
    # Events live in MONTHLY PACKS (events/2026-07.toon holding events[N]) —
    # one diffable text file per month instead of hundreds of tiny files.
    # Byte-level compression is git's job (zlib + delta); packing is about
    # file count, load speed, and clean append-only merges. Legacy per-event
    # files (events/YYYY/MM/evt-*.toon) are still read; `tsubasa pack`
    # migrates them.

    def pack_path(self, ts: str) -> Path:
        dt = parse_ts(ts)
        return self.events_dir / f"{dt.year:04d}-{dt.month:02d}.toon"

    def event_path(self, event: Event) -> Path:
        return self.pack_path(event.ts)

    def _load_pack(self, path: Path) -> list[dict]:
        if not path.is_file():
            return []
        return toon.decode(path.read_text()).get("events", [])

    def has_event(self, event_id: str) -> bool:
        return event_id in {e.id for e in self.load_events()}

    def append_event(self, event: Event) -> Path:
        redact_event(event)  # secret values never reach disk
        path = self.pack_path(event.ts)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = self._load_pack(path)
        records = [r for r in records if r.get("id") != event.id] + [event.to_dict()]
        records.sort(key=lambda r: (str(r.get("ts", "")), str(r.get("id", ""))))
        path.write_text(toon.encode({"events": records}))
        self._events_cache = None
        return path

    _events_cache: list[Event] | None = None

    def load_events(self) -> list[Event]:
        if self._events_cache is not None:
            return list(self._events_cache)
        if not self.events_dir.is_dir():
            return []
        events = []
        for path in sorted(self.events_dir.rglob("*.toon")):
            doc = toon.decode(path.read_text())
            if "events" in doc:  # monthly pack
                events.extend(Event.from_dict(e) for e in doc["events"])
            elif "event" in doc:  # legacy per-event file
                events.append(Event.from_dict(doc["event"]))
        events.sort(key=lambda e: e.ts)
        self._events_cache = events
        return list(events)

    def pack_legacy_events(self) -> int:
        """Migrate legacy per-event files into monthly packs. Returns count."""
        if not self.events_dir.is_dir():
            return 0
        moved = 0
        for path in sorted(self.events_dir.rglob("*.toon")):
            doc = toon.decode(path.read_text())
            if "event" not in doc:
                continue
            event = Event.from_dict(doc["event"])
            path.unlink()
            self.append_event(event)
            moved += 1
        for sub in sorted(self.events_dir.rglob("*"), reverse=True):
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()
        self._events_cache = None
        return moved

    # ------------------------------------------------------------ tasks

    def load_tasks(self) -> dict[str, Task]:
        if not self.tasks_dir.is_dir():
            return {}
        tasks = {}
        for path in sorted(self.tasks_dir.glob("task-*.toon")):
            doc = toon.decode(path.read_text())
            t = Task.from_dict(doc["task"])
            tasks[t.id] = t
        return tasks

    def save_task(self, task: Task) -> Path:
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        path = self.tasks_dir / f"{task.id}.toon"
        path.write_text(toon.encode({"task": task.to_dict()}))
        return path

    # ------------------------------------------------------------ code snapshot

    def save_code_graph(self, entities: list[dict], relations: list[dict], provenance: list[str]) -> None:
        """The code-derived layer: replaced wholesale on every ingest, never
        appended — it is only ever as true as the commit it was read from."""
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        doc = {"snapshot": {"provenance": provenance, "entities": entities, "relations": relations}}
        (self.graph_dir / "code.toon").write_text(toon.encode(doc))

    def load_code_graph(self) -> tuple[dict[str, Entity], list[Relation]]:
        path = self.graph_dir / "code.toon"
        if not path.is_file():
            return {}, []
        snap = toon.decode(path.read_text()).get("snapshot", {})
        entities = {e["id"]: Entity.from_dict(e) for e in snap.get("entities", [])}
        return entities, [Relation.from_dict(r) for r in snap.get("relations", [])]

    # ------------------------------------------------------------ aliases & profiles

    def load_aliases(self) -> dict[str, str]:
        """alias entity id -> canonical entity id (from `tsubasa resolve`)."""
        path = self.graph_dir / "aliases.toon"
        if not path.is_file():
            return {}
        pairs = toon.decode(path.read_text()).get("aliases", [])
        return {p["alias"]: p["canonical"] for p in pairs}

    def save_aliases(self, aliases: dict[str, str]) -> None:
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        # collapse chains (a->b, b->c => a->c) so lookups are single-step
        def root(x: str, seen=()) -> str:
            nxt = aliases.get(x)
            return x if nxt is None or nxt in seen else root(nxt, (*seen, x))
        flat = {a: root(c) for a, c in aliases.items() if a != root(c)}
        doc = {"aliases": [{"alias": a, "canonical": c} for a, c in sorted(flat.items())]}
        (self.graph_dir / "aliases.toon").write_text(toon.encode(doc))

    def load_profiles(self) -> dict[str, dict]:
        """entity id -> {summary, key_facts} (from `tsubasa profile`)."""
        path = self.graph_dir / "profiles.toon"
        if not path.is_file():
            return {}
        return {p["id"]: p for p in toon.decode(path.read_text()).get("profiles", [])}

    def save_profiles(self, profiles: dict[str, dict]) -> None:
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        doc = {"profiles": [profiles[k] for k in sorted(profiles)]}
        (self.graph_dir / "profiles.toon").write_text(toon.encode(doc))

    # ------------------------------------------------------------ anchors

    def load_anchors(self) -> list[dict]:
        """entity <-> graphify-node links: {entity, repo, node, by}.
        node "*" = repo-level anchor; by = seed | xrepo | link | recall."""
        path = self.graph_dir / "anchors.toon"
        if not path.is_file():
            return []
        return toon.decode(path.read_text()).get("anchors", [])

    def save_anchors(self, anchors: list[dict]) -> None:
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        seen, out = set(), []
        for a in anchors:
            k = (a.get("entity"), a.get("repo"), a.get("node"))
            if k not in seen and all(k):
                seen.add(k)
                out.append({"entity": a["entity"], "repo": a["repo"],
                            "node": a["node"], "by": a.get("by", "seed")})
        out.sort(key=lambda a: (a["entity"], a["repo"], a["node"]))
        (self.graph_dir / "anchors.toon").write_text(toon.encode({"anchors": out}))

    # ------------------------------------------------------------ state

    def load_state(self) -> dict:
        path = self.base / "state.toon"
        return toon.decode(path.read_text()) if path.is_file() else {}

    def save_state(self, state: dict) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        (self.base / "state.toon").write_text(toon.encode(state))
