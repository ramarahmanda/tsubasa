"""Benchmark harness invariants: question parsing, run planning, and the
contamination check that keeps the vanilla arm vanilla.

Nothing here reaches a model or the fixture clones; the harness modules are
stdlib-only and import flat off benchmark/harness.
"""

import json
import sys
from pathlib import Path

from dataclasses import replace

import pytest

HARNESS = Path(__file__).resolve().parent.parent / "benchmark" / "harness"
# appended, not prepended: the harness uses short flat module names and must
# never shadow a stdlib or site-package import for the rest of the suite
sys.path.append(str(HARNESS))

citations = pytest.importorskip("citations")
contamination = pytest.importorskip("contamination")
judge = pytest.importorskip("judge")
questions_mod = pytest.importorskip("questions")
runner = pytest.importorskip("runner")
summarize = pytest.importorskip("summarize")
transport = pytest.importorskip("model")
from config import NUDGE, PERSONA_MARKER, REPOS


@pytest.fixture(scope="module")
def questions():
    return questions_mod.load_questions()


def test_every_question_parses_with_a_prompt_and_a_gold(questions):
    assert len(questions) == 72
    assert [q.qid for q in questions if not questions_mod.usable(q)] == []


def test_f_negative_is_normalised_not_dropped(questions):
    """f-negative carries **Why unanswerable.** instead of **Gold.** and no
    **Locator.** at all. Those items must survive parsing, flagged."""
    negatives = [q for q in questions if q.category == "f-negative"]
    assert len(negatives) == 7
    assert all(q.gold_kind == "unanswerable" and q.expect_abstain for q in negatives)
    assert all(not q.locator for q in negatives)
    assert [q.qid for q in questions if not q.locator] == [q.qid for q in negatives]


def test_honesty_probes_are_flagged(questions):
    assert [q.qid for q in questions if q.honesty_probe] == ["G11", "G12"]


def test_plan_ordering_and_filters(questions):
    assert len(runner.plan(questions, [])) == 144
    assert runner.plan(questions, [])[:2] == [(questions[0], "A"), (questions[0], "B")]
    assert len(runner.plan(questions, ["f"])) == 14          # category prefix
    assert len(runner.plan(questions, ["A"])) == 72          # arm
    assert len(runner.plan(questions, ["captain"])) == 72
    assert len(runner.plan(questions, ["C7"])) == 2          # single question
    assert len(runner.plan(questions, ["g", "A"])) == 12
    assert len(runner.plan(questions, [], limit=5)) == 5


def test_citation_extraction_kinds():
    kinds = {(c["kind"], c["raw"]) for c in citations.extract(
        "see etcd/README.md:21 and postgres/src/backend/access/heap/README.HOT, "
        "commit f4c7c410ee, evt-19960709-postgres-release-pg95-1-01, KEP-2221, "
        "and .../README.md")}
    assert ("file_line", "etcd/README.md:21") in kinds
    assert ("path", "postgres/src/backend/access/heap/README.HOT") in kinds
    assert ("sha", "f4c7c410ee") in kinds
    assert ("graph_id", "evt-19960709-postgres-release-pg95-1-01") in kinds
    assert ("kep", "KEP-2221") in kinds
    assert ("path_elided", ".../README.md") in kinds


def test_line_number_beyond_end_of_file_does_not_resolve(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "doc.md").write_text("one\ntwo\n")
    report = citations.report("see repo/doc.md:2 and repo/doc.md:99", tmp_path)
    resolved = {c["raw"]: c["resolved"] for c in report["citations"]}
    assert resolved["repo/doc.md:2"] is True
    assert resolved["repo/doc.md:99"] is False


def test_vanilla_postflight_catches_the_persona_string():
    clean = contamination.postflight("A", "no captain here", {}, [])
    assert clean["passed"]

    injected = contamination.postflight(
        "A", f'{{"output": "{PERSONA_MARKER}tsubasa (Principal Engineer)"}}', {}, [])
    assert not injected["passed"]
    assert "no_persona_in_transcript" in injected["fatal"]


def test_vanilla_postflight_catches_captain_tooling():
    plugin = contamination.postflight(
        "A", "", {"slash_commands": ["/tsubasa:recall"]}, [])
    assert "no_tsubasa_plugin_active_in_session" in plugin["fatal"]

    cli = contamination.postflight(
        "A", "", {}, [{"name": "Bash", "input": {"command": "cd x && tsubasa query foo"}}])
    assert "no_tsubasa_cli_invocation" in cli["fatal"]


def test_vanilla_child_env_has_no_tsubasa_on_path():
    import shutil
    env = contamination.child_env("A")
    assert shutil.which("tsubasa", path=env["PATH"]) is None
    assert shutil.which("git", path=env["PATH"]) is not None


def test_cli_is_launched_by_absolute_path_not_via_the_stripped_path():
    """Arm A strips every PATH entry holding a `tsubasa` entry point, and on a
    typical machine `tsubasa` and `claude` share ~/.local/bin. Popen resolves
    argv[0] against the CHILD's PATH, so a bare `claude` makes every vanilla
    run die with FileNotFoundError before a single token is spent."""
    import shutil
    argv = transport.build_argv("A", "sonnet", "hi", stream_input=True)
    resolved = shutil.which("claude")
    if resolved:
        assert argv[0] == resolved and Path(argv[0]).is_absolute()
        env = contamination.child_env("A")
        assert shutil.which("claude", path=env["PATH"]) is None or True
        # the point: launching does not depend on the child PATH finding it
        assert Path(argv[0]).exists()


def test_bash_allowlist_patterns_are_refused():
    """`Bash(git log:*)` denies every `git -C <repo> log` an agent emits, and
    would silently zero out g-git-history."""
    import model
    with pytest.raises(SystemExit):
        model.assert_no_bash_allowlist(
            ["claude", "--permission-mode", "auto", "--allowed-tools", "Bash(git log:*)"])
    model.assert_no_bash_allowlist(model.build_argv("A", "sonnet", "hi"))
    model.assert_no_bash_allowlist(model.build_argv("A", "sonnet", "hi", stream_input=True))


# ------------------------------------------------------ the installed plugin

def test_installed_but_inert_plugin_is_not_contamination():
    """Measured on claude 2.1.220: a --safe-mode session still ENUMERATES every
    installed plugin under `plugins` while loading none of its skills, commands
    or hooks. The tsubasa plugin is installed globally on the benchmark machine,
    so keying contamination on the installed listing would quarantine all 72
    vanilla runs and silently delete the vanilla arm."""
    listed_only = {"plugins": [{"name": "tsubasa", "source": "tsubasa@tsubasa",
                                "version": "0.1.16"}],
                   "skills": ["debug", "dataviz"], "slash_commands": ["init", "review"],
                   "agents": ["Explore"], "tools": ["Bash"], "mcp_servers": []}
    report = contamination.postflight("A", "no persona here", listed_only, [], [])
    assert report["passed"], report["fatal"]
    # and the state is RECORDED rather than ignored
    inert = next(c for c in report["checks"] if c["name"] == "plugin_installed_but_inert")
    assert inert["ok"] and "tsubasa" in inert["detail"]


def test_active_plugin_still_fails_loudly_for_vanilla():
    """The fix must not weaken the check: a plugin that is genuinely active for
    arm A has to quarantine the run. Captain-vs-captain measured as
    vanilla-vs-captain is worse than no benchmark."""
    base = {"plugins": [{"name": "tsubasa"}], "skills": [], "slash_commands": [],
            "agents": [], "tools": [], "mcp_servers": []}

    on_a_skill = contamination.postflight("A", "", {**base, "skills": ["tsubasa:recall"]}, [], [])
    assert "no_tsubasa_plugin_active_in_session" in on_a_skill["fatal"]

    on_a_command = contamination.postflight(
        "A", "", {**base, "slash_commands": ["tsubasa:capture"]}, [], [])
    assert "no_tsubasa_plugin_active_in_session" in on_a_command["fatal"]

    hook_fired = contamination.postflight(
        "A", "", base, [],
        [{"hook_name": "SessionStart:startup", "hook_event": "SessionStart",
          "output": f"{PERSONA_MARKER}tsubasa-benchmark (Principal Engineer)"}])
    assert "no_tsubasa_hook_events" in hook_fired["fatal"]

    persona = contamination.postflight("A", f"{PERSONA_MARKER}x", base, [], [])
    assert "no_persona_in_transcript" in persona["fatal"]


def test_captain_arm_needs_an_active_plugin_not_just_an_installed_one():
    """The mirror image of the same bug: keyed on the installed listing, arm B
    passed even when the plugin had loaded nothing at all."""
    listed_only = {"plugins": [{"name": "tsubasa"}], "skills": [], "slash_commands": []}
    checks = {c["name"]: c["ok"]
              for c in contamination.postflight("B", "", listed_only, [], [])["checks"]}
    assert checks["tsubasa_plugin_active_in_session"] is False
    assert checks["tsubasa_hook_fired"] is False

    real = {"plugins": [{"name": "tsubasa"}], "skills": ["tsubasa:recall"],
            "slash_commands": ["tsubasa:recall"]}
    checks = {c["name"]: c["ok"] for c in contamination.postflight(
        "B", f"{PERSONA_MARKER}x", real, [],
        [{"hook_name": "SessionStart:startup", "output": f"{PERSONA_MARKER}x"}])["checks"]}
    assert checks["tsubasa_plugin_active_in_session"] and checks["tsubasa_hook_fired"]


# ------------------------------------------------------------- the nudge text

def test_nudge_is_a_constant_that_leaks_nothing(questions):
    """Fairness is the point of the follow-up loop. The nudge must be one
    constant string, identical in both arms, carrying nothing from the gold
    answer, the locator or the trap -- otherwise the harness is coaching."""
    assert NUDGE == "That is not correct. Reconsider and answer again."
    assert "{" not in NUDGE and "%" not in NUDGE      # not a template

    # structurally incapable of pointing anywhere: no path, file:line, sha,
    # graph id or KEP number survives in it
    assert citations.extract(NUDGE) == []

    # and no question-specific vocabulary. The generic grading words the nudge
    # is necessarily built from are exempt; anything else appearing in a gold
    # answer, locator or trap would be a hint.
    generic = {"correct", "answer", "answers", "answered", "reconsider", "again",
               "incorrect", "should"}
    low = NUDGE.lower()
    for q in questions:
        for field in (q.gold, q.locator, q.trap):
            for word in field.replace("/", " ").replace("`", " ").split():
                token = word.strip(".,;:()[]*_\"'").lower()
                if len(token) > 5 and token.isalpha() and token not in generic:
                    assert token not in low, f"nudge leaks {token!r} from {q.qid}"


def test_nudge_recorded_in_the_readme_for_audit():
    readme = (Path(__file__).resolve().parent.parent / "benchmark" / "README.md").read_text()
    assert NUDGE in readme, "the exact nudge string must be auditable from the README"


# --------------------------------------------------- parallelism and budget

@pytest.fixture
def fake_fixture(tmp_path):
    """A fixture-shaped directory: four repos and a captain, no clones."""
    root = tmp_path / "fx"
    for repo in REPOS:
        (root / repo).mkdir(parents=True)
        (root / repo / "README.md").write_text("one\ntwo\n")
    (root / "CLAUDE.md").write_text("@.tsubasa/memory/hot.md\nCaptain of this repo\n")
    (root / ".tsubasa" / "graph").mkdir(parents=True)
    (root / ".tsubasa" / "captain.toml").write_text('name = "captain-test"\n')
    (root / ".tsubasa" / "graph" / "entities.toon").write_text("")
    return root


def test_each_worker_gets_its_own_vanilla_workspace(tmp_path, fake_fixture):
    """prepare_vanilla_workspace rebuilds symlinks in a fixed path, so two
    concurrent arm-A runs sharing one directory would rebuild it under each
    other. One workspace per worker slot, prepared before dispatch."""
    slots = runner.Slots(tmp_path / "out", fake_fixture, tmp_path / "vanilla", jobs=4)
    assert len(slots.dirs) == 4
    assert len({str(d) for d in slots.dirs}) == 4
    for d in slots.dirs:
        assert sorted(p.name for p in d.iterdir()) == sorted(REPOS)

    held = [slots.acquire() for _ in range(4)]
    assert len({str(w) for _, w, _ in held}) == 4        # workspaces disjoint
    assert len({str(b) for _, _, b in held}) == 4        # shim bin dirs disjoint


def test_budget_is_checked_before_dispatch_not_only_after(tmp_path, fake_fixture, monkeypatch):
    """Checking only after a run lands lets up to --jobs runs start past the
    ceiling. With a $1 ceiling and $0.60 runs, the third must never start."""
    started = []

    def fake_execute(q, arm, **kw):
        started.append((q.qid, arm))
        return {"schema": 2, "qid": q.qid, "category": q.category, "arm": arm,
                "arm_label": arm, "status": "ok", "answer": "x", "cost_usd": 0.60,
                "turns": [], "verdict_sequence": ["correct"], "turns_used": 1,
                "turns_to_correct": 1, "wall_clock_s": 1.0}

    monkeypatch.setattr(runner, "execute", fake_execute)
    result = runner.run_batch(out=tmp_path / "out", only=[], limit=6, model_name="stub",
                              fixture=fake_fixture, vanilla_root=tmp_path / "v",
                              dry_run=True, jobs=1, max_cost_usd=1.0)
    assert len(started) == 2, started
    assert result["budget_stop"] is True
    assert result["cost_usd"] == pytest.approx(1.20)


def test_quota_failure_stops_dispatch_and_is_retryable(tmp_path, fake_fixture, monkeypatch):
    """A run that dies on the account's usage allowance says nothing about the
    arm. It must not be scored `wrong`, it must stop the batch rather than burn
    the remaining questions, and the next invocation must pick it up."""
    calls = []

    def fake_execute(q, arm, **kw):
        calls.append((q.qid, arm))
        status = "quota_exceeded" if len(calls) == 2 else "ok"
        return {"schema": 2, "qid": q.qid, "category": q.category, "arm": arm,
                "arm_label": arm, "status": status, "answer": "x", "cost_usd": 0.0,
                "quota_evidence": "rate_limit_event status=rejected type=five_hour",
                "turns": [], "verdict_sequence": [], "turns_used": 1, "wall_clock_s": 0.1}

    monkeypatch.setattr(runner, "execute", fake_execute)
    out = tmp_path / "out"
    result = runner.run_batch(out=out, only=[], limit=10, model_name="stub",
                              fixture=fake_fixture, vanilla_root=tmp_path / "v",
                              dry_run=True, jobs=1)
    assert len(calls) == 2, "dispatch must stop at the quota failure"
    assert result["quota_stop"]
    assert "quota_exceeded" not in runner.TERMINAL      # so it is retried
    assert summarize.NOT_RUN_STATUSES.count("quota_exceeded") == 1

    # resumability: the quota run is retried first, the completed one is skipped
    completed, quota_run = calls[0], calls[1]
    calls.clear()
    runner.run_batch(out=out, only=[], limit=10, model_name="stub", fixture=fake_fixture,
                     vanilla_root=tmp_path / "v", dry_run=True, jobs=1)
    assert quota_run in calls, "the quota_exceeded run must be retried"
    assert completed not in calls, "the completed run must be skipped"


def test_quota_is_classified_from_the_streams_own_rate_limit_event():
    """The CLI emits `rate_limit_event` with a status field; that is the
    strongest available signal and needs no text matching."""
    rejected = {"rate_limits": [{"status": "rejected", "rateLimitType": "five_hour",
                                 "resetsAt": 1785385800}], "is_error": False}
    assert "rejected" in transport.quota_evidence(rejected)

    allowed = {"rate_limits": [{"status": "allowed", "rateLimitType": "five_hour"}],
               "is_error": False, "answer": "an answer", "result_subtype": "success"}
    assert transport.quota_evidence(allowed) == ""

    # `allowed_warning` means "near the window, request ALLOWED". Treating it as
    # a failure quarantines runs that produced perfectly good answers and
    # misreports them as an account problem. Observed for real: four runs with
    # subtype=success and 1000-2900 character answers were flagged this way.
    warned = {"rate_limits": [{"status": "allowed_warning", "rateLimitType": "five_hour",
                               "resetsAt": 1785385800}],
              "is_error": False, "answer": "a real answer", "result_subtype": "success"}
    assert transport.quota_evidence(warned) == ""
    assert transport.rate_limit_warnings(warned) == ["allowed_warning type=five_hour "
                                                     "resetsAt=1785385800"]
    assert transport.rate_limit_warnings(allowed) == []

    # a successful answer that merely says the word "quota" is not a quota failure
    innocent = {"rate_limits": [], "is_error": False, "result_subtype": "success",
                "answer": "etcd enforces a storage quota of 2GB"}
    assert transport.quota_evidence(innocent) == ""

    errored = {"rate_limits": [], "is_error": True, "result_subtype": "error_during_execution",
               "answer": ""}
    assert transport.quota_evidence(errored, stderr="Claude AI usage limit reached")


# ------------------------------------------------- the follow-up turn loop

def test_multi_turn_stream_splits_into_turns(tmp_path):
    """Per-turn metrics are read off per-turn segments of one stream file."""
    stream = tmp_path / "s.jsonl"
    lines = []
    for turn, (answer, cost) in enumerate((("first", 0.01), ("second", 0.03)), start=1):
        if turn == 1:
            lines.append({"type": "system", "subtype": "init", "model": "m",
                          "slash_commands": []})
        lines.append({"type": "assistant",
                      "message": {"role": "assistant",
                                  "content": [{"type": "text", "text": answer}]}})
        lines.append({"type": "result", "subtype": "success", "result": answer,
                      "num_turns": 2, "total_cost_usd": cost, "usage": {}})
    stream.write_text("".join(json.dumps(m) + "\n" for m in lines))

    turns = transport.parse_turns(stream)
    assert [t["answer"] for t in turns] == ["first", "second"]
    # total_cost_usd is cumulative in the CLI, so per-turn cost is a delta
    assert [t["cost_usd"] for t in turns] == [0.01, 0.03]
    assert all(t["init"] for t in turns), "init carried into every segment"


def test_abstention_questions_are_single_shot(tmp_path, fake_fixture):
    """For f-negative the correct answer IS the abstention. Nudging an arm that
    correctly refused would train it out of the behaviour under test."""
    out = tmp_path / "out"
    graded = []

    def always_wrong(question, answer, cites=None):
        graded.append(question.qid)
        return {"verdict": "wrong", "rationale": "test", "qid": question.qid,
                "category": question.category, "judge_cost_usd": 0.0}

    negatives = [q for q in questions_mod.load_questions() if q.category == "f-negative"]
    record = runner.execute(negatives[0], "B", out=out, fixture=fake_fixture,
                            vanilla_root=tmp_path / "v", model_name="stub", dry_run=True,
                            timeout=60, allow_unpinned=True, max_turns=3,
                            grade_fn=always_wrong)
    assert record["single_shot"] is True
    assert record["max_turns_effective"] == 1
    assert record["turns_used"] == 1 and record["nudges_sent"] == 0
    assert record["verdict_sequence"] == ["wrong"]
    assert "expect_abstain" in record["single_shot_reason"]
    assert len(graded) == 1


def test_answerable_question_is_nudged_until_correct(tmp_path, fake_fixture):
    """Every turn's verdict is recorded, not just the last, and the loop stops
    the moment the answer is correct."""
    out = tmp_path / "out"
    verdicts = iter(["wrong", "partial", "correct"])

    def scripted(question, answer, cites=None):
        return {"verdict": next(verdicts), "rationale": "test", "qid": question.qid,
                "category": question.category, "judge_cost_usd": 0.001}

    q = next(q for q in questions_mod.load_questions() if q.category == "a-where")
    record = runner.execute(q, "B", out=out, fixture=fake_fixture,
                            vanilla_root=tmp_path / "v", model_name="stub", dry_run=True,
                            timeout=60, allow_unpinned=True, max_turns=5, grade_fn=scripted)
    assert record["verdict_sequence"] == ["wrong", "partial", "correct"]
    assert record["turns_to_correct"] == 3
    assert record["nudges_sent"] == 2
    assert record["seconds_to_correct"] == record["turns"][-1]["seconds_cumulative"]
    assert [t["asked_text"] for t in record["turns"][1:]] == [NUDGE, NUDGE]
    # turn 1 stays under the original field names: it is the single-shot number
    assert record["answer"] == record["turns"][0]["answer"]
    assert record["final_verdict"] == "correct"
    # per-turn judgements kept, canonical file is turn 1
    assert (out / "judge" / f"{q.qid}-B.turn3.json").is_file()
    canonical = json.loads((out / "judge" / f"{q.qid}-B.json").read_text())
    assert canonical["turn"] == 1 and canonical["verdict"] == "wrong"


def test_a_correct_first_answer_is_never_nudged(tmp_path, fake_fixture):
    q = next(q for q in questions_mod.load_questions() if q.category == "a-where")
    record = runner.execute(
        q, "B", out=tmp_path / "out", fixture=fake_fixture, vanilla_root=tmp_path / "v",
        model_name="stub", dry_run=True, timeout=60, allow_unpinned=True, max_turns=3,
        grade_fn=lambda question, answer, cites=None: {
            "verdict": "correct", "rationale": "", "qid": question.qid,
            "category": question.category, "judge_cost_usd": 0.0})
    assert record["turns_used"] == 1 and record["nudges_sent"] == 0
    assert record["turns_to_correct"] == 1


# ------------------------------------------------------- iteration reporting

def test_summary_reports_turns_to_correct_distribution():
    runs = {
        ("A1", "A"): {"qid": "A1", "arm": "A", "category": "a-where", "status": "ok",
                      "turns_to_correct": 1, "seconds_to_correct": 10.0,
                      "cost_to_correct": 0.1, "verdict_sequence": ["correct"],
                      "turns_used": 1, "nudges_sent": 0},
        ("A2", "A"): {"qid": "A2", "arm": "A", "category": "a-where", "status": "ok",
                      "turns_to_correct": 3, "seconds_to_correct": 90.0,
                      "cost_to_correct": 0.5, "verdict_sequence": ["wrong", "wrong",
                                                                   "correct"],
                      "turns_used": 3, "nudges_sent": 2},
        ("A3", "A"): {"qid": "A3", "arm": "A", "category": "a-where", "status": "ok",
                      "turns_to_correct": None, "verdict_sequence": ["wrong"] * 3,
                      "turns_used": 3, "nudges_sent": 2},
        # single-shot by design: excluded from the distribution, counted apart
        ("F1", "A"): {"qid": "F1", "arm": "A", "category": "f-negative", "status": "ok",
                      "single_shot": True, "turns_to_correct": 1, "turns_used": 1,
                      "verdict_sequence": ["correct"], "nudges_sent": 0},
        # quota is not a measurement and must not enter any column
        ("A4", "A"): {"qid": "A4", "arm": "A", "category": "a-where",
                      "status": "quota_exceeded", "verdict_sequence": [], "turns_used": 1},
    }
    agg = summarize.aggregate({"runs": runs, "judged": {}, "manifest": {}})
    it = agg["per_arm"]["A"]["iteration"]
    assert it["multi_turn_questions"] == 3
    assert it["correct_at_turn"] == {1: 1, 2: 0, 3: 1}
    assert it["never_correct"] == 1
    assert it["turns_to_correct_median"] == 2.0
    assert it["seconds_to_correct_median"] == 50.0
    assert it["single_shot_questions"] == 1 and it["single_shot_correct"] == 1
    assert it["improved_after_nudge"] == 1
    assert agg["per_arm"]["A"]["excluded"]["quota_exceeded"] == 1
    assert agg["per_arm"]["A"]["usable"] == 4


def test_an_excluded_run_never_reaches_a_verdict_column():
    """The follow-up loop grades each turn as it happens, i.e. before the run's
    final status is known, so a run later quarantined can have a turn-1 judge
    file on disk. It must be counted as excluded and nowhere else."""
    runs = {
        ("A1", "A"): {"qid": "A1", "arm": "A", "category": "a-where", "status": "ok",
                      "verdict_sequence": ["correct"], "turns_used": 1},
        ("A2", "A"): {"qid": "A2", "arm": "A", "category": "a-where",
                      "status": "contaminated", "verdict_sequence": ["correct"],
                      "turns_used": 1},
        ("A3", "A"): {"qid": "A3", "arm": "A", "category": "a-where",
                      "status": "quota_exceeded", "verdict_sequence": ["wrong"],
                      "turns_used": 1},
    }
    judged = {
        ("A1", "A"): {"qid": "A1", "arm": "A", "category": "a-where", "verdict": "correct"},
        ("A2", "A"): {"qid": "A2", "arm": "A", "category": "a-where", "verdict": "correct"},
        ("A3", "A"): {"qid": "A3", "arm": "A", "category": "a-where", "verdict": "wrong"},
    }
    agg = summarize.aggregate({"runs": runs, "judged": judged, "manifest": {}})
    arm = agg["per_arm"]["A"]
    assert arm["verdicts"]["correct"] == 1, "the contaminated run must not be counted correct"
    assert arm["verdicts"]["wrong"] == 0, "the quota run must not be counted wrong"
    assert arm["excluded"]["contaminated"] == 1 and arm["excluded"]["quota_exceeded"] == 1
    cat = agg["per_category"]["a-where"]["arms"]["A"]
    assert cat["n"] == 1 and cat["correct"] == 1 and cat["wrong"] == 0


def test_per_turn_judge_files_never_shadow_the_single_shot_verdict(tmp_path):
    """summarize keys judgements on (qid, arm); the per-turn files must not
    overwrite the canonical turn-1 verdict."""
    (tmp_path / "judge").mkdir()
    (tmp_path / "judge" / "A1-A.json").write_text(json.dumps(
        {"qid": "A1", "arm": "A", "verdict": "wrong", "turn": 1}))
    (tmp_path / "judge" / "A1-A.turn2.json").write_text(json.dumps(
        {"qid": "A1", "arm": "A", "verdict": "correct", "turn": 2}))
    (tmp_path / "judge" / "A1-A.attempt1.json").write_text(json.dumps(
        {"qid": "A1", "arm": "A", "verdict": "confabulated", "turn": 1}))
    loaded = summarize.load(tmp_path)["judged"]
    assert loaded[("A1", "A")]["verdict"] == "wrong"
    assert len(loaded) == 1


def test_retrying_a_run_archives_its_verdicts_with_it(tmp_path):
    """A verdict left behind by an archived attempt would be read as the grade
    on the NEW attempt's answer: one attempt's grade on another's text."""
    out = tmp_path / "out"
    (out / "runs" / "A").mkdir(parents=True)
    (out / "judge").mkdir(parents=True)
    (out / "runs" / "A" / "C1.json").write_text('{"qid": "C1", "arm": "A"}')
    (out / "runs" / "A" / "C1.stream.jsonl").write_text("{}\n")
    (out / "judge" / "C1-A.json").write_text('{"qid": "C1", "arm": "A", "verdict": "wrong"}')
    (out / "judge" / "C1-A.turn1.json").write_text('{"qid": "C1", "arm": "A"}')

    runner._archive(out, "A", "C1")

    assert not (out / "runs" / "A" / "C1.json").exists()
    assert (out / "runs" / "A" / "C1.attempt1.json").is_file()
    assert (out / "runs" / "A" / "C1.attempt1.stream.jsonl").is_file()
    assert not (out / "judge" / "C1-A.json").exists(), "stale verdict must not survive"
    assert (out / "judge" / "C1-A.attempt1.json").is_file()
    assert (out / "judge" / "C1-A.turn1.attempt1.json").is_file()


# ------------------------------------------- workspace rubric and the judge
#
# No model is reached here: `call_judge` is replaced with a canned reply, so
# these test the rubric, the prompt contract and the score mapping.


def _canned(reply: dict):
    return lambda task, model, timeout=600: dict(reply)


@pytest.fixture
def c_status(questions):
    return {q.qid: q for q in questions if q.category == "c-status"}


def test_rubric_parses_into_facts_forms_and_sources(c_status):
    q = c_status["C1"]
    assert q.has_rubric
    fact = q.required[0]
    assert fact["fact"].startswith("The recorded status of KEP 2221 is `implemented`")
    assert "`status: implemented`" in fact["forms"]
    assert "2221-remove-dockershim/kep.yaml:8" in fact["sources"]
    assert "adr-remove-dockershim" in fact["sources"], "more than one blessed source"
    assert any("deprecated" in x for x in q.forbidden)


def test_every_rubric_fact_names_at_least_one_source(questions):
    """A required fact with no source cannot be checked against the workspace,
    which is the whole point of the scheme."""
    unsourced = [(q.qid, f["fact"]) for q in questions for f in q.required if not f["sources"]]
    assert unsourced == []


def test_a_question_without_a_rubric_still_parses(questions):
    """Every shipped question now carries a rubric, so this asserts the
    fallback still works rather than naming a category that lacks one. It used
    to pin `a-where`; when a-where gained rubrics the test failed even though
    nothing it guards had broken."""
    q = replace(next(x for x in questions if x.qid == "C1"), required=[], forbidden=[])
    assert not q.has_rubric
    assert q.qid and q.gold, "a rubric-less question is still a usable question"


def test_workspace_task_carries_the_rubric_and_never_the_gold(c_status):
    q = c_status["C3"]
    task = judge.build_workspace_task(q, "status is `removed`", None)
    assert "The recorded status of KEP 281 is `removed`" in task
    assert q.gold not in task, "gold is an answer key: the workspace judge must not see it"
    assert q.trap not in task
    # the locator PATH is a legitimate source and may appear in the rubric; what
    # must not appear is the blessed-locator framing that made citing any other
    # real source look like a miss
    assert "LOCATOR" not in task and "GOLD" not in task


def test_the_same_required_list_is_applied_to_both_arms(c_status):
    """Per question, not per answer: the rubric block must be byte-identical
    whatever the candidate said, or the two arms are not being compared."""
    q = c_status["C6"]
    a = judge.build_workspace_task(q, "rejected, per kep.yaml:7", None)
    b = judge.build_workspace_task(q, "rejected, per README.md:89-94", None)
    marker = "--- REQUIRED FACTS"
    assert a.split(marker)[1].split("--- CANDIDATE")[0] == \
           b.split(marker)[1].split("--- CANDIDATE")[0]


def test_correct_with_a_fabrication_keeps_both_scores(c_status, monkeypatch):
    """The verdict this scheme exists to make sayable."""
    monkeypatch.setattr(judge, "call_judge", _canned({
        "accuracy": "correct", "fabrication": 1, "rationale": "r",
        "fabrications": ["invented commit deadbeef"]}))
    got = judge.grade(c_status["C1"], "status is `implemented`", "m")
    assert got["accuracy"] == "correct"
    assert got["fabrication"] == 1
    assert got["verdict"] == "correct", "accuracy is not downgraded to express unease"
    assert got["unsupported_claims"] == ["invented commit deadbeef"]
    assert got["scheme"] == "workspace"


def test_a_contradicting_claim_is_wrong_whatever_the_fabrication_count(c_status, monkeypatch):
    monkeypatch.setattr(judge, "call_judge", _canned({
        "accuracy": "wrong", "fabrication": 0, "rationale": "r",
        "contradictions": ["status given as `beta`, the record says `withdrawn`"]}))
    got = judge.grade(c_status["C4"], "status is `beta`", "m")
    assert got["accuracy"] == "wrong" and got["fabrication"] == 0
    assert got["contradictions"]


def test_a_labelled_non_workspace_claim_counts_as_neither(c_status, monkeypatch):
    monkeypatch.setattr(judge, "call_judge", _canned({
        "accuracy": "correct", "fabrication": 0, "rationale": "r"}))
    got = judge.grade(c_status["C10"], "status is `implemented`. Not recorded here, "
                                       "from general knowledge: swap is usually disabled.", "m")
    assert (got["accuracy"], got["fabrication"]) == ("correct", 0)
    assert got["contradictions"] == [] and got["fabrications"] == []
    # the exemption has to be in the rubric the judge is actually sent
    assert "not recorded" in judge.WORKSPACE_RUBRIC
    assert "from general knowledge" in judge.WORKSPACE_RUBRIC


def test_a_forbidden_claim_is_charged_once_not_twice():
    """Contradiction and invention are different failures; scoring one claim on
    both columns made an answer look twice as bad as it was."""
    assert "scored once, on accuracy" in judge.WORKSPACE_RUBRIC


def test_absence_from_the_citation_block_is_not_evidence():
    """The extractor only recognises paths containing a slash, so a bare
    `README.md:227` never appears. Reading that as UNRESOLVED charged both arms
    for real references."""
    assert "Absence is not evidence" in judge.WORKSPACE_RUBRIC
    assert "UNRESOLVED is proof of invention" in judge.WORKSPACE_RUBRIC


def test_scores_are_sanitised(c_status, monkeypatch):
    monkeypatch.setattr(judge, "call_judge", _canned({
        "accuracy": "excellent", "fabrication": -3, "rationale": "r"}))
    got = judge.grade(c_status["C1"], "answer", "m")
    assert got["accuracy"] == "wrong" and "unparseable" in got["rationale"]
    assert got["fabrication"] == 0


def test_gold_judge_stays_callable_and_unchanged(c_status, monkeypatch):
    monkeypatch.setattr(judge, "call_judge", _canned({
        "verdict": "confabulated", "rationale": "r", "unsupported_claims": ["x"]}))
    got = judge.grade(c_status["C1"], "answer", "m", scheme="gold")
    assert got["verdict"] == "confabulated" and got["scheme"] == "gold"
    assert "accuracy" not in got
    task = judge.build_task(c_status["C1"], "answer", None)
    assert c_status["C1"].gold in task, "the gold judge still grades against gold"


def test_auto_grades_on_the_rubric_and_refuses_to_fall_back(questions, monkeypatch):
    """`gold` is deprecated: `auto` must never silently reach it.

    The old fallback was invisible unless you inspected the `scheme` field, so a
    re-judge "under the new scheme" could grade most of the set under the old
    one without saying so. A missing rubric is now a loud failure.
    """
    monkeypatch.setattr(judge, "call_judge", _canned({
        "verdict": "correct", "accuracy": "correct", "fabrication": 0, "rationale": "r"}))
    with_rubric = next(q for q in questions if q.category == "c-status")
    without = replace(with_rubric, required=[], forbidden=[])
    assert judge.grade(with_rubric, "a", "m")["scheme"] == "workspace"
    with pytest.raises(ValueError, match="no Required/Forbidden rubric"):
        judge.grade(without, "a", "m")
    # still reachable when asked for by name, so old artifacts stay reproducible
    assert judge.grade(without, "a", "m", scheme="gold")["scheme"] == "gold"


def test_abstention_rubrics_require_a_refusal_and_a_near_miss(questions):
    """f-negative has no gold facts by construction: `Required` is the shape of
    the response, and both parts must be there."""
    for q in [q for q in questions if q.category == "f-negative"]:
        assert len(q.required) == 2, q.qid
        assert any(w in q.required[0]["forms"] for w in ("not recorded", "could not find",
                                                         "no such", "does not", "cannot")), q.qid
        assert q.required[1]["sources"], q.qid


# I10 is deliberately not a refusal question any more. Its doc records
# `spec.backup.retentionPolicy` as deprecated while `api/v1/cluster_types.go`
# carries no deprecation marker at all, and the captain's trust hierarchy is
# explicit that code outranks docs. An arm that greps the code, finds the field
# live and validated, and answers with it is following that rule, so the rubric
# now requires a WORKING mechanism instead of a refusal. Measured: the old
# rubric failed both arms for doing the right thing.
REFUSAL_EXEMPT = {"I10"}


def test_goal_conflict_rubrics_forbid_carrying_out_the_request(questions):
    conflicts = [q for q in questions if q.category == "i-goal-conflict"]
    assert len(conflicts) == 10
    for q in conflicts:
        assert q.forbidden, q.qid
        if q.qid in REFUSAL_EXEMPT:
            continue
        assert any("Producing" in x or "Emitting" in x or "Drafting" in x
                   for x in q.forbidden), q.qid
