<img src="assets/captain.png" align="right" width="160" alt="the tsubasa captain">

# tsubasa

**Your engineering captain.** He knows your code, your outages, your history, and your roadmap. He ships you past storms.

[![Stars](https://img.shields.io/github/stars/ramarahmanda/tsubasa?style=flat)](https://github.com/ramarahmanda/tsubasa) [![PyPI](https://img.shields.io/pypi/v/tsubasa)](https://pypi.org/project/tsubasa/) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue) ![Claude Code plugin](https://img.shields.io/badge/claude--code-plugin-orange) ![License: MIT](https://img.shields.io/badge/license-MIT-blue)

**47% fewer wrong answers · 45% cheaper · 24% faster**

```text
you:         why does payment-svc cap its pool at 40?

no captain:  pool.go sets it to 40. Likely to avoid exhausting database
             connections — a common pattern. You could raise it if your
             database handles more concurrent connections, though you may
             want to load-test first and monitor p99 during the rollout.

captain:     The July 3 flash-sale incident: pool exhaustion took p99 to 8s.
             adr-payments-pool-sizing fixed the cap because RDS connections
             were the real limit. PR-1841, services/payment/pool.go:88.

             goal-replace-apigee retires the layer in front of this service.
             Don't invest in the current routing.
```

## Numbers

The measurement is a real agent doing real work: a headless Claude Code session answering questions about four pinned public repos — cloudnative-pg, etcd, kubernetes/enhancements, postgres — scored on what it cites. 72 questions, the same agent with and without a captain, 144 runs, Claude Sonnet 5 in both arms and as the judge.

<img src="assets/benchmark.svg" alt="each metric as a percent of the no-captain baseline">

| vs no-captain baseline | wrong answers | cost | time | tool calls |
|---|---|---|---|---|
| **captain** | **−47%** | **−40%** | **−24%** | **−49%** |

| per question | vanilla | captain |
|---|---|---|
| cost | $0.36 | **$0.20** |
| time, median | 97s | **73s** |
| time to a correct answer, average | 79s | **66s** |
| model iterations, median | 10 | **7** |
| output tokens | 4.0k | **2.5k** |

The gap is biggest where the answer is a decision, a silence, or a link between repos. Near zero where `grep` already works: four of ten categories tie at full marks.

Full method, per-category tables and limitations: [BENCHMARK.md](BENCHMARK.md) · [sample workspace](https://github.com/ramarahmanda/tsubasa-workspace/tree/main/benchmark-k8s).

## Why

AI agents suck at your system. They read the code and guess the rest — fluently, at length, with a "you may want to load-test first" on every guess.

Managing people around that is worse. Why the pool is capped at 40, what was already tried and reverted, what the migration is quietly retiring: it lives in a few heads. You explain it, someone leaves, you explain it again.

I got tired of being the captain. So I built one.

```text
  alice's session              the repo                bob's session
  ───────────────              ────────                ─────────────
  "we capped the pool     ──►  .tsubasa/ committed ──► "why is the pool
   at 40 after the              reviewed in the PR      capped at 40?"
   flash-sale incident"         cloned with the code
                                                   ◄──  the incident, the ADR,
                                                        PR-1841, pool.go:88
```

Alice never wrote a doc. She said it once and it became a cited record. Bob's agent answers from it a month later, so does CI's, so does the new hire's. Fix a fact and everyone is fixed — it is a file in the repo, so a fix is a diff someone reviews.

That leaves you one job: **review**. Short, cited, or an honest "I don't know" — not a thousand lines of AI prose, not another doc nobody opens.

## How it works

Two graphs, one walk. **graphify** indexes the codebase: files, symbols, imports, from the AST. The **native graph** holds the rest: ADRs, commits, postmortems, decisions, goals. Shared IDs stitch them:

```text
 you: "why does payment-svc cap its pool at 40?"
                       │
                       ▼
        match entities in the question
        (payment-svc → svc-payment · pool → adr-payments-pool-sizing)
                       │
          ┌────────────┴─────────────┐
          ▼                          ▼
   NATIVE GRAPH (the why)      GRAPHIFY (the what-is)
   ADRs · commits ·            code nodes per workspace repo:
   postmortems · decisions ·   files, functions, imports
   goals (events)              (AST-derived, no staleness)
          │                          │
          └───── joined by IDs ──────┘
            ADR ids · file paths · service names
                       │
                       ▼
        walk the joined subgraph a few hops out
          adr-payments-pool-sizing
            ├─ decided_because_of → evt-2026-07-03-flash-sale (postmortem:
            │    pool exhaustion took p99 to 8s during the flash sale)
            └─ anchored_to → services/payment/pool.go:88  ← verified live
                       │
                       ▼
        answer: the why (ADR + incident) + the what-is (file:line),
        every claim cited, or "I don't have knowledge about that"
```

The native graph never answers code structure: it would go stale. graphify never answers history: it never knew it. The join lets one question cross both.

Why two graphs: code truth re-derives free from the AST any time; org truth exists only as recorded events. One store would either rot or forget. Full rationale: [DESIGN.md](DESIGN.md) §3.3.

## The persona

Not a chatbot with retrieval bolted on. `tsubasa init` scaffolds an opinionated persona; the graph gives it something to be opinionated about:

- **Role & domains**: answers as an Engineering Director for `auth, payments`; memory weights follow the domains.
- **Guardrails**: straight answers, cite or say "I don't know", flag only critical issues, minimize changes, push back when a request conflicts with a recorded ADR. To override: change the record first. Plain markdown; edit to fit your org.
- **Style**: Strunk & White, [*The Elements of Style*](https://www.gutenberg.org/ebooks/37134), rule 13: omit needless words. Applied: tables over prose, one-minute read, terse enforced ADR format.
- **Restraint**: [ponytail](https://github.com/DietrichGebert/ponytail), the same instinct applied to code — *the best code is the code you never wrote*. Here, the best answer stops at the record: no volunteered considerations, no hedged alternatives, no findings the graph cannot support.

## Quick start

**Requirements:** [Claude Code](https://claude.com/claude-code) · [uv](https://docs.astral.sh/uv/) · git

### Create a new captain

Install once, then ask for a captain in a session.

```bash
# 1. install the tsubasa CLI
uv tool install tsubasa          # later: uv tool upgrade tsubasa
# fallback, straight from source:
#   uv tool install git+https://github.com/ramarahmanda/tsubasa

# 2. install the Claude Code plugin (one-time, global)
claude plugin marketplace add ramarahmanda/tsubasa
claude plugin install tsubasa@tsubasa

# 3. open a session at your repo or workspace root
cd your-workspace && claude
```

Then say:

> "set up a captain for this workspace, call it tsubasa"

The captain scaffolds `.tsubasa/`, writes principles to `CLAUDE.md`, detects sources (git history, ADRs, postmortems, docs), ingests, and reports what it learned. A workspace with a captain:

```text
your-workspace/
├── .tsubasa/                    # the knowledge graph: commit this
│   ├── captain.toml             # persona, domains, sources, temperature weights
│   ├── graph/
│   │   ├── entities.toon
│   │   ├── relations.toon
│   │   └── events/2026/07/evt-*.toon
│   └── memory/                  # generated: hot.md, index.md, domains/*.md
├── CLAUDE.md                    # persona principles + @.tsubasa/memory/hot.md
├── docs/
│   ├── adr/                     # decision records: highest-value source
│   ├── postmortem/              # incident writeups
│   └── proposals/
├── api-gateway/                 # workspace repos: git submodules or plain clones
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

### Update an existing captain

`uv tool upgrade tsubasa` updates the CLI; `tsubasa upgrade` updates the captain on disk. It is idempotent, safe to re-run, and prints every change it makes:

```bash
uv tool upgrade tsubasa
tsubasa upgrade          # --add also registers sources detection found
```

A captain that needs nothing says so. `tsubasa doctor` tells you when it is due.

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

captain:  PR-2107 linked to adr-auth-xyz (evidence: the merge commit). 2 events written.
```

The model routes each turn to a [skill](#built-in-skills) by itself. Nothing writes without the one-line gate. Coding requests stay ordinary coding — now informed by the graph.

<details><summary>CLI path (CI, scripting, non-Claude clients)</summary>

```bash
uv tool install tsubasa
cd your-workspace

# one command: scaffold, detect sources, run the first ingest
tsubasa init tsubasa --role "Engineering Director" --domains auth,payments
tsubasa query "why does payment-svc cap its pool at 40?"

# only for repos detection could not reach
tsubasa source add git ./api-gateway --branch main --pull
tsubasa ingest git

# optional deepening pass
tsubasa study && tsubasa resolve && tsubasa profile
```

`init` probes the workspace root and every immediate subdirectory that is its own git repo, so a single repo and a multi-repo workspace both come back with sources. `--no-detect` scaffolds only; `--no-ingest` registers what it found without ingesting yet.
</details>

## The knowledge model

| Shape | Semantics | Lifetime |
|---|---|---|
| **Event** | a fact: incident, decision, deploy, merged PR | immutable, append-only |
| **Entity** | a thing: service, ADR, environment, team, secret-ref, goal | derived from events, rebuildable |
| **Relation** | meaning: `caused_by`, `deployed_to`, `supersedes`, `retires` | typed edge + provenance |
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
                       │    adr-auth-xyz  threads ADR ↔ branch ↔ PR ↔ changed files
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
| `captain-capture` | a decision is accepted: ADR + event, goal-alignment checked |
| `captain-inject` | you state a fact or plan: validated, connected, persisted |
| `captain-delegate` | approved work executes: brief subagents, supervise, validate against the graph |

## CLI reference

| Command | Does |
|---|---|
| `init` | scaffold · detect sources · first ingest, in one command (`--no-detect`, `--no-ingest` opt out) |
| `source add` / `ingest` | register a source detection missed · pull knowledge again |
| `query "…" [--as-of DATE]` | subgraph + citations, optionally as of a past date; bridges wording onto graph tokens automatically (`TSUBASA_SEMANTIC=1` adds an LLM hop for zero-overlap phrasing) |
| `vocab [stems]` | the graph's searchable tokens, stem-filtered; what `query` can actually hit |
| `event add` / `goal …` | the captain's write path |
| `study` / `resolve` / `profile` | history distillation (headless Claude) |
| `questions` / `rebuild` / `doctor` | open disputes · replay event log · hygiene lint |
| `upgrade` | bring a captain built by an older tsubasa up to this schema (idempotent) |

## Documentation

- [DESIGN.md](DESIGN.md): full architecture. Data contract, temperature model, reconciliation, orchestration, roadmap.
- [BENCHMARK.md](BENCHMARK.md): captain vs no captain — method, per-category tables, limitations.
- [schema/](schema/): the public contract (Event, Entity, Relation).

## License

MIT
