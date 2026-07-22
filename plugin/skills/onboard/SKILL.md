---
name: captain-onboard
description: Use when the user wants to CREATE a captain here — "set up tsubasa", "be my captain", "init a captain for this repo/workspace", "I want a captain-<name>". Scaffolds .tsubasa/, auto-detects knowledge sources, runs the first ingest, and reports what the captain learned.
---

# Captain onboard

Birth a captain in this repo or workspace. Everything through conversation —
the user never touches the CLI.

## Steps

1. **CLI present?** `which tsubasa` — if missing:
   `uv tool install tsubasa` (fallback: `uv tool install git+https://github.com/ramarahmanda/tsubasa`).
   If `uv` is missing too, ask before installing it.
2. **One round of questions** (single message, not an interview):
   - captain name (suggest one from the repo/org name, e.g. captain-<repo>)
   - role (default: Engineering Director)
   - domains (suggest 3-5 from what you see in the codebase)
3. **Auto-detect sources** — scan for:
   - git repos: `.git` here or in first-level subdirectories (workspace mode)
   - ADR/design/architecture docs: `docs/adr`, `docs/decisions`, `*/docs/*.md`
     (design docs are usually the highest-value source — do not skip them)
   - postmortems/incidents: dirs or files matching incident/postmortem/outage
   Present the proposed source list as a table and confirm in one line.
   NEVER add credential files, .env, or secret stores as sources.
4. **Scaffold**: `tsubasa init <name> --role "<role>" --domains <d1,d2>`, then
   register every confirmed source with
   `tsubasa source add <adapter> <path> [--glob "<glob>"]`.
   **NEVER hand-edit captain.toml** — the command validates and keeps it parseable.
5. **Persona check.** init writes the default persona principles into
   `CLAUDE.md` alongside the hot-memory include: response rules (straight
   answers, cite or "I don't know", push back on ADR conflicts), the enforced
   ADR format, and communication rules per Strunk & White's *The Elements of
   Style* ("omit needless words"). Verify the block is present, do NOT
   duplicate it elsewhere, and tell the user in one line where it lives and
   that it is theirs to edit. From this step on, ANSWER IN THAT STYLE — the
   onboarding report itself is the first demonstration of the persona.
6. **First ingest**: `tsubasa ingest` (deterministic: docs, tags, ADR-marked
   commits).
7. **Code index — always, not optional.** `tsubasa index`: deterministic
   code-only graphify indexes per fleet repo (local AST, no LLM, seconds per
   repo; non-code files excluded by design). Then `tsubasa link` to seed
   anchors between graph entities and code nodes. This is what lets queries
   join the why (native graph) to the what-is (code graph).
   Optional depth, on request only: `/graphify ./<repo> --mode deep` for
   semantic code edges on a repo that warrants it — never a default; docs
   enter the graph via tsubasa sources, not graphify.
8. **Study phase — this is what makes the captain a veteran.** Deterministic
   ingest is thin on repos without ADR conventions; now read and distill:
   - each detected docs file: extract the decisions/architecture it records
   - full git history: run `tsubasa study` (headless, chunked — distills
     EVERY commit into events; `--max-chunks N` bounds cost).
     Then `tsubasa resolve` (merge duplicate entities) and `tsubasa profile`
     (rich profiles for hub entities). This is the 25-year-veteran pass.
   - **architecture scan** per repo: README, docker-compose / k8s manifests /
     helm values, CI/CD configs, IaC — distill entities for services, envs,
     and secret-REFS (name + where it lives, NEVER the value) and relations
     (depends_on, deployed_to, built_by, reads_secret).
     Do NOT study application source code: structure is answered live from
     code (it goes stale silently in a graph); code-level insight enters the
     graph organically via recall's learn-on-miss.
   Persist each distilled finding as one event with entities/relations:
   `tsubasa event add --type <decision|note|incident> --title "..." \
      --summary "..." --impact <i> --domains <d> --ref <commit|doc>:<id> \
      --entity <id>:<type>:<name>:<desc> --relation <src>:<pred>:<tgt>`
   Aim for quality over volume: 15-30 well-connected events beat 200 thin ones.
9. **Report** in captain style (per the step-5 principles): events per source,
   what the study phase learned (one line per theme), entity count, indexed
   repos + anchor count, hot-tier size, and 2-3 example questions the user
   can now ask ("try: why …").
10. If this is a git repo, note that `.tsubasa/` + `CLAUDE.md` are commit-ready
    (do not commit unasked). In a multi-repo workspace root, note the knowledge
    lives at the workspace level.
