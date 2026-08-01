"""Automatic vocab bridge in `tsubasa query`: title-matched events surface on
every plain query (entity walking cannot reach a repo-attached revert), and an
empty entity match prints the graph tokens near the query's wording instead of
silently reading as "unrecorded"."""

import pytest

from tsubasa import cli
from tsubasa.graph.query import serialize, title_events, vocab_hint, vocabulary
from tsubasa.models import Entity, Event


def ev(eid, title, ts="2026-07-01", **kw):
    return Event(id=eid, type="note", ts=ts, title=title, **kw)


def test_hint_bridges_query_stems_onto_graph_tokens():
    ents = {"svc-postgres": Entity(id="svc-postgres", type="service", name="postgres")}
    events = {"evt-1": ev("evt-1", "Get rid of WALBufMappingLock")}
    # no entity matches this wording; "wal" and "lock" are stems of a title token
    out = vocab_hint(vocabulary(ents, events), "cluster-wide lock serialising wal writes")
    assert "no direct entity match" in out
    assert "walbufmappinglock(1)" in out


def test_no_nearby_tokens_means_no_hint():
    events = {"evt-1": ev("evt-1", "Get rid of WALBufMappingLock")}
    assert vocab_hint(vocabulary({}, events), "unrelated question about billing") == ""


def test_title_only_token_surfaces_its_event():
    # the trap: the token exists only in an event title, which entity matching
    # never reaches, so plain query must carry the event itself
    events = {"evt-1": ev("evt-1", "Get rid of WALBufMappingLock")}
    out = serialize({}, [], events, [], text="walbufmappinglock")
    assert "- evt-1 (note, 2026-07-01): Get rid of WALBufMappingLock" in out
    assert "(no knowledge found)" not in out


def test_title_block_surfaces_revert_despite_entity_matches():
    # benchmark G5: entities match, so an empty-match bridge never fires, yet
    # the load-bearing revert is title-only. It must surface, verdict first.
    ents = {"feat-brin-index": Entity(id="feat-brin-index", type="feature",
                                      name="BRIN index")}
    events = {"evt-rev": ev(
        "evt-rev", 'Revert changes in HOT handling of BRIN indexes',
        ts="2026-06-20",
        refs=[], derived_relations=[{"predicate": "reverts", "target": "e3fcca0d0d24"}])}
    matched = [ents["feat-brin-index"]]
    out = serialize(ents, [], events, matched, text="BRIN HOT updates")
    assert "## Events matched by title" in out
    assert "NOT PRESENT (reverted 2026-06-20)" in out
    assert "Revert changes in HOT handling of BRIN indexes" in out


def test_revert_without_recorded_reason_gets_marker():
    # no rationale beyond the title, mechanically: empty, sha boilerplate, or
    # a body that only restates the title all mean the reason is not recorded
    for body in ("", "This reverts commit e3fcca0d0d24.",
                 "Changes in HOT handling of BRIN indexes"):
        events = {"evt-rev": ev(
            "evt-rev", "Revert changes in HOT handling of BRIN indexes",
            ts="2026-06-20", body=body,
            derived_relations=[{"predicate": "reverts", "target": "e3fcca0d0d24"}])}
        out = serialize({}, [], events, [], text="BRIN HOT updates")
        assert "— reason: not recorded" in out, repr(body)


def test_revert_with_recorded_reason_is_unmarked():
    events = {"evt-rev": ev(
        "evt-rev", "Revert changes in HOT handling of BRIN indexes",
        ts="2026-06-20",
        summary="Reverted because the summarization callback crashed during "
                "concurrent vacuum on partitioned tables.",
        derived_relations=[{"predicate": "reverts", "target": "e3fcca0d0d24"}])}
    out = serialize({}, [], events, [], text="BRIN HOT updates")
    assert "NOT PRESENT (reverted 2026-06-20)" in out
    assert "reason: not recorded" not in out


def test_one_common_word_does_not_drag_titles_in():
    events = {"evt-1": ev("evt-1", "Session store replication lag")}
    # only "session" overlaps; a multi-word query needs two stem hits
    assert title_events(events, "session affinity for the billing pods",
                        vocabulary({}, events)) == []


def test_title_events_deduped_and_capped():
    events = {f"evt-{i}": ev(f"evt-{i}", f"WALBufMappingLock tweak {i}",
                             ts=f"2026-07-{i + 1:02d}") for i in range(9)}
    picked = title_events(events, "walbufmappinglock", vocabulary({}, events),
                          exclude={"evt-8"})
    assert len(picked) == 5
    assert picked[0].id == "evt-7"  # newest first, evt-8 already shown


def test_rare_token_hits_outrank_a_flood_of_common_ones():
    # benchmark G9: a wide expansion carries common stems, so many noise
    # titles score 3 hits while the gold title's 2 hits are on rare tokens.
    # Raw hit counts flood the gold out of the cap; rarity weighting must not.
    events = {f"evt-noise-{i}": ev(f"evt-noise-{i}", f"Fix buffer error return path {i}",
                                   ts=f"2026-06-{i + 1:02d}") for i in range(8)}
    events["evt-gold"] = ev("evt-gold", "Fix printf while throwing exceptions",
                            ts="2026-01-01")
    picked = title_events(events, "printf throwing buffer error return check overflow",
                          vocabulary({}, events))
    assert picked[0].id == "evt-gold"


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "testcap"]) == 0
    return tmp_path


def test_query_empty_match_prints_hint_and_titled_event(repo, capsys):
    assert cli.main([
        "event", "add", "--type", "note", "--title", "Get rid of WALBufMappingLock",
        "--ts", "2026-07-01",
    ]) == 0
    capsys.readouterr()
    assert cli.main(["query", "walbufmappinglock"]) == 0
    out = capsys.readouterr().out
    assert "Get rid of WALBufMappingLock" in out
    assert "no direct entity match" in out
    assert "walbufmappinglock(1)" in out
    assert "(no knowledge found)" not in out


def test_query_normal_match_prints_no_hint(repo, capsys):
    assert cli.main([
        "event", "add", "--type", "incident", "--title", "Session store replication lag",
        "--ts", "2026-07-01",
        "--entity", "svc-auth-gateway:service:auth-gateway:Authentication gateway service",
    ]) == 0
    capsys.readouterr()
    assert cli.main(["query", "why is auth-gateway slow?"]) == 0
    out = capsys.readouterr().out
    assert "svc-auth-gateway" in out
    assert "no direct entity match" not in out
    assert "## Events matched by title" not in out  # already cited as a source event


def test_timeline_path_unchanged(repo, capsys):
    assert cli.main([
        "event", "add", "--type", "note", "--title", "Get rid of WALBufMappingLock",
        "--ts", "2026-07-01",
    ]) == 0
    capsys.readouterr()
    assert cli.main(["query", "--timeline", "nothing the graph mentions at all"]) == 0
    out = capsys.readouterr().out
    assert "no direct entity match" not in out
    assert "## Events matched by title" not in out
