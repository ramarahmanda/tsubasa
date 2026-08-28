"""`tsubasa query --unanchored`: does the record constrain this claim?

The inverse of the default path. Only strong (discriminating) title hits
print; a weak match says NO RECORDED CONSTRAINT instead of near-misses, a
vocab hint or a code-anatomy block. The semantic ladder never runs here, so
the mode costs nothing. No test reaches a model.
"""

import pytest

from tsubasa import cli, llm


@pytest.fixture()
def repo(tmp_path, monkeypatch, semantic_pass):
    """A graph with one discriminating title and four common-stem near-misses."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TSUBASA_SEMANTIC_LOG", raising=False)
    assert cli.main(["init", "testcap"]) == 0
    assert cli.main([
        "event", "add", "--type", "note", "--title", "Get rid of WALBufMappingLock",
        "--ts", "2026-07-01",
        "--entity", "svc-postgres:service:postgres:Postgres server",
    ]) == 0
    for i in range(4):
        assert cli.main(["event", "add", "--type", "note",
                         "--title", f"Fix buffer error return path {i}",
                         "--ts", f"2026-06-{i + 1:02d}"]) == 0
    return tmp_path


def never_called(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("run_claude_json must not be called")
    monkeypatch.setattr(llm, "run_claude_json", boom)


def test_weak_match_says_no_recorded_constraint(repo, capsys, monkeypatch):
    # near-misses on buffer/error/return are dropped, not printed
    never_called(monkeypatch)
    capsys.readouterr()
    assert cli.main(["query", "--unanchored", "buffer error handling"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "NO RECORDED CONSTRAINT"
    assert "uncited" in out
    assert "Fix buffer error return path" not in out


def test_no_hit_path_suppresses_hint_anchors_and_anatomy(repo, capsys, monkeypatch):
    never_called(monkeypatch)
    capsys.readouterr()
    assert cli.main(["query", "--unanchored", "how should we shard tenants"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "NO RECORDED CONSTRAINT"
    assert "graph tokens near your wording" not in out
    assert "Code anatomy" not in out
    assert "Anchors" not in out
    assert "Relations" not in out


def test_strong_hit_prints_constraints_with_ids(repo, capsys, monkeypatch):
    never_called(monkeypatch)
    capsys.readouterr()
    assert cli.main(["query", "--unanchored", "should we get rid of walbufmappinglock"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("RECORDED CONSTRAINTS (")
    assert "Get rid of WALBufMappingLock" in out
    assert "evt-20260701-" in out
    assert "NO RECORDED CONSTRAINT" not in out
    assert "Relations" not in out  # constraints only: no 2-hop walk
    assert "Code anatomy" not in out


def test_semantic_expansion_never_runs(repo, capsys, monkeypatch):
    # weak wording is exactly where the default path escalates; this mode
    # must stay on rung one and write no cost line
    never_called(monkeypatch)
    capsys.readouterr()
    assert cli.main(["query", "--unanchored", "cluster wide contention stall"]) == 0
    out = capsys.readouterr().out
    assert "semantic expansion" not in out
    assert not (repo / ".tsubasa" / "semantic-cost.jsonl").exists()


def test_composes_with_as_of(repo, capsys, monkeypatch):
    never_called(monkeypatch)
    capsys.readouterr()
    assert cli.main(["query", "--unanchored", "--as-of", "2026-06-15",
                     "should we get rid of walbufmappinglock"]) == 0
    out = capsys.readouterr().out
    # the event postdates the cutoff, so the record is silent as of then
    assert out.splitlines()[0] == "NO RECORDED CONSTRAINT"
    assert "Code anatomy" not in out


def test_unanchored_with_timeline_is_rejected(repo, capsys, monkeypatch):
    never_called(monkeypatch)
    capsys.readouterr()
    assert cli.main(["query", "--unanchored", "--timeline", "walbufmappinglock"]) == 1
    out = capsys.readouterr()
    assert "--unanchored and --timeline" in out.err
    assert out.out == ""
