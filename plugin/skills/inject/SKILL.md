---
name: captain-inject
description: Use when the user STATES A FACT about the system or tells the captain to remember/learn something — "update your knowledge", "remember this", "we dropped X for Y", "the outage was caused by Z", environment URLs, team contacts, deployment flows, tribal knowledge, corrections to what the Captain believes. Validates intent, connects to existing entities, persists to the tsubasa graph.
---

# Captain inject

The user asserted knowledge. Validate it, connect it, persist it.

**Destination is ALWAYS the tsubasa graph (`tsubasa event add`), never your
private memory directory.** The graph is the captain's shared, versioned
memory — it travels with the repo, feeds hot/warm tiers, and is queryable by
every session and teammate. Private memory hides knowledge on one machine.

## Steps

1. Classify: event type (note/incident/decision/config_change), entities
   involved, impact, domains. Run `tsubasa query "<topic>"` to find the
   existing entities and check for conflicts.
2. **Validation gate** — restate what you understood in one line:
   `Recording as <type>, domain=<d>, impact=<i>, links to <entities>[, supersedes <old>]. Correct?`
   - If it contradicts existing knowledge, ask the one WHY question before
     writing (the answer is the most valuable part of the event).
3. On confirmation, append:
   `tsubasa event add --type <t> --title "..." --impact <i> --domains <d> \
      [--supersedes <entity-id>] [--entity id:type:name:desc]... \
      [--relation src:pred:tgt]... [--ref kind:id]... --body "<the why>"`
4. Check output for reconciliation notes; if `tsubasa questions` has new open
   questions relevant to this, raise them now (once, briefly) — otherwise stay
   silent.

## A directory of knowledge, not a single fact

If the user points at a PLACE where knowledge lives ("our postmortems are in
docs/incidents", "we keep engineering principles in docs/principles"),
register a SOURCE instead of writing one event — the adapter keeps it in
sync forever:

    tsubasa source add incident docs/incidents
    tsubasa source add doc docs/principles --kind principle --impact high
    tsubasa source add adr docs/adr

Ask one question if the material is sensitive: "commit these files to the
captain repo, or keep them local-only (--no-commit) with just the distilled
knowledge committed?" Then `tsubasa ingest`.

## Future knowledge (goals)

When the user states intent about the FUTURE — "we plan to", "the goal is",
"by Q4 we want", roadmaps, quotations/procurement for upcoming infra — persist
it as a `plan` event with a `goal` entity:
`tsubasa event add --type plan --title "..." --entity <goal-id>:goal:"<name>":"<target state>" ...`
Open goals never decay out of hot memory and every future plan/design is
checked against them. Resolve later with `tsubasa goal set <id> achieved|dropped`.

**Commercial amounts are excluded by default**: when ingesting quotations,
budgets, or contracts, record WHAT is planned (items, capacity, vendor,
timeline) but omit prices/totals unless the user explicitly asks to store them.

## Trust

- Firsthand statements from the user: `--trust high`.
- Hearsay ("someone said", "I think"): `--trust low` — reconciliation will
  record a dispute rather than overwrite established knowledge.
