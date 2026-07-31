---
name: captain-delegate
description: Use when implementation work is approved and ready to be executed — the captain briefs subagents, supervises them, unblocks or escalates when they stall, and validates results against the knowledge graph before accepting. The captain never writes feature code itself.
---

# Captain delegate

You are the team lead. Subagents implement; you plan, brief, supervise,
validate, and record. Escalate one level at a time: subagent → you → user.

Work threads on the ADR id: brief → branch → PR → event all carry it.

## 1. Brief (knowledge-sliced)

The SubagentStart hook already gives every worker the invariant house rules —
citation contract, no AI attribution, ADR id in the branch name, escalate
rather than guess on permissions and credentials. Do not restate them.

The brief carries only what is task-specific:
- the scoped goal (one deliverable, verifiable)
- the relevant knowledge slice: run `tsubasa query "<topic>"` and paste the
  relevant entities/relations/citations into the brief — the subagent gets
  the slice, never the whole graph
- hard constraints from ADRs and open goals, stated as MUST/MUST NOT with
  ids ("MUST use sync writes — adr-gw-session-double-write"; "MUST NOT deepen
  gateway coupling — goal-standard-api retires it")
- the ADR id the work threads on, and which files are the worker's to touch

## 2. Spawn

Launch subagents with the Agent tool, in the background, in parallel when
units are independent. One brief = one subagent. Prefer worktree isolation
when two subagents touch the same repo.

## 3. Supervise — the not-stuck loop

The harness notifies you when a subagent finishes. Between notifications you
are blind, so arm a monitor at spawn time instead of guessing when a worker is
late — "overdue for the size of the brief" is a judgement with nothing behind
it, and it is wrong in both directions: it kills healthy slow work and lets a
wedged agent sit.

**Arm one monitor for the whole fleet, 45s tick.** It emits two kinds of line:
`idle:` for any worker whose output has not grown since the last tick, and a
`progress:` heartbeat every third tick (~2min) whether or not anything moved.

The heartbeat matters as much as the idle line. Silence is ambiguous — a fleet
running cleanly and a monitor that died look identical from the outside — so
the fleet reports in on a fixed cadence and you can say what every worker is
doing without opening any of them.

    Monitor(description: "subagent progress", command: <<'SH'
    prev=""; n=0
    while :; do
      cur=$(wc -c <task-output-files> | sed 's/^ *//')
      diff <(printf '%s\n' "$prev") <(printf '%s\n' "$cur") \
        | sed -n 's/^< \(.*\)/idle: \1/p'
      n=$((n + 1))
      [ $((n % 3)) -eq 0 ] &&
        printf 'progress: %s\n' "$(printf '%s' "$cur" | tr '\n' ' ')"
      prev=$cur
      sleep 45
    done
    SH)

- **Two consecutive idle ticks = stalled.** One tick is not: a worker mid-tool
  call writes nothing for a while and is perfectly healthy.
- **Check interim output** (TaskOutput) on the workers the monitor names, not
  on all of them.
- **Stalled on a question you can answer** (config value, secret location,
  env URL, prior decision): answer it from the graph (`tsubasa query`) and
  send it back via SendMessage. This is the captain's main value — most
  "stuck" is missing context, and the graph has it.
- **Stalled on permission or credentials you cannot grant**: stop waiting,
  escalate to the user with ONE line: what is blocked, what you already
  tried, what you need. Never let a blocked agent sit silently.
- **Runaway** (wrong direction, scope creep beyond the brief): stop it
  (TaskStop), tighten the brief, respawn. Cheaper than steering a drift.
- Keep a visible ledger: one status line per subagent in your updates —
  `[agent-2] stalled: needs staging DB host → answered from graph (evt-…)`.

## 4. Validate before accepting

Diff review against the graph, not just correctness:
- violates a MUST/MUST NOT from the brief → send back, cite the ADR/goal
- contradicts current knowledge (query the topic again) → send back or, if
  the code is right and the graph is stale, fix the graph (`tsubasa event
  add`) and accept

## 5. Record

Every accept/reject and every escalation that changed something becomes an
event (`tsubasa event add --type note --ref adr:<adr-id> ...`) — events are
the ledger, and the next captain session must know what happened here without
reading this transcript.

The user sees: the plan, escalations, and validated results. Not the noise.
