---
name: captain-capture
description: Use at DECISION MOMENTS — the user accepts a proposed design/approach after discussion, approves a plan, or concludes "let's do X". Persists the decision as an ADR + task + event in the tsubasa knowledge graph so it is never lost.
---

# Captain capture

A decision was just accepted in conversation. Persist it — this is the moment
that makes the Captain the one who "remembers everything".

## Steps

1. Determine: decision title, domain(s), impact (high/medium/low), which
   existing entities it touches, and whether it supersedes a previous decision
   (check `tsubasa query "<topic>"` first).
   **Also check `tsubasa goal list`** — every plan/design must state, in one
   line, how it aligns or conflicts with each relevant open goal ("supports
   goal-decommission-legacy-sso" / "⚠ conflicts with goal-x because…"). Link
   supporting work with a `--relation <adr>:works_toward:<goal-id>`.
2. **Validation gate** — one line, before writing anything:
   `Saving: adr-<slug>, task[todo], links to <entities>[, supersedes <old>]. OK?`
   If it supersedes something recent or load-bearing, ask the one question that
   captures the WHY (e.g. "that ADR chose Kafka for ordering — has that
   requirement changed?") and put the answer in the event body.
3. On confirmation:
   - Draft the ADR at `docs/adr/adr-<slug>.md` (title, status: accepted, date,
     context, decision, consequences — cite prior incidents/ADRs from the graph).
   - `tsubasa event add --type decision --title "..." --impact <i> --domains <d> \
        --ref adr:adr-<slug> [--supersedes <old-entity-id>] \
        --entity adr-<slug>:adr:"<title>":"<one-line>" --body "..."`
   - `tsubasa task new --title "..." --adr adr-<slug> --domains <d>`
4. Tell the user the branch convention: include `adr-<slug>` in the branch or
   PR name and the task tracks itself from there.

## Rules

- ADR ids: `adr-<kebab-slug>`, stable forever, they are the thread that links
  task → branch → PR → code.
- Never store secret values; secret-refs only (name/location).
- One decision = one event. Do not batch.
