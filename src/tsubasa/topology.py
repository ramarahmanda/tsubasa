"""Stage 3 of discovery: the workspace map `init` writes before anything is distilled.

Two halves, kept apart on purpose.

`workspace_events()` is mechanical. It reads git and the filesystem and emits
one entity per repo and one per knowledge corpus, with the facts a first
session needs to answer "where would I find X": remote, branch, commit count,
history span, primary language, per-corpus adapter/glob/file count/extensions/
date range/sample titles. No model, so `tsubasa rebuild` reproduces it exactly.

`refine()` is judgment. One headless-Claude call takes the candidate map plus
each repo's README/CONTRIBUTING and returns corrections to the offline
classification plus how the repos relate. Anything at all going wrong (no
binary, no network, timeout, malformed JSON, failed validation) returns None
and the caller keeps `discover.plan()`'s offline answer.

Extraction from document *contents* is not done here — that is `study`'s job.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

from . import llm
from .adapters import base as adapters_base
from .models import IMPACT_LEVELS, Entity, Event, now_iso, slugify

MODEL = "haiku"          # one call, classification-scale work
TIMEOUT = 240            # init must stay fast; a stalled model falls back
GIT_TIMEOUT = 60
README_CHARS = 1800
CONTRIB_CHARS = 700
SAMPLES = 5
MAX_ADDED = 8            # sources the model may add on top of the offline plan

ADAPTERS = {"adr", "doc", "incident"}
GLOBS = {"*.md", "**/*.md", "**/README", "*.rst", "**/*.rst", "README.md"}
# DESIGN.md §3.2 vocabulary plus what the adapters already emit. The model
# picks from this list; anything else is rejected rather than invented into
# the graph.
PREDICATES = {"depends_on", "implements", "documented_in", "deployed_to",
              "references", "relates_to", "supersedes", "caused_by",
              "decided_in", "tracked_by"}
EXT_ID_RE = re.compile(r"^ext-[a-z0-9][a-z0-9-]*$")

# what a corpus is called in a question, so "where are the decisions" hits it
LABELS = {"adr": "decisions", "incident": "postmortems", "doc": "documentation"}

# Entity matching is substring-based, so a short or generic alias matches
# inside unrelated words ("doc" from `etcd/etcdctl/doc` hits "dockershim") and
# outranks the real entity. A directory leaf that is one of these carries no
# identifying information anyway; the repo-qualified alias still finds it.
MIN_ALIAS = 5
GENERIC_ALIASES = {
    "adr", "adrs", "api", "apis", "app", "assets", "blog", "book", "build",
    "changelog", "changelogs", "code", "common", "config", "content", "contents",
    "core", "data", "decisions", "design", "designs", "doc", "docs",
    "documentation", "example", "examples", "faq", "files", "guide", "guides",
    "help", "howto", "images", "incidents", "index", "info", "internal", "kep",
    "keps", "lib", "main", "manual", "markdown", "misc", "notes", "pages",
    "postmortems", "proposal", "proposals", "public", "readme", "reference",
    "release-notes", "release_notes", "rfc", "rfcs", "scripts", "shared", "site",
    "spec", "specs", "src", "static", "test", "tests", "text", "tools",
    "tutorial", "tutorials", "web", "wiki", "www",
}


def _useful_alias(alias: str) -> bool:
    a = alias.strip().lower()
    return len(a) >= MIN_ALIAS and a not in GENERIC_ALIASES

CODE_EXT = {
    ".go": "Go", ".py": "Python", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++",
    ".rs": "Rust", ".java": "Java", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".rb": "Ruby", ".sh": "Shell", ".pl": "Perl", ".php": "PHP",
    ".kt": "Kotlin", ".swift": "Swift", ".cs": "C#", ".scala": "Scala", ".ex": "Elixir",
    ".erl": "Erlang", ".hs": "Haskell", ".lua": "Lua", ".m": "Objective-C",
}


# ------------------------------------------------------------------ git facts

def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args],
                             capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def repo_facts(repo: Path) -> dict:
    """Git metadata for one repo. Every field is optional: a directory that
    is not a repo, or a repo with no commits, still yields a usable entity."""
    dates = [d for d in _git(repo, "log", "--format=%ad", "--date=short").splitlines() if d]
    tags = [t for t in _git(repo, "for-each-ref", "refs/tags", "--sort=creatordate",
                            "--format=%(refname:short)").splitlines() if t]
    return {
        "remote": _git(repo, "remote", "get-url", "origin").strip(),
        "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip(),
        "commits": len(dates),
        "first": dates[-1] if dates else "",
        "last": dates[0] if dates else "",
        "language": _language(repo),
        "releases": len(tags),
        # one summary fact beats 686 individual `released:` ones
        "release_span": f"{tags[0]}..{tags[-1]}" if tags else "",
    }


def _language(repo: Path) -> str:
    counts = Counter()
    for name in _git(repo, "ls-files").splitlines():
        lang = CODE_EXT.get(Path(name).suffix)
        if lang:
            counts[lang] += 1
    top = counts.most_common(1)
    return top[0][0] if top and top[0][1] >= 5 else ""


def corpus_facts(root: Path, src: dict) -> dict:
    """Filesystem + git evidence for one planned source."""
    base = root / src["path"]
    glob = src.get("glob") or "**/*.md"
    exclude = src.get("exclude") or []
    paths = sorted(p for p in base.glob(adapters_base.ci_glob(glob))
                   if p.is_file()
                   and not adapters_base.excluded_by(p.relative_to(base).as_posix(), exclude)) \
        if base.is_dir() else []
    repo, inner = _split_repo(src)
    dates = [d for d in _git(root / repo, "log", "--format=%ad", "--date=short",
                             "--", inner).splitlines() if d]
    return {
        "files": len(paths),
        "extensions": [e for e, _ in Counter(p.suffix or p.name for p in paths).most_common(5)],
        "first": dates[-1] if dates else "",
        "last": dates[0] if dates else "",
        "titles": [_title(p) for p in paths[:SAMPLES]],
    }


def _split_repo(src: dict) -> tuple[str, str]:
    """(repo dir relative to the captain root, path inside that repo)."""
    repo = src.get("repo", ".")
    path = src["path"]
    if repo in ("", "."):
        return ".", path
    return repo, path[len(repo) + 1:] or "."


HEADING_RE = re.compile(r"^#{1,2}\s+(.+?)\s*$", re.MULTILINE)


def _title(path: Path) -> str:
    try:
        head = path.read_text(errors="replace")[:2000]
    except OSError:
        return path.stem
    m = HEADING_RE.search(head)
    return (m.group(1) if m else path.stem)[:80]


# ------------------------------------------------------------------ entities

def workspace_events(root: Path, sources: list[dict], refined: dict | None = None,
                     ts: str = "") -> list[Event]:
    """The map, as events. Mechanical facts first, model proposals second.

    Split into two events so a reviewer can tell them apart in the log: the
    map is `trust=high, source=init`, the topology `trust=low,
    source=init-llm`. Both are type `note` — EVENT_TYPES has no `discovery`
    shape, and `note` is its only neutral observation type (every other type
    asserts a decision, incident, deploy, release, PR or plan).
    """
    ts = ts or now_iso()
    day = ts[:10].replace("-", "")
    repos = _repo_entities(root, sources)
    corpora = _corpus_entities(root, sources)
    _dedupe_aliases(repos + [ent for ent, _ in corpora])
    repo_ids = {r["id"] for r in repos}
    relations = [{"source": _repo_id(repo), "predicate": "documented_in", "target": ent["id"]}
                 for ent, repo in corpora if _repo_id(repo) in repo_ids]
    events = [Event(
        id=f"evt-{day}-workspace-map",
        type="note", ts=ts,
        title=f"Workspace map: {len(repos)} repo(s), {len(corpora)} knowledge corpora",
        summary="Where knowledge lives in this workspace, read from git and the "
                "filesystem at init. No document contents were interpreted.",
        impact="medium", trust="high", source="init",
        derived_entities=repos + [ent for ent, _ in corpora],
        derived_relations=relations,
    )]
    if refined:
        events.append(_topology_event(day, ts, refined, repo_ids))
    return events


def _topology_event(day: str, ts: str, refined: dict, repo_ids: set[str]) -> Event:
    ents = [{"id": _repo_id(name), "type": "service", "name": name, "description": desc}
            for name, desc in sorted(refined.get("repos", {}).items())
            if _repo_id(name) in repo_ids]
    ents += list(refined.get("externals", []))
    rels = list(refined.get("topology", []))
    return Event(
        id=f"evt-{day}-workspace-topology",
        type="note", ts=ts,
        title=f"Workspace topology: {len(rels)} proposed relation(s)",
        summary=f"How these repos relate, proposed by {refined.get('model') or 'claude'} "
                "from the candidate map and each repo's README. Unverified.",
        impact="medium", trust="low", source="init-llm",
        derived_entities=ents, derived_relations=rels,
    )


def _repo_id(name: str) -> str:
    return f"svc-{slugify(name, 64)}"


def _repo_entities(root: Path, sources: list[dict]) -> list[dict]:
    out = []
    for src in sources:
        if src["adapter"] != "git":
            continue
        path = src["path"]
        name = root.name if path in ("", ".") else Path(path).name
        f = repo_facts(root / path)
        facts = [f"path: {path}"]
        for label, key in (("remote", "remote"), ("default branch", "branch"),
                           ("primary language", "language")):
            if f[key]:
                facts.append(f"{label}: {f[key]}")
        if f["commits"]:
            facts.append(f"history: {f['commits']} commits, {f['first']}..{f['last']}")
        if f["releases"]:
            facts.append(f"releases: {f['releases']}, {f['release_span']}")
        out.append(Entity(
            id=_repo_id(name), type="service", name=name,
            description=f"repository {path}",
            key_facts=facts,
        ).to_dict())
    return out


def _corpus_entities(root: Path, sources: list[dict]) -> list[tuple[dict, str]]:
    out = []
    for src in sources:
        if src["adapter"] == "git":
            continue
        f = corpus_facts(root, src)
        label = LABELS.get(src["adapter"], "documents")
        kind = src.get("kind") or label
        facts = [f"adapter: {src['adapter']}",
                 f"glob: {src['path']}/{src.get('glob', '**/*.md')}",
                 f"files: {f['files']}"]
        if f["extensions"]:
            facts.append("extensions: " + ", ".join(f["extensions"]))
        if f["first"]:
            facts.append(f"changed: {f['first']}..{f['last']}")
        if f["titles"]:
            facts.append("samples: " + "; ".join(f["titles"]))
        ent = Entity(
            id=f"corpus-{slugify(src['path'], 64)}", type="doc",
            name=src["path"],
            # path/glob/count/range are key facts; repeating them here doubles
            # the cost of every corpus line in hot.md and index.md
            description=f"{kind} of {src.get('repo', '.')} ({Path(src['path']).name})",
            aliases=[a for a in (f"{src.get('repo', '.')} {label}", Path(src["path"]).name)
                     if _useful_alias(a)],
            key_facts=facts,
        ).to_dict()
        out.append((ent, src.get("repo", ".")))
    return out


def _dedupe_aliases(ents: list[dict]) -> None:
    """Aliases exist so a question finds the corpus; a *shared* alias instead
    files a reconciliation question on every init. Drop any that is not unique."""
    names = Counter(n.lower() for e in ents for n in [e["name"]] + list(e.get("aliases", [])))
    for e in ents:
        keep = [a for a in e.get("aliases", []) if names[a.lower()] == 1]
        if keep:
            e["aliases"] = keep
        else:
            e.pop("aliases", None)


# ------------------------------------------------------------------ llm pass

PROMPT = """\
You are configuring a knowledge captain for an engineering workspace. A
filesystem scan produced the candidate directories below; an offline
heuristic turned some of them into knowledge sources. Correct that
classification, and say how the repos relate.

<repos>
{repos}
</repos>

<candidates>
path | files | extensions | samples
{candidates}
</candidates>

<offline_plan>
{plan}
</offline_plan>

Classification:
- adr       decision records: ADRs, RFCs, KEPs, design proposals
- incident  postmortems, outage write-ups
- doc       durable human-written knowledge: architecture, guides, principles
- drop      NOT knowledge: generated API/CRD reference, auto-generated docs,
            translations, test fixtures, boilerplate, release-note dumps

Set kind=principle impact=high for contributor guides and best-practice docs.

Dropping is destructive: the captain goes blind to that directory. Drop only
when the content is clearly machine-generated or boilerplate. Hand-written
prose in an unusual shape (extensionless README files, appendices, per-module
design notes) is exactly the knowledge worth keeping. When unsure, keep.

`suspect=` on a plan line lists files that are far longer than their
siblings. That shape is typical of generated API/CRD reference dumps sitting
inside a hand-written corpus. When a suspect really is generated reference,
keep the source and return `exclude` with that file's path relative to the
source path (globs allowed, e.g. `*.v1.md`); do not drop the whole directory
for it. A long hand-written document is not a reason to exclude anything.

Report ONLY entries you are CHANGING; anything you omit keeps its offline
classification. Copy paths and repo names VERBATIM from the lists above.
Do not extract anything from document contents — you are classifying
directories and describing repos, nothing else.

Return ONLY JSON, no prose:
{{"sources": [{{"path": "...", "adapter": "adr|doc|incident|drop", "kind": "", "impact": "high|medium|low", "exclude": ["file or glob, relative to path"], "why": "short"}}],
  "repos": [{{"name": "<repo dir name>", "description": "one line: what this repo is"}}],
  "externals": [{{"id": "ext-<slug>", "name": "...", "description": "..."}}],
  "topology": [{{"source": "svc-<repo>", "predicate": "{predicates}", "target": "svc-<repo> or ext-<slug>", "why": "short"}}]}}
"""


def refine(root: Path, candidates: list[dict], planned: list[dict], repos: list[str],
           claude_cmd: str = "claude", model: str = MODEL, timeout: int = TIMEOUT,
           log=print) -> dict | None:
    """One call: refined classification + workspace topology. None on any failure."""
    if not llm.claude_available(claude_cmd):
        log(f"warning: '{claude_cmd}' not found — keeping the offline classification "
            "(`--no-llm` to silence this)")
        return None
    log(f"classifying {len(planned)} candidate source(s) with claude "
        f"({model or 'default model'}), one call...")
    prompt = PROMPT.format(
        repos=_repo_briefs(root, repos), candidates=_candidate_lines(candidates),
        plan=_plan_lines(planned), predicates=" | ".join(sorted(PREDICATES)),
    )
    try:
        data = llm.extract_json(llm.run_claude(prompt, model=model, claude_cmd=claude_cmd,
                                               timeout=timeout))
        refined = _validate(data, candidates, planned, repos, model)
    except Exception as e:  # a classification pass must never fail an init
        log(f"warning: topology pass failed ({str(e)[:160]}) — keeping the offline classification")
        return None
    if refined is None:
        log("warning: topology pass returned unusable output — keeping the offline classification")
    return refined


def _repo_briefs(root: Path, repos: list[str]) -> str:
    out = []
    for path in repos:
        base = root / path
        name = root.name if path in ("", ".") else Path(path).name
        f = repo_facts(base)
        head = f"## {name}  ({f['commits']} commits, {f['first']}..{f['last']}, {f['language'] or 'n/a'})"
        body = [head, f"remote: {f['remote'] or 'n/a'}"]
        for fname, cap in (("README.md", README_CHARS), ("CONTRIBUTING.md", CONTRIB_CHARS)):
            p = base / fname
            if p.is_file():
                body.append(f"--- {fname}\n{p.read_text(errors='replace')[:cap]}")
        out.append("\n".join(body))
    return "\n\n".join(out)


def _candidate_lines(candidates: list[dict]) -> str:
    return "\n".join(
        f"{c['path']} | {c['files']} | {','.join(c['extensions']) or '-'} | "
        f"{','.join(c['samples'][:3]) or '-'}"
        for c in candidates)


def _plan_lines(planned: list[dict]) -> str:
    return "\n".join(
        f"{s['path']} | adapter={s['adapter']} kind={s.get('kind', '-')} "
        f"impact={s.get('impact', '-')} glob={s.get('glob', '-')} files={s['files']}"
        + (f" | excluded={','.join(s['exclude'])}" if s.get("exclude") else "")
        + (f" | suspect={'; '.join(s['suspect'])}" if s.get("suspect") else "")
        for s in planned)


# ------------------------------------------------------------------ validation

def _validate(data, candidates: list[dict], planned: list[dict], repos: list[str],
              model: str) -> dict | None:
    """Reject anything not grounded in what we asked about. Garbage in the
    config is worse than the offline guess it would replace."""
    if not isinstance(data, dict):
        return None
    by_path = {c["path"]: c for c in candidates}
    planned_paths = {s["path"] for s in planned}
    repo_names = {(Path(p).name if p not in ("", ".") else p): p for p in repos}
    repo_ids = {_repo_id(n) for n in repo_names}

    verdicts = []
    for s in _items(data, "sources")[:40]:
        path, adapter = str(s.get("path", "")), str(s.get("adapter", ""))
        if path not in by_path and path not in planned_paths:
            continue
        if adapter not in ADAPTERS and adapter != "drop":
            continue
        glob = str(s.get("glob", ""))
        v = {"path": path, "adapter": adapter}
        if glob in GLOBS:
            v["glob"] = glob
        if s.get("kind"):
            v["kind"] = slugify(str(s["kind"]), 24)
        if s.get("impact") in IMPACT_LEVELS:
            v["impact"] = s["impact"]
        exclude = _excludes(s.get("exclude"))
        if exclude:
            v["exclude"] = exclude
        verdicts.append(v)

    descriptions = {str(r.get("name", "")): str(r.get("description", ""))[:200]
                    for r in _items(data, "repos")
                    if str(r.get("name", "")) in repo_names and r.get("description")}

    externals = []
    for e in _items(data, "externals")[:4]:
        eid = str(e.get("id", ""))
        if EXT_ID_RE.match(eid) and e.get("name"):
            externals.append({"id": eid, "type": "external", "name": str(e["name"])[:80],
                              "description": str(e.get("description", ""))[:200]})
    known = repo_ids | {e["id"] for e in externals}

    topology = []
    for r in _items(data, "topology")[:12]:
        src, pred, tgt = str(r.get("source", "")), str(r.get("predicate", "")), str(r.get("target", ""))
        if pred in PREDICATES and src in known and tgt in known and src != tgt:
            topology.append({"source": src, "predicate": pred, "target": tgt})

    if not (verdicts or descriptions or topology):
        return None
    return {"sources": verdicts, "repos": descriptions, "externals": externals,
            "topology": topology, "model": model}


MAX_EXCLUDES = 8
EXCLUDE_RE = re.compile(r"^[A-Za-z0-9_.*?\[\]/-]{1,120}$")


def _excludes(value) -> list[str]:
    """Exclusion globs, relative to the source path. An escape upward or an
    absolute path is rejected outright rather than sanitized into something
    the model did not ask for."""
    out = []
    for raw in (value if isinstance(value, (list, tuple)) else [value]):
        pat = str(raw or "").strip().strip("/")
        if pat and ".." not in pat and EXCLUDE_RE.match(pat) and pat not in out:
            out.append(pat)
    return out[:MAX_EXCLUDES]


def _items(data: dict, key: str) -> list[dict]:
    v = data.get(key)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


# ------------------------------------------------------------------ apply

def _matched(base: Path, glob: str, exclude: list[str]) -> int:
    if not base.is_dir():
        return 0
    n = 0
    for p in base.glob(adapters_base.ci_glob(glob or "**/*.md")):
        if p.is_file() and not adapters_base.excluded_by(p.relative_to(base).as_posix(), exclude):
            n += 1
    return n


def apply_sources(root: Path, planned: list[dict], candidates: list[dict],
                  refined: dict, log=print) -> list[dict]:
    """Fold the model's verdicts into the offline plan. Returns the new plan."""
    by_path = {s["path"]: dict(s) for s in planned}
    cands = {c["path"]: c for c in candidates}
    added = 0
    for v in refined["sources"]:
        path, adapter = v["path"], v["adapter"]
        if adapter == "drop":
            if by_path.pop(path, None) is not None:
                log(f"  llm: dropped {path} (not knowledge)")
            continue
        src = by_path.get(path)
        if src is None:
            c = cands.get(path)
            if c is None or added >= MAX_ADDED:
                continue
            src = {"path": path, "repo": c["repo"], "bucket": c["bucket"] or "knowledge",
                   "glob": "**/*.md"}
            added += 1
            log(f"  llm: added {path}")
        before = (src.get("adapter"), src.get("kind"), src.get("impact"))
        src["adapter"] = adapter
        for key in ("glob", "kind", "impact"):
            if v.get(key):
                src[key] = v[key]
        if v.get("exclude"):
            added = [p for p in v["exclude"] if p not in src.get("exclude", [])]
            src["exclude"] = (src.get("exclude") or []) + added
            if added:
                log(f"  llm: excluded {', '.join(added)} from {path} (generated)")
        src["files"] = _matched(root / path, src["glob"], src.get("exclude") or [])
        if before[0] is not None and before != (adapter, src.get("kind"), src.get("impact")):
            log(f"  llm: reclassified {path} {before} -> "
                f"({adapter}, {src.get('kind')}, {src.get('impact')})")
        by_path[path] = src
    return [by_path[p] for p in sorted(by_path) if by_path[p]["files"]]
