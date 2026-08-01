"""Opt-in semantic expansion in `tsubasa query` (TSUBASA_SEMANTIC=1): accepted
vocab tokens widen the lexical match, invented tokens are discarded, any model
failure falls back to the lexical path, and the cost log never lands inside
the benchmark graph fingerprint. No test reaches a model."""

import json
import sys
from pathlib import Path

import pytest

from tsubasa import cli, llm, semantic

HARNESS = Path(__file__).resolve().parent.parent / "benchmark" / "harness"
sys.path.append(str(HARNESS))
guard = pytest.importorskip("guard")


def envelope(tokens, cost=0.0012):
    return {"result": json.dumps(tokens), "total_cost_usd": cost, "duration_ms": 800}


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TSUBASA_SEMANTIC", raising=False)
    monkeypatch.delenv("TSUBASA_SEMANTIC_LOG", raising=False)
    monkeypatch.delenv("TSUBASA_SEMANTIC_MODEL", raising=False)
    assert cli.main(["init", "testcap"]) == 0
    assert cli.main([
        "event", "add", "--type", "note", "--title", "Get rid of WALBufMappingLock",
        "--ts", "2026-07-01",
    ]) == 0
    return tmp_path


def expansion(monkeypatch, tokens, cost=0.0012):
    """Stub the headless call; returns the list of (prompt, model) it saw."""
    calls = []

    def fake(prompt, model="", claude_cmd="claude", timeout=600, cwd=None):
        calls.append((prompt, model))
        return envelope(tokens, cost)

    monkeypatch.setattr(llm, "run_claude_json", fake)
    return calls


def never_called(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("run_claude_json must not be called")
    monkeypatch.setattr(llm, "run_claude_json", boom)


# ------------------------------------------------------------------ off = inert

def test_env_off_means_zero_behavior_change(repo, capsys, monkeypatch):
    never_called(monkeypatch)
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr()
    assert "semantic expansion" not in out.out + out.err
    assert not (repo / ".tsubasa" / "semantic-cost.jsonl").exists()


def test_timeline_path_never_expands(repo, capsys, monkeypatch):
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")
    never_called(monkeypatch)
    assert cli.main(["query", "--timeline", "cluster wide contention stall"]) == 0
    assert "semantic expansion" not in capsys.readouterr().out


# ------------------------------------------------------------------ merging

def test_accepted_tokens_reach_title_matching(repo, capsys, monkeypatch):
    # no query token is a stem of any title token, so lexical alone finds
    # nothing; the expansion must carry the titled event into the result
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")
    calls = expansion(monkeypatch, ["walbufmappinglock", "rid"])
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr().out
    assert "semantic expansion: [walbufmappinglock rid]" in out
    assert "Get rid of WALBufMappingLock" in out
    assert "(no knowledge found)" not in out
    # the model saw the question and the full vocabulary, on the default model
    prompt, model = calls[0]
    assert "cluster wide contention stall" in prompt
    assert "walbufmappinglock" in prompt
    assert model == semantic.DEFAULT_MODEL


def test_accepted_tokens_reach_entity_matching(repo, capsys, monkeypatch):
    assert cli.main([
        "event", "add", "--type", "incident", "--title", "Session store replication lag",
        "--ts", "2026-07-02",
        "--entity", "svc-auth-gateway:service:auth-gateway:Authentication gateway service",
    ]) == 0
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")
    expansion(monkeypatch, ["auth", "gateway"])
    capsys.readouterr()
    assert cli.main(["query", "why is the login portal slow"]) == 0
    out = capsys.readouterr().out
    assert "semantic expansion: [auth gateway]" in out
    assert "svc-auth-gateway" in out


def test_selection_capped_at_six(repo, capsys, monkeypatch):
    # cap is 6, not 12: a wide selection full of common stems floods the
    # rarity-ranked title cap with multi-hit noise titles (benchmark G9)
    assert cli.main([
        "event", "add", "--type", "note",
        "--title", "alpha bravo charlie delta echo foxtrot golf",
        "--ts", "2026-07-03",
    ]) == 0
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")
    calls = expansion(monkeypatch, ["alpha", "bravo", "charlie", "delta",
                                    "echo", "foxtrot", "golf"])
    capsys.readouterr()
    assert cli.main(["query", "phonetic alphabet drill"]) == 0
    out = capsys.readouterr().out
    assert "semantic expansion: [alpha bravo charlie delta echo foxtrot]" in out
    assert "up to 6 tokens" in calls[0][0]  # the prompt asks for the new cap


def test_invented_tokens_discarded(repo, capsys, monkeypatch):
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")
    expansion(monkeypatch, ["walbufmappinglock", "hallucinated", "rid"])
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr().out
    assert "hallucinated" not in out
    assert "semantic expansion: [walbufmappinglock rid]" in out


def test_all_invented_reads_as_none_matched(repo, capsys, monkeypatch):
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")
    expansion(monkeypatch, ["totallyfake"])
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr().out
    assert "semantic expansion: none matched" in out
    assert "(no knowledge found)" in out  # lexical result, unchanged


# ------------------------------------------------------------------ fallback

def test_empty_selection_falls_back_cleanly(repo, capsys, monkeypatch):
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")
    expansion(monkeypatch, [])
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr().out
    assert "semantic expansion: none matched" in out
    assert "(no knowledge found)" in out


def test_claude_failure_never_fails_the_query(repo, capsys, monkeypatch):
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")

    def boom(*a, **k):
        raise llm.LLMError("claude exited 1: no credits")
    monkeypatch.setattr(llm, "run_claude_json", boom)
    capsys.readouterr()
    assert cli.main(["query", "walbufmappinglock"]) == 0
    out = capsys.readouterr()
    assert "semantic expansion: unavailable" in out.err
    assert "Get rid of WALBufMappingLock" in out.out  # lexical path intact


# ------------------------------------------------------------------ cost log

def test_cost_log_written_to_env_path(repo, capsys, monkeypatch, tmp_path):
    log = tmp_path / "logs" / "cost.jsonl"
    log.parent.mkdir()
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")
    monkeypatch.setenv("TSUBASA_SEMANTIC_LOG", str(log))
    expansion(monkeypatch, ["walbufmappinglock", "rid"], cost=0.0034)
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["question"] == "cluster wide contention stall"
    assert rec["tokens"] == ["walbufmappinglock", "rid"]
    assert rec["cost_usd"] == 0.0034
    assert rec["model"] == semantic.DEFAULT_MODEL
    assert "seconds" in rec and "ts" in rec
    assert not (repo / ".tsubasa" / "semantic-cost.jsonl").exists()


def test_default_log_is_outside_the_graph_fingerprint(repo, capsys, monkeypatch):
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")
    expansion(monkeypatch, ["walbufmappinglock", "rid"])
    before = guard.graph_fingerprint(repo)
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    assert (repo / ".tsubasa" / "semantic-cost.jsonl").exists()
    assert guard.graph_fingerprint(repo) == before


def test_unwritable_log_falls_back_to_stderr(repo, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("TSUBASA_SEMANTIC", "1")
    monkeypatch.setenv("TSUBASA_SEMANTIC_LOG", str(tmp_path / "missing-dir" / "cost.jsonl"))
    expansion(monkeypatch, ["walbufmappinglock", "rid"])
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr()
    assert "semantic-cost: " in out.err
    assert "Get rid of WALBufMappingLock" in out.out  # the query still answered
