# adr-query-vocab-bridge

- status: accepted
- date: 2026-08-01

## Context

```
question (user's words) --> match_entities (lexical) --> entities or nothing
gold answer often lives in event titles the wording never touches:
  "freeze pass" vs title "anti-wraparound"; "formatted-output" vs "printf"
```

Constraints:
- Retrieval is lexical; a question phrased in words the graph never uses returns noise or nothing.
- Models do not follow optional skill instructions: `tsubasa vocab` was invoked in 1 of 12 benchmark sessions when instruction-driven.
- Raw hit-count ranking floods rare-token gold under wide queries.

## Decision

Bridge is in the CLI, not in instructions. Escalation ladder, cheapest first:

```
tsubasa query "<words>"
  |-- entity match (lexical, $0)
  |-- ALWAYS: event-title match, rarity-weighted (sum 1/freq, >=2 stems, cap 5)
  |       reverted events print outcome first: NOT PRESENT (reverted <date>)
  |-- empty entity match --> vocab hint: "graph tokens near your wording: ..."
  '-- zero title hits --> haiku selects <=6 rare vocab tokens, appended to
          the match text, match redone; audit line + cost log per call;
          any failure falls back to the lexical result (no env gate)
```

Pointers: src/tsubasa/graph/query.py (vocabulary, title_events, vocab_hint),
src/tsubasa/semantic.py, src/tsubasa/cli.py cmd_query, cmd_vocab.

## Data source mapping

| data | from | join key | assumption |
|---|---|---|---|
| vocab tokens + freq | entity ids/names/aliases + event titles | `_tokens` (same as matcher) | every listed token is matchable |
| semantic tokens | headless haiku over full vocab | verbatim membership check | invented tokens discarded |
| expansion cost | claude -p result JSON | per call | logged outside graph fingerprint |

## Phases

1. `tsubasa vocab` command + skill instruction: shipped, measured insufficient (1/12 compliance).
2. Auto lexical bridge (title match + hint): shipped, validated.
3. Semantic expansion, env-gated: shipped, validated on zero-overlap questions.
4. Rarity-weighted ranking, expansion cap 6: shipped, validated.
5. Ladder default: semantic auto-fires only on zero title hits; env gate removed. Open: G9 turn-1 tail unexplained.

### BREAKING CHANGE

None. `TSUBASA_SEMANTIC` / `TSUBASA_SEMANTIC_MODEL` existed only on this
branch, never in a release; no shipped contract changes. Behavior at the
first release carrying this ADR: a weak lexical result attempts one haiku
expansion, silent fallback without the claude CLI. `TSUBASA_SEMANTIC_LOG`
kept (cost-log path only). Output is additive; timeline path unchanged.

## Consequences

- Retrieval no longer depends on the model choosing to bridge.
- Benchmark g-git-history: 11/12 reach correct (baseline 8/12); turn-1 flips on G2, G3, G5, G7, G10.
- Semantic run on 4 questions cost less than lexical ($3.51 vs $4.25): saved nudge turns outweigh $0.04/call expansions.

## Risks

- Semantic over-selection injects noise events (observed once: G9 t2->t3 before ranking fix).
- Per-call expansion cost scales with vocab size; mitigations known (session-once, rare-token subset, cached prefix), not built.
- Honesty-probe failures (G12) are answer discipline, out of scope for retrieval.

## Goal alignment

- No open goals recorded (`tsubasa goal list`: none); no conflicts.
