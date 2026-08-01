---
name: captain-recall
description: Use when the user asks WHY or WHAT-HAPPENED questions about this system — historical decisions, past incidents, why code/config is shaped a certain way, where something is deployed, who decided what, or the state of work in flight. Queries the tsubasa knowledge graph for cited answers.
---

# Captain recall

You are the Captain of this repo (persona and hot knowledge were loaded at
session start from `.tsubasa/persona.md` and `.tsubasa/memory/hot.md`).

**This skill is READ-ONLY.** Never edit `captain.toml`, never run
`tsubasa ingest`, never write events from here. If the graph is empty or
missing, say so and offer — in one line — to set up / refresh the captain;
act only after the user says yes (that's onboard territory).

## Steps

1. **Bridge the question onto the graph's own words first.** The query matcher
   is lexical: a question phrased in words the graph never uses returns noise,
   not nothing — and answering from noise is how wrong answers happen. Run
   `tsubasa vocab <stems>` with 3-6 short stems guessed from the question's
   concepts (stems are substrings, so `stat` finds `pgstats` without knowing
   the word). Then pick up to 12 tokens FROM THAT OUTPUT that match the
   question's intent. Hard rules:
   - Pick only tokens the vocab output actually lists. Never invent a token,
     never substitute a near-synonym from your own knowledge.
   - A question concept with no plausible token in the vocab is skipped, not
     approximated.
   - If no stems hit at all, try one more round of different stems; if still
     nothing, the graph does not talk about this — fall to step 6 (repo
     evidence), do not fabricate a search.
   - Say what you did, in one line: `query terms (from graph vocab): ...` —
     the expansion is part of the answer's audit trail.
2. Run `tsubasa query "<the picked tokens>"`. It returns matched entities,
   a 2-hop relation subgraph, and source events — every line carries citations
   (event ids, ADR ids, PR numbers, file paths). When the question's own words
   already appear in the vocab output verbatim, querying with them directly is
   fine — the bridge matters when wording differs.
3. If the match is thin, check `.tsubasa/memory/index.md` for the entity's
   canonical id and query again with that id.
4. If a matched entity still lacks detail (no key_facts, one-line
   description) and its source events carry a `ref doc:<path>` citation,
   Read that file (path is relative to the workspace root) — prose docs
   only surface their title + first paragraph as an entity, so deeper
   detail lives in the file itself, not the query output.
5. Answer from the returned context, plus anything read in step 4, plus
   anything you verified yourself in the repo below. What you may NOT add is
   unsourced recollection. When a record shows a reversal or removal and the
   record does not state why (the query output marks these
   "reason: not recorded"), say the reason is not recorded and stop — a
   supplied cause is fabrication.
6. **The graph is a layer over the workspace, not a replacement for it.** If the
   graph does not carry something, that is a fact about the graph, never about
   the repo: `git log --before=<date> -- <file>` and `git show <sha>:<file>`
   answer questions no snapshot can. Evidence you gathered yourself is evidence.
   Never write "not recorded" about something you just read.

## History questions want the timeline, not the snapshot

A plain query answers with the *current* state. When the question is about how the
topic reached that state, run `tsubasa query --timeline "<topic>"` instead: it returns
the topic's events in ascending order with `reverts` / `supersedes` transitions marked.
The topic goes through the same vocab bridge as step 1 — timeline title-matching is
just as lexical as entity matching.

Use it for:

- **"has this been tried?"** — an attempt that was later reverted is still an answer.
- **"why is X like this?"** — the reason lives in the transition, not the end state.
- **"what is the status of X?"** — the status is the last transition, not the first.

Read the sequence to its end before answering. Measured failure: the snapshot said a
feature was "added", so the captain reported it as present; it had been reverted seven
months later.

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
- **Lead with the literal recorded value**, exactly as recorded, before any context.
  Answer the question asked, not the question you would rather answer. If the record
  is superseded, give its recorded status first and name the successor after it; the
  successor never becomes the headline.
- **Never emit an id you did not read.** An event or entity id may appear in your
  answer only if it appeared verbatim in this session's `tsubasa query` output. Otherwise
  cite the file path or the commit. An invented `evt-<today>-...` id is a fabricated
  citation and sinks the answer even when everything else is right.
- **Separate the record from what you know.** State what the record says with its
  citation. Context you cannot cite is still worth giving, but mark it:
  "not recorded here, from general knowledge". Never let an uncited claim stand in
  the same voice as a cited one, and never let one contradict the record.
- Trust hierarchy: code snapshot (`code@repo:sha` provenance) > ADRs and user
  statements > other docs. Anything marked [trust=low — doc-derived] must be
  verified against the code before you assert it as fact.
- Proactively flag only critical security / performance / risk findings.
- Prefer ASCII flows or comparison tables over prose when explaining structure.
- Knowledge marked [SUPERSEDED] or [DISPUTED] must be labeled as such.
