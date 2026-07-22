"""GitHub adapter: merged PRs via `gh` CLI → pr_merged events + task sync.

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
                 "--json", "number,title,mergedAt,headRefName,url,files"],
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
            events.append(Event(
                id=f"evt-{date.replace('-', '')}-{slugify(repo_name)}-pr{pr['number']}",
                type="pr_merged", ts=date,
                title=f"{repo_name} {num}: {pr.get('title', '')[:120]}",
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
        self.state["prs"] = sorted(seen)
        return events
