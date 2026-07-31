"""Turn the raw artifacts into tables. Deterministic: two runs diff cleanly.

Nothing here recomputes a grade or a citation; it reads what the runner, the
judge and the resolver wrote. Categories that are expected to tie or to lose
are printed in the same table as the rest, at full strength.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from config import ARMS, CATEGORY_ORDER, EXPECTED_TIE, arm_label

VERDICTS = ("correct", "partial", "wrong", "confabulated", "no_answer")
# `quota_exceeded` belongs here and nowhere else: the account ran out of
# allowance, which is a fact about the account and not about the arm.
NOT_RUN_STATUSES = ("contaminated", "error", "tool_denied", "graph_mutated",
                    "quota_exceeded", "judge_error")


def load(out: Path) -> dict:
    runs, judged = {}, {}
    for path in sorted((out / "runs").rglob("*.json")) if (out / "runs").is_dir() else []:
        if ".attempt" in path.name:
            continue
        run = json.loads(path.read_text())
        runs[(run["qid"], run["arm"])] = run
    for path in sorted((out / "judge").glob("*.json")) if (out / "judge").is_dir() else []:
        # `<qid>-<arm>.turnN.json` are the per-turn judgements of the follow-up
        # loop; the canonical `<qid>-<arm>.json` is turn 1, the single-shot
        # verdict comparable with earlier rounds. `.attemptN.` files are the
        # verdicts of a superseded attempt, kept as evidence, never counted.
        # `.final.` is the ASSISTED verdict (the answer after up to two nudges)
        # and must never land in `judged`: it sorts after the canonical file, so
        # letting it through would silently replace every single-shot verdict in
        # the summary with the nudged one.
        if any(mark in path.name for mark in (".turn", ".attempt", ".final")):
            continue
        verdict = json.loads(path.read_text())
        judged[(verdict["qid"], verdict["arm"])] = verdict
    manifest = {}
    if (out / "manifest.json").is_file():
        manifest = json.loads((out / "manifest.json").read_text())
    return {"runs": runs, "judged": judged, "manifest": manifest}


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 3) if values else 0.0


MAX_REPORTED_TURNS = 3


def _reconciled(run: dict, verdict: dict | None) -> dict:
    """`run` with turns_to_correct reconciled against the canonical verdict.

    `turns_to_correct` is written by the INLINE judge while the run executes and
    is then frozen; `judge --force` never revisits it. So a rubric fix that
    flips turn 1 to correct leaves the run artifact still claiming the arm never
    got there. Measured: after four f-negative rubric defects were corrected,
    F2-B, F5-B, F6-A and F6-B all read `final_verdict` wrong against a canonical
    `correct`, and two of them still carried gold-scheme values.

    The canonical judge is the authority on turn 1, so a canonical `correct`
    means turns_to_correct is 1 whatever the run recorded. Turns 2+ cannot be
    reconciled this way and are left as they were: re-grading them needs a
    re-run, not a re-judge.
    """
    if not verdict or verdict.get("accuracy") != "correct":
        return run
    if run.get("turns_to_correct") == 1:
        return run
    return {**run, "turns_to_correct": 1,
            "seconds_to_correct": run.get("wall_clock_turn1_s"),
            "cost_to_correct": run.get("cost_usd_turn1")}


def _iteration_stats(counted: list[dict]) -> dict:
    """Turns-to-correct, read straight off the run artifacts.

    This is the measurement the single-shot table cannot make. "Vanilla is fast
    at being wrong" is only a claim about speed until you also count what it
    costs to fix: a first answer delivered in 20s that needs three turns to
    become correct is worse than one correct answer in 90s.

    Questions whose correct answer is an ABSTENTION are excluded from the
    distribution and counted separately: they are single-shot by design, so
    "never reached correct" would mean "was wrong once and never asked again",
    which is not the same measurement as the multi-turn questions'.
    """
    multi = [r for r in counted if not r.get("single_shot")]
    single = [r for r in counted if r.get("single_shot")]

    at_turn = {n: sum(1 for r in multi if r.get("turns_to_correct") == n)
               for n in range(1, MAX_REPORTED_TURNS + 1)}
    reached = [r for r in multi if r.get("turns_to_correct")]
    never = [r for r in multi if not r.get("turns_to_correct")]
    return {
        "iteration": {
            "multi_turn_questions": len(multi),
            "single_shot_questions": len(single),
            "single_shot_correct": sum(1 for r in single
                                       if r.get("turns_to_correct") == 1),
            "correct_at_turn": at_turn,
            "correct_beyond_reported_turns": sum(
                1 for r in reached if (r.get("turns_to_correct") or 0) > MAX_REPORTED_TURNS),
            "never_correct": len(never),
            "reached_correct": len(reached),
            "turns_to_correct_median": _median(
                [float(r["turns_to_correct"]) for r in reached]),
            "seconds_to_correct_median": _median(
                [float(r.get("seconds_to_correct") or 0.0) for r in reached]),
            "cost_to_correct_median": _median(
                [float(r.get("cost_to_correct") or 0.0) for r in reached]),
            "seconds_to_correct_total": round(
                sum(float(r.get("seconds_to_correct") or 0.0) for r in reached), 1),
            "nudges_sent": sum(r.get("nudges_sent") or 0 for r in counted),
            "turns_used_median": _median([float(r.get("turns_used") or 1) for r in multi]),
            # What the extra turns bought. Keyed on turns_to_correct > 1, NOT on
            # the inline turn-1 verdict: reconciliation sets turns_to_correct to
            # 1 when the canonical judge says turn 1 was correct, while
            # verdict_sequence still holds the stale inline verdict. Testing the
            # sequence counted every reconciled run as "fixed by a nudge" when no
            # nudge was sent at all, and reported 18 for an arm whose turn-2 and
            # turn-3 wins total 5.
            "improved_after_nudge": sum(
                1 for r in multi if (r.get("turns_to_correct") or 0) > 1),
        }
    }


def _kind_totals(runs: list[dict]) -> dict:
    """Citation counts per kind, summed across runs."""
    out: dict[str, int] = {}
    for run in runs:
        for kind, stats in ((run.get("citations") or {}).get("by_kind") or {}).items():
            out[kind] = out.get(kind, 0) + (stats or {}).get("total", 0)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def aggregate(data: dict) -> dict:
    runs, judged = data["runs"], data["judged"]
    per_arm: dict[str, dict] = {}
    for arm in ARMS:
        mine = [r for (_, a), r in sorted(runs.items()) if a == arm]
        counted = [r for r in mine if r["status"] not in NOT_RUN_STATUSES]
        # Verdicts come from USABLE runs only. The follow-up loop grades each
        # turn as it happens, i.e. before the run's final status is known, so a
        # run later quarantined as contaminated / graph-mutated / quota-stopped
        # can have a turn-1 judge file on disk. Excluded runs are counted in
        # their own column and must never reach a verdict column.
        graded = [judged[(r["qid"], arm)] for r in counted if (r["qid"], arm) in judged]
        per_arm[arm] = {
            "label": arm_label(arm),
            "runs": len(mine),
            "usable": len(counted),
            "excluded": {s: sum(1 for r in mine if r["status"] == s) for s in NOT_RUN_STATUSES},
            "verdicts": {v: sum(1 for g in graded if g["verdict"] == v) for v in VERDICTS},
            "citations": {
                "total": sum((r.get("citations") or {}).get("total", 0) for r in counted),
                "resolved": sum((r.get("citations") or {}).get("resolved", 0) for r in counted),
                "unresolved": sum((r.get("citations") or {}).get("unresolved", 0) for r in counted),
                "elided": sum((r.get("citations") or {}).get("elided", 0) for r in counted),
                # By kind, because the kinds are not interchangeable. `graph_id`
                # is an event or entity the arm read out of the knowledge graph:
                # a citation for WHY something is the way it is. An arm without a
                # graph cannot emit one at any skill level, so this is the column
                # that separates citing the record from citing the tree.
                "by_kind": _kind_totals(counted),
            },
            "cost_usd": round(sum(r.get("cost_usd") or 0.0 for r in mine), 4),
            "tokens": {
                key: sum((r.get("usage") or {}).get(key) or 0 for r in counted)
                for key in ("input_tokens", "output_tokens",
                            "cache_creation_input_tokens", "cache_read_input_tokens")
            },
            "wall_clock_median_s": _median([r.get("wall_clock_s") or 0.0 for r in counted]),
            "wall_clock_turn1_median_s": _median(
                [r.get("wall_clock_turn1_s") or r.get("wall_clock_s") or 0.0 for r in counted]),
            "turns_median": _median([float(r.get("num_turns") or 0) for r in counted]),
            "tool_calls_total": sum(r.get("tool_call_count") or 0 for r in counted),
            "denied_tool_calls": sum(r.get("denied_tool_call_count") or 0 for r in counted),
            "graph_mutations": sum(
                0 if (r.get("graph_fingerprint") or {}).get("unchanged", True) else 1
                for r in mine),
            "cost_usd_turn1": round(sum(r.get("cost_usd_turn1") or 0.0 for r in mine), 4),
            "judge_cost_usd": round(sum(r.get("judge_cost_usd") or 0.0 for r in mine), 4),
            # counted, not silently applied: the run artifacts still carry the
            # inline judge's frozen verdict, so a summary that quietly disagreed
            # with them would be the harder bug to find
            "ttc_reconciled": sum(
                1 for r in counted
                if _reconciled(r, judged.get((r["qid"], arm))) is not r),
            **_iteration_stats(
                [_reconciled(r, judged.get((r['qid'], arm))) for r in counted]),
        }

    per_category: dict[str, dict] = {}
    for category in CATEGORY_ORDER:
        row = {"expected_tie": EXPECTED_TIE.get(category, ""), "arms": {}}
        for arm in ARMS:
            resolved = [runs[(qid, a)] for (qid, a) in sorted(runs)
                        if a == arm and runs[(qid, a)]["category"] == category
                        and runs[(qid, a)]["status"] not in NOT_RUN_STATUSES]
            usable_keys = {(r["qid"], arm) for r in resolved}
            graded = [g for (qid, a), g in sorted(judged.items())
                      if a == arm and g.get("category") == category
                      and (qid, a) in usable_keys]
            row["arms"][arm] = {
                "n": len(graded),
                **{v: sum(1 for g in graded if g["verdict"] == v) for v in VERDICTS},
                "cites_total": sum((r.get("citations") or {}).get("total", 0) for r in resolved),
                "cites_resolved": sum(
                    (r.get("citations") or {}).get("resolved", 0) for r in resolved),
            }
        per_category[category] = row

    paired = []
    for qid in sorted({q for q, _ in runs}, key=lambda x: (x[0], int(x[1:]))):
        entry = {"qid": qid}
        for arm in ARMS:
            run = runs.get((qid, arm), {})
            verdict = judged.get((qid, arm), {})
            cites = run.get("citations") or {}
            entry[arm] = {
                "status": run.get("status", "-"),
                "verdict": verdict.get("verdict", "-"),
                "cites": f"{cites.get('resolved', 0)}/{cites.get('total', 0)}",
                "cost_usd": run.get("cost_usd") or 0.0,
                "turns_to_correct": run.get("turns_to_correct"),
                "verdict_sequence": run.get("verdict_sequence") or [],
                "seconds_to_correct": run.get("seconds_to_correct"),
                "single_shot": bool(run.get("single_shot")),
            }
        paired.append(entry)

    return {"per_arm": per_arm, "per_category": per_category, "paired": paired,
            "manifest": data["manifest"]}


# -------------------------------------------------------------- rendering

def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def render(agg: dict) -> str:
    manifest = agg["manifest"]
    stub = manifest.get("dry_run")
    out: list[str] = []
    if stub:
        out += ["> **STUB DATA, NOT RESULTS.** Every answer and every verdict below was",
                "> generated by the dry-run stub. Answer text is templated, verdicts are a",
                "> hash of the judge task, costs are zero. The only real measurements here",
                "> are the citation resolutions, which ran against the fixture on disk.", ""]
    out += [
        f"generated: {manifest.get('generated_at', '-')} · model: {manifest.get('model', '-')} · "
        f"fixture: {manifest.get('fixture', '-')}",
        f"questions parsed: {manifest.get('questions_parsed', '-')} · "
        f"unusable: {manifest.get('questions_unusable') or 'none'}",
        f"jobs: {manifest.get('jobs', '-')} · max turns: {manifest.get('max_turns', 1)} · "
        f"judge: {manifest.get('judge_model', manifest.get('model', '-'))}",
        "",
        "## Overall",
        "",
        "Verdict rows are the **single-shot (turn 1)** answer: one question, one",
        "answer, graded blind. That is the number comparable with the earlier rounds",
        "of this benchmark. What the follow-up turns bought is the next table.",
        "",]
    out += [
        _row(["metric"] + [f"{a} {arm_label(a)}" for a in ARMS]),
        _row(["---"] * (1 + len(ARMS))),
    ]

    def line(label, fn):
        out.append(_row([label] + [str(fn(agg["per_arm"][a])) for a in ARMS]))

    line("runs", lambda d: d["runs"])
    line("graded", lambda d: sum(d["verdicts"].values()))
    for verdict in VERDICTS:
        line(f"{verdict} (turn 1)", lambda d, v=verdict: d["verdicts"][v])
    line("excluded (contaminated/error/denied/mutated/quota)",
         lambda d: sum(d["excluded"].values()))
    for status in NOT_RUN_STATUSES:
        if any(agg["per_arm"][a]["excluded"].get(status) for a in ARMS):
            line(f"  {status}", lambda d, s=status: d["excluded"].get(s, 0))
    line("citations resolved / total",
         lambda d: f"{d['citations']['resolved']}/{d['citations']['total']}")
    line("citations unresolved", lambda d: d["citations"]["unresolved"])
    line("  of which elided shorthand", lambda d: d["citations"]["elided"])
    # Grounding density. Citations alone reward a long answer that cites a lot;
    # output tokens alone reward a short answer that asserts. The ratio is the
    # thing actually wanted: evidence per word. On c-status both arms scored
    # 10/10, and this was the metric that still separated them.
    line("citations per 1k output tokens",
         lambda d: f"{1000 * d['citations']['total'] / max(d['tokens']['output_tokens'], 1):.1f}")
    line("graph-id citations (record, not tree)",
         lambda d: d["citations"]["by_kind"].get("graph_id", 0))
    for kind in sorted({k for a in ARMS for k in agg["per_arm"][a]["citations"]["by_kind"]}
                       - {"graph_id"}):
        line(f"  cites: {kind}", lambda d, k=kind: d["citations"]["by_kind"].get(k, 0))
    line("cost USD (all turns)", lambda d: f"{d['cost_usd']:.4f}")
    line("cost USD (turn 1 only)", lambda d: f"{d['cost_usd_turn1']:.4f}")
    line("judge cost USD (excluded from arm cost)", lambda d: f"{d['judge_cost_usd']:.4f}")
    # Split, not summed. On c-status the captain wrote 41% FEWER output tokens
    # than vanilla while writing 3.5x more cache, and a combined "fresh tokens"
    # figure cancels those against each other and reports a wash. The two move
    # in opposite directions and are priced differently (output is the dearest
    # class, cache reads the cheapest), so the split is what explains a cost
    # gap; the total never does.
    line("output tokens (dearest class)", lambda d: d["tokens"]["output_tokens"])
    line("cache write tokens", lambda d: d["tokens"]["cache_creation_input_tokens"])
    line("cache read tokens", lambda d: d["tokens"]["cache_read_input_tokens"])
    line("input tokens (uncached)", lambda d: d["tokens"]["input_tokens"])
    # Context bought per retrieval. Separates "searched a lot" from "pulled a
    # lot back": an arm can make far fewer tool calls and still cost more,
    # because each call returns a whole subgraph rather than a few grep lines.
    line("cache write tokens per tool call",
         lambda d: round(d["tokens"]["cache_creation_input_tokens"]
                         / max(d["tool_calls_total"], 1)))
    line("wall clock median s (turn 1)", lambda d: d["wall_clock_turn1_median_s"])
    line("wall clock median s (whole run)", lambda d: d["wall_clock_median_s"])
    line("model iterations median (turn 1)", lambda d: d["turns_median"])
    line("tool calls", lambda d: d["tool_calls_total"])
    # non-zero means a rubric was corrected after these runs executed: the
    # canonical turn-1 verdict now disagrees with the frozen inline one, and
    # turns-to-correct follows the canonical judge. The run artifacts still
    # hold what the inline judge said; only turns 2+ remain unreconcilable.
    line("turns-to-correct reconciled to canonical",
         lambda d: d.get("ttc_reconciled", 0))
    line("denied tool calls", lambda d: d["denied_tool_calls"])
    line("graph mutations detected", lambda d: d["graph_mutations"])

    # ------------------------------------------------ iteration to correct
    out += ["", "## Iteration to a correct answer", "",
            f"Cap: {manifest.get('max_turns', 1)} turns. After any verdict that is not",
            "`correct` the same session is nudged with one constant string, identical in",
            f"both arms: {manifest.get('nudge', '(not recorded)')!r}",
            "",
            "Questions whose correct answer is an abstention (`f-negative`, and the two",
            "`g-git-history` honesty probes) are **single-shot by design** and are excluded",
            "from this distribution: nudging an arm that correctly refused would train it",
            "out of the behaviour being measured. They are counted on their own row.",
            "",
            _row(["metric"] + [f"{a} {arm_label(a)}" for a in ARMS]),
            _row(["---"] * (1 + len(ARMS)))]

    def iline(label, fn):
        out.append(_row([label] + [str(fn(agg["per_arm"][a]["iteration"])) for a in ARMS]))

    iline("multi-turn questions", lambda d: d["multi_turn_questions"])
    for n in range(1, MAX_REPORTED_TURNS + 1):
        iline(f"correct at turn {n}", lambda d, n=n: d["correct_at_turn"].get(n, 0))
    iline("never correct within the cap", lambda d: d["never_correct"])
    iline("reached correct at all", lambda d: d["reached_correct"])
    iline("median turns to correct (of those that got there)",
          lambda d: d["turns_to_correct_median"])
    iline("median seconds to correct", lambda d: d["seconds_to_correct_median"])
    iline("median USD to correct", lambda d: f"{d['cost_to_correct_median']:.4f}")
    iline("fixed by a nudge (wrong at turn 1, correct later)",
          lambda d: d["improved_after_nudge"])
    iline("nudges sent", lambda d: d["nudges_sent"])
    iline("median turns used", lambda d: d["turns_used_median"])
    iline("single-shot (abstain-correct) questions", lambda d: d["single_shot_questions"])
    iline("  of which correct", lambda d: d["single_shot_correct"])

    out += ["", "## By category", "",
            _row(["category", "n"]
                 + [f"{a}:{v[:4]}" for a in ARMS for v in ("correct", "partial", "wrong",
                                                           "confabulated", "no_answer")]
                 + [f"{a}:cites" for a in ARMS] + ["note"]),
            _row(["---"] * (2 + 5 * len(ARMS) + len(ARMS) + 1))]
    for category in CATEGORY_ORDER:
        row = agg["per_category"][category]
        cells = [category, str(max(row["arms"][a]["n"] for a in ARMS))]
        for arm in ARMS:
            cells += [str(row["arms"][arm][v]) for v in
                      ("correct", "partial", "wrong", "confabulated", "no_answer")]
        for arm in ARMS:
            data = row["arms"][arm]
            cells.append(f"{data['cites_resolved']}/{data['cites_total']}")
        cells.append(row["expected_tie"] or "")
        out.append(_row(cells))

    out += ["", "## Per question", "",
            "`verdicts` is every turn's verdict in order; `ttc` is the turn the answer",
            "first became correct, `-` if it never did (`1shot` where a nudge is withheld",
            "by design).",
            "",
            _row(["qid"] + [f"{a} verdicts" for a in ARMS] + [f"{a} ttc" for a in ARMS]
                 + [f"{a} cites" for a in ARMS] + [f"{a} status" for a in ARMS]),
            _row(["---"] * (1 + 4 * len(ARMS)))]
    for entry in agg["paired"]:
        cells = [entry["qid"]]
        cells += [",".join(entry[a]["verdict_sequence"]) or entry[a]["verdict"] for a in ARMS]
        cells += [str(entry[a]["turns_to_correct"]
                      or ("1shot" if entry[a]["single_shot"] else "-")) for a in ARMS]
        cells += [entry[a]["cites"] for a in ARMS]
        cells += [entry[a]["status"] for a in ARMS]
        out.append(_row(cells))

    out += ["", "## What these numbers do not show", "",
            "- Citation counts are EXISTENCE checks. A resolved citation proves the file,",
            "  commit or record id exists; it does not prove the source supports the claim.",
            "  That judgement belongs to the judge, and a run can be 100% resolved and still",
            "  graded `confabulated`.",
            "- `no_answer` is reported separately and never folded into `wrong`: a session",
            "  that stopped without answering is a harness outcome, not an arm's answer.",
            "- Excluded runs are counted, not hidden. Any non-zero contamination or",
            "  graph-mutation count invalidates the arm it belongs to until it is re-run.",
            "- `quota_exceeded` is an account outcome, not an arm's answer: the session/usage",
            "  allowance ran out mid-campaign. Those runs are retryable and are excluded",
            "  from every verdict column rather than scored.",
            "- Turns-to-correct is measured against THIS judge. The nudge says only that the",
            "  previous answer was wrong, so a turn-2 correction is the arm re-searching its",
            "  own workspace, not following a hint -- but 'wrong, try again' is still",
            "  information, and an arm that guesses differently each turn can reach `correct`",
            "  without ever having had grounds for it.",
            "- Seconds-to-correct and cost-to-correct count the arm's own turns only. The",
            "  judge latency and judge cost between turns are harness overhead and are",
            "  reported separately.",
            ""]
    return "\n".join(out) + "\n"


def write(out: Path) -> dict:
    agg = aggregate(load(out))
    (out / "summary.json").write_text(json.dumps(agg, indent=2, ensure_ascii=False) + "\n")
    text = render(agg)
    (out / "summary.md").write_text(text)
    return {"summary": text, "aggregate": agg}
