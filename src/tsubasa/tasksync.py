"""Task sync: the ADR ID is the thread (DESIGN.md §5.3).

After ingest, any event that references an ADR moves the tasks linked to
that ADR — always with evidence, never silently:

    commit mentioning the ADR   → in_progress
    PR merged carrying the ADR  → done
"""

from __future__ import annotations

from .models import Event, Task


def sync(tasks: dict[str, Task], new_events: list[Event]) -> list[str]:
    by_adr: dict[str, list[Task]] = {}
    for t in tasks.values():
        if t.adr:
            by_adr.setdefault(t.adr, []).append(t)

    notes: list[str] = []
    for ev in sorted(new_events, key=lambda e: e.ts):
        adr_ids = {r.id for r in ev.refs if r.kind == "adr"} | set(ev.supersedes)
        pr_ids = [r.id for r in ev.refs if r.kind == "pr"]
        for adr_id in adr_ids:
            for task in by_adr.get(adr_id, []):
                if task.state in ("done", "abandoned"):
                    continue
                for pr in pr_ids:
                    if pr not in task.prs:
                        task.prs.append(pr)
                target = "done" if ev.type == "pr_merged" and pr_ids else "in_progress"
                # plain commits only ever push a task forward to in_progress
                if target == "in_progress" and task.state in ("in_review",):
                    continue
                evidence = f"{ev.id}" + (f" via {pr_ids[0]}" if pr_ids else "")
                if task.transition(target, by="adapter:" + ev.source, evidence=evidence, ts=ev.ts):
                    notes.append(f"task: {task.id} -> {target} ({evidence})")
    return notes
