"""Mechanical citation resolution.

No model is involved here. Every citation an answer emits is extracted by
regex and resolved against the fixture on disk and against the captain graph:

    path            does the file exist under the fixture?
    file:line       does the file exist, and does it have that many lines?
    commit sha      `git cat-file -e <sha>^{commit}` in each fixture repo
    graph id        evt-* / adr-* / task-* present in .tsubasa/graph?
    KEP-<n>         does keps/<sig>/<n>-*/ exist?

This resolves EXISTENCE ONLY. It does not check that the cited source
supports the claim; that judgement is the judge's job. A run can therefore
have 100% resolved citations and still be graded `confabulated`, and that
combination is exactly what a fabricated-rationale answer looks like.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

from config import REPOS

# `path/to/file` or `path/to/file:123` or `path/to/file:12-34`, with or
# without backticks. Requires a slash, so bare words never qualify.
# `file:12`, `file:12-34`, and `file:49,53-56`. The comma form is common when an
# answer cites two passages of one document; dropping the tail quoted the wrong
# lines to the judge, which then confirmed "fabricated" against text that did
# support the claim (C3).
PATH_RE = re.compile(r"`?((?:[\w.+-]+/)+[\w.+@-]+)(?::(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*))?`?")
SHA_RE = re.compile(r"\b([0-9a-f]{8,40})\b")
GRAPH_ID_RE = re.compile(r"\b((?:evt|adr|task|inc|goal)-[a-z0-9][a-z0-9-]{3,})\b")
KEP_RE = re.compile(r"\bKEP[- ](\d{2,5})\b", re.IGNORECASE)

# A markdown link target, a URL and a bare version string are not citations.
_SKIP_PREFIXES = ("http://", "https://", "mailto:")

# A URL is not a file path, and PATH_RE happily matches *inside* one: the group
# in `https://github.com/etcd-io/etcd.git` is `github.com/etcd-io/etcd.git`,
# which carries no scheme, so the `"://" in path` guard never fires. Those became
# unresolved "paths" and the judge, told UNRESOLVED means fabricated, graded a
# correct answer `confabulated` for quoting four real git remotes (B1, arm A).
# Same failure as prose-as-path: a citation shape that was never a file claim.
_URL_SPAN_RE = re.compile(r"(?:https?://|mailto:|git@|ssh://|git://|www\.)\S+")
# ...and a scheme-less host: `github.com/etcd-io/etcd`, `k8s.io/component-base`,
# `postgr.es/m/<message-id>`. Structural rather than a TLD allowlist: a TLD list
# missed `postgr.es`, so a mailing-list Message-ID was reported as an unresolved
# *file path* and cost arm A a `confabulated` on G1. No path under this fixture
# begins with a dotted segment, so "first segment looks like a host" is enough.
# A leading dot (`.github/workflows/ci.yml`) is a hidden directory, not a host.
_HOSTNAME_RE = re.compile(r"^[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}$")


def _is_url_like(path: str) -> bool:
    """First segment is a hostname, so the whole thing is a URL, not a path."""
    head = path.split("/", 1)[0]
    return not head.startswith(".") and bool(_HOSTNAME_RE.match(head))


# A `word/word` shape is not automatically a file citation. Prose produces plenty
# ("stage/last milestone" in a table cell, "the kubernetes/kubernetes tree"), and
# once the judge is told UNRESOLVED means fabricated, every false positive becomes
# a phantom fabrication charged to the arm. So a path must look like a file, or
# name a fixture repo, or be a known extensionless doc.
_BARE_DOC = {"README", "Makefile", "LICENSE", "NOTICE", "OWNERS", "CHANGELOG"}


def _looks_like_a_file(path: str) -> bool:
    head, _, tail = path.rpartition("/")
    if "." in tail and not tail.endswith("."):
        return True                       # has an extension
    if tail in _BARE_DOC or tail.startswith("README"):
        return True                       # postgres design docs are extensionless
    if tail in REPOS:
        # A slash-joined enumeration of the fixture repos, not a path:
        # "the cloudnative-pg/postgres/etcd trees" is repo-rooted and has a
        # deeper segment, so the branch below used to accept it, and the
        # resulting UNRESOLVED entry was handed to the judge as a proven
        # fabrication (C4, arm B). Shape-only: a real file is never named
        # exactly after one of the four repos.
        return False
    return head.split("/")[0] in REPOS and "/" in head   # repo-rooted deeper path

def _spans(ranges: str) -> list[tuple[int, int]]:
    """`49,53-56` -> [(49,49),(53,56)]. Each cited passage is quoted separately;
    spanning 49..56 in one block would pad the prompt with unrelated lines."""
    out = []
    for part in ranges.split(","):
        lo, _, hi = part.partition("-")
        out.append((int(lo), int(hi or lo)))
    return out


def extract(answer: str) -> list[dict]:
    """Citations in first-appearance order, deduplicated on (kind, raw)."""
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, raw: str, **extra):
        key = (kind, raw)
        if key not in seen:
            seen.add(key)
            found.append({"kind": kind, "raw": raw, **extra})

    url_spans = [m.span() for m in _URL_SPAN_RE.finditer(answer)]

    def inside_url(start: int, end: int) -> bool:
        return any(s <= start and end <= e for s, e in url_spans)

    for m in PATH_RE.finditer(answer):
        path = m.group(1)
        if any(path.startswith(p) for p in _SKIP_PREFIXES) or "://" in path:
            continue
        if inside_url(m.start(1), m.end(1)) or _is_url_like(path):
            add("url", path)             # not a file claim: never counted either way
            continue
        # `$HOME/.kube` and `~/.kube/config` are shell expansions, not claims
        # about a file in the workspace. Neither `$` nor `~` is in PATH_RE's
        # character class, so the match silently starts one character in and
        # yields `HOME/.kube`, which can never resolve. Measured: an arm that
        # correctly explained kubectl's config lookup was charged a fabricated
        # citation for writing the variable's name. Discarded like a URL.
        if m.start(1) and answer[m.start(1) - 1] in "$~":
            add("shell", path)
            continue
        if "..." in path:
            # `.../README.md` shorthand: a real reference, but not one that can
            # be resolved. Kept as its own kind so it is never counted as an
            # invented path.
            #
            # The elision is not always leading, and it is not always a whole
            # path segment. Answers abbreviate the middle of a long KEP path far
            # more often than the front, usually inside a segment
            # (`keps/sig-node/5554-.../README.md`), so matching on startswith --
            # or on a bare `...` segment -- sent those through as ordinary paths,
            # where they could not resolve and were reported to the judge as
            # fabrications. That hit arm B hardest, because it cites long KEP
            # paths the most: C8, C9 and C5 were each graded `confabulated` on
            # turn 1 partly on this evidence and then nudged. Three consecutive
            # dots never occur in a real path here, so the test is just `in`.
            add("path_elided", m.group(0).strip("`"))
        elif m.group(2):
            spans = _spans(m.group(2))
            add("file_line", m.group(0).strip("`"), path=path,
                line=spans[0][0], end_line=spans[-1][1], spans=spans)
        elif _looks_like_a_file(path):
            add("path", path, path=path)
        else:
            add("prose", path)          # not a citation: never counted either way
    for m in GRAPH_ID_RE.finditer(answer):
        add("graph_id", m.group(1))
    for m in KEP_RE.finditer(answer):
        add("kep", f"KEP-{m.group(1)}", number=m.group(1))
    for m in SHA_RE.finditer(answer):
        sha = m.group(1)
        # a sha is only a sha if it is not part of an already-claimed path/id
        if any(sha in c["raw"] for c in found):
            continue
        add("sha", sha)
    return found


@lru_cache(maxsize=1)
def _graph_ids(fixture_str: str) -> frozenset[str]:
    """Every id the captain graph can be asked about. Imported from the repo's
    own storage layer so the harness never re-implements the on-disk format;
    a missing or unreadable graph degrades to an empty set (arm A has none)."""
    import sys
    src = str(Path(__file__).resolve().parents[2] / "src")
    if src not in sys.path:
        sys.path.append(src)
    try:
        from tsubasa.storage import Store  # noqa: PLC0415
    except Exception as exc:  # a silent empty set marks every real id fabricated
        raise RuntimeError(
            f"cannot import tsubasa.storage from {src}: {exc}. Run the harness "
            "under the project venv (`uv run python benchmark/harness/run.py ...`)."
        ) from exc
    store = Store(Path(fixture_str))
    try:
        # no load_tasks: task management was removed from tsubasa, and the
        # bare `except` below turned that AttributeError into an empty id set,
        # which made every real graph id resolve UNRESOLVED
        ids = set(store.load_entities()) | {e.id for e in store.load_events()}
    except FileNotFoundError:
        return frozenset()          # arm A has no graph, which is correct
    except Exception as exc:        # anything else is a harness bug, not an absence
        raise RuntimeError(f"graph at {fixture_str} exists but could not be read: {exc}") from exc
    return frozenset(ids)


def _resolve_path(fixture: Path, path: str) -> tuple[bool, str]:
    candidates = [fixture / path]
    head = path.split("/", 1)[0]
    if head not in REPOS:
        candidates += [fixture / repo / path for repo in REPOS]
    for cand in candidates:
        if cand.exists():
            return True, str(cand.relative_to(fixture))
    # An answer routinely shortens a long path to its distinctive tail:
    # `5573/kep.yaml` for the cgroup-v1 KEP, `postmaster/autovacuum.c` for a
    # postgres backend file. The file is real and the claim checkable, but a
    # literal join misses it and the judge is then told the citation is
    # fabricated. Accept a suffix ONLY when it identifies exactly one file, so
    # an ambiguous shorthand still fails rather than resolving to the wrong doc.
    hit = _suffix_match(fixture, path)
    if hit:
        return True, hit
    return False, f"no such path under {fixture}"


@lru_cache(maxsize=1)
def _all_files(fixture_str: str) -> tuple[str, ...]:
    root = Path(fixture_str)
    return tuple(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and ".git/" not in str(p.relative_to(root))
    )


def _suffix_match(fixture: Path, path: str) -> str:
    needle = "/" + path.strip("/")
    hits = [f for f in _all_files(str(fixture)) if f.endswith(needle)]
    if len(hits) == 1:
        return hits[0]
    if hits:
        return ""                     # ambiguous: refuse rather than guess
    # A directory segment is often cited by its distinctive prefix: `5573/kep.yaml`
    # for `keps/sig-node/5573-remove-cgroup-v1/kep.yaml`. Allow each cited segment
    # to be a prefix of the real one, still requiring a unique hit.
    segs = [re.escape(x) for x in path.strip("/").split("/")]
    pat = re.compile("(^|/)" + "[^/]*/".join(segs[:-1]) + ("[^/]*/" if segs[:-1] else "") + segs[-1] + "$")
    loose = [f for f in _all_files(str(fixture)) if pat.search(f)]
    return loose[0] if len(loose) == 1 else ""


QUOTE_CHARS = 700          # per citation, so a wide range cannot flood the prompt
QUOTE_CONTEXT = 2          # lines either side: a claim often sits just outside the cite


def _lines(path, start: int, end: int) -> str:
    """The text a `file:line` citation points at, so the judge can check the
    claim instead of guessing. Slightly widened, because an answer citing
    line 53 is often supported by 52 or 54 as well."""
    try:
        rows = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    lo = max(0, start - 1 - QUOTE_CONTEXT)
    hi = min(len(rows), end + QUOTE_CONTEXT)
    out = "\n".join(f"{i + 1}: {rows[i]}" for i in range(lo, hi))
    return out[:QUOTE_CHARS] + ("..." if len(out) > QUOTE_CHARS else "")


def _line_count(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


@lru_cache(maxsize=4096)
def _sha_exists(repo_str: str, sha: str) -> bool:
    try:
        out = subprocess.run(["git", "-C", repo_str, "cat-file", "-e", f"{sha}^{{commit}}"],
                             capture_output=True, text=True, timeout=30,
                             env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin"})
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def resolve(citations: list[dict], fixture: Path) -> list[dict]:
    out = []
    for cite in citations:
        rec = dict(cite)
        kind = cite["kind"]
        if kind in ("path", "file_line"):
            ok, detail = _resolve_path(fixture, cite["path"])
            if ok and kind == "file_line":
                target = fixture / detail
                count = _line_count(target) if target.is_file() else 0
                if cite["end_line"] > count:
                    ok, detail = False, f"{detail} has {count} lines, cite says {cite['end_line']}"
                else:
                    # The judge runs tools-disabled, so without this it can only
                    # ask "does gold mention it?" and calls anything else invented.
                    # Four c-status answers were graded `confabulated` for claims
                    # quoted verbatim from the very lines they cited.
                    rec["quoted"] = "\n  ...\n".join(
                        _lines(target, lo, hi)
                        for lo, hi in cite.get("spans") or [(cite["line"], cite["end_line"])])
            rec.update(resolved=ok, detail=detail)
        elif kind == "sha":
            hits = [r for r in REPOS if (fixture / r / ".git").exists()
                    and _sha_exists(str(fixture / r), cite["raw"])]
            rec.update(resolved=bool(hits), detail=",".join(hits) or "no fixture repo has it")
        elif kind == "graph_id":
            ids = _graph_ids(str(fixture))
            known = cite["raw"] in ids
            detail = "graph"
            if not known:
                # Graph ids run long (`evt-20250518-kubernetes-enhancements-dynamic-
                # resource-allocation-dra-partitionable-de`), so answers truncate
                # them. The id is real and the claim checkable; reporting it as
                # fabricated cost a correct answer its verdict. Unique prefix only.
                hits = [i for i in ids if i.startswith(cite["raw"])]
                if len(hits) == 1:
                    known, detail = True, f"graph (prefix of {hits[0]})"
                else:
                    detail = "not in .tsubasa/graph"
            rec.update(resolved=known, detail=detail)
        elif kind == "kep":
            hits = sorted(str(p.relative_to(fixture)) for p in
                          (fixture / "kubernetes-enhancements" / "keps").glob(
                              f"*/{cite['number']}-*"))
            hits += sorted(str(p.relative_to(fixture)) for p in
                           (fixture / "kubernetes-enhancements" / "keps").glob(
                               f"*/*/{cite['number']}-*"))
            rec.update(resolved=bool(hits), detail=hits[0] if hits else "no such KEP directory")
        elif kind == "path_elided":
            rec.update(resolved=False, detail="elided path shorthand; not resolvable")
        else:
            rec.update(resolved=False, detail="unknown citation kind")
        out.append(rec)
    return out


def report(answer: str, fixture: Path) -> dict:
    everything = resolve(extract(answer), fixture)
    # `prose` and `url` are not citations. Keeping them in the denominator would
    # deflate both arms' validity ratio and, worse, hand the judge phantom
    # fabrications: an UNRESOLVED entry is presented to it as proven invention.
    cites = [c for c in everything if c["kind"] not in ("prose", "url", "shell")]
    resolved = sum(1 for c in cites if c["resolved"])
    elided = sum(1 for c in cites if c["kind"] == "path_elided")
    return {
        "total": len(cites),
        "resolved": resolved,
        "unresolved": len(cites) - resolved,
        "elided": elided,
        "by_kind": {kind: {
            "total": sum(1 for c in cites if c["kind"] == kind),
            "resolved": sum(1 for c in cites if c["kind"] == kind and c["resolved"]),
        } for kind in sorted({c["kind"] for c in cites})},
        "citations": cites,
        "prose_discarded": sum(1 for c in everything if c["kind"] == "prose"),
        "url_discarded": sum(1 for c in everything if c["kind"] == "url"),
        "shell_discarded": sum(1 for c in everything if c["kind"] == "shell"),
    }
