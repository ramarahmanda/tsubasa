"""GitHub adapter: merged PRs via `gh` CLI → pr_merged events, threaded by ADR id.

Skips gracefully when `gh` is missing or the path isn't a GitHub repo, so
offline/self-hosted setups still work with the plain git adapter.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..models import ADR_ID_RE, Event, Ref, slugify
from .base import Adapter


class GithubAdapter(Adapter):
    name = "github"

    def collect(self) -> list[Event]:
        if shutil.which("gh") is None:
            return []
        repo = (self.root / self.source.path).resolve()
        if not (repo / ".git").exists():
            return []
        limit = int(self.source.options.get("limit", 100))
        try:
            out = subprocess.run(
                ["gh", "pr", "list", "--state", "merged", "--limit", str(limit),
                 "--json", "number,title,mergedAt,headRefName,url,files,author,reviewDecision,latestReviews"],
                cwd=repo, capture_output=True, text=True, timeout=120,
            )
            if out.returncode != 0:
                return []
            prs = json.loads(out.stdout or "[]")
        except (subprocess.SubprocessError, json.JSONDecodeError):
            return []

        repo_name = self.source.options.get("service") or repo.name
        svc_id = f"svc-{slugify(repo_name)}"
        seen = set(self.state.setdefault("prs", []))
        events: list[Event] = []
        for pr in prs:
            num = f"PR-{pr['number']}"
            if num in seen:
                continue
            seen.add(num)
            date = (pr.get("mergedAt") or "")[:10] or "1970-01-01"
            branch = pr.get("headRefName", "")
            hay = f"{pr.get('title', '')} {branch}".lower()
            adr_ids = sorted({m for m in ADR_ID_RE.findall(hay)})
            files = [f["path"] for f in pr.get("files", [])][:20]
            author = (pr.get("author") or {}).get("login", "")
            latest = [r for r in pr.get("latestReviews") or [] if isinstance(r, dict)]
            reviewers = [l for l in ((r.get("author") or {}).get("login", "") for r in latest) if l]
            events.append(Event(
                id=f"evt-{date.replace('-', '')}-{slugify(repo_name)}-pr{pr['number']}",
                type="pr_merged", ts=date,
                title=f"{repo_name} {num}: {pr.get('title', '')[:120]}",
                summary=_review_arc(latest),
                actors=[a for a in dict.fromkeys([author, *reviewers]) if a],
                impact="low", source=self.name,
                refs=[Ref(kind="pr", id=num), Ref(kind="url", id=pr.get("url", ""))]
                    + [Ref(kind="adr", id=a) for a in adr_ids]
                    + [Ref(kind="file", id=f) for f in files[:5]],
                derived_entities=[{
                    "id": svc_id, "type": "service", "name": repo_name,
                    "description": f"Repository at {self.source.path}",
                }],
                derived_relations=[{"source": num, "predicate": "merged_into", "target": svc_id}]
                    + [{"source": num, "predicate": "implements", "target": a} for a in adr_ids],
            ))
            if adr_ids:
                review_ev = self._review_event(repo, pr, num, date, repo_name, adr_ids)
                if review_ev is not None:
                    events.append(review_ev)
        self.state["prs"] = sorted(seen)
        return events

    def _review_event(self, repo: Path, pr: dict, num: str, date: str,
                      repo_name: str, adr_ids: list[str]) -> Event | None:
        """One note per ADR-carrying PR whose review pushed back: the
        changes-requested rationale is exactly the knowledge the merge fact
        drops, in the reviewer's own words. Gated on ADR id AND pushback so
        routine merges stay un-evented. `latestReviews` cannot see pushback a
        reviewer later upgraded to an approval, so the full review history is
        fetched, but only for these few PRs. Refs carry `pr` and `adr`, both
        timeline node kinds, so `query --timeline <adr>` threads decision,
        merge and review together."""
        try:
            out = subprocess.run(
                ["gh", "pr", "view", str(pr["number"]), "--json", "reviews"],
                cwd=repo, capture_output=True, text=True, timeout=60,
            )
            if out.returncode != 0:
                return None
            reviews = [r for r in json.loads(out.stdout or "{}").get("reviews", [])
                       if isinstance(r, dict)]
        except (subprocess.SubprocessError, json.JSONDecodeError):
            return None
        pushback = [r for r in reviews if r.get("state") == "CHANGES_REQUESTED"]
        if not pushback:
            return None
        body = "\n\n".join(
            f"{(r.get('author') or {}).get('login', 'reviewer')}: {(r.get('body') or '').strip()[:600]}"
            for r in pushback if (r.get("body") or "").strip())
        reviewers = sorted({l for l in ((r.get("author") or {}).get("login", "")
                                        for r in reviews) if l})
        return Event(
            id=f"evt-{date.replace('-', '')}-{slugify(repo_name)}-pr{pr['number']}-review",
            type="note", ts=date,
            title=f"{repo_name} {num} review: changes requested on {', '.join(adr_ids)}",
            summary=_review_arc(reviews), impact="medium", source=self.name,
            actors=reviewers, body=body,
            refs=[Ref(kind="pr", id=num), Ref(kind="url", id=pr.get("url", ""))]
                + [Ref(kind="adr", id=a) for a in adr_ids],
        )


def _review_arc(reviews: list[dict]) -> str:
    """One line of who judged what, in order: "review: changes requested by
    alice, then approved by bob". Comment-only reviews carry no verdict and
    are left out."""
    steps = []
    for r in reviews:
        state = {"APPROVED": "approved",
                 "CHANGES_REQUESTED": "changes requested"}.get(str(r.get("state", "")))
        login = (r.get("author") or {}).get("login", "")
        if state and login:
            steps.append(f"{state} by {login}")
    return "review: " + ", then ".join(steps) if steps else ""
