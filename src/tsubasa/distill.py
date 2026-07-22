"""Experience-density passes: study, resolve, profile.

These are what turn a thin graph into the 25-year veteran — the cookbook
pipeline (extract → resolve → summarize) run headlessly over the full
history via `claude -p`, in chunks, at extraction-model cost.

    study    git history + doc prose -> distilled events (source: study)
    resolve  duplicate entities -> alias map (applied at assembly, log untouched)
    profile  hub entities -> summary + key facts overlay
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from . import llm
from .config import CaptainConfig, SourceConfig
from .models import Event, Ref, now_iso, slugify
from .storage import Store

STUDY_MODEL = "haiku"      # extraction scale; resolve/profile use the default model
CHUNK_COMMITS = 250
DOC_CHARS_CAP = 8000       # per-file cap so a huge doc doesn't blow the extraction budget

STUDY_PROMPT = """\
You are distilling git history into an engineering knowledge graph for the
repo "{repo}". Below are commits (oldest first), format: sha|date|subject.

<commits>
{commits}
</commits>

Extract the 3-8 SIGNIFICANT themes: migrations, incident fixes, major
features, performance/security work, architectural changes. Skip routine
chores, version bumps, typo fixes. Ground every theme in its commit shas.

Known domains (use these when applicable): {domains}
Existing entity ids to REUSE when the theme touches them: {entity_ids}

Return ONLY a JSON array (no prose):
[{{"title": "...", "summary": "2-3 sentences, past tense, concrete",
  "type": "note|decision|incident", "impact": "high|medium|low",
  "domains": ["..."], "date": "YYYY-MM-DD (of the latest relevant commit)",
  "commits": ["sha", ...],
  "entities": [{{"id": "svc-x|feat-x|ext-x", "type": "service|feature|module|external", "name": "...", "description": "..."}}],
  "relations": [{{"source": "id", "predicate": "verb_phrase", "target": "id"}}]}}]
"""

STUDY_DOC_PROMPT = """\
You are distilling a curated knowledge doc into an engineering knowledge
graph. The doc below may discuss services, external integrations, database
tables, or other things already tracked as entities — reuse their ids when
the doc clearly refers to the same thing, and propose new entities (e.g.
ext-<slug> for a third-party integration) for anything genuinely new.

<doc kind="{kind}" path="{path}">
{text}
</doc>

Known domains (use these when applicable): {domains}
Existing entity ids to REUSE when the doc touches them: {entity_ids}

Return ONLY a JSON object (no prose), or {{}} if nothing extractable:
{{"title": "...", "summary": "1-2 sentences, factual",
  "domains": ["..."],
  "entities": [{{"id": "svc-x|ext-x|feat-x|module-x", "type": "service|external|feature|module", "name": "...", "description": "..."}}],
  "relations": [{{"source": "id", "predicate": "verb_phrase", "target": "id"}}]}}
"""

RESOLVE_PROMPT = """\
These are "{etype}" entities from an engineering knowledge graph, format:
id | name | aliases | description

<entities>
{listing}
</entities>

Cluster ids that refer to the SAME real-world thing (use descriptions —
do not merge things that merely share a word). Most entities are already
distinct; only report clusters with 2+ members. The canonical id should be
the most complete/established one.

Return ONLY a JSON array (no prose), empty array if nothing to merge:
[{{"canonical": "<id>", "duplicates": ["<id>", ...]}}]
"""

PROFILE_PROMPT = """\
Write a knowledge-graph profile for this entity of an engineering org.

Entity: {name} ({etype})
Description: {description}

Events that touched it (newest first):
{events}

Its graph relations:
{relations}

Return ONLY a JSON object (no prose):
{{"summary": "2-3 sentence factual profile, grounded in the events",
  "key_facts": ["3-5 atomic facts, each traceable to an event above"]}}
"""


# ------------------------------------------------------------------ study

def study(store: Store, cfg: CaptainConfig, root: Path, claude_cmd: str = "claude",
          chunk: int = CHUNK_COMMITS, max_chunks: int = 0, model: str = STUDY_MODEL,
          log=print) -> list[Event]:
    """Distill full git history of every git source, and the prose in every
    doc source, into events."""
    existing_ids = sorted(store.load_entities())
    new_events: list[Event] = []
    repos = [s for s in cfg.sources if s.adapter == "git"]
    for src in repos:
        repo = (root / src.path).resolve()
        if not (repo / ".git").exists():
            continue
        lines = _git_log(repo)
        chunks = [lines[i:i + chunk] for i in range(0, len(lines), chunk)]
        if max_chunks:
            chunks = chunks[-max_chunks:]  # newest history first in priority
        log(f"[study] {repo.name}: {len(lines)} commits in {len(chunks)} chunk(s)")
        for n, chunk_lines in enumerate(chunks, 1):
            span = f"{chunk_lines[0].split('|')[1]}..{chunk_lines[-1].split('|')[1]}"
            log(f"[study] {repo.name} chunk {n}/{len(chunks)}: distilling {len(chunk_lines)} commits ({span})...")
            try:
                raw = llm.run_claude(
                    STUDY_PROMPT.format(
                        repo=repo.name, commits="\n".join(chunk_lines),
                        domains=", ".join(cfg.domains) or "any",
                        entity_ids=", ".join(existing_ids[:60]) or "none yet",
                    ),
                    model=model, claude_cmd=claude_cmd,
                )
                items = llm.extract_json(raw)
            except llm.LLMError as e:
                log(f"[study] {repo.name} chunk {n}/{len(chunks)}: SKIPPED ({e})")
                continue
            made = 0
            for item in items if isinstance(items, list) else []:
                ev = _study_event(item, repo.name, repo)
                if ev is not None and not store.has_event(ev.id):
                    store.append_event(ev)
                    new_events.append(ev)
                    made += 1
            log(f"[study] {repo.name} chunk {n}/{len(chunks)}: {made} event(s)")
    doc_sources = [s for s in cfg.sources if s.adapter == "doc"]
    for src in doc_sources:
        new_events.extend(_study_docs(store, src, root, cfg, existing_ids, claude_cmd, model, log))
    return new_events


def _git_log(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--reverse", "--date=short", "--format=%h|%ad|%s"],
        capture_output=True, text=True, timeout=120,
    )
    return [l for l in out.stdout.splitlines() if l.strip()]


def _study_docs(store: Store, src: SourceConfig, root: Path, cfg: CaptainConfig,
                existing_ids: list[str], claude_cmd: str, model: str, log) -> list[Event]:
    """Distill the prose of one doc source, file by file. `.toon` files are
    skipped — DocAdapter already turns them into structured entities
    deterministically (see adapters/docs.py); this pass is for free text
    a heading/table parser can't pull entities out of."""
    base = (root / src.path).resolve()
    if not base.is_dir():
        return []
    pattern = src.glob or "**/*.md"
    kind = slugify(str(src.options.get("kind", "doc"))) or "doc"
    paths = [p for p in sorted(base.glob(pattern)) if p.is_file() and p.suffix != ".toon"]
    if not paths:
        return []
    log(f"[study] {src.path}: distilling {len(paths)} doc(s)...")
    new_events: list[Event] = []
    for path in paths:
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        text = path.read_text(errors="replace")
        digest = hashlib.sha1(text.encode()).hexdigest()[:12]
        try:
            raw = llm.run_claude(
                STUDY_DOC_PROMPT.format(
                    kind=kind, path=rel, text=text[:DOC_CHARS_CAP],
                    domains=", ".join(cfg.domains) or "any",
                    entity_ids=", ".join(existing_ids[:60]) or "none yet",
                ),
                model=model, claude_cmd=claude_cmd,
            )
            item = llm.extract_json(raw)
        except llm.LLMError as e:
            log(f"[study] {rel}: SKIPPED ({e})")
            continue
        ev = _study_doc_event(item, kind, rel, digest) if isinstance(item, dict) else None
        if ev is not None and not store.has_event(ev.id):
            store.append_event(ev)
            new_events.append(ev)
    log(f"[study] {src.path}: {len(new_events)} event(s)")
    return new_events


def _study_doc_event(item: dict, kind: str, rel: str, digest: str) -> Event | None:
    title = str(item.get("title", "")).strip()
    if not title:
        return None
    return Event(
        id=f"evt-study-{kind}-{slugify(title)}-{digest[:8]}",
        type="note", ts=now_iso()[:10],
        title=f"{kind}: {title}",
        summary=str(item.get("summary", ""))[:600],
        impact="low",
        domains=[str(d) for d in item.get("domains", [])][:4],
        trust="low",  # doc-derived, verify in code — same hierarchy as adr.py's non-ADR docs
        refs=[Ref(kind="doc", id=rel)],
        source="study",
        derived_entities=[e for e in item.get("entities", []) if isinstance(e, dict) and e.get("id")][:8],
        derived_relations=[r for r in item.get("relations", [])
                           if isinstance(r, dict) and r.get("source") and r.get("target")][:10],
    )


def _study_event(item: dict, repo_name: str, repo: Path) -> Event | None:
    title = str(item.get("title", "")).strip()
    date = str(item.get("date", ""))[:10]
    if not title or len(date) != 10:
        return None
    etype = item.get("type", "note")
    commit_shas = [str(c)[:12] for c in item.get("commits", [])[:8]]
    files: list[str] = []
    for sha in commit_shas:
        for f in _changed_files(repo, sha):
            if f not in files:
                files.append(f)
    return Event(
        id=f"evt-{date.replace('-', '')}-{slugify(repo_name)}-{slugify(title)}",
        type=etype if etype in ("note", "decision", "incident") else "note",
        ts=date, title=f"{repo_name}: {title}",
        summary=str(item.get("summary", ""))[:600],
        impact=item.get("impact", "low") if item.get("impact") in ("high", "medium", "low") else "low",
        domains=[str(d) for d in item.get("domains", [])][:4],
        refs=[Ref(kind="commit", id=c) for c in commit_shas]
            + [Ref(kind="file", id=f) for f in files[:10]],
        source="study",
        derived_entities=[e for e in item.get("entities", []) if isinstance(e, dict) and e.get("id")][:6],
        derived_relations=[r for r in item.get("relations", [])
                           if isinstance(r, dict) and r.get("source") and r.get("target")][:8],
    )


def _changed_files(repo: Path, sha: str, limit: int = 10) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", sha],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        return []
    return [f for f in out.stdout.splitlines() if f.strip()][:limit]


# ------------------------------------------------------------------ index (graphify)

# Code-only graphify pipeline: detect -> AST extract (code files ONLY) ->
# build -> cluster -> graph.json. Runs graphify's own Python engine directly —
# deterministic, local, zero LLM. Non-code files (docs, data/, *.txt dumps)
# are excluded by design: semantic doc extraction is a separate, explicit,
# in-session decision on curated doc dirs, never a side effect of indexing.
CODE_INDEX_SCRIPT = r"""
import json, sys
from pathlib import Path
from graphify.detect import detect
from graphify.extract import collect_files, extract
from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.export import to_json

out_path = sys.argv[1]  # temp json; tsubasa converts to TOON at the captain root
d = detect(Path('.'))
code = []
for f in d.get('files', {}).get('code', []):
    p = Path(f)
    code.extend(collect_files(p) if p.is_dir() else [p])
if not code:
    print('no code files'); sys.exit(0)
res = extract(code, cache_root=Path('.'))
G = build_from_json({'nodes': res['nodes'], 'edges': res['edges'], 'hyperedges': []},
                    root='.', directed=False)
if G.number_of_nodes() == 0:
    print('empty graph'); sys.exit(1)
communities = cluster(G)
to_json(G, communities, out_path)
print(f'{G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(communities)} communities')
"""


def graphify_python(log=print) -> str | None:
    """Resolve (installing if needed) the graphify engine's interpreter."""
    log("[index] resolving graphify engine (first run may install it)...")
    def probe() -> str | None:
        try:
            out = subprocess.run(
                ["uv", "tool", "run", "--from", "graphifyy", "python", "-c",
                 "import graphify, sys; print(sys.executable)"],
                capture_output=True, text=True, timeout=300)
            return out.stdout.strip() or None
        except (subprocess.SubprocessError, OSError):
            return None
    py = probe()
    if py is None:
        log("[index] graphify engine not available (needs uv; tried graphifyy) — skipped")
    return py


def index_code(cfg: CaptainConfig, root: Path, only: str = "",
               timeout: int = 600, log=print) -> int:
    """Build/refresh code-only graphify indexes for fleet repos.

    Deterministic and free: code sources are indexed from AST alone, so no
    model, no agents, no permissions. graph.json per repo is the artifact
    the anchor layer and unified query consume."""
    import json as json_mod
    import tempfile
    from . import codegraph
    py = graphify_python(log)
    if py is None:
        return 0
    done = 0
    for src in cfg.sources:
        if src.adapter != "git":
            continue
        if only and Path(src.path).name != Path(only).name:
            continue
        repo = (root / src.path).resolve()
        if not (repo / ".git").exists():
            continue
        log(f"[index] {repo.name}: indexing (AST, code-only)...")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp = tf.name
        try:
            out = subprocess.run([py, "-c", CODE_INDEX_SCRIPT, tmp], cwd=repo,
                                 capture_output=True, text=True, timeout=timeout)
            msg = (out.stdout.strip() or out.stderr.strip()[:200]).splitlines()
            log(f"[index] {repo.name}: {msg[-1] if msg else 'no output'}")
            tmp_path = Path(tmp)
            if tmp_path.stat().st_size > 2:
                graph = json_mod.loads(tmp_path.read_text(errors="replace"))
                codegraph.save(root, repo.name, graph)  # TOON at rest, JSON runtime
                done += 1
        except (subprocess.SubprocessError, json_mod.JSONDecodeError, OSError) as e:
            log(f"[index] {repo.name}: FAILED ({e})")
        finally:
            Path(tmp).unlink(missing_ok=True)
            _clean_engine_cache(repo)
    return done


def _clean_engine_cache(repo: Path) -> None:
    """The engine leaves an AST cache under <repo>/graphify-out as a side
    effect; remove it so fleet repos stay pristine. A user-built full
    graphify-out (has graph.json / report) is left alone."""
    import shutil
    gfo = repo / "graphify-out"
    if gfo.is_dir() and not (gfo / "graph.json").exists() and not (gfo / "GRAPH_REPORT.md").exists():
        shutil.rmtree(gfo, ignore_errors=True)


# ------------------------------------------------------------------ link --llm

LINK_PROMPT = """\
You connect an engineering org's knowledge graph to its code graphs.
For each ENTITY below (org concepts: services, features, goals, decisions),
pick which CODE NODES implement or embody it — if any.

<entities>
{entities}
</entities>

<code_nodes repo="{repo}">
{nodes}
</code_nodes>

Rules:
- Only link when the node clearly implements/embodies the entity (the entity
  description and its facts are your evidence). No speculative links.
- Node names must be copied VERBATIM from the code_nodes list — never invent.
- 0-4 nodes per entity; most entities should get zero.

Return ONLY a JSON array (no prose), empty if nothing:
[{{"entity": "<entity-id>", "nodes": ["<node name verbatim>", ...]}}]
"""


def link_llm(store, cfg: CaptainConfig, root: Path, claude_cmd: str = "claude",
             model: str = "", log=print) -> int:
    """Semantic anchor pass: LLM ties entities to code nodes that lexical
    seeding can't match. Scales with #entities, not #files — node NAMES only,
    never source code. Results land in anchors.toon (by=link), reviewable."""
    from .graph import graphify_bridge
    entities = store.load_entities()
    anchors = store.load_anchors()
    anchored = {(a["entity"], a["repo"], a["node"]) for a in anchors}
    candidates = [e for e in entities.values()
                  if e.type in ("service", "feature", "goal", "adr", "module")
                  and e.status == "active"]
    if not candidates:
        return 0
    ent_lines = "\n".join(
        f"- {e.id} ({e.type}): {e.description[:150]}"
        + (f" | facts: {'; '.join(e.key_facts[:2])[:150]}" if e.key_facts else "")
        for e in sorted(candidates, key=lambda e: e.id))
    added = 0
    for repo_name, g in graphify_bridge.load_graphs(root, cfg):
        nodes = [n for n in g.get("nodes", []) if isinstance(n, dict)]
        # shortlist: highest-degree node names, not all thousands
        degree: dict[str, int] = {}
        for e_ in (g.get("edges") or g.get("links") or []):
            if isinstance(e_, dict):
                for end in (str(e_.get("source", "")), str(e_.get("target", ""))):
                    degree[end] = degree.get(end, 0) + 1
        by_id = {str(n.get("id", "")): graphify_bridge._node_name(n) for n in nodes}
        ranked = sorted(by_id, key=lambda i: -degree.get(i, 0))
        names = list(dict.fromkeys(by_id[i] for i in ranked[:300] if by_id[i]))
        if not names:
            continue
        log(f"[link-llm] {repo_name}: proposing links for {len(candidates)} entities "
            f"against {len(names)} node names...")
        try:
            proposals = llm.extract_json(llm.run_claude(
                LINK_PROMPT.format(entities=ent_lines, repo=repo_name, nodes="\n".join(names)),
                model=model, claude_cmd=claude_cmd))
        except llm.LLMError as e:
            log(f"[link-llm] {repo_name}: SKIPPED ({e})")
            continue
        valid_names = set(names)
        for p in proposals if isinstance(proposals, list) else []:
            eid = p.get("entity", "")
            if eid not in entities:
                continue
            for node in p.get("nodes", [])[:4]:
                if node in valid_names and (eid, repo_name, node) not in anchored:
                    anchors.append({"entity": eid, "repo": repo_name, "node": node, "by": "link"})
                    anchored.add((eid, repo_name, node))
                    added += 1
                    log(f"[link-llm] {eid} <=> {node} [{repo_name}]")
    if added:
        store.save_anchors(anchors)
    return added


# ------------------------------------------------------------------ resolve

def resolve(store: Store, claude_cmd: str = "claude", model: str = "", log=print) -> int:
    """Cluster duplicate entities; returns number of new alias mappings."""
    entities = store.load_entities()
    aliases = store.load_aliases()
    by_type: dict[str, list] = {}
    for e in entities.values():
        by_type.setdefault(e.type, []).append(e)
    added = 0
    for etype, group in sorted(by_type.items()):
        if len(group) < 2 or etype in ("adr", "goal", "task"):  # ids are convention-stable
            continue
        listing = "\n".join(
            f"{e.id} | {e.name} | {','.join(e.aliases) or '-'} | {e.description[:120]}"
            for e in sorted(group, key=lambda e: e.id)
        )
        try:
            clusters = llm.extract_json(llm.run_claude(
                RESOLVE_PROMPT.format(etype=etype, listing=listing),
                model=model, claude_cmd=claude_cmd,
            ))
        except llm.LLMError as e:
            log(f"[resolve] {etype}: SKIPPED ({e})")
            continue
        ids = {e.id for e in group}
        for cl in clusters if isinstance(clusters, list) else []:
            canonical = cl.get("canonical", "")
            if canonical not in ids:
                continue
            for dup in cl.get("duplicates", []):
                if dup in ids and dup != canonical and dup not in aliases:
                    aliases[dup] = canonical
                    added += 1
                    log(f"[resolve] {dup} -> {canonical}")
    if added:
        store.save_aliases(aliases)
    return added


# ------------------------------------------------------------------ profile

def profile(store: Store, claude_cmd: str = "claude", model: str = "", top: int = 12,
            min_events: int = 3, log=print) -> int:
    """Generate profiles for hub entities (highest degree, enough history)."""
    entities = store.load_entities()
    relations = store.load_relations()
    events = {e.id: e for e in store.load_events()}
    degree: dict[str, int] = {}
    for r in relations:
        for end in (r.source, r.target):
            degree[end] = degree.get(end, 0) + 1
    hubs = sorted(
        (e for e in entities.values()
         if len(e.source_events) >= min_events and e.type not in ("secret-ref",)),
        key=lambda e: -(degree.get(e.id, 0) + len(e.source_events)),
    )[:top]
    profiles = store.load_profiles()
    done = 0
    for e in hubs:
        ev_lines = []
        for ev_id in reversed(e.source_events[-10:]):
            ev = events.get(ev_id)
            if ev:
                ev_lines.append(f"- {ev.ts[:10]} [{ev.type}] {ev.title}: {ev.summary[:200]}")
        rel_lines = [f"({r.source}) --[{r.predicate}]--> ({r.target})"
                     for r in relations if e.id in (r.source, r.target)][:15]
        try:
            prof = llm.extract_json(llm.run_claude(
                PROFILE_PROMPT.format(name=e.name, etype=e.type, description=e.description,
                                      events="\n".join(ev_lines) or "-",
                                      relations="\n".join(rel_lines) or "-"),
                model=model, claude_cmd=claude_cmd,
            ))
        except llm.LLMError as err:
            log(f"[profile] {e.id}: SKIPPED ({err})")
            continue
        if isinstance(prof, dict) and prof.get("summary"):
            profiles[e.id] = {"id": e.id, "summary": str(prof["summary"])[:500],
                              "key_facts": [str(f)[:200] for f in prof.get("key_facts", [])][:5]}
            done += 1
            log(f"[profile] {e.id} ✓")
    if done:
        store.save_profiles(profiles)
    return done
