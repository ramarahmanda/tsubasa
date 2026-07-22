"""Git adapter: repo history → release events + ADR-linked work detection.

Emits:
  - release events for tags
  - pr_merged/config_change-ish events for merge commits whose branch name
    or message carries an ADR id (adr-<slug>) — these also drive task sync
  - a service entity for the repo itself
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..models import ADR_ID_RE, Event, Ref, slugify
from .base import Adapter

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
        return events

    def _refresh(self, repo: Path) -> str:
        """Fetch latest on the configured (or default) branch; return the rev
        to read history from. `pull = true` also fast-forwards the local
        branch (ff-only — never creates merge commits in a working tree)."""
        branch = self.source.options.get("branch") or _default_branch(repo)
        if not _has_remote(repo):
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
        if branch and _rev_exists(repo, f"origin/{branch}"):
            return f"origin/{branch}"
        return branch or "HEAD"

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
                derived_relations=[{"source": svc_id, "predicate": "released", "target": tag}],
            ))
        self.state["tags"] = sorted(seen)
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
