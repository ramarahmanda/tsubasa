"""tsubasa CLI — the deterministic core the Captain's skills call into.

    tsubasa init <name>          scaffold .tsubasa/ in the current repo
    tsubasa ingest [adapter]     run source adapters -> events -> graph -> tiers
    tsubasa event add ...        append a validated event (used by capture/inject skills)
    tsubasa task new|list|set    task lifecycle
    tsubasa query "<text>"       entity match + subgraph + citations
    tsubasa questions            open reconciliation questions
    tsubasa rebuild              replay the event log into a fresh graph
    tsubasa tiers                regenerate hot/warm/cold memory files
    tsubasa doctor               validate graph files, lint for secret-looking values
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import config as cfg_mod
from . import toon
from .adapters.base import get_adapter
from .config import CaptainConfig, TSUBASA_DIR
from .graph import assemble, query as query_mod, reconcile
from .memory import tiers
from .models import (
    EVENT_TYPES, IMPACT_LEVELS, TASK_STATES, TRUST_LEVELS,
    Event, Ref, Task, TaskHistoryEntry, now_iso, slugify,
)
from .storage import Store
from . import tasksync


def main(argv: list[str] | None = None) -> int:
    # long-running commands (study/ingest) are often piped or backgrounded;
    # line-buffer stdout so progress is visible as it happens
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        return args.func(args) or 0
    except Exception as e:  # surface a clean error, not a traceback
        print(f"error: {e}", file=sys.stderr)
        return 1


def _ctx() -> tuple[Path, CaptainConfig, Store]:
    root = cfg_mod.find_root()
    if root is None:
        raise RuntimeError("no .tsubasa/captain.toml found here or above — run `tsubasa init <name>` first")
    return root, cfg_mod.load(root), Store(root)


# ------------------------------------------------------------------ init

def cmd_init(args) -> int:
    root = Path.cwd()
    base = root / TSUBASA_DIR
    if (base / cfg_mod.CONFIG_FILE).exists():
        print(f"already initialized: {base}")
        return 1
    base.mkdir(parents=True, exist_ok=True)
    domains = [d.strip() for d in (args.domains or "").split(",") if d.strip()]
    domain_lines = "\n".join(f'{d} = 1.0' for d in domains) or '# payments = 1.0'
    sources = cfg_mod.SOURCE_TEMPLATE.format(adapter="adr", path="docs/adr", glob="**/*.md")
    (base / cfg_mod.CONFIG_FILE).write_text(cfg_mod.CONFIG_TEMPLATE.format(
        name=args.name, role=args.role, domains=domain_lines, sources=sources,
    ))
    for sub in ("graph/events", "tasks", "memory"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    _ensure_claude_md(root, args.name)
    print(f"initialized captain '{args.name}' in {base}")
    print("persona principles written to CLAUDE.md — edit them to fit your org")
    print("next: edit .tsubasa/captain.toml [captain.domains] and [[sources]], then `tsubasa ingest`")
    return 0


# Default persona guardrails written into CLAUDE.md at init — not stored as a
# doc source, so the user can edit them freely after scaffolding.
PRINCIPLES_MD = """\
Principles (non-negotiable):

### Response
1. **Straightforward answers only.** No hedging, no option surveys unless asked.
2. **Flag only critical issues**: security, performance, or leak. Everything else waits to be asked.
3. **Respect reading time.** Short, concise, straight to the point. Minimize output.
4. **Prefer ASCII flows and comparison tables** over prose.
5. **Every claim cites** (event ID, ADR, PR, file:line) or say "I don't know."
6. **Minimize changes.** Don't refactor beyond what the task requires.
7. **Remove false positives.** Only surface confirmed, above-critical findings.
8. **Push back for consistency.** Fight the user when their request conflicts with existing ADRs, patterns, or decisions; cite the conflict. Only override: change the record (ADR or graph). Then follow it.

### ADR format (enforced, reject non-conforming ADRs)
- **Not verbose.** High-level and constraints only. No implementation detail.
- **Context**: existing flow in ASCII, plus constraints.
- **Decision**: target flow in ASCII, plus pointers. No paragraphs.
- **`### BREAKING CHANGE`** section per contract/schema/data-source change.
- **Data source mapping**: what data, from where, join key, assumption.
- **Phases, consequences, risks, goal alignment**: one-liners.

### Communication
9. **Respect peers' time.** Output is intended for humans. Max 1-minute read.
10. **Omit needless words.** Follow Strunk & White's *The Elements of Style*: vigorous writing is concise. A sentence should contain no unnecessary words, a paragraph no unnecessary sentences.
11. **Good code is few lines changed.** Respect the maintainer's time. Remove unused code. Reduce optional config unless it serves as a necessary flag. Once accepted, remove the flag.
12. **No AI attribution in commits or PRs.** No "Co-Authored-By" or "Generated with" trailers.
13. **No em dashes.** Never "—" or "--" as punctuation; use commas, colons, periods.
"""


def _ensure_claude_md(root: Path, name: str) -> None:
    line = f"@{TSUBASA_DIR}/memory/hot.md"
    claude_md = root / "CLAUDE.md"
    header = (
        f"\n# Captain {name} (tsubasa)\n\n{PRINCIPLES_MD}\n"
        f"Always-loaded knowledge tier:\n{line}\n"
    )
    if claude_md.exists():
        text = claude_md.read_text()
        if line not in text:
            claude_md.write_text(text.rstrip() + "\n" + header)
    else:
        claude_md.write_text(header.lstrip())


# ------------------------------------------------------------------ ingest

def cmd_ingest(args) -> int:
    root, cfg, store = _ctx()
    state = store.load_state()
    adapters_state = state.setdefault("adapters", {})
    new_events: list[Event] = []
    snap_entities: list[dict] = []
    snap_relations: list[dict] = []
    snap_prov: list[str] = []
    ran_snapshot = False
    for i, src in enumerate(cfg.sources):
        if args.adapter and src.adapter != args.adapter:
            continue
        key = f"{src.adapter}:{src.path}"
        a_state = adapters_state.setdefault(key, {})
        adapter = get_adapter(src.adapter)(root, cfg, src, a_state)
        if getattr(adapter, "snapshot_mode", False):
            ents, rels, prov = adapter.snapshot()
            snap_entities.extend(ents)
            snap_relations.extend(rels)
            snap_prov.extend(prov)
            ran_snapshot = True
            print(f"[{key}] snapshot: {len(ents)} entities, {len(rels)} relations @ {', '.join(prov) or 'n/a'}")
            continue
        collected = adapter.collect()
        fresh = [e for e in collected if not store.has_event(e.id)]
        for ev in fresh:
            store.append_event(ev)
        new_events.extend(fresh)
        print(f"[{key}] {len(fresh)} new event(s)")
    store.save_state(state)
    if ran_snapshot:
        store.save_code_graph(snap_entities, snap_relations, snap_prov)
    _apply_and_regen(root, cfg, store, new_events)
    return 0


def _apply_and_regen(root: Path, cfg: CaptainConfig, store: Store, new_events: list[Event]) -> None:
    entities = store.load_entities()
    relations = store.load_relations()
    aliases = store.load_aliases()
    notes: list[str] = []
    for ev in sorted(new_events, key=lambda e: e.ts):
        notes.extend(assemble.apply_event(entities, relations, ev, aliases))
        # reconciliation may mark the event disputed — persist that verdict
        store.append_event(ev)
    assemble.apply_profiles(entities, store.load_profiles())
    tasks = store.load_tasks()
    notes.extend(tasksync.sync(tasks, new_events))
    for t in tasks.values():
        store.save_task(t)
    store.save_entities(entities)
    store.save_relations(relations)
    n_q = reconcile.queue_questions(store, notes, now_iso())
    stats = tiers.generate(store, cfg)
    for n in notes:
        print(n)
    print(f"graph: {stats['entities']} entities, {stats['tasks']} tasks | "
          f"hot: ~{stats['hot_tokens']} tokens (budget {stats['hot_budget']})"
          + (f" | {n_q} open question(s) — see `tsubasa questions`" if n_q else ""))


# ------------------------------------------------------------------ event add

def cmd_event_add(args) -> int:
    root, cfg, store = _ctx()
    ts = args.ts or now_iso()
    ev_id = args.id or f"evt-{ts[:10].replace('-', '')}-{slugify(args.title)}"
    if store.has_event(ev_id):
        raise RuntimeError(f"event id already exists: {ev_id}")
    body = sys.stdin.read() if args.body_stdin else (args.body or "")
    event = Event(
        id=ev_id, type=args.type, ts=ts, title=args.title,
        summary=args.summary or "", impact=args.impact,
        domains=_csv(args.domains), actors=_csv(args.actors), trust=args.trust,
        refs=[_parse_ref(r) for r in args.ref or []],
        supersedes=_csv(args.supersedes), body=body, source="manual",
        derived_entities=[_parse_entity(e) for e in args.entity or []],
        derived_relations=[_parse_relation(r) for r in args.relation or []],
    )
    store.append_event(event)
    print(f"event written: {store.event_path(event).relative_to(root)}")
    _apply_and_regen(root, cfg, store, [event])
    return 0


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _parse_ref(spec: str) -> Ref:
    kind, _, rid = spec.partition(":")
    if not rid:
        raise RuntimeError(f"--ref wants kind:id, got {spec!r}")
    return Ref(kind=kind, id=rid)


def _parse_entity(spec: str) -> dict:
    parts = spec.split(":", 3)
    if len(parts) < 3:
        raise RuntimeError(f"--entity wants id:type:name[:description], got {spec!r}")
    d = {"id": parts[0], "type": parts[1], "name": parts[2]}
    if len(parts) == 4:
        d["description"] = parts[3]
    return d


def _parse_relation(spec: str) -> dict:
    parts = spec.split(":", 2)
    if len(parts) != 3:
        raise RuntimeError(f"--relation wants source:predicate:target, got {spec!r}")
    return {"source": parts[0], "predicate": parts[1], "target": parts[2]}


# ------------------------------------------------------------------ tasks

def cmd_task_new(args) -> int:
    root, cfg, store = _ctx()
    ts = now_iso()
    task_id = args.id or f"task-{slugify(args.title)}"
    tasks = store.load_tasks()
    if task_id in tasks:
        raise RuntimeError(f"task exists: {task_id}")
    task = Task(
        id=task_id, title=args.title, state="todo", adr=args.adr or "",
        domains=_csv(args.domains), created=ts, updated=ts,
        history=[TaskHistoryEntry(ts=ts, state="todo", by=args.by)],
    )
    store.save_task(task)
    print(f"task created: {task_id}" + (f" (adr: {task.adr})" if task.adr else ""))
    tiers.generate(store, cfg)
    return 0


def cmd_task_list(args) -> int:
    _, _, store = _ctx()
    tasks = sorted(store.load_tasks().values(), key=lambda t: (t.state, t.id))
    if not tasks:
        print("no tasks")
        return 0
    for t in tasks:
        line = f"{t.id:40s} [{t.state:11s}] {t.title}"
        if t.adr:
            line += f"  adr={t.adr}"
        if t.prs:
            line += f"  prs={','.join(t.prs)}"
        print(line)
    return 0


def cmd_task_set(args) -> int:
    root, cfg, store = _ctx()
    tasks = store.load_tasks()
    if args.id not in tasks:
        raise RuntimeError(f"unknown task: {args.id}")
    task = tasks[args.id]
    if task.transition(args.state, by=args.by, evidence=args.evidence or ""):
        store.save_task(task)
        tiers.generate(store, cfg)
        print(f"{task.id} -> {task.state}")
    else:
        print(f"{task.id} already {task.state}")
    return 0


# ------------------------------------------------------------------ sources

def cmd_source_add(args) -> int:
    root, cfg, store = _ctx()
    get_adapter(args.adapter)  # validate name before touching config
    target = (root / args.path).resolve()
    if not target.exists():
        raise RuntimeError(f"source path does not exist: {args.path}")
    if any(s.adapter == args.adapter and Path(s.path).as_posix() == Path(args.path).as_posix()
           for s in cfg.sources):
        print(f"source already configured: {args.adapter} {args.path}")
        return 0
    block = f'\n[[sources]]\nadapter = "{args.adapter}"\npath = "{args.path}"\n'
    if args.glob:
        block += f'glob = "{args.glob}"\n'
    if getattr(args, "branch", ""):
        block += f'branch = "{args.branch}"\n'
    if getattr(args, "pull", False):
        block += "pull = true\n"
    if getattr(args, "kind", ""):
        block += f'kind = "{args.kind}"\n'
    if getattr(args, "impact", ""):
        block += f'impact = "{args.impact}"\n'
    if getattr(args, "no_commit", False):
        # source material stays local; only the knowledge distilled from it
        # (events, always redacted/curated) enters the committed graph
        block += "commit = false\n"
    cfg_path = root / TSUBASA_DIR / cfg_mod.CONFIG_FILE
    cfg_path.write_text(cfg_path.read_text().rstrip() + "\n" + block)
    cfg_mod.load(root)  # verify the file still parses
    if getattr(args, "no_commit", False) and (root / ".git").exists():
        _gitignore_add(root, args.path)
    print(f"source added: {args.adapter} {args.path}"
          + (f" (glob {args.glob})" if args.glob else "")
          + (" [local-only, not committed]" if getattr(args, "no_commit", False) else ""))
    return 0


def _gitignore_add(root: Path, path: str) -> None:
    gi = root / ".gitignore"
    line = "/" + path.strip("./").rstrip("/") + "/"
    existing = gi.read_text() if gi.exists() else ""
    if line not in existing.splitlines():
        # gitignore has no inline comments — comment on its own line
        gi.write_text(existing.rstrip("\n") + ("\n" if existing else "")
                      + f"# tsubasa source: local-only\n{line}\n")


def cmd_source_list(args) -> int:
    _, cfg, _ = _ctx()
    if not cfg.sources:
        print("no sources configured")
    for s in cfg.sources:
        print(f"{s.adapter:10s} {s.path}"
              + (f"  glob={s.glob}" if s.glob else "")
              + ("  [local-only]" if s.options.get("commit") is False else ""))
    return 0


# ------------------------------------------------------------------ goals

def cmd_goal_list(args) -> int:
    _, _, store = _ctx()
    goals = [e for e in store.load_entities().values() if e.type == "goal"]
    if not goals:
        print("no goals")
        return 0
    for g in sorted(goals, key=lambda e: (e.status != "active", e.id)):
        print(f"{g.id:44s} [{g.status:9s}] {g.description or g.name}")
    return 0


def cmd_goal_set(args) -> int:
    root, cfg, store = _ctx()
    entities = store.load_entities()
    goal = entities.get(args.id)
    if goal is None or goal.type != "goal":
        raise RuntimeError(f"unknown goal: {args.id}")
    ts = now_iso()
    event = Event(
        id=f"evt-{ts[:10].replace('-', '')}-{slugify(args.id)}-{args.status}",
        type="plan", ts=ts,
        title=f"Goal {args.status}: {goal.name}",
        summary=args.evidence or "", impact="medium", trust="high", source="manual",
        derived_entities=[{"id": goal.id, "type": "goal", "name": goal.name, "status": args.status}],
    )
    store.append_event(event)
    _apply_and_regen(root, cfg, store, [event])
    return 0


# ------------------------------------------------------------------ query & memory

def cmd_query(args) -> int:
    root, cfg, store = _ctx()
    if args.as_of:
        # temporal recall: the graph as the captain knew it on that date.
        # The code snapshot is current-state only, so it is excluded.
        entities, relations, _ = assemble.replay(store, as_of=args.as_of)
        events = {e.id: e for e in store.load_events() if e.ts[:10] <= args.as_of}
        matched = query_mod.match_entities(entities, args.text, limit=args.limit)
        print(f"# knowledge as of {args.as_of} (code snapshot excluded — it is current-state only)")
        print(query_mod.serialize(entities, relations, events, matched, hops=args.hops))
        return 0
    entities = store.load_entities()
    relations = store.load_relations()
    # merge the code snapshot: event knowledge wins on collision (it carries
    # the why), but code contributes structure the event log can't know
    code_entities, code_relations = store.load_code_graph()
    merged = {**code_entities, **entities}
    for cid, ce in code_entities.items():
        if cid in entities and ce.description and ce.description not in entities[cid].description:
            merged[cid].key_facts = list(dict.fromkeys(entities[cid].key_facts + [f"[code] {ce.description}"]))
    events = {e.id: e for e in store.load_events()}
    matched = query_mod.match_entities(merged, args.text, limit=args.limit)
    print(query_mod.serialize(merged, relations + code_relations, events, matched, hops=args.hops))
    from .graph import anchors as anchors_mod, graphify_bridge
    anchor_rows = store.load_anchors()
    matched_ids = {e.id for e in matched}
    ent_anchors = [a for a in anchors_mod.for_entities(anchor_rows, matched_ids) if a["node"] != "*"]
    xrepo = [edge for edge in anchors_mod.cross_repo_edges(anchor_rows, merged)
             if edge[0] in matched_ids or edge[2] in matched_ids]
    if ent_anchors or xrepo:
        print("\n## Anchors (memory <-> code)")
        for a in ent_anchors[:15]:
            print(f"{a['entity']} <=> {a['node']}  [graphify:{a['repo']}, by={a['by']}]")
        for src, pred, tgt in xrepo[:15]:
            print(f"({src}) --[{pred}]--> ({tgt})  [anchor:xrepo]")
    code_graph = graphify_bridge.query(root, cfg, args.text)
    if code_graph:
        print("\n## Code anatomy (graphify)")
        print(code_graph)
    return 0


def cmd_questions(args) -> int:
    _, _, store = _ctx()
    qs = [q for q in reconcile.load_questions(store) if q.get("status") == "open"]
    if not qs:
        print("no open questions")
    for i, q in enumerate(qs):
        print(f"[{i}] ({q.get('ts', '')[:10]}) {q['text']}")
    return 0


def cmd_pack(args) -> int:
    _, _, store = _ctx()
    moved = store.pack_legacy_events()
    print(f"packed {moved} legacy event file(s) into monthly packs")
    return 0


def cmd_rebuild(args) -> int:
    root, cfg, store = _ctx()
    entities, relations, notes = assemble.replay(store)
    store.save_entities(entities)
    store.save_relations(relations)
    reconcile.queue_questions(store, notes, now_iso())
    stats = tiers.generate(store, cfg)
    print(f"rebuilt from event log: {stats['entities']} entities, "
          f"{len(relations)} relations, {stats['tasks']} tasks")
    return 0


def cmd_tiers(args) -> int:
    _, cfg, store = _ctx()
    stats = tiers.generate(store, cfg)
    print(f"hot: ~{stats['hot_tokens']}/{stats['hot_budget']} tokens | "
          f"{stats['entities']} entities | demoted: {stats['demoted']} | "
          f"domains: {', '.join(f'{d}({n})' for d, n in stats['domains'].items())}")
    return 0


# ------------------------------------------------------------------ density passes

def cmd_study(args) -> int:
    root, cfg, store = _ctx()
    from . import distill, llm
    if not llm.claude_available(args.claude_cmd):
        raise RuntimeError(f"'{args.claude_cmd}' not found — study needs the Claude CLI")
    events = distill.study(store, cfg, root, claude_cmd=args.claude_cmd,
                           chunk=args.chunk, max_chunks=args.max_chunks, model=args.model)
    _apply_and_regen(root, cfg, store, events)
    print(f"study complete: {len(events)} new event(s)")
    if not args.no_index:
        # code-only indexing is deterministic and free (local AST, no LLM),
        # so the learning pass runs it by default
        distill.index_code(cfg, root)
    return 0


def cmd_link(args) -> int:
    root, cfg, store = _ctx()
    from .graph import anchors as anchors_mod
    anchors_mod.seed(store, root, cfg)
    if args.llm:
        from . import distill, llm
        if not llm.claude_available(args.claude_cmd):
            raise RuntimeError(f"'{args.claude_cmd}' not found — --llm needs the Claude CLI")
        added = distill.link_llm(store, cfg, root, claude_cmd=args.claude_cmd, model=args.model)
        print(f"semantic pass: {added} new anchor(s)")
    return 0


def cmd_index(args) -> int:
    root, cfg, store = _ctx()
    from . import distill
    done = distill.index_code(cfg, root, only=args.repo, timeout=args.timeout)
    print(f"indexed {done} repo(s)")
    return 0


def cmd_resolve(args) -> int:
    root, cfg, store = _ctx()
    from . import distill, llm
    if not llm.claude_available(args.claude_cmd):
        raise RuntimeError(f"'{args.claude_cmd}' not found — resolve needs the Claude CLI")
    added = distill.resolve(store, claude_cmd=args.claude_cmd, model=args.model)
    if added:
        return cmd_rebuild(args)  # alias map applies at replay time
    print("resolve: nothing to merge")
    return 0


def cmd_profile(args) -> int:
    root, cfg, store = _ctx()
    from . import distill, llm
    if not llm.claude_available(args.claude_cmd):
        raise RuntimeError(f"'{args.claude_cmd}' not found — profile needs the Claude CLI")
    done = distill.profile(store, claude_cmd=args.claude_cmd, model=args.model, top=args.top)
    if done:
        entities = store.load_entities()
        assemble.apply_profiles(entities, store.load_profiles())
        store.save_entities(entities)
        tiers.generate(store, cfg)
    print(f"profiled {done} hub entit{'y' if done == 1 else 'ies'}")
    return 0


# ------------------------------------------------------------------ doctor

SECRET_RES = [
    re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*\S{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\b"),  # JWT
]


def cmd_doctor(args) -> int:
    root, cfg, store = _ctx()
    problems = 0
    for path in sorted(store.base.rglob("*.toon")):
        rel = path.relative_to(root)
        try:
            toon.decode(path.read_text())
        except toon.ToonError as e:
            print(f"PARSE  {rel}: {e}")
            problems += 1
            continue
        for rex in SECRET_RES:
            if rex.search(path.read_text()):
                print(f"SECRET {rel}: matches {rex.pattern[:40]!r} — knowledge stores secret-REFS, never values")
                problems += 1
    if (root / ".git").exists():
        import subprocess
        for s in cfg.sources:
            if s.options.get("commit") is False:
                out = subprocess.run(["git", "-C", str(root), "ls-files", "--", s.path],
                                     capture_output=True, text=True)
                if out.stdout.strip():
                    print(f"TRACKED local-only source '{s.path}' has committed files — "
                          f"`git rm -r --cached {s.path}` to untrack")
                    problems += 1
    entities = store.load_entities()
    code_entities, _ = store.load_code_graph()
    known = set(entities) | set(code_entities)  # either layer legitimizes an endpoint
    for r in store.load_relations():
        for end in (r.source, r.target):
            if end not in known and not re.match(r"^(PR-|evt-|adr-|task-|inc-)", end) and "/" not in end:
                print(f"ORPHAN relation endpoint '{end}' has no entity ({r.source} -[{r.predicate}]-> {r.target})")
                problems += 1
    print(f"doctor: {problems} problem(s)" if problems else "doctor: all clear")
    return 1 if problems else 0


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tsubasa", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers()

    sp = sub.add_parser("init", help="scaffold .tsubasa/ here")
    sp.add_argument("name")
    sp.add_argument("--role", default="Engineering Director")
    sp.add_argument("--domains", default="", help="comma-separated domain list")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("ingest", help="run source adapters")
    sp.add_argument("adapter", nargs="?", help="only this adapter")
    sp.set_defaults(func=cmd_ingest)

    ev = sub.add_parser("event", help="event operations").add_subparsers()
    sp = ev.add_parser("add", help="append a validated event")
    sp.add_argument("--id")
    sp.add_argument("--type", required=True, choices=sorted(EVENT_TYPES))
    sp.add_argument("--title", required=True)
    sp.add_argument("--summary")
    sp.add_argument("--ts")
    sp.add_argument("--impact", default="low", choices=sorted(IMPACT_LEVELS))
    sp.add_argument("--domains")
    sp.add_argument("--actors")
    sp.add_argument("--trust", default="normal", choices=sorted(TRUST_LEVELS))
    sp.add_argument("--ref", action="append", metavar="KIND:ID")
    sp.add_argument("--supersedes", help="comma-separated entity ids")
    sp.add_argument("--entity", action="append", metavar="ID:TYPE:NAME[:DESC]")
    sp.add_argument("--relation", action="append", metavar="SRC:PRED:TGT")
    sp.add_argument("--body")
    sp.add_argument("--body-stdin", action="store_true")
    sp.set_defaults(func=cmd_event_add)

    tk = sub.add_parser("task", help="task operations").add_subparsers()
    sp = tk.add_parser("new")
    sp.add_argument("--id")
    sp.add_argument("--title", required=True)
    sp.add_argument("--adr")
    sp.add_argument("--domains")
    sp.add_argument("--by", default="captain")
    sp.set_defaults(func=cmd_task_new)
    sp = tk.add_parser("list")
    sp.set_defaults(func=cmd_task_list)
    sp = tk.add_parser("set")
    sp.add_argument("id")
    sp.add_argument("state", choices=sorted(TASK_STATES))
    sp.add_argument("--evidence")
    sp.add_argument("--by", default="captain")
    sp.set_defaults(func=cmd_task_set)

    gl = sub.add_parser("goal", help="future knowledge: goals").add_subparsers()
    sp = gl.add_parser("list")
    sp.set_defaults(func=cmd_goal_list)
    sp = gl.add_parser("set", help="resolve or reopen a goal")
    sp.add_argument("id")
    sp.add_argument("status", choices=["achieved", "dropped", "active"])
    sp.add_argument("--evidence")
    sp.set_defaults(func=cmd_goal_set)

    so = sub.add_parser("source", help="manage knowledge sources").add_subparsers()
    sp = so.add_parser("add", help="append a [[sources]] entry safely")
    sp.add_argument("adapter")
    sp.add_argument("path")
    sp.add_argument("--glob", default="")
    sp.add_argument("--branch", default="", help="git: read history from this branch (default: repo's default branch)")
    sp.add_argument("--pull", action="store_true", help="git: fast-forward the branch on every ingest")
    sp.add_argument("--no-commit", action="store_true",
                    help="keep source files out of the captain repo (gitignored); only distilled events are committed")
    sp.add_argument("--kind", default="", help="doc adapter: what these documents are (principle, runbook, rfc…)")
    sp.add_argument("--impact", default="", choices=["", "high", "medium", "low"],
                    help="doc adapter: impact for distilled events (high keeps principles hot)")
    sp.set_defaults(func=cmd_source_add)
    sp = so.add_parser("list")
    sp.set_defaults(func=cmd_source_list)

    sp = sub.add_parser("query", help="knowledge lookup with citations")
    sp.add_argument("text")
    sp.add_argument("--hops", type=int, default=2)
    sp.add_argument("--limit", type=int, default=5)
    sp.add_argument("--as-of", default="", metavar="YYYY-MM-DD",
                    help="temporal recall: the graph as known on that date")
    sp.set_defaults(func=cmd_query)

    for name, fn, extra in (
        ("study", cmd_study, "distill full git history into events (headless claude, chunked)"),
        ("resolve", cmd_resolve, "merge duplicate entities via alias map"),
        ("profile", cmd_profile, "generate profiles for hub entities"),
    ):
        sp = sub.add_parser(name, help=extra)
        sp.add_argument("--claude-cmd", default="claude")
        sp.add_argument("--model", default="haiku" if name == "study" else "")
        if name == "study":
            sp.add_argument("--chunk", type=int, default=250)
            sp.add_argument("--max-chunks", type=int, default=0, help="cap chunks per repo (newest kept)")
            sp.add_argument("--no-index", action="store_true",
                            help="skip the code-only graphify index pass (deterministic, no LLM)")
        if name == "profile":
            sp.add_argument("--top", type=int, default=12)
        sp.set_defaults(func=fn)

    sp = sub.add_parser("link", help="seed anchors between the knowledge graph and code indexes")
    sp.add_argument("--llm", action="store_true",
                    help="also run the semantic pass: LLM ties entities to code nodes (node names only, few calls)")
    sp.add_argument("--claude-cmd", default="claude")
    sp.add_argument("--model", default="")
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("index", help="build/refresh code-only graphify indexes (deterministic, no LLM)")
    sp.add_argument("--repo", default="", help="only this repo (source path or name)")
    sp.add_argument("--timeout", type=int, default=600, help="seconds per repo (default 600)")
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("questions", help="open reconciliation questions")
    sp.set_defaults(func=cmd_questions)

    sp = sub.add_parser("rebuild", help="replay event log into a fresh graph")
    sp.set_defaults(func=cmd_rebuild)

    sp = sub.add_parser("pack", help="migrate legacy per-event files into monthly packs")
    sp.set_defaults(func=cmd_pack)

    sp = sub.add_parser("tiers", help="regenerate memory tiers")
    sp.set_defaults(func=cmd_tiers)

    sp = sub.add_parser("doctor", help="validate graph files")
    sp.set_defaults(func=cmd_doctor)
    return p


if __name__ == "__main__":
    sys.exit(main())
