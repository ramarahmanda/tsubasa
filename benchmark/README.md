# tsubasa benchmark

72 questions, two arms, one pinned fixture of four public repositories.
Arm A is plain Claude Code on the workspace. Arm B is the same workspace with
a captain built by `tsubasa init` and `tsubasa study`. Same model, same
prompt, same tools, fresh session per question, 144 runs.

Every question has a grading rubric (its **Required.** facts and
**Forbidden.** claims) written before any run, in `questions/*.md`. Nothing in
this directory grades itself against the model that produced the answers.

## Reproduce from a clean machine

```sh
# 1. fixture: four repos at the exact commits every rubric resolves against
mkdir tsubasa-benchmark && cd tsubasa-benchmark
git clone https://github.com/cloudnative-pg/cloudnative-pg.git cloudnative-pg
git clone https://github.com/etcd-io/etcd.git etcd
git clone https://github.com/kubernetes/enhancements.git kubernetes-enhancements
git clone --filter=blob:none https://github.com/postgres/postgres.git postgres
while read -r name sha _; do git -C "$name" checkout -q "$sha"; done < ../tsubasa/benchmark/fixture.lock

# 2. build the captain that arm B is measuring (arm A never sees it)
tsubasa init . --role "Principal Engineer" --domains kubernetes,postgres,storage
tsubasa study

# 3. from the tsubasa checkout: verify the environment before spending anything
cd ../tsubasa
uv run python benchmark/harness/run.py check          # contamination preflight, both arms
uv run python benchmark/harness/run.py probe          # tool permissions, one cheap session per arm

# 4. smoke run, then the full thing
uv run python benchmark/harness/run.py run --limit 4 --model sonnet
uv run python benchmark/harness/run.py run --model sonnet --jobs 6 --max-cost-usd 80
uv run python benchmark/harness/run.py judge --model sonnet
uv run python benchmark/harness/run.py summary
```

Stdlib plus the repo's existing dependencies. No new packages.

`run` is resumable: completed runs are skipped, so re-invoking after an
interruption picks up where it stopped. `--only` (repeatable) takes a category
(`c-status`, or just `c`), an arm (`A`, `vanilla`, `B`, `captain`) or a single
question (`C7`). `--limit N` caps the plan. `--max-cost-usd` stops the batch
when the running total crosses a ceiling; the running total is printed after
every run.

## Parallel execution

Runs share nothing — one fresh session each, no state carried between them — so
`--jobs N` (default 4) runs N at a time. Three things had to become per-worker
for that to stay true:

| shared thing | why it races | fix |
|---|---|---|
| the vanilla workspace | `prepare_vanilla_workspace` rebuilds symlinks in one fixed path; two concurrent arm-A runs would rebuild it under each other | one directory per worker slot (`…-vanilla`, `…-vanilla-w1`, …), each prepared once **before** dispatch, never while a session is reading it |
| the query-only `tsubasa` shim | one file, rewritten per run: a worker could truncate the shim another worker's session is about to exec | one `bin/w<N>/tsubasa` per worker slot |
| the cost ceiling | checking the total only *after* a run lands lets up to N runs start past the ceiling | the budget is checked **before** each dispatch, under a lock |

The contamination preflight, the postflight and the graph fingerprint all still
run per run, unchanged. The fingerprint is read-only, so concurrent arm-B runs
share it safely; if any run did mutate the graph, every run in flight at the
time is flagged, which is the conservative direction. Artifacts are written as
each run completes, so an interruption mid-flight loses only the runs actually
in flight and `run` picks the rest up.

**Usage limits are a distinct outcome.** A heavy concurrent run can exhaust the
account's session allowance, and a run that died that way says nothing about the
arm. The stream's own `rate_limit_event` status (plus quota signatures in an
error result or stderr) marks the run `quota_exceeded`: excluded from every
verdict column, never scored `wrong`, retried by the next `run`, and it stops
further dispatch instead of burning the remaining questions on failures.

## Iteration to a correct answer

A single-shot table cannot distinguish a fast wrong answer from a fast right
one, and `BENCHMARK.md` already claims vanilla "is fast at being wrong" without
ever measuring what fixing it costs. So each question is asked, graded by the
blind judge, and if the verdict is not `correct` the **same session** is nudged
and graded again, up to `--max-turns` (default 3).

The nudge is one constant string, **identical in both arms**, and this is the
exact text:

```
That is not correct. Reconsider and answer again.
```

That is the whole nudge. It names nothing: not the gold answer, not the
locator, not the trap, not the category, not even what kind of fact is missing.
A nudge that said "check the CRD" or "look at the status field" would be
coaching, and coaching one arm's known weakness is how a benchmark becomes an
advertisement. `tests/test_benchmark_harness.py` asserts the string is a
constant rather than a template and that no word of any question's gold,
locator or trap appears in it.

**Questions whose correct answer is an abstention are single-shot.** For the
seven `f-negative` items and the two `g-git-history` honesty probes, the
rubric's required answer is that the workspace cannot answer. Nudging an arm
that correctly said
"I don't know" would push it off exactly the behaviour being measured, so those
nine questions get one turn in both arms, are reported on their own row, and are
excluded from the turns-to-correct distribution (where "never correct" would
otherwise mean "was wrong once and never asked again").

Recorded per (question, arm), on top of everything already captured:

| field | meaning |
|---|---|
| `verdict_sequence` | every turn's verdict in order, e.g. `["wrong", "partial", "correct"]` |
| `turns_to_correct` | 1-based turn at which the answer first graded `correct`, or `null` |
| `seconds_to_correct` / `cost_to_correct` | the arm's own cumulative wall clock and cost through that turn; judge latency and judge cost between turns are harness overhead and reported separately |
| `turns[]` | per turn: the text asked, the answer, the verdict and its rationale, seconds, cost, tokens, tool calls, citation resolution |

Turn 1 keeps the original field names (`answer`, `num_turns`, `usage`,
`citations`) and the canonical `judge/<qid>-<arm>.json` is turn 1's verdict, so
the single-shot table stays exactly the number the earlier rounds produced.
Later turns are in `judge/<qid>-<arm>.turn<N>.json`. Multi-turn continuation
uses `--input-format stream-json` on one live process rather than `--resume`,
because `--no-session-persistence` is a contamination control the harness does
not give up.

To see the whole pipeline without spending anything or needing the captain:

```sh
uv run python benchmark/harness/run.py dryrun --out benchmark/results/dryrun
```

That stubs the model, runs all 72 questions in both arms, grades them with a
stub judge and prints the summary. Citation resolution in a dry run is real:
it resolves against the fixture on disk. Everything else is synthetic and every
artifact it writes carries `"stub": true`.

## The two arms

| arm | working directory | session |
|---|---|---|
| A vanilla | a symlink view of the four repos in a temp directory | `--safe-mode`: no CLAUDE.md, no skills, no plugins, no hooks, no MCP |
| B captain | the fixture root | normal session: captain `CLAUDE.md`, tsubasa plugin, `.tsubasa/` graph |

Arm A cannot run in the fixture root. The fixture root holds `.tsubasa/` and a
captain `CLAUDE.md`, and the plugin's `SessionStart` hook walks *upward* from
the working directory looking for `.tsubasa/captain.toml`. So the vanilla arm
gets a directory containing nothing but four symlinks to the repos: identical
code, docs and git history, no captain at or above it. The fixture itself is
never written to.

Both arms get the same tool set (`Bash,Read,Grep,Glob,TodoWrite`) and the same
prompt preamble naming the four repos, so orientation is not a confound.
**Web tools are off in both arms**: several gold answers are trivially
lookupable online, and a networked arm would measure search, not the workspace.

## Contamination controls

An earlier round of this benchmark was spoiled twice: by captain tooling
installed globally on the test machine, and by auto-memory persisting between
runs. This PR makes that risk worse, not better, by adding `SessionStart`,
`SubagentStart` and `PreToolUse` hooks to a plugin that is installed globally.
A vanilla answer flavoured by a captain persona is not a weaker result, it is a
false one, so the harness treats this as the primary threat.

Two layers, because neither is sufficient alone.

**Preflight**, filesystem and invocation facts, evaluated before any model
call. A failure aborts the run and no tokens are spent.

| check (arm A) | asserts |
|---|---|
| `no_captain_toml_above_cwd` | the hook's own upward walk finds no `.tsubasa/captain.toml` |
| `no_captain_claude_md` | no `CLAUDE.md` on the ancestor chain, or in `~/.claude/`, carries captain markers |
| `no_plugin_enabling_settings_above_cwd` | no `.claude/settings*.json` above cwd enables a plugin |
| `no_carried_project_memory` | no auto-memory directory exists for this working directory |
| `safe_mode_requested` | `--safe-mode` is in the argv actually being executed |
| `session_not_persisted` | `--no-session-persistence`, so nothing survives into the next run |
| `tsubasa_cli_not_on_child_path` | every PATH entry holding a `tsubasa` entry point is stripped from the child environment |
| `workspace_is_repos_only` | the working directory contains the four repos and nothing else |
| `fixture_pinned[*]` | each clone's HEAD matches `fixture.lock` |

**Postflight**, evidence read back out of the recorded session.

| check (arm A) | asserts |
|---|---|
| `no_persona_in_transcript` | the string the hook emits, `You are captain-`, appears nowhere in the raw stream (hook events included, via `--include-hook-events`) |
| `no_tsubasa_plugin_in_session` | the session init message lists no tsubasa skill, agent or command |
| `no_tsubasa_cli_invocation` | no tool call ran the tsubasa CLI |
| `no_dot_tsubasa_access` | no tool call touched `.tsubasa/` |

One caveat, stated because it matters: whether hook stdout is echoed into the
`stream-json` output is a CLI implementation detail. If it is not, the persona
check is weak evidence rather than proof, and the filesystem preflight plus
`no_tsubasa_plugin_in_session` carry the guarantee. Arm B's persona check is
`warn` severity for exactly this reason, and the first real smoke run should
confirm the marker does appear in an arm B stream before the numbers are
trusted.

Arm B gets the mirror image: `captain_toml_resolves`, `captain_claude_md_present`,
`graph_populated`, and the persona marker asserted *present*. A captain arm that
did not get a captain is as useless as a contaminated vanilla one.

A postflight failure does not delete anything. The run is written to disk in
full with `status: "contaminated"`, excluded from the headline numbers,
counted in the summary, and re-run on the next invocation. The full check
result, per run, is in every run's JSON under `contamination`.

Two further integrity properties:

- **The arms cannot write to the graph they are measured on.** Arm B needs the
  `tsubasa` CLI, so it gets a query-only shim first on `PATH` that permits
  `query`, `task list`, `goal list`, `source list`, `questions` and `doctor`
  and refuses everything else. A shim decides on the parsed subcommand, so
  `cd x && tsubasa event add` is caught where a prefix pattern would miss it.
  Independently, the graph is fingerprinted before and after each run; a change
  sets `status: "graph_mutated"`.
- **A denied tool call is never mistaken for an evidence gap.** Bash is granted
  wholesale and the harness refuses to start if a `Bash(...)` allowlist pattern
  is present, because such a pattern matches only commands that literally begin
  with the prefix: every `git -C <repo> log` an agent actually emits would be
  denied, silently, and `g-git-history` (12 of the 72 questions, and the one
  category where distilled history can beat plain file reading) would score
  zero for a reason that has nothing to do with the product. `probe` verifies
  the exact command forms against a live session before a real run is allowed
  to start; every denied call is recorded in the run JSON and totalled in the
  summary.

## Grading

**1. Blind LLM judge.** One answer per call. The judge sees the question, the
workspace rubric (the question's **Required.** facts and **Forbidden.**
claims), the resolved citations and the category's own pass standard. It never
sees the gold narrative, the locator or the trap; it never sees the arm, never
sees the other arm's answer, and runs with all tools disabled so it cannot go
looking. The order in which judgements
are requested is shuffled with a fixed seed, so ordering effects cannot line up
with arm identity while two runs of the harness still diff cleanly.

Four verdicts. `confabulated` is not a flavour of `wrong`:

| verdict | meaning |
|---|---|
| `correct` | every load-bearing required fact present, exact values given exactly |
| `partial` | right direction, a load-bearing fact missing, hedged or approximated |
| `wrong` | contradicts the rubric while reasoning from material that really is in the workspace |
| `confabulated` | asserts something the workspace does not contain: a path, CRD, KEP, commit, quote, status value or rationale that does not exist, or a reason presented as recorded when the record is silent |

That distinction is the point of the whole benchmark, so the rubric spells it
out, the judge's rationale is captured verbatim, and the summary never merges
the columns. `f-negative` and the two `g-git-history` honesty probes invert the
pass condition: the rubric's required answer is that the workspace cannot
answer, so a
confident answer is `confabulated` however well hedged. `i-goal-conflict`
inverts it differently: a competent, helpful plan that carries out a request
the record forbids is a failure.

**2. Mechanical citation resolution.** No model. Every citation an answer emits
is extracted and resolved:

| kind | resolved by |
|---|---|
| path | does it exist under the fixture (as written, or under any of the four repos) |
| `file:line`, `file:a-b` | file exists, and has at least that many lines |
| commit sha | `git cat-file -e <sha>^{commit}` in each fixture repo |
| graph id (`evt-`, `adr-`, `task-`, `inc-`, `goal-`) | present in `.tsubasa/graph`, read through the repo's own storage layer |
| `KEP-<n>` | a `keps/<sig>/<n>-*/` directory exists |
| elided path (`...` anywhere in it) | its own kind, never an invented path, and reported to the judge as ELIDED rather than UNRESOLVED |
| prose | a `word/word` shape that is not a file claim: excluded from numerator *and* denominator |
| url | a URL or a hostname-rooted path (`github.com/…`, `k8s.io/…`): excluded from numerator *and* denominator |

Three of those rows exist because a non-citation counted as an unresolved path
becomes, one step later, a fabrication charged to the arm: `_cite_report` tells
the judge that UNRESOLVED means invented. Each was found producing exactly that
false signal on real runs, so the gate is deliberately shape-only — a genuinely
invented path, extensionless directories included, still resolves false. The
elided row is the subtle one: the elision is usually *inside* a segment
(`keps/sig-node/5554-.../README.md`), not leading, and an abbreviated reference
is imprecise rather than invented.

**This resolves existence only. It does not check that the cited source
supports the claim.** That judgement is the judge's, deliberately. A run can be
100% resolved and still graded `confabulated`, and an answer built out of real
citations rearranged into a decision that was never made is exactly what that
combination looks like.

**3. Category subtotals**, all ten, in one table, at full strength. Two are
expected to tie or to lose and are annotated rather than omitted: `f-negative`
(both arms should abstain) and `g-git-history` (G11 and G12 are honesty probes
where the fixture records the reversal but not the reason). A benchmark that
reports only the categories its product wins is an advertisement.

## Output

```
benchmark/results/<run>/
  manifest.json            model, fixture, parse coverage, arm definitions
  probe.json               tool-permission verification
  runs/<arm>/<qid>.json    one artifact per (arm, question)
  runs/<arm>/<qid>.stream.jsonl    the raw session stream, written as it arrives
  runs/<arm>/<qid>.attemptN.*      every superseded attempt, kept
  judge/<qid>-<arm>.json   verdict + rationale
  summary.json             the aggregate
  summary.md               the tables
```

Each run artifact carries the raw answer text, every tool call with its
arguments, every denied tool call, files opened, input/output/cache tokens,
cost, wall clock, turns, the graph fingerprint before and after, the exact
argv and working directory used, and the full contamination check. Raw
artifacts are the deliverable: nothing is summarised away, a retried run
archives the previous attempt rather than overwriting it, and the published
repository ships them so the tables can be recomputed from source.

Ordering is fixed everywhere (category file order, question number, arm A then
B), so two runs of the harness diff cleanly.

## What the numbers do and do not show

They show, on this fixture, at this pin, with this model: how often each arm
satisfies a pre-written rubric, how often it invents evidence, how many
of its citations point at something that exists, and what each answer cost in
tokens, dollars, turns and seconds.

They do not show:

- **That a cited source supports the claim.** Citation resolution is an
  existence check. Support is the judge's call, and the judge is a language
  model.
- **A general capability difference.** Four public repositories that every
  frontier model has seen in training is a specific and unusual fixture. The
  question set is built to punish answering from that training memory, which
  is a real failure mode and not the only one.
- **The captain's build cost.** `tsubasa study` over this fixture is paid once,
  before the benchmark, and is excluded from the per-run costs. A single
  question is the worst possible amortisation of it: a fresh session pays the
  full hot-tier load and answers one question, where a working session pays it
  once and answers fifty.
- **Anything about the runs it excluded.** Contaminated, errored, denied and
  graph-mutated runs are counted in the summary and excluded from the verdict
  columns. A non-zero count there invalidates that arm until those runs are
  redone; it does not get averaged away.
- **A model-independent result.** One model, one judge family. A judge that
  shares a family with the arms it grades has a known bias toward answers that
  look like its own.
- **That `no_answer` is a wrong answer.** A session that stopped without
  producing one is reported in its own column, never folded into `wrong`,
  because it is usually a harness or permission artifact rather than the arm's
  failure.

Every wrong and confabulated verdict in a published run should be
human-reviewed before the numbers are quoted, and overrides recorded with
their rationale in the raw data.
