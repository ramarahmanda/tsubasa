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
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

from . import llm
from .config import CaptainConfig, SourceConfig
from .models import Event, Ref, now_iso, slugify
from .storage import Store

STUDY_MODEL = "haiku"      # extraction scale; resolve/profile use the default model
CHUNK_COMMITS = 250
DOC_CHARS_CAP = 8000       # per-file cap so a huge doc doesn't blow the extraction budget
STUDY_JOBS = 6             # concurrent `claude -p` calls
DEFAULT_SINCE = "3y"       # full history is decades of noise; recent history is the knowledge

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
          since: str | None = None, noise_filter: bool = True, jobs: int = STUDY_JOBS,
          stats: dict | None = None, log=print) -> list[Event]:
    """Distill recent git history of every git source, and the prose in every
    doc source, into events.

    `since=None` means "resume from the watermark, else the default window";
    any explicit value (including "" for all history) overrides the watermark.
    The window itself resolves per repo: explicit `since` wins, else the
    source's own `since`, else DEFAULT_SINCE (see `_window`).
    Chunks are distilled concurrently but appended in chunk order, so the
    resulting event log is identical to a sequential run.

    `stats` collects counts the caller wants to summarize (chunks, docs)."""
    counted = stats if stats is not None else {}
    from .adapters import gitlog
    existing_ids = sorted(store.load_entities())
    new_events: list[Event] = []
    state = store.load_state()
    marks = state.setdefault("study", {})
    dirty = False
    log = _serialized(log)  # worker threads log; keep lines from interleaving
    repos = [s for s in cfg.sources if s.adapter == "git"]
    for src in repos:
        repo = (root / src.path).resolve()
        if not (repo / ".git").exists():
            continue
        requested, from_source = _window(since, src)
        window = since_arg(requested)
        if window and not _since_understood(repo, window):
            raise ValueError(
                f"{'source since' if from_source else '--since'} {requested!r} is not a "
                "date git understands (try '3y', '18 months', '2015-01-01', "
                "or '' for all history)")
        key = f"git:{src.path}"
        mark = marks.get(key) or {}
        after = _watermark(repo, mark, since, window, noise_filter, log)
        # same rev the git adapter reads, so `pull --study` sees what pull fetched
        rev = gitlog.local_rev(repo, str(src.options.get("branch") or ""))
        commits = _git_log(repo, since=window, after=after, rev=rev)
        files_by_sha = {c.sha: list(c.files) for c in commits}
        dropped = {"bot": 0, "merge": 0, "mechanical": 0}
        if noise_filter:
            kept = []
            for c in commits:
                reason = _noise(c)
                if reason:
                    dropped[reason] += 1
                else:
                    kept.append(c)
            commits = kept
        # every repo line states the window it used; the origin is named only
        # when it is the source's own setting, which is what distinguishes a
        # per-source window from the global default
        where = " (source since)" if from_source else ""
        note = f"{requested} window{where}" if window else f"all history{where}"
        scope = f"resuming from {after} ({mark.get('date', '?')}), {note}" if after else note
        filtered = (f" ({sum(dropped.values()):,} filtered: {dropped['bot']:,} bot, "
                    f"{dropped['merge']:,} merge, {dropped['mechanical']:,} mechanical)"
                    if noise_filter else " (filter off)")
        if not commits:
            log(f"[study] {repo.name}: {scope}, nothing new")
            continue
        chunks = [commits[i:i + chunk] for i in range(0, len(commits), chunk)]
        truncated = bool(max_chunks) and len(chunks) > max_chunks
        if max_chunks:
            chunks = chunks[-max_chunks:]  # newest history first in priority
        # Chunks already distilled in an earlier run, identified by their last
        # commit. The contiguous watermark below cannot express "3 failed but
        # 4-34 succeeded", so those would otherwise all be re-paid for on resume.
        # Identity is the chunk boundary, so a changed window or filter reshuffles
        # boundaries, nothing matches, and the work is redone: conservative, never wrong.
        done: set[str] = set(mark.get("done") or [])
        pending = [(i, g) for i, g in enumerate(chunks, 1) if g[-1].sha not in done]
        skipped = len(chunks) - len(pending)
        total = len(chunks)
        counted["chunks"] = counted.get("chunks", 0) + len(pending)
        log(f"[study] {repo.name}: {scope}, {len(commits):,} commits{filtered}, {total} chunk(s)"
            + (f", {skipped} already distilled" if skipped else ""))
        if not pending:
            log(f"[study] {repo.name}: every chunk already distilled")
            continue

        def distill_chunk(numbered: tuple[int, list[_Commit]]) -> list[Event] | None:
            """None means the chunk failed; the watermark must not pass it."""
            n, group = numbered
            span = f"{group[0].date}..{group[-1].date}"
            log(f"[study] {repo.name} chunk {n}/{total}: distilling {len(group)} commits ({span})...")
            try:
                items = llm.extract_json(llm.run_claude(
                    STUDY_PROMPT.format(
                        repo=repo.name, commits="\n".join(c.line for c in group),
                        domains=", ".join(cfg.domains) or "any",
                        entity_ids=", ".join(existing_ids[:60]) or "none yet",
                    ),
                    model=model, claude_cmd=claude_cmd,
                ))
            except llm.LLMError as e:
                log(f"[study] {repo.name} chunk {n}/{total}: SKIPPED ({e})")
                return None
            return [ev for ev in (_study_event(it, repo.name, repo, files_by_sha)
                                  for it in (items if isinstance(items, list) else []))
                    if ev is not None]

        finished = [0]

        def note(i: int, result: list[Event] | None) -> None:
            finished[0] += 1  # chunks land out of order; say which one and how far along
            what = "SKIPPED" if result is None else f"{len(result)} event(s)"
            log(f"[study] {repo.name} chunk {i}/{total}: {what} "
                f"[{finished[0]}/{len(pending)} done]")

        results = _pmap(distill_chunk, pending, jobs, note)
        added = 0
        for result in results:  # serial, in chunk order: identical to a sequential run
            for ev in result or []:
                if not store.has_event(ev.id):
                    store.append_event(ev)
                    new_events.append(ev)
                    added += 1
        log(f"[study] {repo.name}: {added} new event(s) from {len(pending)} chunk(s)")
        # record every chunk that landed, so a later run skips exactly these
        done |= {g[-1].sha for (_, g), r in zip(pending, results) if r is not None}
        # the contiguous prefix, in original chunk order, decides the watermark
        by_index = {i: r for (i, _), r in zip(pending, results)}
        ordered = [by_index.get(i, []) for i in range(1, total + 1)]
        failed = next((i for i, r in enumerate(ordered) if r is None), None)
        safe = total if failed is None else failed
        if truncated:
            log(f"[study] {repo.name}: --max-chunks skipped older history, watermark not advanced")
        elif safe == 0:
            log(f"[study] {repo.name}: chunk 1 failed, watermark not advanced")
        else:
            last = chunks[safe - 1][-1]
            covered = {g[-1].sha for g in chunks[:safe]}  # subsumed by the watermark
            marks[key] = {"sha": last.sha, "date": last.date,
                          "since": window, "filter": noise_filter,
                          "done": sorted(done - covered)}
            # Persist per repo, not once at the end. A run killed in a later repo
            # (quota, Ctrl-C, OOM) must not discard the repos that already
            # finished — resume matters most exactly when a run does not.
            store.save_state(state)
            if failed is not None:
                log(f"[study] {repo.name}: chunk {failed + 1} failed, "
                    f"watermark held at {last.sha} ({last.date})")
    doc_sources = [s for s in cfg.sources if s.adapter == "doc"]
    for src in doc_sources:
        docs = _study_docs(store, src, root, cfg, existing_ids, claude_cmd, model, jobs, log)
        counted["docs"] = counted.get("docs", 0) + len(docs)
        new_events.extend(docs)
    if dirty:
        store.save_state(state)
    return new_events


def _serialized(log):
    lock = threading.Lock()

    def emit(msg: str) -> None:
        with lock:
            log(msg)
    return emit


def _pmap(fn, items: list, jobs: int, on_done=None) -> list:
    """Map fn over items with a thread pool (the work is blocking subprocesses),
    returning results in INPUT order however they complete. fn must swallow its
    own errors: one bad item must not drain the pool."""
    results: list = [None] * len(items)
    if jobs <= 1 or len(items) < 2:
        for i, item in enumerate(items):
            results[i] = fn(item)
            if on_done:
                on_done(i, results[i])
        return results
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
        for fut in as_completed(futures):
            i = futures[fut]
            results[i] = fut.result()
            if on_done:
                on_done(i, results[i])
    return results


# ------------------------------------------------------------------ git window

class _Commit(NamedTuple):
    sha: str
    date: str
    author: str          # "name <email>", for the bot check
    subject: str
    files: tuple[str, ...]
    merge: bool = False

    @property
    def line(self) -> str:
        return f"{self.sha}|{self.date}|{self.subject}"


# \x1e delimits records so the --name-only file lists parse back
_GIT_FORMAT = "%x1e%h|%ad|%an <%ae>|%p|%s"

_SHORTHAND = re.compile(r"^(\d+)\s*([a-z]+)$", re.IGNORECASE)
_UNITS = {"y": "years", "yr": "years", "yrs": "years", "year": "years", "years": "years",
          "mo": "months", "mon": "months", "m": "months", "month": "months", "months": "months",
          "w": "weeks", "wk": "weeks", "week": "weeks", "weeks": "weeks",
          "d": "days", "day": "days", "days": "days"}


def _window(since: str | None, src: SourceConfig) -> tuple[str, bool]:
    """Resolve one repo's history window: an explicit `--since` overrides
    everything (the escape hatch), else the source's own `since`, else the
    global default. Returns (requested, came_from_the_source)."""
    if since is not None:
        return since, False
    if src.since is not None:
        return src.since, True
    return DEFAULT_SINCE, False


def since_arg(since: str) -> str:
    """Expand the shorthand we advertise into something git actually parses.
    git's approxidate reads a bare "3y" as ~26 days and "90d" as the year 1990,
    silently, so never hand it through untouched."""
    s = since.strip()
    m = _SHORTHAND.match(s)
    unit = _UNITS.get(m.group(2).lower()) if m else None
    return f"{m.group(1)} {unit} ago" if unit else s


def _since_understood(repo: Path, since: str) -> bool:
    """approxidate resolves anything it cannot parse to *now*, which would
    quietly study zero commits. Anything landing within seconds of now is junk."""
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", f"--since={since}"],
                         capture_output=True, text=True, timeout=30)
    m = re.search(r"--max-age=(\d+)", out.stdout)
    return bool(m) and time.time() - int(m.group(1)) > 5


def _git_log(repo: Path, since: str = "", after: str = "", rev: str = "HEAD") -> list[_Commit]:
    """One pass for subjects, authors and files touched. --name-only reads trees
    only, so this still works on a blobless clone."""
    cmd = ["git", "-C", str(repo), "log", "--reverse", "--date=short",
           "--name-only", f"--format={_GIT_FORMAT}"]
    cmd.append(f"{after}..{rev}" if after else rev)
    if since and not after:  # a watermark range is already exact
        cmd.append(f"--since={since}")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        return []
    commits: list[_Commit] = []
    for record in out.stdout.split("\x1e")[1:]:
        head, _, rest = record.partition("\n")
        parts = head.split("|", 4)
        if len(parts) != 5:
            continue
        sha, date, author, parents, subject = parts
        commits.append(_Commit(sha, date, author, subject,
                               tuple(f for f in rest.splitlines() if f.strip()),
                               merge=len(parents.split()) > 1))
    return commits


# ------------------------------------------------------------------ noise filter

_BOT_MARKERS = ("[bot]", "dependabot", "renovate", "github-actions")
_LOCKFILES = {"go.mod", "go.sum", "package-lock.json", "yarn.lock", "cargo.lock"}
_MECHANICAL_DIRS = {".github", "vendor"}
# Anchored conventional-commit types only. Free text is never enough on its own:
# "Revert bump allocator change" and "test(backup): widen deadline margin" are
# both real knowledge, so no substring match and no test(/style( in this set.
_CHORE_RE = re.compile(r"^(chore|ci|build|deps)(\([^)]*\))?!?:", re.IGNORECASE)


def _mechanical_path(path: str) -> bool:
    parts = path.lower().split("/")
    return parts[-1] in _LOCKFILES or any(p in _MECHANICAL_DIRS for p in parts[:-1])


def _noise(c: _Commit) -> str:
    """"" to keep the commit, else the bucket it was dropped into. Judged on who
    authored it and what it touches, not on what the subject says."""
    # a merge has no diff of its own and every commit it merges is listed
    # separately in the same log, so it is pure duplication (43% of etcd's window)
    if c.merge:
        return "merge"
    if any(m in c.author.lower() for m in _BOT_MARKERS):
        return "bot"
    if _CHORE_RE.match(c.subject):
        return "mechanical"
    # a commit that touches real source alongside a lockfile is real work
    if c.files and all(_mechanical_path(f) for f in c.files):
        return "mechanical"
    return ""


# ------------------------------------------------------------------ watermark

def _watermark(repo: Path, mark: dict, since: str | None, window: str,
               noise_filter: bool, log) -> str:
    """The sha to resume after, or "" to (re)scan the whole window."""
    if since is not None or not mark.get("sha"):
        return ""
    if mark.get("since") != window or bool(mark.get("filter")) != noise_filter:
        log(f"[study] {repo.name}: window/filter changed since last run, rescanning")
        return ""
    if not _rev_exists(repo, str(mark["sha"])):
        log(f"[study] {repo.name}: watermark {mark['sha']} is gone (history rewritten), rescanning")
        return ""
    return str(mark["sha"])


def _rev_exists(repo: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True, timeout=30).returncode == 0


def _study_docs(store: Store, src: SourceConfig, root: Path, cfg: CaptainConfig,
                existing_ids: list[str], claude_cmd: str, model: str, jobs: int,
                log) -> list[Event]:
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

    def distill_doc(path: Path) -> Event | None:
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        text = path.read_text(errors="replace")
        digest = hashlib.sha1(text.encode()).hexdigest()[:12]
        try:
            item = llm.extract_json(llm.run_claude(
                STUDY_DOC_PROMPT.format(
                    kind=kind, path=rel, text=text[:DOC_CHARS_CAP],
                    domains=", ".join(cfg.domains) or "any",
                    entity_ids=", ".join(existing_ids[:60]) or "none yet",
                ),
                model=model, claude_cmd=claude_cmd,
            ))
        except llm.LLMError as e:
            log(f"[study] {rel}: SKIPPED ({e})")
            return None
        return _study_doc_event(item, kind, rel, digest) if isinstance(item, dict) else None

    new_events: list[Event] = []
    for ev in _pmap(distill_doc, paths, jobs):  # concurrent calls, append in path order
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
        domains=[s for s in (slugify(str(d)) for d in item.get("domains", [])) if s][:4],
        trust="low",  # doc-derived, verify in code — same hierarchy as adr.py's non-ADR docs
        refs=[Ref(kind="doc", id=rel)],
        source="study",
        derived_entities=[e for e in item.get("entities", []) if isinstance(e, dict) and e.get("id")][:8],
        derived_relations=[r for r in item.get("relations", [])
                           if isinstance(r, dict) and r.get("source") and r.get("target")][:10],
    )


def _study_event(item: dict, repo_name: str, repo: Path,
                 files_by_sha: dict[str, list[str]] | None = None) -> Event | None:
    title = str(item.get("title", "")).strip()
    date = str(item.get("date", ""))[:10]
    if not title or len(date) != 10:
        return None
    etype = item.get("type", "note")
    commit_shas = [str(c)[:12] for c in item.get("commits", [])[:8]]
    files: list[str] = []
    for sha in commit_shas:
        # the window pass already read every file list; only a sha it never saw
        # (an older or hallucinated one) costs a subprocess
        cached = (files_by_sha or {}).get(sha)
        for f in cached[:10] if cached is not None else _changed_files(repo, sha):
            if f not in files:
                files.append(f)
    return Event(
        id=f"evt-{date.replace('-', '')}-{slugify(repo_name)}-{slugify(title)}",
        type=etype if etype in ("note", "decision", "incident") else "note",
        ts=date, title=f"{repo_name}: {title}",
        summary=str(item.get("summary", ""))[:600],
        impact=item.get("impact", "low") if item.get("impact") in ("high", "medium", "low") else "low",
        domains=[s for s in (slugify(str(d)) for d in item.get("domains", [])) if s][:4],
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


# The engine's PyPI name is graphifyy (double y); the import name is graphify.
# Do NOT "fix" the spelling — the single-y name on PyPI is not this project.
# The exact-version pin is the supply chain boundary: PyPI releases are
# immutable, so a hostile future owner of the name can only publish new
# versions, never alter this one. Invisible to dependabot — bump deliberately.
GRAPHIFY_SPEC = "graphifyy==0.9.31"


def graphify_python(log=print) -> str | None:
    """Resolve (installing if needed) the graphify engine's interpreter."""
    log("[index] resolving graphify engine (first run may install it)...")
    def probe() -> str | None:
        try:
            out = subprocess.run(
                ["uv", "tool", "run", "--from", GRAPHIFY_SPEC, "python", "-c",
                 "import graphify, sys; print(sys.executable)"],
                capture_output=True, text=True, timeout=300)
            return out.stdout.strip() or None
        except (subprocess.SubprocessError, OSError):
            return None
    py = probe()
    if py is None:
        log(f"[index] graphify engine not available (needs uv; tried {GRAPHIFY_SPEC}) — skipped")
    return py


def index_code(cfg: CaptainConfig, root: Path, only: str = "",
               timeout: int = 600, log=print) -> int:
    """Build/refresh code-only graphify indexes for workspace repos.

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
    effect; remove it so workspace repos stay pristine. A user-built full
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
        # `task` is retired but a schema-1 log still replays them, and their
        # ids are as convention-stable as the live types beside it
        if len(group) < 2 or etype in ("adr", "goal", "task"):
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
