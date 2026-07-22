---
name: captain-sync
description: Use ONLY when the user mentions completed or in-flight work — "just merged the PR", "shipped it", "started on the auth task" — asks for task status, or explicitly asks to refresh/re-ingest. Never fire on ordinary questions; answering questions is captain-recall's read-only job.
---

# Captain sync

Keep task state and the graph current from the real world.

## Steps

1. Run `tsubasa ingest` (all sources; incremental via cursors). It detects
   ADR ids in branch names / PR titles and moves tasks with evidence.
2. Run `tsubasa task list` and report only what CHANGED — one line per
   transition, with its evidence (PR number, event id).
3. If `tsubasa questions` shows open reconciliation questions, surface them
   briefly now — this is the "next natural moment".
4. If the working tree gained `.tsubasa/` or `docs/adr/` changes, remind the
   user they are commit-ready (never commit without being asked).

## Manual transitions

When the user states work status that sources can't see yet
("I'm picking up the session task"):
`tsubasa task set <task-id> <state> --by user --evidence "<what they said>"`
