#!/usr/bin/env python3
"""tsubasa benchmark harness.

    python benchmark/harness/run.py questions          # parse report
    python benchmark/harness/run.py check              # environment + fixture
    python benchmark/harness/run.py probe              # verify tool permissions
    python benchmark/harness/run.py run [--only ...]   # execute the run plan
    python benchmark/harness/run.py judge              # blind grading
    python benchmark/harness/run.py summary            # tables
    python benchmark/harness/run.py dryrun             # all of the above, stubbed

Stdlib only. Flat imports off this directory so the harness stays runnable
straight out of a published results bundle with no package install.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import contamination  # noqa: E402
import judge as judging  # noqa: E402
import model as transport  # noqa: E402
import runner  # noqa: E402
import summarize  # noqa: E402
from config import (DEFAULT_FIXTURE, DEFAULT_JOBS, DEFAULT_MAX_TURNS,  # noqa: E402
                    DEFAULT_OUT, DEFAULT_VANILLA_WORKSPACE, REPOS)
from questions import load_questions, usable  # noqa: E402


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT / "latest",
                        help="artifact directory (default: benchmark/results/latest)")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--model", default="sonnet", help="same model for both arms")


def cmd_questions(args) -> int:
    questions = load_questions()
    bad = [q for q in questions if not usable(q)]
    warned = [q for q in questions if q.parse_warnings]
    print(f"parsed {len(questions)} questions from {len(set(q.category for q in questions))} "
          f"category files")
    for q in questions:
        flags = []
        if q.gold_kind == "unanswerable":
            flags.append("unanswerable-by-design")
        if q.honesty_probe:
            flags.append("honesty-probe")
        if not q.locator:
            flags.append("no-locator")
        print(f"  {q.qid:<4} {q.category:<16} gold={len(q.gold):>5}b "
              f"locator={len(q.locator):>4}b trap={len(q.trap):>5}b "
              f"{' '.join(flags)}")
    print(f"\nquestions with parse warnings: {len(warned)}")
    for q in warned:
        print(f"  {q.qid}: {'; '.join(q.parse_warnings)}")
    print(f"unusable (no question text or no gold): {[q.qid for q in bad] or 'none'}")
    if args.json:
        Path(args.json).write_text(
            json.dumps([q.to_dict() for q in questions], indent=2, ensure_ascii=False) + "\n")
    return 0


def cmd_check(args) -> int:
    fixture = args.fixture
    vanilla = DEFAULT_VANILLA_WORKSPACE
    runner.prepare_vanilla_workspace(vanilla, fixture)
    failures = 0
    for arm, cwd in (("A", vanilla), ("B", fixture)):
        # the real invocation, so the check reflects what would actually run
        argv = transport.build_argv(arm, args.model, "(check)", stream_input=True)
        env = contamination.child_env(arm)
        report = contamination.preflight(arm, cwd, fixture, REPOS, argv, env)
        print(f"\narm {arm} ({cwd}) -> {'PASS' if report['passed'] else 'FAIL'}")
        for check in report["checks"]:
            mark = "ok " if check["ok"] else ("FAIL" if check["severity"] == "fatal" else "warn")
            print(f"  [{mark}] {check['name']}: {check['detail']}")
        failures += 0 if report["passed"] else 1
    return 1 if failures else 0


def cmd_probe(args) -> int:
    payload = runner.run_probe(args.out, args.model, fixture=args.fixture)
    print(json.dumps(payload["arms"], indent=2)[:4000])
    print(f"\nprobe ok: {payload['ok']}  -> {args.out / 'probe.json'}")
    return 0 if payload["ok"] else 1


def cmd_run(args) -> int:
    result = runner.run_batch(
        out=args.out, only=args.only, limit=args.limit, model_name=args.model,
        fixture=args.fixture, dry_run=args.dry_run, timeout=args.timeout,
        force=args.force, max_cost_usd=args.max_cost_usd,
        allow_unpinned=args.allow_unpinned, skip_probe=args.skip_probe,
        contaminate_qid=args.simulate_contamination,
        jobs=args.jobs, max_turns=args.max_turns, judge_model=args.judge_model)
    print(f"\nplanned={result['planned']} ran={result['ran']} skipped={result['skipped']} "
          f"cost=${result['cost_usd']:.2f}")
    if result.get("statuses"):
        print("statuses: " + "  ".join(f"{k}={v}" for k, v in sorted(result["statuses"].items())))
    if result["failed"]:
        print(f"non-ok runs: {result['failed']}")
    if result.get("quota_stop"):
        print(f"\nquota/usage limit stopped dispatch: {result['quota_stop'][:200]}\n"
              "those runs are `quota_exceeded` (retryable), NOT `wrong`. "
              "Re-run the same command to pick them up.")
        return 2
    # a quota stop is the only non-zero exit that means "nothing is wrong with
    # the harness"; anything else non-ok is still worth a non-zero status
    return 1 if result["failed"] else 0


def cmd_judge(args) -> int:
    result = judging.judge_batch(args.out, load_questions(), args.judge_model or args.model,
                                 dry_run=args.dry_run, only=args.only, limit=args.limit,
                                 force=args.force)
    print(f"graded={result['graded']} skipped={result['skipped']} pending={result['pending']}")
    return 0


def cmd_summary(args) -> int:
    print(summarize.write(args.out)["summary"])
    return 0


def cmd_reset_budget(args) -> int:
    forgiven, gross = runner.reset_budget(args.out)
    print(f"budget reset: ${forgiven:.2f} forgiven, ${gross:.2f} gross spend recorded in "
          f"{runner.BUDGET_BASELINE}\n"
          "--max-cost-usd now counts only runs made from here on")
    return 0


def cmd_dryrun(args) -> int:
    args.dry_run = True
    args.skip_probe = True
    for step in (cmd_run, cmd_judge):
        step(args)
    return cmd_summary(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark-harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("questions", help="parse questions and report coverage")
    _common(p)
    p.add_argument("--json", default="", help="also write parsed questions here")
    p.set_defaults(func=cmd_questions)

    p = sub.add_parser("check", help="contamination preflight for both arms, no model call")
    _common(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("probe", help="verify the git command forms the arms emit are permitted")
    _common(p)
    p.set_defaults(func=cmd_probe)

    for name, fn, helptext in (("run", cmd_run, "execute the run plan"),
                               ("dryrun", cmd_dryrun, "run+judge+summary with the model stubbed")):
        p = sub.add_parser(name, help=helptext)
        _common(p)
        p.add_argument("--only", action="append", default=[],
                       metavar="CATEGORY|ARM|QID",
                       help="repeatable: c-status, f, A, vanilla, captain, C7")
        p.add_argument("--limit", type=int, default=0, help="first N runs, for a smoke run")
        p.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                       help=f"concurrent runs (default {DEFAULT_JOBS}); each worker gets its "
                            "own vanilla workspace and its own query-only shim")
        p.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS,
                       help=f"graded attempts per question (default {DEFAULT_MAX_TURNS}): the "
                            "question, then an identical neutral nudge in both arms after any "
                            "verdict that is not `correct`. Questions whose correct answer is "
                            "an abstention are always single-shot.")
        p.add_argument("--timeout", type=int, default=1800)
        p.add_argument("--force", action="store_true", help="re-run completed runs")
        p.add_argument("--max-cost-usd", type=float, default=0.0)
        p.add_argument("--allow-unpinned", action="store_true",
                       help="proceed even if the fixture drifted off fixture.lock")
        p.add_argument("--skip-probe", action="store_true")
        p.add_argument("--dry-run", action="store_true", help="stub the model")
        p.add_argument("--judge-model", default="")
        p.add_argument("--simulate-contamination", default="", metavar="QID",
                       help="dry run only: contaminate this question's vanilla run "
                            "to demonstrate the check firing")
        p.set_defaults(func=fn)

    p = sub.add_parser("judge", help="blind grading of completed runs")
    _common(p)
    p.add_argument("--only", action="append", default=[])
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--judge-model", default="")
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("reset-budget",
                       help="zero the campaign ledger --max-cost-usd counts against; "
                            "gross spend is preserved in the baseline file")
    _common(p)
    p.set_defaults(func=cmd_reset_budget)

    p = sub.add_parser("summary", help="render tables from the artifacts")
    _common(p)
    p.set_defaults(func=cmd_summary)

    args = parser.parse_args(argv)
    args.out = args.out.resolve()
    args.fixture = args.fixture.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
