# adr-unanchored-query

- status: accepted
- date: 2026-08-29

## Context

```
tsubasa query "<general research question>"
  |-- lexical entity match      --> nothing
  |-- event-title match          --> nothing discriminating
  |-- empty match                --> vocab hint printed
  |-- zero title hits            --> paid haiku expansion fires
  '-- graphify anatomy printed regardless
        result: a question about the world comes back wearing citations
```

Constraints:
- The escalation ladder (adr-query-vocab-bridge) escalates hardest exactly when the match is weakest. Correct for recall, wrong for a question the graph should not answer.
- The captain cannot distinguish "the record says this" from "the record happens to share words with this".
- Persona rule 9 already separates the record from general knowledge, but nothing gives the captain a retrieval mode that returns nothing.
- The benchmark measures the default path; changing it invalidates comparison.

## Decision

One flag inverts the question the command answers.

```
tsubasa query "<q>"              what does the record SAY about this?
tsubasa query --unanchored "<q>" does the record CONTRADICT or CONSTRAIN this?

--unanchored:
  |-- lexical pass only, never semantic: no LLM call, no cost-log line
  |-- strong title hits only (titles_discriminate); common-stem near-misses dropped
  |-- suppressed: vocab hint, code anatomy, anchors, the 2-hop relation walk
  |-- no hits  --> NO RECORDED CONSTRAINT, answer built outside, marked uncited
  '-- hits     --> RECORDED CONSTRAINTS (N), existing citation format
rejected with --timeline; composes with --as-of
```

Pointers: src/tsubasa/cli.py (cmd_query, _print_constraints),
src/tsubasa/graph/query.py (strong_title_events).

## Data source mapping

| data | from | join key | assumption |
|---|---|---|---|
| constraints | event titles passing the rare-token gate | `_tokens` | a discriminating title hit is a real bearing, a common-stem hit is not |
| entity hits | lexical entity match | entity id | printed only alongside a strong title hit, never alone |
| absence | zero strong hits | none | silence in the graph is evidence of nothing, not evidence of absence |

## Phases

1. Flag, suppression, absence verdict: shipped.
2. Captain uses it for questions about the world rather than about this system: skill-instructed, a recorded limit.

### BREAKING CHANGE

None. Additive flag. The default query path, the timeline path, and the output
format are untouched, so benchmark results stay comparable across this change.

## Consequences

- Retrieval can return nothing, which it could not before.
- A research answer is uncited by construction rather than by discipline.
- Cheaper: the mode that used to trigger the expansion is the mode that now forbids it.

## Risks

- A constraint recorded only in an entity description, with no discriminating title, is invisible here. Accepted: that shape is exactly the noise the mode exists to drop.
- Phase 2 is instruction, and instruction-only mechanisms measured at 1/12 compliance (adr-query-vocab-bridge). If unused, the trigger moves into the CLI or a hook.

## Goal alignment

- No open goals recorded (`tsubasa goal list`: none); no conflicts.
