# Contributing

Short version: small diffs, tests pass, cite why. A PR should be a one-minute read.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) (`pyproject.toml`).

```sh
git clone https://github.com/ramarahmanda/tsubasa && cd tsubasa
uv sync              # installs the dev group (pytest)
uv run pytest        # whole suite, tests/ (testpaths in pyproject.toml)
uv run tsubasa --help
```

## Repo map

| Path | What | Note |
|---|---|---|
| `src/tsubasa/` | CLI, adapters, graph, memory tiers | deterministic core: parsing, schema validation, graph ops |
| `plugin/` | Claude Code plugin: `skills/`, `hooks/` | judgment work: extraction, resolution, answering |
| `schema/` | JSON Schemas: event, entity, relation | the public contract; retired shapes must stay readable (DESIGN.md §3.2) |
| `benchmark/` | `harness/`, `questions/`, `results/`, `fixture.lock` | `results/` are harness artifacts: regenerated, never hand-edited |
| `tests/` | pytest suite | flat `test_*.py`, fixtures in `conftest.py` |

The CLI/skill split is architectural, not incidental: keep deterministic work in
Python, judgment in skills (DESIGN.md §4.1).

## Change conventions

- Good code is few lines changed. No refactors beyond what the task requires.
- Remove unused code and flags in the same PR that obsoletes them.
- Architectural decisions get an ADR. Ids follow `adr-<slug>` (convention
  enforced in `src/tsubasa/adapters/adr.py`); put the id in the branch name or
  PR title so ingestion threads ADR to PR to changed files (DESIGN.md §5.3).
- The sdist ships `src/tsubasa`, `schema/`, README, LICENSE only
  (`pyproject.toml`). Nothing else may grow a packaging dependency.

## Commits and PRs

- Imperative subject: "Add X", not "Added X".
- PR description says why, not just what. Link the ADR or issue.
- No AI attribution anywhere: no "Generated with" lines, no `Co-Authored-By`
  AI trailers, in commits or PRs.

## Benchmark integrity

- Never tune `benchmark/questions/`, the nudge text, or judge grading against
  published results. The nudge is a constant that names nothing;
  `tests/test_benchmark_harness.py` asserts it (`benchmark/README.md`).
- Exercise harness changes with the stubbed pipeline before spending tokens:
  `uv run python benchmark/harness/run.py dryrun`.
- `benchmark/results/` is a build artifact of the harness. Do not edit it by
  hand; verdict overrides go in the raw data with a rationale
  (`benchmark/README.md`).

## Security

Do not open a public issue for anything exploitable. Report privately via
[GitHub vulnerability reporting](https://github.com/ramarahmanda/tsubasa/security/advisories/new);
first response within a week. Only the latest PyPI release and matching plugin
get fixes. Full policy: [SECURITY.md](SECURITY.md).
