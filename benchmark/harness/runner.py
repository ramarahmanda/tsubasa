"""Run plan execution: one fresh session per (arm, question), resumable.

Ordering is fixed (category file order, then question number, then arm A then
B) so two invocations of the harness produce diffable output. Artifacts are
written per run and never rewritten in place: a retried run moves the previous
attempt aside rather than deleting it.

Two things make a run more than one model call:

**The follow-up loop.** A question is asked, the answer is graded by the blind
judge, and if the verdict is not `correct` the SAME session is nudged and
graded again, up to --max-turns. This measures how many turns and how much wall
clock an arm needs to reach a correct answer, which a single-shot table cannot
show: a fast wrong first answer that takes three turns to fix is worse than a
slower correct one. The nudge is a constant (config.NUDGE), identical in both
arms, and leaks nothing about the gold answer. Questions whose correct answer
is an ABSTENTION are single-shot: nudging an arm that correctly refused would
train it out of the behaviour being measured.

**Parallelism.** Runs share no state -- separate sessions, separate
workspaces -- so --jobs N runs N at a time. What is NOT shared has to be made
per-worker to keep it that way:

  * the vanilla workspace. prepare_vanilla_workspace() rebuilds symlinks in a
    fixed path; two concurrent arm-A runs would rebuild it under each other.
    Each worker slot gets its own directory, prepared once before dispatch.
  * the query-only `tsubasa` shim. One file per worker slot, so no worker
    truncates a shim another worker's session is about to exec.

Cost is checked BEFORE a run is dispatched, not only after it lands, or
--max-cost-usd would be exceeded by up to N runs. A run that dies because the
account ran out of allowance is `quota_exceeded`: retryable, never `wrong`,
and it stops further dispatch rather than burning the remaining questions.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import citations
import contamination
import guard
import judge as judging
import model as transport
import stubmodel
from config import (ARMS, DEFAULT_FIXTURE, DEFAULT_JOBS, DEFAULT_MAX_TURNS,
                    DEFAULT_VANILLA_WORKSPACE, NUDGE, REPOS, SINGLE_SHOT_REASON,
                    arm_label, normalise_arm)
from questions import Question, load_questions, usable

# complete; never re-run automatically. `quota_exceeded` is deliberately NOT
# here: it is an account outcome, not a measurement, and must be retried.
TERMINAL = ("ok", "no_answer")
SCHEMA = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def run_path(out: Path, arm: str, qid: str) -> Path:
    return out / "runs" / arm / f"{qid}.json"


def stream_path(out: Path, arm: str, qid: str) -> Path:
    return out / "runs" / arm / f"{qid}.stream.jsonl"


def load_run(out: Path, arm: str, qid: str) -> dict | None:
    path = run_path(out, arm, qid)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def _archive(out: Path, arm: str, qid: str) -> None:
    """Keep every attempt. Raw artifacts are the deliverable; nothing is
    summarised away and nothing is silently overwritten.

    The judge verdicts go with the attempt that produced them. A verdict left
    behind would be read as the verdict on the NEW attempt's answer -- one
    attempt's grade attached to another attempt's text, which is the one way
    this directory could tell a lie about itself.
    """
    base = run_path(out, arm, qid)
    if not base.is_file():
        return
    n = 1
    while (base.parent / f"{qid}.attempt{n}.json").exists():
        n += 1
    base.rename(base.parent / f"{qid}.attempt{n}.json")
    stream = stream_path(out, arm, qid)
    if stream.is_file():
        stream.rename(stream.parent / f"{qid}.attempt{n}.stream.jsonl")
    judge_dir = out / "judge"
    if judge_dir.is_dir():
        for verdict in sorted(judge_dir.glob(f"{qid}-{arm}.json")) + \
                sorted(judge_dir.glob(f"{qid}-{arm}.turn*.json")):
            verdict.rename(judge_dir / f"{verdict.stem}.attempt{n}{verdict.suffix}")


# ------------------------------------------------------------- workspaces

def prepare_vanilla_workspace(path: Path, fixture: Path) -> Path:
    """A view of the fixture with nothing but the four repos in it.

    The fixture root itself carries .tsubasa/ and a captain CLAUDE.md, and the
    SessionStart hook walks upward from cwd, so the vanilla arm cannot run
    there. Symlinked children give identical code, docs and git history with
    no captain anywhere at or above the working directory. Nothing is written
    below the fixture.
    """
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
    for repo in REPOS:
        (path / repo).symlink_to(fixture / repo, target_is_directory=True)
    return path


def workspace_for(arm: str, fixture: Path, vanilla_root: Path, prepare: bool = True) -> Path:
    if arm == "B":
        return fixture
    return prepare_vanilla_workspace(vanilla_root, fixture) if prepare else vanilla_root


def slot_dir(vanilla_root: Path, index: int) -> Path:
    """Per-worker vanilla workspace. Slot 0 keeps the default path so a
    single-job run is byte-identical to the old sequential behaviour."""
    return vanilla_root if index == 0 else vanilla_root.parent / f"{vanilla_root.name}-w{index}"


class Slots:
    """A worker's private (vanilla workspace, shim bin dir) pair.

    Handed out one at a time and returned on completion, so no two concurrent
    runs ever share either. Workspaces are prepared once, up front: rebuilding
    symlinks while a live session is reading the directory is exactly the race
    --jobs would otherwise introduce.
    """

    def __init__(self, out: Path, fixture: Path, vanilla_root: Path, jobs: int,
                 prepare: bool = True):
        self._q: queue.Queue[tuple[int, Path, Path]] = queue.Queue()
        self.dirs: list[Path] = []
        for i in range(max(1, jobs)):
            workspace = slot_dir(vanilla_root, i)
            if prepare:
                prepare_vanilla_workspace(workspace, fixture)
            bin_dir = out / "bin" / f"w{i}"
            self.dirs.append(workspace)
            self._q.put((i, workspace, bin_dir))

    def acquire(self) -> tuple[int, Path, Path]:
        return self._q.get()

    def release(self, slot: tuple[int, Path, Path]) -> None:
        self._q.put(slot)


# ------------------------------------------------------------- the run plan

def plan(questions: list[Question], only: list[str], limit: int = 0) -> list[tuple[Question, str]]:
    arms = [a for a in ARMS]
    # a single lowercase letter is a category prefix (`f`), a single uppercase
    # letter is an arm (`A`); anything with a digit suffix is a question id
    cats = {t for t in only if "-" in t or (len(t) == 1 and t.islower())}
    qids = {t.upper() for t in only if len(t) > 1 and t[0].isalpha() and t[1:].isdigit()}
    picked_arms = {normalise_arm(t) for t in only
                   if not (len(t) == 1 and t.islower()) and normalise_arm(t)}
    if picked_arms:
        arms = [a for a in arms if a in picked_arms]

    out: list[tuple[Question, str]] = []
    for q in questions:
        if cats and not (q.category in cats or q.category[0] in cats):
            continue
        if qids and q.qid not in qids:
            continue
        for arm in arms:
            out.append((q, arm))
    return out[:limit] if limit else out


# ------------------------------------------------------------- a single run

def grader(model_name: str, dry_run: bool):
    """The blind judge, as a callable. Injected into `execute` so the dry run
    and the tests can supply their own without the runner knowing."""
    def grade(question: Question, answer: str, cites: dict | None = None) -> dict:
        return judging.grade(question, answer, model_name, dry_run=dry_run, cites=cites)
    return grade


def execute(q: Question, arm: str, *, out: Path, fixture: Path, vanilla_root: Path,
            model_name: str, dry_run: bool, timeout: int, allow_unpinned: bool,
            contaminate: bool = False, max_turns: int = DEFAULT_MAX_TURNS,
            grade_fn=None, bin_dir: Path | None = None,
            prepare_workspace: bool = True) -> dict:
    """One (question, arm) run: up to `max_turns` graded turns in one session."""
    cwd = workspace_for(arm, fixture, vanilla_root, prepare=prepare_workspace)
    prompt = transport.build_prompt(q.prompt_text, REPOS)
    argv = transport.build_argv(arm, model_name, prompt, stream_input=True)
    transport.assert_no_bash_allowlist(argv)
    grade_fn = grade_fn or grader(model_name, dry_run)

    env = contamination.child_env(arm)
    guard_log = out / "runs" / arm / f"{q.qid}.guard.log"
    if guard_log.exists():
        guard_log.unlink()
    if arm == "B":
        # arm B may read its graph; it may not write to it. Per-worker bin dir:
        # two concurrent runs must not rewrite one shim file.
        env.update(guard.write_query_only_shim(bin_dir or (out / "bin"), guard_log))

    pre = contamination.preflight(arm, cwd, fixture, REPOS, argv, env,
                                  allow_unpinned=allow_unpinned or dry_run)

    # An abstention is the correct answer for f-negative and the honesty
    # probes. Nudging an arm that correctly abstained would coach it out of the
    # behaviour under test, so those questions get exactly one turn.
    single_shot = bool(q.expect_abstain)
    turn_cap = 1 if single_shot else max(1, max_turns)

    record: dict = {
        "schema": SCHEMA,
        "qid": q.qid, "category": q.category, "arm": arm, "arm_label": arm_label(arm),
        "model_requested": model_name,
        "started_at": _now(),
        "dry_run": bool(dry_run),
        "stub": bool(dry_run),
        "prompt": prompt,
        "nudge": NUDGE,
        "max_turns_configured": max(1, max_turns),
        "max_turns_effective": turn_cap,
        "single_shot": single_shot,
        "single_shot_reason": SINGLE_SHOT_REASON if single_shot else "",
        "invocation": {"argv": argv, "cwd": str(cwd),
                       "path_head": env.get("PATH", "").split(":")[0]},
    }

    if not pre["passed"]:
        record.update(status="contaminated", finished_at=_now(), answer="",
                      contamination={"passed": False, "fatal": pre["fatal"],
                                     "preflight": pre["checks"], "postflight": []},
                      error="preflight contamination check failed; no model call was made")
        return record

    before = guard.graph_fingerprint(fixture)
    stream = stream_path(out, arm, q.qid)
    started = time.time()

    if dry_run:
        session = stubmodel.StubSession(q, arm, model_name, stream, contaminate=contaminate)
    else:
        session = transport.Session(argv, cwd, env, stream, timeout=timeout)

    turns: list[dict] = []
    quota: str = ""
    judge_error = ""
    cumulative_cost = 0.0
    cumulative_seconds = 0.0
    try:
        for index in range(1, turn_cap + 1):
            asked = prompt if index == 1 else NUDGE
            turn = session.ask(asked)
            parsed = transport.parse_messages(
                turn["messages"], "".join(json.dumps(m) + "\n" for m in turn["messages"]))

            # cumulative in the CLI, so the per-turn figure is a delta
            total_cost = parsed["cost_usd"]
            total_cost = cumulative_cost if total_cost is None else total_cost
            turn_cost = round(max(0.0, total_cost - cumulative_cost), 6)
            cumulative_cost = max(cumulative_cost, total_cost)
            cumulative_seconds = round(cumulative_seconds + turn["seconds"], 3)

            entry = {
                "turn": index,
                "asked": "question" if index == 1 else "nudge",
                "asked_text": asked,
                "answer": parsed["answer"],
                "seconds": turn["seconds"],
                "seconds_cumulative": cumulative_seconds,
                "cost_usd": turn_cost,
                "cost_usd_cumulative": round(cumulative_cost, 6),
                "model_iterations": parsed["num_turns"],
                "usage": parsed["usage"],
                "tool_call_count": len(parsed["tool_calls"]),
                "denied_tool_call_count": len(parsed["denied_tool_calls"]),
                "result_subtype": parsed["result_subtype"],
                "timed_out": turn["timed_out"],
                "session_ended": turn["eof"],
                "citations": citations.report(parsed["answer"], fixture),
            }

            quota = transport.quota_evidence(parsed, session.stderr, None)
            if quota:
                entry.update(verdict="", verdict_rationale="", quota_evidence=quota)
                turns.append(entry)
                break

            if not parsed["answer"]:
                # nothing to grade and nothing to nudge: a dead or silent turn
                entry.update(verdict="", verdict_rationale="no answer produced")
                turns.append(entry)
                break

            try:
                # hand the judge what the resolver already proved, so a real
                # graph id is not mistaken for an invented one
                verdict = grade_fn(q, parsed["answer"], entry["citations"])
            except Exception as exc:                      # noqa: BLE001
                text = f"{type(exc).__name__}: {exc}"
                if transport.looks_like_quota(text):
                    quota = f"judge call failed on allowance: {text[:200]}"
                    entry.update(verdict="", verdict_rationale="", quota_evidence=quota)
                    turns.append(entry)
                    break
                judge_error = text[:400]
                entry.update(verdict="", verdict_rationale=f"judge failed: {judge_error}")
                turns.append(entry)
                break

            entry.update(verdict=verdict.get("verdict", ""),
                         verdict_rationale=verdict.get("rationale", ""),
                         judge=verdict)
            turns.append(entry)

            # write the per-turn judgement as its own artifact; turn 1's is
            # also the canonical single-shot verdict for this (question, arm)
            _write_json(out / "judge" / f"{q.qid}-{arm}.turn{index}.json",
                        {**verdict, "arm": arm, "turn": index})
            if index == 1:
                canonical = judging.judge_path(out, q.qid, arm)
                if not canonical.is_file():
                    _write_json(canonical, {**verdict, "arm": arm, "turn": 1,
                                            "single_shot": True})

            if entry["verdict"] == "correct" or turn["eof"] or turn["timed_out"]:
                break
    finally:
        proc = session.close()

    parsed_all = transport.parse_stream(stream)
    per_turn = transport.parse_turns(stream)
    after = guard.graph_fingerprint(fixture)

    post = contamination.postflight(arm, parsed_all["raw"], parsed_all["init"],
                                    parsed_all["tool_calls"], parsed_all["hook_events"])
    merged = contamination.merge(pre, post)

    first = turns[0] if turns else {}

    def _token(t: dict) -> str:
        """What this turn is, when it has no verdict. `no_answer`, `quota` and
        `ungraded` are three different events and folding them together would
        report a harness or account failure as an arm's answer."""
        if t.get("verdict"):
            return t["verdict"]
        if t.get("quota_evidence"):
            return "quota"
        if not t.get("answer"):
            return "no_answer"
        return "ungraded"

    verdict_sequence = [_token(t) for t in turns]
    correct_at = next((t["turn"] for t in turns if t.get("verdict") == "correct"), None)
    reached = next((t for t in turns if t.get("verdict") == "correct"), {})

    git_denials = [d for d in parsed_all["denied_tool_calls"]
                   if "git" in json.dumps(d.get("input", {}))]
    status = "ok"
    if quota:
        # an account limit says nothing about the arm: retryable, never `wrong`
        status = "quota_exceeded"
    elif not merged["passed"]:
        status = "contaminated"
    elif before != after:
        status = "graph_mutated"
    elif judge_error:
        status = "judge_error"
    elif proc["returncode"] != 0 and not first.get("answer"):
        status = "error"
    elif git_denials:
        status = "tool_denied"
    elif not first.get("answer"):
        # distinct from a wrong answer: the session stopped without producing
        # one, which is a harness or permission artifact, not an arm failure
        status = "no_answer"

    record.update({
        "finished_at": _now(),
        "status": status,
        "wall_clock_s": round(time.time() - started, 3),
        "process": {"returncode": proc["returncode"], "stderr": proc["stderr"][:2000]},
        "session_model": parsed_all["session_model"],

        # turn 1, kept under the original names: this is the single-shot number,
        # the one comparable with the earlier rounds of this benchmark
        "answer": first.get("answer", ""),
        "num_turns": first.get("model_iterations"),
        "usage": first.get("usage") or parsed_all["usage"],
        "citations": first.get("citations") or citations.report("", fixture),
        "cost_usd_turn1": first.get("cost_usd") or 0.0,
        "wall_clock_turn1_s": first.get("seconds") or 0.0,

        # the follow-up loop
        "turns": turns,
        "turns_used": len(turns),
        "nudges_sent": max(0, len([t for t in turns if t["asked"] == "nudge"])),
        "verdict_sequence": verdict_sequence,
        "turns_to_correct": correct_at,
        "seconds_to_correct": reached.get("seconds_cumulative"),
        "cost_to_correct": reached.get("cost_usd_cumulative"),
        "final_answer": turns[-1].get("answer", "") if turns else "",
        "final_verdict": (turns[-1].get("verdict") or "") if turns else "",

        # whole-session totals
        "cost_usd": round(cumulative_cost, 6),
        "usage_all_turns": parsed_all["usage"],
        "tool_calls": parsed_all["tool_calls"],
        "tool_call_count": len(parsed_all["tool_calls"]),
        "denied_tool_calls": parsed_all["denied_tool_calls"],
        "denied_tool_call_count": len(parsed_all["denied_tool_calls"]),
        "files_opened": parsed_all["files_opened"],
        "judge_cost_usd": round(sum((t.get("judge") or {}).get("judge_cost_usd") or 0.0
                                    for t in turns), 6),
        "turns_parsed_from_stream": len(per_turn),
        "rate_limits": parsed_all["rate_limits"],
        "rate_limit_warnings": transport.rate_limit_warnings(parsed_all),
        "quota_evidence": quota,
        "judge_error": judge_error,
        "graph_fingerprint": {"before": before, "after": after, "unchanged": before == after},
        "guard_log": guard.read_guard_log(guard_log),
        "contamination": merged,
        "raw_stream": str(stream.relative_to(out)),
    })
    return record


# ------------------------------------------------------------- the batch

BUDGET_BASELINE = "budget-baseline.json"


def prior_spend(out: Path) -> float:
    """What this artifact directory has cost since the last budget reset, so
    --max-cost-usd is a ceiling on the whole campaign rather than on one
    invocation of it.

    Superseded attempts ARE counted. Money spent on a run that was later
    retried is still money spent, and excluding it would let a directory that
    retried often drift arbitrarily far past the ceiling.

    That is right within one campaign and wrong across several. Developing the
    harness means re-running the same questions to test a harness change, and
    those attempts accumulate into a ledger that no longer describes the cost
    of the results on disk: the ceiling then stops runs for spending that
    happened before the thing being measured existed. `reset_budget` records an
    offset for exactly that, and it is explicit rather than automatic, because
    silently forgetting spend is how a ceiling stops being one.
    """
    return _gross_spend(out) - _baseline(out)


def _gross_spend(out: Path) -> float:
    total = 0.0
    for existing in sorted((out / "runs").rglob("*.json")) if (out / "runs").is_dir() else []:
        try:
            payload = json.loads(existing.read_text())
        except ValueError:
            continue
        total += payload.get("cost_usd") or 0.0
        total += payload.get("judge_cost_usd") or 0.0
    return total


def _baseline(out: Path) -> float:
    try:
        return float(json.loads((out / BUDGET_BASELINE).read_text())["spent_before_reset"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def reset_budget(out: Path) -> tuple[float, float]:
    """Zero the campaign ledger. Returns (forgiven, gross) for reporting.

    The gross total is kept in the file rather than deleting artifacts, so what
    the directory truly cost stays recoverable and a reset is auditable.
    """
    gross = _gross_spend(out)
    prior = _baseline(out)
    (out / BUDGET_BASELINE).write_text(json.dumps(
        {"spent_before_reset": gross, "previous_baseline": prior}, indent=2) + "\n")
    return gross - prior, gross


def run_batch(*, out: Path, only: list[str], limit: int, model_name: str,
              fixture: Path = DEFAULT_FIXTURE, vanilla_root: Path | None = None,
              dry_run: bool = False, timeout: int = 1800, force: bool = False,
              max_cost_usd: float = 0.0, allow_unpinned: bool = False,
              skip_probe: bool = False, contaminate_qid: str = "",
              jobs: int = DEFAULT_JOBS, max_turns: int = DEFAULT_MAX_TURNS,
              judge_model: str = "") -> dict:
    questions = load_questions()
    unusable = [q.qid for q in questions if not usable(q)]
    vanilla_root = vanilla_root or DEFAULT_VANILLA_WORKSPACE
    jobs = max(1, jobs)

    if not dry_run and not skip_probe:
        probe = out / "probe.json"
        if not probe.is_file():
            raise SystemExit(
                f"no {probe}: run `probe` first. It verifies that the exact git command\n"
                "forms the arms emit are actually permitted. A denied `git -C <repo> log`\n"
                "is invisible at grading time and would zero out g-git-history.")
        if not json.loads(probe.read_text()).get("ok"):
            raise SystemExit(f"{probe} reports blocked commands; fix permissions first")

    todo = plan(questions, only, limit)

    # Resume filtering and attempt archiving happen up front, single-threaded,
    # so no worker can race another's archive.
    work: list[tuple[Question, str]] = []
    skipped = 0
    for q, arm in todo:
        prior = load_run(out, arm, q.qid)
        if prior and prior.get("status") in TERMINAL and not force:
            skipped += 1
            continue
        if prior:
            _archive(out, arm, q.qid)
        work.append((q, arm))

    slots = Slots(out, fixture, vanilla_root, jobs)
    grade_fn = grader(judge_model or model_name, dry_run)

    lock = threading.Lock()
    state = {"spent": prior_spend(out), "next": 0, "done": 0,
             "quota_stop": "", "budget_stop": False, "failed": [], "statuses": {}}
    print(f"running {len(work)} of {len(todo)} planned ({skipped} already complete) "
          f"· jobs={jobs} · max-turns={max_turns} · "
          f"prior spend ${state['spent']:.2f}"
          + (f" · ceiling ${max_cost_usd:.2f}" if max_cost_usd else ""))

    def worker() -> None:
        while True:
            with lock:
                if state["quota_stop"]:
                    return
                # checked BEFORE dispatch: checking only after a run lands lets
                # up to `jobs` runs start past the ceiling
                if max_cost_usd and state["spent"] >= max_cost_usd:
                    state["budget_stop"] = True
                    return
                if state["next"] >= len(work):
                    return
                index = state["next"]
                state["next"] += 1
            q, arm = work[index]
            slot = slots.acquire()
            _, workspace, bin_dir = slot
            try:
                record = execute(
                    q, arm, out=out, fixture=fixture, vanilla_root=workspace,
                    model_name=model_name, dry_run=dry_run, timeout=timeout,
                    allow_unpinned=allow_unpinned, max_turns=max_turns,
                    grade_fn=grade_fn, bin_dir=bin_dir, prepare_workspace=False,
                    contaminate=(contaminate_qid == q.qid and arm == "A"))
            except Exception as exc:                      # noqa: BLE001
                record = {"schema": SCHEMA, "qid": q.qid, "category": q.category,
                          "arm": arm, "arm_label": arm_label(arm), "status": "error",
                          "answer": "", "finished_at": _now(),
                          "error": f"{type(exc).__name__}: {exc}"[:2000],
                          "cost_usd": 0.0, "turns": [], "verdict_sequence": []}
            finally:
                slots.release(slot)

            _write_json(run_path(out, arm, q.qid), record)
            cost = (record.get("cost_usd") or 0.0) + (record.get("judge_cost_usd") or 0.0)
            with lock:
                state["spent"] += cost
                state["done"] += 1
                status = record.get("status", "?")
                state["statuses"][status] = state["statuses"].get(status, 0) + 1
                if status != "ok":
                    state["failed"].append(f"{q.qid}/{arm}:{status}")
                if status == "quota_exceeded" and not state["quota_stop"]:
                    state["quota_stop"] = record.get("quota_evidence") or "quota"
                    print(f"\n!! quota/usage limit hit on {q.qid}/{arm}: "
                          f"{state['quota_stop'][:160]}\n"
                          f"!! stopping dispatch. These runs are marked "
                          f"`quota_exceeded`, not `wrong`, and will be retried by the "
                          f"next `run` invocation.\n")
                seq = ",".join(record.get("verdict_sequence") or []) or "-"
                ttc = record.get("turns_to_correct")
                print(f"[{state['done']:>3}/{len(work)}] {q.qid:<4} {arm} "
                      f"{arm_label(arm):<8} {status:<14} "
                      f"{record.get('wall_clock_s', 0):>6.1f}s "
                      f"${record.get('cost_usd') or 0:.4f} "
                      f"turns={record.get('turns_used', 0)} "
                      f"ttc={ttc if ttc else '-':<4} {seq:<28} "
                      f"running ${state['spent']:.2f}")

    threads = [threading.Thread(target=worker, name=f"bench-w{i}", daemon=True)
               for i in range(min(jobs, max(1, len(work))))]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        with lock:
            state["quota_stop"] = state["quota_stop"] or "interrupted"
        print("\ninterrupted: waiting for in-flight runs to finish writing their artifacts")
        for t in threads:
            t.join(timeout=timeout + 120)

    if state["budget_stop"]:
        print(f"stopping: running cost ${state['spent']:.2f} reached "
              f"--max-cost-usd {max_cost_usd}")

    manifest = {
        "harness_schema": SCHEMA,
        "generated_at": _now(),
        "model": model_name,
        "judge_model": judge_model or model_name,
        "fixture": str(fixture),
        "dry_run": bool(dry_run),
        "jobs": jobs,
        "max_turns": max_turns,
        "nudge": NUDGE,
        "nudge_policy": "identical constant string in both arms; carries nothing from the "
                        "question, gold answer, locator or trap",
        "single_shot_categories": SINGLE_SHOT_REASON,
        "questions_parsed": len(questions),
        "questions_unusable": unusable,
        "vanilla_workspaces": [str(d) for d in slots.dirs],
        "arms": {a: {"label": ARMS[a][0], "setup": ARMS[a][1]} for a in ARMS},
    }
    _write_json(out / "manifest.json", manifest)
    return {"planned": len(todo), "ran": state["done"], "skipped": skipped,
            "failed": state["failed"], "cost_usd": round(state["spent"], 4),
            "statuses": state["statuses"], "quota_stop": state["quota_stop"],
            "budget_stop": state["budget_stop"], "manifest": manifest}


def run_probe(out: Path, model_name: str, fixture: Path = DEFAULT_FIXTURE,
              vanilla_root: Path | None = None, timeout: int = 600) -> dict:
    """One cheap session per arm that runs the exact git command forms the
    arms emit, and reports which were permitted."""
    vanilla_root = vanilla_root or DEFAULT_VANILLA_WORKSPACE
    results = {}
    for arm in ARMS:
        cwd = workspace_for(arm, fixture, vanilla_root)
        argv = transport.build_argv(arm, model_name, guard.probe_prompt())
        transport.assert_no_bash_allowlist(argv)
        env = contamination.child_env(arm)
        stream = out / "probe" / f"{arm}.stream.jsonl"
        proc = transport.invoke(argv, cwd, env, stream, timeout=timeout)
        parsed = transport.parse_stream(stream)
        results[arm] = {
            "returncode": proc["returncode"],
            "answer": parsed["answer"],
            "denied_tool_calls": parsed["denied_tool_calls"],
            **guard.probe_verdict(parsed["answer"]),
        }
    payload = {"generated_at": _now(), "model": model_name,
               "commands": list(guard.PROBE_COMMANDS),
               "ok": all(r["ok"] for r in results.values()), "arms": results}
    _write_json(out / "probe.json", payload)
    return payload
