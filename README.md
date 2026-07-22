# tsubasa

![License: MIT](https://img.shields.io/badge/license-MIT-blue) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue) ![Claude Code plugin](https://img.shields.io/badge/claude--code-plugin-orange)

**Build a captain for your fleet**: a persistent, domain-expert AI persona for the services, repos, and infrastructure you operate. It holds what a 25-year veteran would: each service's history, the decisions behind it, the incidents, the deploys, the goals in flight.

```text
you:      why does payment-svc cap its pool at 40?

captain:  The July 3rd flash-sale incident: pool exhaustion took p99 to 8s.
          adr-payments-pool-sizing chose a fixed cap because RDS connections
          were the real limit. See PR-1841, services/payment/pool.go:88.

          Note: goal-replace-apigee retires the layer in front of this
          service. Don't invest in the current routing.
```

## Why

AI agents read your code. They don't know your organization:

- **Why**: decisions, rejected alternatives, tribal knowledge
- **What happened**: incidents, migrations, launches
- **What is**: what runs where, which secrets a service reads, what depends on what
- **What's next**: goals new designs must align with

That knowledge lives in senior heads and leaves with them. tsubasa keeps it **in your repo, in git**: diffable, reviewable, cloned with the code.

## How it works

Two graphs, one walk. **graphify** indexes the codebase: files, symbols, imports, from the AST. The **native graph** holds the rest: ADRs, commits, postmortems, tasks, goals. Shared IDs stitch them:

```text
 you: "why does dispatch-service cap the driver search radius at 5km?"
                       │
                       ▼
        match entities in the question
        (dispatch-service → svc-dispatch · radius → adr-dispatch-radius-cap)
                       │
          ┌────────────┴─────────────┐
          ▼                          ▼
   NATIVE GRAPH (the why)      GRAPHIFY (the what-is)
   ADRs · commits ·            code nodes per fleet repo:
   postmortems · tasks ·       files, functions, imports
   goals (events)              (AST-derived, no staleness)
          │                          │
          └───── joined by IDs ──────┘
            ADR ids · file paths · service names
                       │
                       ▼
        walk the joined subgraph a few hops out
          adr-dispatch-radius-cap
            ├─ decided_because_of → evt-2025-09-surge-meltdown (postmortem:
            │    unbounded search flooded the ETA service during rain surge)
            └─ anchored_to → dispatch/matching/radius.go:47  ← verified live
                       │
                       ▼
        answer: the why (ADR + incident) + the what-is (file:line),
        every claim cited, or "I don't have knowledge about that"
```

The native graph never answers code structure: it would go stale. graphify never answers history: it never knew it. The join lets one question cross both.

Why two graphs: code truth re-derives free from the AST any time; org truth exists only as recorded events. One store would either rot or forget. Full rationale and the deep-mode comparison: [DESIGN.md](DESIGN.md) §3.3.

Measured against a vanilla agent session, paired runs on a production workspace ([BENCHMARK.md](BENCHMARK.md)):

| | vanilla | captain |
|---|---|---|
| correct on org-memory-only questions | 0% | **100%** |
| correct overall | 33% | **83%** |
| wrong answers | 33% | **0%** |
| cost per correct answer | $1.67 | **$0.51** |

Every claim cites: event id, ADR, PR, commit, or `file:line`. If the graph doesn't know, the captain says so.

## The persona

Not a chatbot with retrieval bolted on. `tsubasa init` scaffolds an opinionated persona; the graph gives it something to be opinionated about:

- **Role & domains** (`captain.toml`): answers as an Engineering Director for `auth, payments`; memory weights follow the domains.
- **Guardrails** (`CLAUDE.md`, written at init): straight answers, cite or say "I don't know", flag only critical issues, minimize changes, push back when a request conflicts with a recorded ADR. To override: change the record first. Plain markdown; edit to fit your org.
- **Style**: Strunk & White, [*The Elements of Style*](https://www.gutenberg.org/ebooks/37134), rule 13, "Omit needless words": *"Vigorous writing is concise. A sentence should contain no unnecessary words, a paragraph no unnecessary sentences, for the same reason that a drawing should have no unnecessary lines and a machine no unnecessary parts."* Applied: ASCII flows and tables over prose, one-minute read, terse enforced ADR format.
- **Standing on**: graphify for the objective layer (what calls what); the event graph for the subjective (why it's shaped that way).

## Features

| Feature | Meaning |
|---|---|
| One captain per workspace | named persona (e.g. `captain-tsubasa`) at repo or workspace root |
| Ambient capture | no slash commands; conversation becomes knowledge behind a one-line gate |
| Two-layer graph | append-only *events* (history, never rots) + *code snapshot* per ingest (never stale) |
| Future knowledge | goals stay hot until resolved; new plans checked against them |
| Self-correcting | every write reconciled; contradictions supersede, never delete |
| Trust hierarchy | `code > ADRs & user > other docs`; doc-derived claims labeled for verification |
| Task tracking | ADR id in branch/PR moves the task `todo → in_progress → done` with evidence |
| Temporal queries | `--as-of 2026-03-01`: the graph as known then |
| History distillation | `study` distills full git history; `resolve` merges duplicates; `profile` summarizes hubs |
| Secret safety | write-time redaction + `doctor` lint; secret names and locations, never values |

## Quick start

**Requirements:** [Claude Code](https://claude.com/claude-code) · [uv](https://docs.astral.sh/uv/) · git

### Create a new captain

```bash
# 1. install the tsubasa CLI
uv tool install tsubasa
#    (while unpublished to PyPI:
#     uv tool install git+https://github.com/ramarahmanda/tsubasa)

# 2. install the Claude Code plugin (one-time, global)
claude plugin marketplace add ramarahmanda/tsubasa
claude plugin install tsubasa@tsubasa

# 3. open a session at your repo or workspace root
cd your-workspace && claude
```

Then say:

> "set up a captain for this workspace, call it tsubasa"

The captain scaffolds `.tsubasa/`, writes principles to `CLAUDE.md`, detects sources (repos, ADRs, deploy manifests, postmortems), ingests, and reports what it learned. A workspace with a captain:

```text
your-workspace/
├── .tsubasa/                    # the knowledge graph: commit this
│   ├── captain.toml             # persona, domains, sources, temperature weights
│   ├── graph/
│   │   ├── entities.toon
│   │   ├── relations.toon
│   │   └── events/2026/07/evt-*.toon
│   ├── tasks/task-*.toon
│   └── memory/                  # generated: hot.md, index.md, domains/*.md
├── CLAUDE.md                    # persona principles + @.tsubasa/memory/hot.md
├── docs/
│   ├── adr/                     # decision records: highest-value source
│   ├── postmortem/              # incident writeups
│   └── proposals/
├── api-gateway/                 # fleet repos: git submodules or plain clones
├── auth-service/
├── account-service/
└── infra-gitops/                # k8s / helm / CI: deploy truth
```

Only knowledge lives in `.tsubasa/`: no binaries, no DB, no secrets. Commit and push; knowledge travels with the repo. Then talk: *"why does X…"*, *"we decided Y"*, *"we plan to Z by Q4"*, *"I merged the PR"*.

### Join an existing captain

Knowledge arrives with the clone:

```bash
# 1. clone the captain repo
git clone <your-remote>/your-workspace && cd your-workspace

# 2. install the CLI + plugin (one-time, as above)
uv tool install tsubasa
claude plugin marketplace add ramarahmanda/tsubasa
claude plugin install tsubasa@tsubasa

# 3. open a session: the captain loads its memory automatically
claude
```

### Daily use: a normal Claude session

No commands to learn. The session *is* the captain:

```text
you:      why do we sign webhooks with HMAC instead of mTLS?

captain:  adr-webhook-signing (2025-11): partners couldn't manage client
          certs, so HMAC with rotating secrets. See docs/adr/webhook-signing.md,
          evt-2025-11-08-webhook-design.

you:      heads up: the rate limiter dropped Redis, it's in-memory per pod now.

captain:  Recording: config_change, domain=platform, supersedes
          adr-rate-limit-redis. Correct?          ← one-line validation gate

you:      correct. and I merged PR-2107 for adr-auth-xyz.

captain:  task-auth-xyz → done (evidence: PR-2107). 2 events written.
```

The model routes each turn to a [skill](#built-in-skills) by itself. Nothing writes without the one-line gate. Coding requests stay ordinary, knowledge-flavored.

<details><summary>CLI path (CI, scripting, non-Claude clients)</summary>

```bash
uv tool install tsubasa
cd your-workspace
tsubasa init tsubasa --role "Engineering Director" --domains auth,payments
tsubasa source add git ./api-gateway --branch main --pull
tsubasa source add adr docs/adr
tsubasa ingest && tsubasa study && tsubasa resolve && tsubasa profile
```
</details>

## The knowledge model

| Shape | Semantics | Lifetime |
|---|---|---|
| **Event** | a fact: incident, decision, deploy, merged PR | immutable, append-only |
| **Entity** | a thing: service, ADR, environment, team, secret-ref, goal | derived from events, rebuildable |
| **Relation** | meaning: `caused_by`, `deployed_to`, `supersedes`, `retires` | typed edge + provenance |
| **Task** | in-flight work, threaded by ADR id | state machine with evidence |
| **Goal** | intended future state | hot until `achieved` / `dropped` |

Plain TOON files with published [JSON Schemas](schema/): any agent that reads them can act as a client.

### How knowledge is built and linked

```text
 adapters ingest                     ambient capture
 (git · ADRs · postmortems ·        ("we dropped Redis for in-memory")
  deploy config · PRs)                       │
   tsubasa ingest                            │  one-line validation gate,
        │                                    │  then: tsubasa event add
        └──────────────┬─────────────────────┘
                       ▼
     EVENT: immutable, append-only fact
                       │  extract entities · resolve aliases to canonical
                       │  IDs ("payments" = svc-payment)
                       │  tsubasa study (git history) · resolve (dedup)
                       │  · profile (hub summaries)
                       ▼
     ENTITY ──typed relation──▶ ENTITY        derived from events; replay
       (service, ADR, incident, goal, …)      any time: tsubasa rebuild
                       │
                       │  linked across worlds by shared IDs:
                       │    adr-auth-xyz  threads ADR ↔ task ↔ branch ↔ PR
                       │    file paths    join events ↔ graphify code graph
                       │    service names join deploys ↔ incidents ↔ secrets
                       │  tsubasa index (code graphs) · link (anchors)
                       ▼
     RECONCILE: automatic on every write
       ├─ no conflict          → append
       ├─ contradiction        → supersede (old kept, cooled, traversable)
       ├─ low-trust conflict   → recorded as disputed
       └─ unclear              → a question for you: tsubasa questions
                       ▼
     TEMPERATURE: recency × impact × domain × access   (tsubasa tiers)
       ├─ HOT   memory/hot.md: injected into every session
       ├─ WARM  per-domain index: loaded when the topic comes up
       └─ COLD  full events: reached at query time
                       ▼
     ANSWERS: tsubasa query "…" [--as-of DATE]
     cited (event · ADR · PR · file:line) or "I don't know"
```

Full mechanics: [DESIGN.md](DESIGN.md) §3.2 contract · §3.3 graphify joins · §3.4 temperature · §5.5 reconciliation.

## Built-in skills

| Skill | Fires when |
|---|---|
| `captain-onboard` | "set up a captain here": scaffold, detect sources, ingest, study |
| `captain-recall` | you ask *why / what happened / where*: read-only, cited answers |
| `captain-capture` | a decision is accepted: ADR + task + event, goal-alignment checked |
| `captain-inject` | you state a fact or plan: validated, connected, persisted |
| `captain-sync` | you mention finished work: tasks advance with evidence |
| `captain-delegate` | approved work executes: brief subagents, supervise, validate against the graph |

## CLI reference

| Command | Does |
|---|---|
| `init` / `source add` / `ingest` | scaffold · register sources · pull knowledge |
| `query "…" [--as-of DATE]` | subgraph + citations, optionally as of a past date |
| `event add` / `task …` / `goal …` | the captain's write path |
| `study` / `resolve` / `profile` | history distillation (headless Claude) |
| `questions` / `rebuild` / `doctor` | open disputes · replay event log · hygiene lint |

## Documentation

- [DESIGN.md](DESIGN.md): full architecture. Data contract, temperature model, reconciliation, orchestration, roadmap.
- [BENCHMARK.md](BENCHMARK.md): captain vs no captain. Token usage, accuracy, quality, speed, iteration.
- [schema/](schema/): the public contract (Event, Entity, Relation, Task).

## License

MIT
