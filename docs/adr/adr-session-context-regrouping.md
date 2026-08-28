# adr-session-context-regrouping

- status: accepted
- date: 2026-08-29

## Context

```
turn 1..5    akasha AB    vault akasha/ab/db, deploy/ab/values.yaml (edited)
turn 6..9    akasha ZY    kubectl -n akasha-zy, deploy/zy/values.yaml (read)
turn 10      "push the fix"
                --> resolves to the most recent target, ZY
                --> ZY's secret reaches AB's pipeline
```

Constraints:
- The captain already holds the session in its own context; a second summarizer duplicates it.
- Persona prose telling the captain to re-clarify is advisory. Instruction-only mechanisms measured at 1/12 compliance (adr-query-vocab-bridge).
- No persisted session state: a session file goes stale on `/compact` and needs a lifecycle nobody maintains.
- False-asks cost more than misses. A captain that interrupts straight-line work is worse than one that drifts.

## Decision

Split: the hook decides *when to speak* (mechanical, no model call), the captain decides *what the groups are* (judgment, already in context).

```
UserPromptSubmit hook
  reads prompt + transcript_path
  a) >=2 distinct target sets in the session?
        paths (first 2 segments), -n <ns>, vault paths, hosts, repo names
  b) prompt is deictic?
        short closed list: push/deploy/run/apply/ship/commit + it/that/the fix/same
        AND no target token present in the prompt
  a AND b  --> additionalContext: "STOP. Compact the session, prioritise the
                last 10 turns, group the contexts, confirm before acting."
  else     --> exit 0, silent, zero cost

captain
  compacts the WHOLE session, weighted toward the last 10 turns
  renders per group: doing / access / dev / test / next
  asks [1]?  --> the user's one-word answer is the scope, and it lives
                 in the transcript. Nothing else stores it.
```

Pointers: plugin/hooks/context_check.sh, plugin/hooks/hooks.json,
src/tsubasa/cli.py (context-check), plugin/hooks/agent_spawn.sh (delivery precedent).

## Data source mapping

| data | from | join key | assumption |
|---|---|---|---|
| session turns | `transcript_path` JSONL on hook stdin | session | Claude Code transcript schema stable |
| target sets | regex over tool inputs and prompts | normalized token | distinct targets imply distinct contexts |
| deixis | prompt vs short closed verb/pronoun list | none | short list keeps precision high |
| the grouping | captain's own context, whole session, recent-weighted | none | no extra model call, no stored summary |

## Phases

1. `hooks.json` registers `UserPromptSubmit`; `tsubasa context-check` prints the injection or nothing.
2. Card format travels in the injected text, not in persona.md, so it ships with the mechanism.
3. Benchmark arm: single-context (false-ask ~0), two-context + deictic (must catch), two-context + explicit target (must stay silent).
4. Tune the deictic list against false-ask rate before release.

### BREAKING CHANGE

`plugin/hooks/hooks.json` gains a `UserPromptSubmit` entry. Every captain
picks it up on `tsubasa upgrade`. Behavior change: an ambiguous prompt in a
multi-target session now yields a confirmation question before any tool call.
Additive only: no graph schema change, no `captain.toml` key, no stored state.

## Consequences

- Scope confirmation becomes a mechanism instead of persona prose.
- The confirmation lives in the transcript, so it needs no lifecycle and cannot go stale.
- Self-healing: after `/compact` the grouping is gone, re-derived on the next ambiguous prompt.
- Carrying a credential across contexts now requires an explicit answer.

## Risks

- False-asks on single-context sessions. Mitigated by requiring both conditions, never one.
- Regex target extraction misses shapes it has no pattern for; those fall through to prose, which is today's baseline.
- The transcript schema is not a public contract. A format change silently disarms the hook. Mitigation: exit 0 on any parse failure, and a test pinned to a recorded fixture.

## Goal alignment

- No open goals recorded (`tsubasa goal list`: none); no conflicts.
