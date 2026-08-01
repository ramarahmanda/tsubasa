"""The semantic escalation ladder in `tsubasa query`: lexical pass first, one
expansion exactly when it finds no title-matched events, never otherwise.
Accepted vocab tokens widen the lexical match, invented tokens are discarded,
any model failure falls back to the lexical path, and the cost log never lands
inside the benchmark graph fingerprint. No test reaches a model."""

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
def repo(tmp_path, monkeypatch, semantic_pass):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TSUBASA_SEMANTIC_LOG", raising=False)
    assert cli.main(["init", "testcap"]) == 0
    assert cli.main([
        "event", "add", "--type", "note", "--title", "Get rid of WALBufMappingLock",
        "--ts", "2026-07-01",
    ]) == 0
    return tmp_path


def add_entity_event(kind="incident"):
    assert cli.main([
        "event", "add", "--type", kind, "--title", "Session store replication lag",
        "--ts", "2026-07-02",
        "--entity", "svc-auth-gateway:service:auth-gateway:Authentication gateway service",
    ]) == 0


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


# ------------------------------------------------------------------ trigger

def test_ladder_fires_when_entities_match_but_titles_miss(repo, capsys, monkeypatch):
    # the G9 shape: generic entities matched, zero title hits. Entity-only
    # matches are weak; the ladder must escalate and land the title block.
    add_entity_event()
    # two tokens: the title gate needs two stem hits, and haiku picks several
    calls = expansion(monkeypatch, ["walbufmappinglock", "rid"])
    capsys.readouterr()
    assert cli.main(["query", "why is auth-gateway slow"]) == 0
    out = capsys.readouterr().out
    assert "svc-auth-gateway" in out  # the lexical entity match survived
    assert "semantic expansion: [walbufmappinglock rid]" in out
    assert "Get rid of WALBufMappingLock" in out
    assert len(calls) == 1


def add_noise_events(n=4):
    # common-stem titles only: enough of them that buffer/error/return exceed
    # DISCRIMINATING_FREQ in the vocabulary
    for i in range(n):
        assert cli.main(["event", "add", "--type", "note",
                         "--title", f"Fix buffer error return path {i}",
                         "--ts", f"2026-06-{i + 1:02d}"]) == 0


def test_near_miss_only_title_block_escalates(repo, capsys, monkeypatch):
    # G5/G10: the title block fired, but every hit rode common stems and the
    # gold was absent; common-stem-only evidence must escalate like an empty
    # block, or the session concludes "never tried"
    add_noise_events()
    calls = expansion(monkeypatch, ["walbufmappinglock", "rid"])
    capsys.readouterr()
    assert cli.main(["query", "buffer error handling"]) == 0
    out = capsys.readouterr().out
    assert len(calls) == 1
    assert "semantic expansion: [walbufmappinglock rid]" in out
    assert "Get rid of WALBufMappingLock" in out  # the expansion landed the gold


def test_rare_token_title_hit_blocks_escalation(repo, capsys, monkeypatch):
    # one hit on a discriminating token makes the block strong evidence, even
    # with common-stem noise alongside it
    add_noise_events()
    assert cli.main(["event", "add", "--type", "note",
                     "--title", "Fix printf buffer error", "--ts", "2026-06-20"]) == 0
    never_called(monkeypatch)
    capsys.readouterr()
    assert cli.main(["query", "printf buffer error"]) == 0
    out = capsys.readouterr().out
    assert "Fix printf buffer error" in out
    assert "semantic expansion" not in out


def test_no_expansion_when_a_title_already_hits(repo, capsys, monkeypatch):
    never_called(monkeypatch)
    capsys.readouterr()
    assert cli.main(["query", "walbufmappinglock"]) == 0
    out = capsys.readouterr().out
    assert "Get rid of WALBufMappingLock" in out
    assert "semantic expansion" not in out


def test_title_hit_query_makes_zero_llm_calls(repo, capsys, monkeypatch):
    calls = expansion(monkeypatch, ["walbufmappinglock"])
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    assert len(calls) == 1  # weak wording escalated
    assert cli.main(["query", "walbufmappinglock"]) == 0
    assert len(calls) == 1  # title hit: the ladder stayed on rung one


def test_timeline_path_never_expands(repo, capsys, monkeypatch):
    never_called(monkeypatch)
    assert cli.main(["query", "--timeline", "cluster wide contention stall"]) == 0
    assert "semantic expansion" not in capsys.readouterr().out


# ------------------------------------------------------------------ merging

def test_accepted_tokens_reach_title_matching(repo, capsys, monkeypatch):
    # no query token is a stem of any title token, so lexical alone finds
    # nothing; the expansion must carry the titled event into the result
    calls = expansion(monkeypatch, ["walbufmappinglock", "rid"])
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr().out
    assert "semantic expansion: [walbufmappinglock rid]" in out
    assert "Get rid of WALBufMappingLock" in out
    assert "(no knowledge found)" not in out
    # the model saw the question and the full vocabulary, on the fixed model
    prompt, model = calls[0]
    assert "cluster wide contention stall" in prompt
    assert "walbufmappinglock" in prompt
    assert model == semantic.DEFAULT_MODEL


def test_accepted_tokens_reach_entity_matching(repo, capsys, monkeypatch):
    add_entity_event()
    expansion(monkeypatch, ["auth", "gateway"])
    capsys.readouterr()
    assert cli.main(["query", "why is the login portal down"]) == 0
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
    calls = expansion(monkeypatch, ["alpha", "bravo", "charlie", "delta",
                                    "echo", "foxtrot", "golf"])
    capsys.readouterr()
    assert cli.main(["query", "phonetic alphabet drill"]) == 0
    out = capsys.readouterr().out
    assert "semantic expansion: [alpha bravo charlie delta echo foxtrot]" in out
    assert "up to 6 tokens" in calls[0][0]  # the prompt asks for the cap


def test_invented_tokens_discarded(repo, capsys, monkeypatch):
    expansion(monkeypatch, ["walbufmappinglock", "hallucinated", "rid"])
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr().out
    assert "hallucinated" not in out
    assert "semantic expansion: [walbufmappinglock rid]" in out


def test_all_invented_reads_as_none_matched(repo, capsys, monkeypatch):
    expansion(monkeypatch, ["totallyfake"])
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr().out
    assert "semantic expansion: none matched" in out
    assert "(no knowledge found)" in out  # lexical result, unchanged


# ------------------------------------------------------------------ fallback

def test_empty_selection_falls_back_cleanly(repo, capsys, monkeypatch):
    expansion(monkeypatch, [])
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr().out
    assert "semantic expansion: none matched" in out
    assert "(no knowledge found)" in out


def test_claude_failure_never_fails_the_query(repo, capsys, monkeypatch):
    def boom(*a, **k):
        raise llm.LLMError("claude exited 1: no credits")
    monkeypatch.setattr(llm, "run_claude_json", boom)
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr()
    assert "semantic expansion: unavailable" in out.err  # one stderr line
    assert "(no knowledge found)" in out.out  # the lexical result stands


# ------------------------------------------------------------------ cost log

def test_cost_log_written_to_env_path(repo, capsys, monkeypatch, tmp_path):
    log = tmp_path / "logs" / "cost.jsonl"
    log.parent.mkdir()
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
    expansion(monkeypatch, ["walbufmappinglock", "rid"])
    before = guard.graph_fingerprint(repo)
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    assert (repo / ".tsubasa" / "semantic-cost.jsonl").exists()
    assert guard.graph_fingerprint(repo) == before


def test_unwritable_log_falls_back_to_stderr(repo, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("TSUBASA_SEMANTIC_LOG", str(tmp_path / "missing-dir" / "cost.jsonl"))
    expansion(monkeypatch, ["walbufmappinglock", "rid"])
    capsys.readouterr()
    assert cli.main(["query", "cluster wide contention stall"]) == 0
    out = capsys.readouterr()
    assert "semantic-cost: " in out.err
    assert "Get rid of WALBufMappingLock" in out.out  # the query still answered
