"""tsubasa CLI — the deterministic core the Captain's skills call into.

    tsubasa init <name>          scaffold .tsubasa/, detect sources, ingest
    tsubasa upgrade              bring an existing captain up to this schema
    tsubasa source detect        classify knowledge dirs in the workspace
    tsubasa ingest [adapter]     run source adapters -> events -> graph -> tiers
    tsubasa pull [--study]       fetch + fast-forward every git source, re-ingest
    tsubasa study                learn: distil -> resolve -> index -> link -> profile
    tsubasa event add ...        append a validated event (used by capture/inject skills)
    tsubasa query "<text>"       entity match + subgraph + citations
    tsubasa query --timeline     what happened when: state, then the sequence
    tsubasa questions            open reconciliation questions
    tsubasa rebuild              replay the event log into a fresh graph
    tsubasa tiers                regenerate hot/warm/cold memory files
    tsubasa doctor               validate graph files, lint for secret-looking values
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import config as cfg_mod
from . import discover
from . import scaffold
from . import toon
from . import topology
from .adapters.base import get_adapter
from .config import SCHEMA_VERSION, CaptainConfig, TSUBASA_DIR
from .graph import assemble, query as query_mod, reconcile
from .memory import tiers
from .models import (
    DOMAIN_RE, EVENT_TYPES, IMPACT_LEVELS, RETIRED_EVENT_TYPES, TRUST_LEVELS,
    Event, Ref, now_iso, slugify,
)
from .storage import Store


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
    detected, dropped, refined = _detect(root, args)
    base.mkdir(parents=True, exist_ok=True)
    domains = [d.strip() for d in (args.domains or "").split(",") if d.strip()]
    domain_lines = "\n".join(f'{d} = 1.0' for d in domains) or '# payments = 1.0'
    blocks = "".join(_source_block(s) for s in detected)
    sources = (_provenance(refined) + blocks) if blocks else NO_SOURCES_HINT
    (base / cfg_mod.CONFIG_FILE).write_text(cfg_mod.CONFIG_TEMPLATE.format(
        schema_version=SCHEMA_VERSION,
        name=args.name, role=args.role, domains=domain_lines, sources=sources,
    ))
    scaffold.ensure_layout(base)
    notes = scaffold.ensure_claude_md(root, args.name)
    print(f"initialized captain '{args.name}' in {base}")
    for note in notes:
        print(note)
    if not detected:
        print("no knowledge sources found here (looked for git history, ADRs, postmortems, docs)")
        print("next: `tsubasa source add adr docs/adr`, then `tsubasa ingest`")
        return 0
    print(f"\ndetected {len(detected)} source(s):")
    _print_sources(detected, dropped)
    if args.no_ingest:
        print("\nnext: `tsubasa ingest`  (writes the workspace map too)")
        return 0
    # the map of where knowledge lives is worth having before anything is
    # distilled, so it is emitted as events and replayed like everything else
    print("\nmapping the workspace (git metadata + file evidence, no model)...")
    try:
        # No git network I/O while scaffolding (the source-classification
        # `claude -p` call above is separate, and `--no-llm` skips it). One
        # `git fetch` per detected repo turns into minutes of hanging on an SSH
        # remote that wants a passphrase. `tsubasa pull` is where fetching lives.
        cmd_ingest(argparse.Namespace(
            adapter=None, offline=True,
            seed_events=topology.workspace_events(root, detected, refined)))
    except Exception as e:  # the scaffold is valid even if one source is not
        print(f"warning: initial ingest failed: {e}", file=sys.stderr)
        print("scaffold is in place — fix the source and run `tsubasa ingest`")
        return 0
    print('\nask it something:  tsubasa query "why does X work this way?"')
    print("latest history:    tsubasa pull      (fetches every repo, then re-ingests)")
    print("deeper history:    tsubasa study     (distills recent git history, uses Claude)")
    return 0


# ------------------------------------------------------------- source detection

NO_SOURCES_HINT = """\
# No knowledge sources detected at init. Add them as you go:
#   tsubasa source add git ./service-repo --pull
#   tsubasa source add adr docs/adr
#   tsubasa source add incident docs/postmortem
"""


def _detect(root: Path, args) -> tuple[list[dict], list[dict], dict | None]:
    """Offline plan first; the LLM pass only ever amends it, never replaces it."""
    if args.no_detect:
        return [], [], None
    candidates = discover.scan(root, args.depth)
    planned, dropped = discover.plan(candidates, root)
    git = discover.git_sources(root)
    refined = None
    if not args.no_llm and planned:
        refined = topology.refine(root, candidates, planned, [s["path"] for s in git],
                                  claude_cmd=args.claude_cmd, model=args.model)
        if refined:
            planned = topology.apply_sources(root, planned, candidates, refined)
    return git + planned, dropped, refined


def _provenance(refined: dict | None) -> str:
    how = (f"claude ({refined['model'] or 'default model'})" if refined
           else "offline heuristics, no model")
    return (
        "\n# MACHINE-PROPOSED SOURCES — these are proposals, not facts.\n"
        f"# Detected by `tsubasa init` on {now_iso()[:10]} via {how}.\n"
        "# A human should skim this list before trusting coverage: drop what is\n"
        "# generated or noisy, add what was missed (`tsubasa source add ...`).\n"
    )


def _source_block(src: dict) -> str:
    block = f'\n[[sources]]\nadapter = "{src["adapter"]}"\npath = "{src["path"]}"\n'
    for key in ("glob", "kind", "impact"):  # order matches cmd_source_add's block
        if src.get(key):
            block += f'{key} = "{src[key]}"\n'
    if src.get("exclude"):
        block += "exclude = [" + ", ".join(f'"{p}"' for p in src["exclude"]) + "]\n"
    return block


def _print_sources(sources: list[dict], dropped: list[dict]) -> None:
    for s in sources:
        files = str(s["files"]) if "files" in s else "-"
        print(f"  {s.get('bucket', 'history'):13s} {s['adapter']:9s} {files:>5s}  {s['path']}"
              + (f"  glob={s['glob']}" if s.get("glob") else ""))
        # an exclusion that isn't printed is a lie about coverage, same as a cap
        for pat in s.get("exclude", []):
            print(f"    excluded (generated): {s['path']}/{pat}")
        for hit in s.get("suspect", []):
            print(f"    suspect (long outlier, kept): {s['path']}/{hit}")
    for s in dropped:  # a cap that hides what it cut is a lie about coverage
        print(f"  dropped (per-repo cap): {s['adapter']} {s['path']} glob={s['glob']} ({s['files']} files)")


# ------------------------------------------------------------------ upgrade

RETIRED_DIR = "retired"


def cmd_upgrade(args) -> int:
    """Bring an existing captain up to what this version of `init` would write.

    Idempotent by construction: every step asks the captain what it already
    has. Two rules decide what happens without being asked.

    Derived state is rewritten freely — the source graph, the replayed
    entities and relations, the memory tiers, the version stamp — because it
    is reproducible from the event log and
    losing it costs nothing. captain.toml is not derived: a human wrote it,
    and detection is a proposal, not a verdict (see `_provenance`). So newly
    detected sources are reported and only written with `--add`, the same
    contract `source detect` already has. User data is never deleted: retired
    task files move aside, and their events stay in the log.
    """
    root, cfg, store = _ctx()
    was = cfg.schema_version
    if was > SCHEMA_VERSION:
        raise RuntimeError(
            f"captain.toml is schema {was}, this CLI writes {SCHEMA_VERSION} — "
            "upgrade tsubasa itself (`uv tool upgrade tsubasa`)")
    stamp = "no stamp, pre-versioning" if was == 1 else f"schema {was}"
    print(f"captain '{cfg.name}' at {store.base}: {stamp}; this CLI writes schema {SCHEMA_VERSION}\n")

    changes: list[str] = []
    changes += _upgrade_sources(root, cfg, args)
    if changes:  # sources were added: re-read before anything maps them
        cfg = cfg_mod.load(root)

    scaffold.ensure_layout(store.base)
    changes += scaffold.ensure_claude_md(root, cfg.name)

    # Schema 3: fresh captains are born with delegate_only = true; a config
    # that predates the key gains it here. An explicit false is never touched.
    if cfg_mod.ensure_delegate_only(root):
        changes.append("captain.toml: added delegate_only = true under [captain] "
                       "(set false to disarm)")

    new_events = _upgrade_map(root, cfg, store, args)
    if new_events:
        changes.append(f"source graph: emitted {len(new_events)} map event(s) "
                       f"({', '.join(e.id for e in new_events)})")
    changes += _retire_tasks(root, store)

    # Replay, never incrementally apply. entities.toon may be missing, stale, or
    # written by a version that derived it differently; the log is the only
    # authority. Idempotent, so a no-op upgrade reproduces it byte for byte.
    cmd_rebuild(args)

    if was != SCHEMA_VERSION:
        cfg_mod.stamp_version(root)
        changes.append(f"captain.toml: stamped schema_version = {SCHEMA_VERSION} (was {stamp})")

    print()
    if not changes:
        print("nothing to upgrade: this captain already has everything this version writes")
        return 0
    print(f"upgraded ({len(changes)} change(s)):")
    for c in changes:
        print(f"  - {c}")
    print("\nnext: `tsubasa pull` for fresh history, `tsubasa study` to distil it")
    return 0


def _upgrade_sources(root: Path, cfg: CaptainConfig, args) -> list[str]:
    """Report sources detection finds that captain.toml does not have."""
    if args.no_detect:
        return []
    detected, dropped, _ = _detect(root, argparse.Namespace(
        no_detect=False, no_llm=True, depth=args.depth,
        claude_cmd=args.claude_cmd, model=args.model))
    have = {(s.adapter, Path(s.path).as_posix()) for s in cfg.sources}
    missing = [s for s in detected
               if (s["adapter"], Path(s["path"]).as_posix()) not in have]
    if not missing:
        print(f"sources: {len(cfg.sources)} configured, detection found nothing missing")
        return []
    print(f"sources: {len(cfg.sources)} configured, {len(missing)} detected but not registered:")
    _print_sources(missing, dropped)
    if not args.add:
        print("  (hand-written sources are never touched; `tsubasa upgrade --add` registers these)")
        return []
    print()
    for s in missing:
        cmd_source_add(argparse.Namespace(
            adapter=s["adapter"], path=s["path"], glob=s.get("glob", ""),
            kind=s.get("kind", ""), impact=s.get("impact", ""),
            exclude=s.get("exclude", []), branch="", pull=False, no_commit=False))
    return [f"captain.toml: registered {len(missing)} detected source(s)"]


def _upgrade_map(root: Path, cfg: CaptainConfig, store: Store, args) -> list[Event]:
    """Emit the source graph for a captain that never had one.

    Built from captain.toml, not from a fresh detection pass, so hand-written
    sources are mapped exactly like auto-detected ones.
    """
    if scaffold.has_workspace_map(store):
        print("source graph: already present (svc-/corpus- entities from a workspace-map event)")
        return []
    if not cfg.sources:
        print("source graph: nothing to map, no sources configured")
        return []
    refined = None
    if args.llm:
        candidates = discover.scan(root, args.depth)
        planned, _ = discover.plan(candidates, root)
        refined = topology.refine(root, candidates, planned,
                                  [s.path for s in cfg.sources if s.adapter == "git"],
                                  claude_cmd=args.claude_cmd, model=args.model)
    print("source graph: mapping the workspace (git metadata + file evidence)...")
    sources = scaffold.source_dicts(root, cfg)
    return scaffold.seed(store, topology.workspace_events(root, sources, refined))


def _retire_tasks(root: Path, store: Store) -> list[str]:
    """Move .tsubasa/tasks/ aside. The events stay: the log is append-only,
    and a future version could resurface them."""
    out: list[str] = []
    n_events = sum(1 for e in store.load_events() if e.type in RETIRED_EVENT_TYPES)
    if n_events:
        # an observation, not a change: nothing was done to them, on purpose
        print(f"tasks: left {n_events} retired task event(s) in the log untouched "
              "(append-only; they still replay)")
    src = store.legacy_tasks_dir
    if not src.is_dir():
        return out
    files = sorted(p for p in src.iterdir() if p.is_file())
    if files:
        dest = store.base / RETIRED_DIR / "tasks"
        dest.mkdir(parents=True, exist_ok=True)
        for p in files:
            p.replace(dest / p.name)
        out.append(f"retired {len(files)} task file(s): "
                   f"{src.relative_to(root)}/ -> {dest.relative_to(root)}/ (not deleted)")
    if not any(src.iterdir()):
        src.rmdir()
        if not files:
            out.append(f"removed empty {src.relative_to(root)}/")
    return out


# ------------------------------------------------------------------ ingest

def cmd_ingest(args) -> int:
    root, cfg, store = _ctx()
    state = store.load_state()
    adapters_state = state.setdefault("adapters", {})
    # `init` hands its workspace map in here so derived state has one write path
    new_events: list[Event] = scaffold.seed(store, getattr(args, "seed_events", None) or [])
    snap_entities: list[dict] = []
    snap_relations: list[dict] = []
    snap_prov: list[str] = []
    ran_snapshot = False
    for i, src in enumerate(cfg.sources):
        if args.adapter and src.adapter != args.adapter:
            continue
        key = f"{src.adapter}:{src.path}"
        a_state = adapters_state.setdefault(key, {})
        adapter = get_adapter(src.adapter)(root, cfg, src, a_state,
                                           offline=getattr(args, "offline", False))
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


# ------------------------------------------------------------------ pull

def cmd_pull(args) -> int:
    """Fetch every git source, re-ingest, optionally distil the new history.
    The routine "refresh my captain" verb, hence top-level alongside `ingest`
    and `study` rather than under the config verbs `source add`/`source list`."""
    root, cfg, store = _ctx()
    from .adapters import gitlog
    repos = [s for s in cfg.sources if s.adapter == "git"]
    failures: list[str] = []
    stale: list[str] = []
    gained = 0
    for src in repos:
        repo = (root / src.path).resolve()
        if not (repo / ".git").exists():
            failures.append(f"{src.path}: not a git repo")
            print(f"[pull] {src.path}: FAILED (not a git repo)")
            continue
        # ff every source, not only `pull = true` ones: the adr/doc/incident
        # adapters read the working tree, so fetching alone would re-ingest
        # stale documents while reporting success
        r = gitlog.fetch(repo, str(src.options.get("branch") or ""))
        gained += r.gained
        if r.error:  # one unreachable remote must not stop the other repos
            failures.append(f"{src.path}: {r.error}")
            print(f"[pull] {src.path}: FAILED ({r.error})")
            continue
        line = f"[pull] {src.path}: {r.gained} new commit(s)"
        if r.warning:
            stale.append(f"{src.path}: {r.warning}")
            print(f"{line}, TREE NOT ADVANCED ({r.warning}), {r.behind} behind — "
                  "document sources from this repo ingest at the stale revision")
        elif r.moved:
            print(f"{line}, tree at {r.tree_after} (was {r.tree_before})")
        else:
            print(f"{line}, tree unchanged at {r.tree_after or r.tree_before}")
    if repos and len(failures) == len(repos):
        print(f"pull failed for all {len(repos)} git source(s)", file=sys.stderr)
        return 1
    before = len(Store(root).load_events())
    cmd_ingest(argparse.Namespace(adapter=None, seed_events=None))
    added = len(Store(root).load_events()) - before
    if not gained and not added:
        print("nothing new: no commits fetched, no events added")
    else:
        print(f"pull complete: {gained} commit(s) fetched, {added} new event(s)")
    rc = _learn(args) if args.study else 0   # the identical learning pipeline
    for f in failures:
        print(f"warning: {f}", file=sys.stderr)
    for s in stale:  # answering from stale documents is the silent failure here
        print(f"warning: working tree not advanced — {s}", file=sys.stderr)
    return rc


def _apply_and_regen(root: Path, cfg: CaptainConfig, store: Store, new_events: list[Event]) -> None:
    entities = store.load_entities()
    relations = store.load_relations()
    aliases = store.load_aliases()
    notes: list[str] = []
    event_trust = {e.id: e.trust for e in store.load_events()}
    for ev in sorted(new_events, key=lambda e: e.ts):
        event_trust[ev.id] = ev.trust
        notes.extend(assemble.apply_event(entities, relations, ev, aliases, event_trust))
        # reconciliation may mark the event disputed — persist that verdict
        store.append_event(ev)
    assemble.apply_profiles(entities, store.load_profiles())
    store.save_entities(entities)
    store.save_relations(relations)
    n_q = reconcile.queue_questions(store, notes, now_iso())
    stats = tiers.generate(store, cfg)
    for n in notes:
        print(n)
    print(f"graph: {stats['entities']} entities | "
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
    domains = _csv(args.domains)
    bad = next((d for d in domains if not DOMAIN_RE.match(d)), None)
    if bad is not None:
        raise RuntimeError(
            f"--domains wants kebab-case labels (a-z, 0-9, hyphen), got {bad!r}: "
            f"each label becomes memory/domains/<label>.md")
    event = Event(
        id=ev_id, type=args.type, ts=ts, title=args.title,
        summary=args.summary or "", impact=args.impact,
        domains=domains, actors=_csv(args.actors), trust=args.trust,
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


# ------------------------------------------------------------------ sources

def cmd_source_add(args) -> int:
    root, cfg, store = _ctx()
    get_adapter(args.adapter)  # validate name before touching config
    target = (root / args.path).resolve()
    if not target.exists():
        raise RuntimeError(f"source path does not exist: {args.path}")
    block = f'\n[[sources]]\nadapter = "{args.adapter}"\npath = "{args.path}"\n'
    if args.glob:
        block += f'glob = "{args.glob}"\n'
    excludes = [p for p in (getattr(args, "exclude", None) or []) if p.strip()]
    if excludes:
        block += "exclude = [" + ", ".join(f'"{p.strip()}"' for p in excludes) + "]\n"
    if getattr(args, "branch", ""):
        block += f'branch = "{args.branch}"\n'
    since = getattr(args, "since", None)
    if since is not None:  # "" is meaningful: study all history of this repo
        block += f'since = "{since}"\n'
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
    text = cfg_path.read_text()
    prev = next((s for s in cfg.sources if s.adapter == args.adapter
                 and Path(s.path).as_posix() == Path(args.path).as_posix()), None)
    if prev is not None:
        # init auto-detects sources with no options, so an explicit `source add`
        # restates the entry instead of being dropped as a duplicate
        text, old = _drop_source(text, prev.adapter, prev.path)
        if old.strip() == block.strip():
            print(f"source already configured: {args.adapter} {args.path}")
            return 0
    cfg_path.write_text(text.rstrip() + "\n" + block)
    cfg_mod.load(root)  # verify the file still parses
    if getattr(args, "no_commit", False) and (root / ".git").exists():
        _gitignore_add(root, args.path)
    print(f"source added: {args.adapter} {args.path}"
          + (f" (glob {args.glob})" if args.glob else "")
          + (f" (since {since or 'all history'})" if since is not None else "")
          + (" [local-only, not committed]" if getattr(args, "no_commit", False) else ""))
    return 0


def _drop_source(text: str, adapter: str, path: str) -> tuple[str, str]:
    """Cut one [[sources]] entry out of captain.toml; return (rest, cut block)."""
    head, *blocks = re.split(r"(?m)^\[\[sources\]\]\n", text)
    cut, keep = "", []
    for b in blocks:
        if f'adapter = "{adapter}"\n' in b and f'path = "{path}"\n' in b:
            cut = "[[sources]]\n" + b
        else:
            keep.append(b)
    return head.rstrip() + "".join(f"\n\n[[sources]]\n{b.strip()}\n" for b in keep), cut


def _gitignore_add(root: Path, path: str) -> None:
    gi = root / ".gitignore"
    line = "/" + path.strip("./").rstrip("/") + "/"
    existing = gi.read_text() if gi.exists() else ""
    if line not in existing.splitlines():
        # gitignore has no inline comments — comment on its own line
        gi.write_text(existing.rstrip("\n") + ("\n" if existing else "")
                      + f"# tsubasa source: local-only\n{line}\n")


def cmd_source_detect(args) -> int:
    """Stage 1 discovery: report candidates, optionally register the best guesses."""
    captain = cfg_mod.find_root()
    root = captain or Path.cwd()  # scanning is read-only, so it works before init too
    candidates = discover.scan(root, args.depth)
    if args.json:
        print(json.dumps(candidates, indent=2))
        return 0
    planned, dropped = discover.plan(candidates, root)
    sources = discover.git_sources(root) + planned
    if not sources:
        print(f"no knowledge sources found under {root} (depth {args.depth})")
        return 0
    _print_sources(sources, dropped)
    unknown = [c for c in candidates if not c["adapter"]]
    if unknown:
        # the vocabulary is a guess, not a verdict, so show what it could not name
        print(f"\n{len(unknown)} unclassified candidate(s), `--json` for the full map:")
        for c in sorted(unknown, key=lambda c: -c["files"])[:10]:
            print(f"  {c['files']:5d}  {c['path']}  ({', '.join(c['extensions']) or 'no ext'})")
    if not args.add:
        print("\nread-only. `tsubasa source detect --add` writes these to captain.toml")
        return 0
    if captain is None:
        raise RuntimeError("no .tsubasa/captain.toml here or above, run `tsubasa init <name>` first")
    print()
    for s in sources:
        cmd_source_add(argparse.Namespace(
            adapter=s["adapter"], path=s["path"], glob=s.get("glob", ""),
            kind=s.get("kind", ""), impact=s.get("impact", ""),
            exclude=s.get("exclude", []),
            branch="", pull=False, no_commit=False))
    return 0


def cmd_source_list(args) -> int:
    _, cfg, _ = _ctx()
    if not cfg.sources:
        print("no sources configured")
    for s in cfg.sources:
        print(f"{s.adapter:10s} {s.path}"
              + (f"  glob={s.glob}" if s.glob else "")
              + (f"  exclude={','.join(s.exclude)}" if s.exclude else "")
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
    if args.timeline:
        # sequence, not snapshot: replayed from the event log, so the code
        # snapshot (current-state only) plays no part either way
        print(query_mod.timeline(store, args.text, hops=args.hops,
                                 limit=args.limit, as_of=args.as_of))
        return 0
    if args.as_of:
        # temporal recall: the graph as the captain knew it on that date.
        # The code snapshot is current-state only, so it is excluded.
        entities, relations, _ = assemble.replay(store, as_of=args.as_of)
        events = {e.id: e for e in store.load_events() if e.ts[:10] <= args.as_of}
        vocab = query_mod.vocabulary(entities, events)
        text, matched = _semantic_match(root, args, entities, events, vocab)
        print(f"# knowledge as of {args.as_of} (code snapshot excluded — it is current-state only)")
        print(query_mod.serialize(entities, relations, events, matched, hops=args.hops,
                                  text=text, vocab=vocab))
        if not matched:
            _print_vocab_hint(vocab, text)
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
    vocab = query_mod.vocabulary(merged, events)
    text, matched = _semantic_match(root, args, merged, events, vocab)
    print(query_mod.serialize(merged, relations + code_relations, events, matched, hops=args.hops,
                              text=text, vocab=vocab))
    if not matched:
        _print_vocab_hint(vocab, text)
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


def _semantic_match(root, args, entities, events, vocab):
    """(text, matched entities) after the semantic escalation ladder.

    Trigger contract: lexical pass first; escalate unless the title match is
    STRONG, i.e. at least one hit rides a discriminating token
    (`titles_discriminate`). An empty title block and a block of common-stem
    near-misses are the same weakness: both leave the verdict surface without
    evidence, and entity-only matches count as weak too (generic entities
    with zero real title hits are exactly the shape that must escalate). At
    most one expansion per query; accepted vocab tokens are appended to the
    match text, never replacing the original wording. Expansion failure is
    one stderr line and the lexical result stands; the query never fails or
    blocks on it.
    """
    from . import semantic
    matched = query_mod.match_entities(entities, args.text, limit=args.limit)
    if query_mod.titles_discriminate(events, args.text, vocab):
        return args.text, matched
    extra = semantic.expand(root, args.text, vocab)
    if not extra:
        return args.text, matched
    text = f"{args.text} {' '.join(extra)}"
    return text, query_mod.match_entities(entities, text, limit=args.limit)


def _print_vocab_hint(vocab, text) -> None:
    # automatic vocab bridge: the benchmark showed callers do not run
    # `tsubasa vocab` on their own (0 of 12 sessions), so an empty match must
    # carry the bridge itself or a recorded topic silently reads as unrecorded
    hint = query_mod.vocab_hint(vocab, text)
    if hint:
        print(hint)


def cmd_vocab(args) -> int:
    """The graph's searchable tokens, filtered by stems.

    `match_entities` is lexical: a question phrased in words the graph never
    uses returns nothing. This is the bridge — the captain maps the question's
    concepts onto tokens that verifiably exist, then queries with those. The
    full list runs to thousands of tokens, so the default surface is a stem
    lookup; rare tokens are the valuable ones (a frequency-1 token picks out
    one record), which is why there is no frequency floor.
    """
    _, _, store = _ctx()
    entities = store.load_entities()
    code_entities, _ = store.load_code_graph()
    events = {e.id: e for e in store.load_events()}
    vocab = query_mod.vocabulary({**code_entities, **entities}, events)
    if args.stems:
        stems = [s.lower() for s in args.stems]
        rows = {t: c for t, c in vocab.items() if any(s in t for s in stems)}
        if not rows:
            print(f"no graph tokens match stems: {' '.join(stems)} "
                  f"({len(vocab)} tokens total). The graph does not talk about "
                  "this in these words; try other stems before concluding it "
                  "is unrecorded.")
            return 0
        print(f"# {len(rows)} of {len(vocab)} graph tokens matching: {' '.join(stems)}")
    else:
        rows = vocab
        print(f"# all {len(vocab)} graph tokens (pass stems to filter; "
              "output is large)")
    print(" ".join(f"{t}({c})" for t, c in sorted(rows.items())))
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
          f"{len(relations)} relations")
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
    return _learn(args)


def _learn(args) -> int:
    """The whole learning pipeline, which is what `study` means to a user: nobody
    remembers to chain the five passes by hand.

    Stage ORDER IS LOAD-BEARING. `resolve` merges duplicate entities, so it runs
    before `link` (anchors seeded against an id resolve later merges away are
    dangling) and before `profile` (hub summaries should describe the
    deduplicated graph). `index` must precede `link`, which has nothing to
    anchor into without a code graph.

    Every stage degrades on its own: distillation is the expensive part and a
    later stage failing must never throw it away. The exit code reports total
    failure, not partial."""
    root, cfg, store = _ctx()
    from . import distill, llm
    if not llm.claude_available(args.claude_cmd):
        raise RuntimeError(f"'{args.claude_cmd}' not found — study needs the Claude CLI")

    def run_study() -> str:
        stats: dict = {}
        events = distill.study(store, cfg, root, claude_cmd=args.claude_cmd,
                               chunk=getattr(args, "chunk", distill.CHUNK_COMMITS),
                               max_chunks=getattr(args, "max_chunks", 0),
                               model=args.model, since=args.since,
                               noise_filter=not args.no_filter, jobs=args.jobs,
                               stats=stats)
        _apply_and_regen(root, cfg, store, events)
        docs = f", {stats['docs']} doc event(s)" if stats.get("docs") else ""
        return f"{stats.get('chunks', 0)} chunks, {len(events)} event(s){docs}"

    def run_resolve() -> str:
        added = distill.resolve(store, claude_cmd=args.claude_cmd, model=args.model)
        if added:
            cmd_rebuild(args)  # the alias map only applies at replay time
        return f"{added} alias mapping(s)"

    def run_index() -> str:
        return f"{distill.index_code(cfg, root)} repo(s) indexed"

    def run_link() -> str:
        from .graph import anchors as anchors_mod
        anchors_mod.seed(store, root, cfg)
        semantic = 0
        try:  # the deterministic seeding already landed; never discard it
            semantic = distill.link_llm(store, cfg, root, claude_cmd=args.claude_cmd,
                                        model=args.model)
        except Exception as e:
            print(f"[link] semantic pass FAILED ({e})", file=sys.stderr)
        return f"{len(store.load_anchors())} anchor(s) ({semantic} semantic)"

    def run_profile() -> str:
        done = distill.profile(store, claude_cmd=args.claude_cmd, model=args.model,
                               top=getattr(args, "top", 12))
        if done:
            entities = store.load_entities()
            assemble.apply_profiles(entities, store.load_profiles())
            store.save_entities(entities)
            tiers.generate(store, cfg)
        return f"{done} hub profile(s)"

    summary: list[tuple[str, str]] = []
    ok, failed = [], []

    def stage(name: str, off_flag: str, fn) -> None:
        # distillation itself has no opt-out: it is what the command is for
        if off_flag and getattr(args, off_flag[2:].replace("-", "_"), False):
            summary.append((name, f"skipped ({off_flag})"))
            return
        try:
            summary.append((name, fn()))
            ok.append(name)
        except ValueError:  # bad input (e.g. --since), not a degraded stage
            raise
        except Exception as e:
            print(f"[{name}] FAILED ({e})", file=sys.stderr)
            summary.append((name, f"FAILED ({e})"))
            failed.append(name)

    stage("study", "", run_study)
    stage("resolve", "--no-resolve", run_resolve)
    stage("index", "--no-index", run_index)      # deterministic AST, no LLM
    stage("link", "--no-link", run_link)
    stage("profile", "--no-profile", run_profile)
    print("\nlearning pipeline:")
    for name, text in summary:
        print(f"[{name}]".ljust(10) + text)
    return 1 if failed and not ok else 0


def cmd_link(args) -> int:
    root, cfg, store = _ctx()
    from .graph import anchors as anchors_mod
    anchors_mod.seed(store, root, cfg)
    if args.llm:
        from . import distill, llm
        if not llm.claude_available(args.claude_cmd):
            raise RuntimeError(f"'{args.claude_cmd}' not found — --llm needs the Claude CLI")
        added = distill.link_llm(store, cfg, root, claude_cmd=args.claude_cmd, model=args.model,
                                 only=args.repo)
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
    if cfg.schema_version < SCHEMA_VERSION:
        stamp = ("no schema stamp (built before versioning)" if cfg.schema_version == 1
                 else f"schema {cfg.schema_version}")
        print(f"SCHEMA captain.toml has {stamp}, this CLI writes {SCHEMA_VERSION} — "
              "run `tsubasa upgrade`")
        problems += 1
    elif cfg.schema_version > SCHEMA_VERSION:
        print(f"SCHEMA captain.toml is schema {cfg.schema_version}, newer than this CLI "
              f"({SCHEMA_VERSION}) — upgrade tsubasa itself")
        problems += 1
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

def _learn_flags(sp) -> None:
    """Opt-outs for the learning pipeline. Opt-outs only: a stage nobody
    remembers to enable is a stage that never runs."""
    sp.add_argument("--no-resolve", action="store_true",
                    help="skip merging duplicate entities")
    sp.add_argument("--no-index", action="store_true",
                    help="skip the code-only graphify index pass (deterministic, no LLM)")
    sp.add_argument("--no-link", action="store_true",
                    help="skip anchoring entities to code nodes (seed + semantic pass)")
    sp.add_argument("--no-profile", action="store_true",
                    help="skip hub entity profiles")


def build_parser() -> argparse.ArgumentParser:
    from . import distill as distill_mod  # only for study's defaults
    p = argparse.ArgumentParser(prog="tsubasa", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers()

    sp = sub.add_parser("init", help="scaffold .tsubasa/, detect sources, ingest")
    sp.add_argument("name")
    sp.add_argument("--role", default="Engineering Director")
    sp.add_argument("--domains", default="", help="comma-separated domain list")
    sp.add_argument("--no-detect", action="store_true", help="scaffold only: don't probe for sources")
    sp.add_argument("--depth", type=int, default=discover.DEPTH,
                    help=f"detection: directory levels walked inside each repo (default {discover.DEPTH})")
    sp.add_argument("--no-ingest", action="store_true", help="register detected sources but don't ingest yet")
    sp.add_argument("--no-llm", action="store_true",
                    help="skip the topology pass: classify sources offline only")
    sp.add_argument("--claude-cmd", default="claude")
    sp.add_argument("--model", default=topology.MODEL,
                    help=f"topology pass model (default {topology.MODEL})")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser(
        "upgrade", help="bring an existing captain up to this schema (idempotent)",
        description="Re-run everything `init` writes beyond captain.toml itself: the "
                    "source graph, the memory tiers, the CLAUDE.md include, the schema "
                    "stamp. Safe to re-run; a captain that needs nothing says so. "
                    "captain.toml is only edited with --add.")
    sp.add_argument("--add", action="store_true",
                    help="also register detected sources missing from captain.toml "
                         "(existing entries are never rewritten)")
    sp.add_argument("--no-detect", action="store_true", help="skip probing for missing sources")
    sp.add_argument("--llm", action="store_true",
                    help="also run the topology pass when emitting the source graph "
                         "(one claude call; offline by default)")
    sp.add_argument("--depth", type=int, default=discover.DEPTH,
                    help=f"detection: directory levels walked inside each repo (default {discover.DEPTH})")
    sp.add_argument("--claude-cmd", default="claude")
    sp.add_argument("--model", default=topology.MODEL,
                    help=f"--llm topology pass model (default {topology.MODEL})")
    sp.set_defaults(func=cmd_upgrade)

    sp = sub.add_parser("ingest", help="run source adapters")
    sp.add_argument("adapter", nargs="?", help="only this adapter")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("pull", help="fetch every git source, re-ingest, optionally study")
    sp.add_argument("--study", action="store_true",
                    help="also distil the new history, resuming from the last watermark")
    sp.add_argument("--claude-cmd", default="claude")
    sp.add_argument("--model", default=distill_mod.STUDY_MODEL)
    sp.add_argument("--since", default=None, metavar="DATE",
                    help="--study window in git date syntax; overrides the watermark and "
                         "every source's own `since` (default: resume from the watermark, "
                         f"else the source's `since`, else {distill_mod.DEFAULT_SINCE})")
    sp.add_argument("--no-filter", action="store_true",
                    help="--study keeps bot, merge and dependency/CI-only commits")
    sp.add_argument("--jobs", type=int, default=distill_mod.STUDY_JOBS, metavar="N",
                    help=f"--study concurrent claude calls (default {distill_mod.STUDY_JOBS})")
    _learn_flags(sp)
    sp.set_defaults(func=cmd_pull)

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
    sp.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="skip files matching this glob (relative to path); repeatable")
    sp.add_argument("--branch", default="", help="git: read history from this branch (default: repo's default branch)")
    sp.add_argument("--pull", action="store_true", help="git: fast-forward the branch on every ingest")
    sp.add_argument("--since", default=None, metavar="DATE",
                    help="git: `study` history window for THIS repo ('10y', '18 months', "
                         "'2015-01-01'); '' studies all of it. A deep repo whose decisions "
                         f"live in old commits wants more than the {distill_mod.DEFAULT_SINCE} default")
    sp.add_argument("--no-commit", action="store_true",
                    help="keep source files out of the captain repo (gitignored); only distilled events are committed")
    sp.add_argument("--kind", default="", help="doc adapter: what these documents are (principle, runbook, rfc…)")
    sp.add_argument("--impact", default="", choices=["", "high", "medium", "low"],
                    help="doc adapter: impact for distilled events (high keeps principles hot)")
    sp.set_defaults(func=cmd_source_add)
    sp = so.add_parser("detect", help="classify knowledge dirs in the workspace (read-only by default)")
    sp.add_argument("--add", action="store_true", help="append what was found to captain.toml (idempotent)")
    sp.add_argument("--depth", type=int, default=discover.DEPTH,
                    help=f"directory levels walked inside each repo (default {discover.DEPTH})")
    sp.add_argument("--json", action="store_true",
                    help="dump the raw candidate map with evidence, for a downstream classifier")
    sp.set_defaults(func=cmd_source_detect)
    sp = so.add_parser("list")
    sp.set_defaults(func=cmd_source_list)

    sp = sub.add_parser("query", help="knowledge lookup with citations")
    sp.add_argument("text")
    sp.add_argument("--hops", type=int, default=2)
    sp.add_argument("--limit", type=int, default=5)
    sp.add_argument("--as-of", default="", metavar="YYYY-MM-DD",
                    help="temporal recall: the graph as known on that date")
    sp.add_argument("--timeline", action="store_true",
                    help="what happened when: resulting state first, then the events in "
                         "ascending time order with transitions marked and reverts/"
                         "supersessions walked in. Composes with --as-of")
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("vocab", help="searchable graph tokens, filtered by stems; "
                        "bridge a question's wording onto words the graph uses")
    sp.add_argument("stems", nargs="*",
                    help="substrings to filter by, e.g. `tsubasa vocab stat lsn`; "
                         "omit for the full list")
    sp.set_defaults(func=cmd_vocab)

    for name, fn, extra in (
        ("study", cmd_study, "full learning pipeline: distil -> resolve -> index -> link -> profile"),
        ("resolve", cmd_resolve, "merge duplicate entities via alias map"),
        ("profile", cmd_profile, "generate profiles for hub entities"),
    ):
        sp = sub.add_parser(name, help=extra)
        sp.add_argument("--claude-cmd", default="claude")
        sp.add_argument("--model", default="haiku" if name == "study" else "")
        if name == "study":
            sp.add_argument("--chunk", type=int, default=250)
            sp.add_argument("--max-chunks", type=int, default=0, help="cap chunks per repo (newest kept)")
            sp.add_argument("--since", default=None, metavar="DATE",
                            help="history window in git date syntax ('3y', '18 months', "
                                 "'2015-01-01'); '' studies all history. Overrides every "
                                 "source's own `since` and the watermark. Default: resume "
                                 "after the last studied commit, else the source's `since` "
                                 f"(`source add --since`), else {distill_mod.DEFAULT_SINCE}")
            sp.add_argument("--no-filter", action="store_true",
                            help="keep bot commits, dependency/CI-only commits and "
                                 "chore|ci|build|deps: subjects (dropped by default)")
            sp.add_argument("--jobs", type=int, default=distill_mod.STUDY_JOBS, metavar="N",
                            help=f"concurrent claude calls (default {distill_mod.STUDY_JOBS})")
            _learn_flags(sp)
        if name == "profile":
            sp.add_argument("--top", type=int, default=12)
        sp.set_defaults(func=fn)

    sp = sub.add_parser("link", help="seed anchors between the knowledge graph and code indexes")
    sp.add_argument("--llm", action="store_true",
                    help="also run the semantic pass: LLM ties entities to code nodes (node names only, few calls)")
    sp.add_argument("--repo", default="",
                    help="scope the semantic pass to one repo's code index (retry a failed repo alone)")
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
