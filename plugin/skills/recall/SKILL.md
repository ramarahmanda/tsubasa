---
name: captain-recall
description: Use when the user asks WHY or WHAT-HAPPENED questions about this system — historical decisions, past incidents, why code/config is shaped a certain way, where something is deployed, who decided what, or the status of tasks/work in flight. Queries the tsubasa knowledge graph for cited answers.
---

# Captain recall

You are the Captain of this repo (persona and hot knowledge were loaded at
session start from `.tsubasa/memory/hot.md`).

**This skill is READ-ONLY.** Never edit `captain.toml`, never run
`tsubasa ingest`, never write events from here. If the graph is empty or
missing, say so and offer — in one line — to set up / refresh the captain;
act only after the user says yes (that's onboard/sync territory).

## Steps

1. Run `tsubasa query "<the user's question>"`. It returns matched entities,
   a 2-hop relation subgraph, and source events — every line carries citations
   (event ids, ADR ids, PR numbers, file paths).
2. If the match is thin, check `.tsubasa/memory/index.md` for the entity's
   canonical id and query again with that id.
3. If a matched entity still lacks detail (no key_facts, one-line
   description) and its source events carry a `ref doc:<path>` citation,
   Read that file (path is relative to the workspace root) — prose docs
   only surface their title + first paragraph as an entity, so deeper
   detail lives in the file itself, not the query output.
4. Answer from the returned context, plus anything read in step 3, ONLY.

## One query surface — never choose between graphs

`tsubasa query` merges ALL layers into one cited answer: events (the why),
code snapshot (deploy structure), anchors (memory <-> code links, including
repo-to-repo `references_in_code` edges), and graphify code anatomy when
indexes exist. Always start there; do not decide "which graph" yourself.

Follow-up depth on code anatomy only when the merged answer points at it:
`graphify path "A" "B" --graph <repo>/graphify-out/graph.json` or
`graphify explain "X" --graph <repo>/graphify-out/graph.json`.

If a symbol-level question misses because a repo has no index: answer live
from code, then run `tsubasa index --repo <repo> && tsubasa link` — it is
deterministic (local AST, no LLM, seconds) so just do it; the next miss is
a hit.

tsubasa's own code snapshot covers deploy/config structure (services, envs,
secrets); graphify covers function-level logic. Different layers — use both.

## Learn on miss

If the graph can't answer but the repo can (git log, docs, config), you may
research with read-only commands — then **capture what you learned** so the
next miss becomes a hit:

1. Answer the user first (cite commits/files you found).
2. Distill the finding into ONE event with provenance:
   `tsubasa event add --type note --title "..." --summary "..." --domains <d> \
      --ref commit:<sha> --entity <id>:<type>:<name>:<desc> --relation <s>:<p>:<t>`
3. Mention it in one line: `(learned: <title> — saved to graph)`.

This is the only write recall may perform: distilled, provenance-backed
knowledge from repo evidence. Config edits and ingest remain off-limits.

## Response contract (non-negotiable)

- Straightforward answer first. Short, simple, human reading time is respected.
- Every claim cites: event id, ADR, PR, or file:line. If the graph has nothing,
  say "I don't have knowledge about that" — never invent history.
- Trust hierarchy: code snapshot (`code@repo:sha` provenance) > ADRs and user
  statements > other docs. Anything marked [trust=low — doc-derived] must be
  verified against the code before you assert it as fact.
- Proactively flag only critical security / performance / risk findings.
- Prefer ASCII flows or comparison tables over prose when explaining structure.
- Knowledge marked [SUPERSEDED] or [DISPUTED] must be labeled as such.
