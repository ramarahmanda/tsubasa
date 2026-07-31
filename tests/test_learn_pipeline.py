"""`tsubasa study` runs the whole learning pipeline, in a load-bearing order,
with each stage degrading on its own."""

import pytest

from tsubasa import cli, distill
from tsubasa.graph import anchors as anchors_mod
from tsubasa.storage import Store
from test_study_fast import commit, git, make_captain, stub_cmd

STAGES = ("study", "resolve", "index", "link", "profile")


@pytest.fixture()
def traced(monkeypatch):
    """Replace every stage with a recorder, so order and skipping are visible
    without paying for (or stubbing) five different LLM shapes."""
    order: list[str] = []

    def fake_study(store, cfg, root, **kw):
        order.append("study")
        kw.get("stats", {})["chunks"] = 3
        return []

    def fake_resolve(store, **kw):
        order.append("resolve")
        return 0

    def fake_index(cfg, root, **kw):
        order.append("index")
        return 4

    def fake_seed(store, root, cfg, **kw):
        order.append("link")
        return 0

    def fake_link_llm(store, cfg, root, **kw):
        order.append("link-llm")
        return 38

    def fake_profile(store, **kw):
        order.append("profile")
        return 0

    monkeypatch.setattr(distill, "study", fake_study)
    monkeypatch.setattr(distill, "resolve", fake_resolve)
    monkeypatch.setattr(distill, "index_code", fake_index)
    monkeypatch.setattr(distill, "link_llm", fake_link_llm)
    monkeypatch.setattr(distill, "profile", fake_profile)
    monkeypatch.setattr(anchors_mod, "seed", fake_seed)
    return order


@pytest.fixture()
def cap(tmp_path, monkeypatch):
    return make_captain(tmp_path / "a", monkeypatch, ["feat: one", "feat: two"])


def test_study_runs_every_stage_in_order(cap, traced, capsys):
    cmd = stub_cmd(cap)
    assert cli.main(["study", "--claude-cmd", cmd]) == 0
    # resolve before link and profile; index before link; profile last
    assert traced == ["study", "resolve", "index", "link", "link-llm", "profile"]
    out = capsys.readouterr().out
    assert "learning pipeline:" in out
    for stage in STAGES:
        assert f"[{stage}]" in out


def test_summary_reports_each_stage(cap, traced, capsys):
    cmd = stub_cmd(cap)
    cli.main(["study", "--claude-cmd", cmd])
    out = capsys.readouterr().out
    assert "[study]   3 chunks, 0 event(s)" in out
    assert "[resolve] 0 alias mapping(s)" in out
    assert "[index]   4 repo(s) indexed" in out
    assert "[link]    0 anchor(s) (38 semantic)" in out
    assert "[profile] 0 hub profile(s)" in out


@pytest.mark.parametrize("flag, gone", [
    ("--no-resolve", "resolve"),
    ("--no-index", "index"),
    ("--no-link", "link"),
    ("--no-profile", "profile"),
])
def test_each_opt_out_skips_exactly_its_stage(cap, traced, capsys, flag, gone):
    cmd = stub_cmd(cap)
    assert cli.main(["study", "--claude-cmd", cmd, flag]) == 0
    ran = [s for s in traced if s != "link-llm"]
    assert gone not in ran
    assert ran == [s for s in STAGES if s != gone]
    assert f"skipped ({flag})" in capsys.readouterr().out


def test_no_link_skips_the_semantic_pass_too(cap, traced):
    cmd = stub_cmd(cap)
    cli.main(["study", "--claude-cmd", cmd, "--no-link"])
    assert "link-llm" not in traced


def test_all_opt_outs_leave_only_distillation(cap, traced):
    cmd = stub_cmd(cap)
    assert cli.main(["study", "--claude-cmd", cmd, "--no-resolve", "--no-index",
                     "--no-link", "--no-profile"]) == 0
    assert traced == ["study"]


# ------------------------------------------------------------------ degradation

def test_a_failing_middle_stage_keeps_the_earlier_results(cap, monkeypatch, capsys):
    """The expensive distillation was already paid for; resolve blowing up must
    not discard it, and must not stop the stages after it."""
    cmd = stub_cmd(cap)
    assert cli.main(["study", "--claude-cmd", cmd, "--no-resolve", "--no-index",
                     "--no-link", "--no-profile"]) == 0
    distilled = [e.id for e in Store(cap).load_events() if e.source == "study"]
    assert distilled

    def boom(*a, **k):
        raise RuntimeError("claude exited 1: overloaded")

    monkeypatch.setattr(distill, "resolve", boom)
    capsys.readouterr()
    # a partial failure is not a failed run
    assert cli.main(["study", "--claude-cmd", cmd, "--since", "3y", "--no-index"]) == 0
    out = capsys.readouterr().out
    assert "[resolve] FAILED (claude exited 1: overloaded)" in out
    assert "[profile] 0 hub profile(s)" in out          # later stages still ran
    after = [e.id for e in Store(cap).load_events() if e.source == "study"]
    assert set(distilled) <= set(after)                 # nothing was thrown away


def test_semantic_link_failure_keeps_the_deterministic_seeding(cap, traced,
                                                               monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("no model")

    monkeypatch.setattr(distill, "link_llm", boom)
    cmd = stub_cmd(cap)
    assert cli.main(["study", "--claude-cmd", cmd]) == 0
    out = capsys.readouterr().out
    assert "link" in traced                       # seeding ran
    assert "[link]    0 anchor(s) (0 semantic)" in out
    assert "[link]    FAILED" not in out          # the stage did not fail


def test_exit_1_only_when_every_attempted_stage_fails(cap, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("nope")

    for name in ("study", "resolve", "index_code", "profile"):
        monkeypatch.setattr(distill, name, boom)
    monkeypatch.setattr(anchors_mod, "seed", boom)
    cmd = stub_cmd(cap)
    assert cli.main(["study", "--claude-cmd", cmd]) == 1
    assert cli.main(["study", "--claude-cmd", cmd, "--no-resolve", "--no-index",
                     "--no-link", "--no-profile"]) == 1


def test_bad_since_is_a_usage_error_not_a_degraded_stage(cap, capsys):
    """Real distillation here (no `traced`): git resolves `garbage` to "now",
    so the run must fail outright, not shrug because the later stages passed."""
    cmd = stub_cmd(cap)
    assert cli.main(["study", "--claude-cmd", cmd, "--since", "garbage"]) == 1
    assert "learning pipeline:" not in capsys.readouterr().out


def test_missing_claude_fails_before_any_stage(cap, traced):
    assert cli.main(["study", "--claude-cmd", "definitely-not-a-real-binary"]) == 1
    assert traced == []


# ------------------------------------------------------------------ standalone

def test_standalone_stage_commands_are_unchanged(cap, traced, capsys):
    cmd = stub_cmd(cap)
    assert cli.main(["resolve", "--claude-cmd", cmd]) == 0
    assert traced == ["resolve"]
    assert cli.main(["index"]) == 0
    assert traced == ["resolve", "index"]
    assert cli.main(["link"]) == 0                        # no --llm: seed only
    assert traced == ["resolve", "index", "link"]
    assert cli.main(["profile", "--claude-cmd", cmd]) == 0
    assert traced == ["resolve", "index", "link", "profile"]
    assert "learning pipeline:" not in capsys.readouterr().out


def test_pull_study_runs_the_same_pipeline(cap, traced, capsys):
    cmd = stub_cmd(cap)
    assert cli.main(["pull", "--study", "--claude-cmd", cmd]) == 0
    assert traced == ["study", "resolve", "index", "link", "link-llm", "profile"]


def test_pull_without_study_runs_no_pipeline(cap, traced, capsys):
    assert cli.main(["pull"]) == 0
    assert traced == []
    assert "learning pipeline:" not in capsys.readouterr().out
