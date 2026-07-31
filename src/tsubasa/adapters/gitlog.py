"""Git adapter: repo history → release events + ADR-linked work detection.

Emits:
  - release events for tags
  - pr_merged/config_change-ish events for merge commits whose branch name
    or message carries an ADR id (adr-<slug>) — the ADR id is what threads
    the decision to the branch, the PR and the changed files
  - a service entity for the repo itself
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from ..models import ADR_ID_RE, Event, Ref, slugify
from .base import Adapter

# "This reverts commit <sha>" is git's own wording, emitted by `git revert` and
# by hand for decades. A revert is the single highest-signal commit for "has this
# been tried?", and study's ~50:1 chunk digest drops it: on the reference corpus
# the reverted feature survived as one clause in a five-topic summary and the
# reversal had no event at all. Deterministic extraction here means the timeline
# lands at `ingest`, so `init` has it, offline and free.
# `commits?`, and every sha in the clause rather than the first. git's own
# boilerplate is singular, but a revert that undoes a feature branch names them
# all: "This reverts commits 5753d4ee32 and fe60b67250". Requiring whitespace
# straight after `commit` missed exactly those, and they are the reverts worth
# finding, the ones that pull a whole feature back out. Measured on the
# reference corpus: the plural form is the difference between a revert being
# classified as a REVERTED transition and being elided from the timeline as an
# ordinary `decision` with "no recorded state change".
REVERTS_RE = re.compile(r"[Tt]his reverts commits?\s+([0-9a-f]{7,40})")
REVERTED_SHAS_RE = re.compile(
    r"[Tt]his reverts commits?\s+((?:[0-9a-f]{7,40}(?:[\s,]+and\s+|[\s,]+)?)+)")
SHA_RE = re.compile(r"[0-9a-f]{7,40}")


# How a revert that undoes a whole feature branch names its casualties. git's
# boilerplate covers only the single-commit case; these are what postgres, and
# any project that reverts by hand, actually writes:
#   "    commit f79b803dc: Common SQL/JSON clauses"      (indented, labelled)
#   "06c8a5090e  Improve code comments in b0eaa4c51b."   (bare, at line start)
#   " - 0da92dc530c9251735fc70b20cd004d9630a1266"        (bulleted, full sha)
LISTED_SHA_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:commit\s+)?([0-9a-f]{8,40})(?=[\s:]|$)", re.M)


def _reverted_shas(body: str, is_revert: bool = False) -> list[str]:
    """Every sha this commit undid, in order, deduplicated.

    `is_revert` gates the list forms on the subject line actually saying
    "Revert". The boilerplate clause is self-identifying and needs no gate, but
    a bare sha at the start of a line is not: plenty of ordinary commits open a
    paragraph by naming the commit they build on, and harvesting those would
    invent `reverts` edges pointing the wrong way.
    """
    found: list[str] = []
    m = REVERTED_SHAS_RE.search(body)
    if m:
        found += SHA_RE.findall(m.group(1))
    if is_revert:
        found += LISTED_SHA_RE.findall(body)
    seen, out = set(), []
    for sha in (s[:12] for s in found):
        if sha not in seen:
            seen.add(sha)
            out.append(sha)
    return out
REVERT_SUBJECT_RE = re.compile(r'^Revert\s+"?(.+?)"?\.?$')

FIELD_SEP = "\x1f"
RECORD_SEP = "\x1e"


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()[:200]}")
    return out.stdout


class GitAdapter(Adapter):
    name = "git"

    def collect(self) -> list[Event]:
        repo = (self.root / self.source.path).resolve()
        if not (repo / ".git").exists():
            return []
        repo_name = self.source.options.get("service") or repo.name
        svc_id = f"svc-{slugify(repo_name)}"
        rev = self._refresh(repo)
        events: list[Event] = []
        events.extend(self._tags(repo, repo_name, svc_id))
        events.extend(self._adr_commits(repo, repo_name, svc_id, rev))
        events.extend(self._reverts(repo, repo_name, svc_id, rev))
        return events

    def _refresh(self, repo: Path) -> str:
        """Fetch latest on the configured (or default) branch; return the rev
        to read history from. `pull = true` also fast-forwards the local
        branch (ff-only — never creates merge commits in a working tree)."""
        branch = self.source.options.get("branch") or _default_branch(repo)
        # `init` ingests with no git network I/O: a fetch per detected repo can
        # block for the full 120s timeout each when an SSH remote wants a
        # passphrase, and a fresh clone already has the history. `pull` and
        # `ingest` are where fetching happens.
        if self.offline or not _has_remote(repo):
            return branch or "HEAD"
        try:
            _git(repo, "fetch", "--quiet", "origin", *( [branch] if branch else [] ))
        except RuntimeError:
            return branch or "HEAD"  # offline: read what we have
        if self.source.options.get("pull"):
            try:
                _git(repo, "pull", "--ff-only", "--quiet", "origin", branch)
                return branch
            except RuntimeError:
                pass  # diverged working tree: fall through to remote ref
        return local_rev(repo, branch)

    def _tags(self, repo: Path, repo_name: str, svc_id: str) -> list[Event]:
        seen = set(self.state.setdefault("tags", []))
        try:
            raw = _git(repo, "for-each-ref", "refs/tags",
                       f"--format=%(refname:short){FIELD_SEP}%(creatordate:short){FIELD_SEP}%(subject)")
        except RuntimeError:
            return []
        events = []
        for line in raw.strip().splitlines():
            tag, date, subject = (line.split(FIELD_SEP) + ["", ""])[:3]
            if not tag or tag in seen:
                continue
            seen.add(tag)
            events.append(Event(
                id=f"evt-{date.replace('-', '') or 'undated'}-{slugify(repo_name)}-release-{slugify(tag)}",
                type="release", ts=date or "1970-01-01",
                title=f"{repo_name} release {tag}",
                summary=subject[:200], impact="low", source=self.name,
                refs=[Ref(kind="commit", id=tag)],
                derived_entities=[_svc_entity(svc_id, repo_name, self.source.path)],
                # No `released` edge: a tag is not a graph entity, so assembly
                # degraded every one of them into a `released: <tag>` key fact
                # (686 on postgres) that buried the repo's identifying facts.
                # The tag is on the event (id, title, ref) and summarized once
                # on the repo entity by topology.repo_facts().
            ))
        self.state["tags"] = sorted(seen)
        return events

    def _reverts(self, repo: Path, repo_name: str, svc_id: str, rev: str = "HEAD") -> list[Event]:
        """One event per revert, carrying git's own stated reason and a `reverts`
        edge to what it undid.

        This is the timeline the "has this been tried before?" question needs.
        The revert body is the rationale verbatim, so no model is involved and
        the result survives `rebuild`. Reverts that name no parent commit still
        get an event: the reversal is the fact, the link is a bonus.
        """
        last = self.state.get("reverts_at", "")
        rev_range = f"{last}..{rev}" if last else rev
        try:
            raw = _git(repo, "log", rev_range, "--date=short", "--grep=^Revert",
                       f"--format=%H{FIELD_SEP}%ad{FIELD_SEP}%s{FIELD_SEP}%b{RECORD_SEP}")
        except RuntimeError:
            return []
        events, head = [], ""
        for rec in raw.split(RECORD_SEP):
            rec = rec.strip()
            if not rec:
                continue
            sha, date, subject, body = (rec.split(FIELD_SEP) + ["", "", ""])[:4]
            head = head or sha
            m = REVERT_SUBJECT_RE.match(subject.strip())
            undone = _reverted_shas(body, is_revert=bool(m))
            what = (m.group(1) if m else subject).strip()
            refs = [Ref(kind="commit", id=sha[:12])]
            relations, lead = [], ""
            if undone:
                refs += [Ref(kind="commit", id=p) for p in undone]
                relations += [{"source": sha[:12], "predicate": "reverts", "target": p}
                              for p in undone]
                # stated up front, not buried in the body
                lead = f"Reverts {', '.join(undone)}. "
            events.append(Event(
                id=f"evt-{date.replace('-', '')}-{slugify(repo_name)}-revert-{sha[:8]}",
                type="decision", ts=date,
                title=f"{repo_name}: reverted {what[:100]}",
                summary=(lead if undone else "") + body.strip()[:600],
                impact="medium",              # a reversal outranks an ordinary commit
                source=self.name,
                refs=refs,
                derived_entities=[_svc_entity(svc_id, repo_name, self.source.path)],
                derived_relations=[{"source": svc_id, "predicate": "changed_by",
                                    "target": sha[:12]}] + relations,
            ))
        if head:
            self.state["reverts_at"] = head
        return events

    def _adr_commits(self, repo: Path, repo_name: str, svc_id: str, rev: str = "HEAD") -> list[Event]:
        last = self.state.get("last_commit", "")
        rev_range = f"{last}..{rev}" if last else rev
        try:
            raw = _git(repo, "log", rev_range, "--date=short",
                       f"--format=%H{FIELD_SEP}%ad{FIELD_SEP}%s{RECORD_SEP}")
        except RuntimeError:
            return []
        events = []
        head = ""
        for rec in raw.split(RECORD_SEP):
            rec = rec.strip()
            if not rec:
                continue
            sha, date, subject = (rec.split(FIELD_SEP) + ["", ""])[:3]
            head = head or sha
            adr_ids = {m.lower() for m in ADR_ID_RE.findall(subject.lower())}
            if not adr_ids:
                continue
            events.append(Event(
                id=f"evt-{date.replace('-', '')}-{slugify(repo_name)}-{sha[:8]}",
                type="pr_merged", ts=date,
                title=f"{repo_name}: {subject[:120]}",
                impact="low", source=self.name,
                refs=[Ref(kind="commit", id=sha[:12])] + [Ref(kind="adr", id=a) for a in sorted(adr_ids)]
                    + [Ref(kind="file", id=f) for f in _changed_files(repo, sha)],
                derived_entities=[_svc_entity(svc_id, repo_name, self.source.path)],
                derived_relations=[
                    {"source": svc_id, "predicate": "changed_by", "target": sha[:12]}
                ] + [
                    {"source": sha[:12], "predicate": "implements", "target": a} for a in sorted(adr_ids)
                ],
            ))
        if head:
            self.state["last_commit"] = head
        return events


def local_rev(repo: Path, branch: str = "") -> str:
    """The rev to read history from without fetching. A plain `pull` moves the
    remote-tracking ref but not the working tree, so prefer origin/<branch>:
    reading HEAD would miss everything that was just fetched."""
    branch = branch or _default_branch(repo)
    if branch and _rev_exists(repo, f"origin/{branch}"):
        return f"origin/{branch}"
    return branch or "HEAD"


class FetchResult(NamedTuple):
    gained: int = 0       # commits gained on origin/<branch>
    tree_before: str = "" # HEAD before the fast-forward
    tree_after: str = ""  # HEAD after it
    behind: int = 0       # how far HEAD still trails origin/<branch>
    error: str = ""       # the fetch itself failed
    warning: str = ""     # fetched, but the working tree could not advance

    @property
    def moved(self) -> bool:
        return bool(self.tree_after) and self.tree_after != self.tree_before


def fetch(repo: Path, branch: str = "", ff: bool = True) -> FetchResult:
    """Deliberate network refresh, for `tsubasa pull`.

    Fast-forwards the WORKING TREE by default, because the adr/doc/incident
    adapters read files from the tree, not from git. Fetching alone would
    refresh commit history while re-ingesting last month's ADRs and
    postmortems, and report success while doing it. Strictly --ff-only, so it
    can never merge or clobber. Errors are returned, never raised: one
    unreachable remote must not stop the other repos."""
    if not _has_remote(repo):
        return FetchResult()
    branch = branch or _default_branch(repo)
    ref = f"origin/{branch}" if branch else "FETCH_HEAD"
    before_ref = _rev(repo, ref)
    tree_before = _rev(repo, "HEAD")[:7]
    try:
        _git(repo, "fetch", "--quiet", "origin", *([branch] if branch else []))
    except RuntimeError as e:
        return FetchResult(tree_before=tree_before, tree_after=tree_before, error=str(e))
    after_ref = _rev(repo, ref)
    gained = _count(repo, before_ref, after_ref) if before_ref and after_ref else 0
    result = FetchResult(gained=gained, tree_before=tree_before, tree_after=tree_before)
    if not ff:
        return result
    blocked = _cannot_ff(repo, branch)
    if not blocked:
        try:
            _git(repo, "merge", "--ff-only", "--quiet", ref)
        except RuntimeError as e:
            blocked = f"diverged from origin/{branch}: {e.args[0][:120]}"
    behind = _count(repo, "HEAD", ref) if branch else 0
    return result._replace(tree_after=_rev(repo, "HEAD")[:7], behind=behind,
                           warning=blocked)


def _cannot_ff(repo: Path, branch: str) -> str:
    """Why the working tree must be left alone, or ""."""
    if not branch:
        return "no tracked branch"
    if not _rev_exists(repo, f"origin/{branch}"):
        return f"no origin/{branch}"
    try:
        _git(repo, "symbolic-ref", "--quiet", "HEAD")
    except RuntimeError:
        return "detached HEAD"
    try:
        if _git(repo, "status", "--porcelain").strip():
            return "uncommitted changes"
    except RuntimeError as e:
        return str(e)
    return ""


def _rev(repo: Path, ref: str) -> str:
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", ref).strip()
    except RuntimeError:
        return ""


def _count(repo: Path, before: str, after: str) -> int:
    if not before or not after or before == after:
        return 0
    try:
        return int(_git(repo, "rev-list", "--count", f"{before}..{after}").strip() or 0)
    except (RuntimeError, ValueError):
        return 0


def _default_branch(repo: Path) -> str:
    try:
        ref = _git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").strip()
        if ref.startswith("refs/remotes/origin/"):
            return ref.rsplit("/", 1)[-1]
    except RuntimeError:
        pass
    for candidate in ("main", "master"):
        if _rev_exists(repo, candidate):
            return candidate
    return ""


def _has_remote(repo: Path) -> bool:
    try:
        return "origin" in _git(repo, "remote").split()
    except RuntimeError:
        return False


def _rev_exists(repo: Path, rev: str) -> bool:
    try:
        _git(repo, "rev-parse", "--verify", "--quiet", rev)
        return True
    except RuntimeError:
        return False


def _changed_files(repo: Path, sha: str, limit: int = 10) -> list[str]:
    try:
        raw = _git(repo, "show", "--name-only", "--format=", sha)
    except RuntimeError:
        return []
    return [f for f in raw.splitlines() if f.strip()][:limit]


def _svc_entity(svc_id: str, repo_name: str, path: str) -> dict:
    return {
        "id": svc_id, "type": "service", "name": repo_name,
        "description": f"Repository at {path}",
    }
