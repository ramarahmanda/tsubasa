"""Output contract of `tsubasa query` (benchmark G9): the verdict prints above
the fold and every section is hard-capped with an explicit elision marker. A
reader pipes the answer through `head`; a load-bearing line below the pipe is
a line that does not exist, and silent truncation reads as "not recorded"."""

from pathlib import Path

from tsubasa.graph import graphify_bridge
from tsubasa.graph.query import serialize
from tsubasa.models import Entity, Event, Ref, Relation


def ent(eid, name, **kw):
    return Entity(id=eid, type="feature", name=name, **kw)


def ev(eid, title, ts="2026-07-01", **kw):
    return Event(id=eid, type="note", ts=ts, title=title, **kw)


def rel(src, pred, tgt):
    return Relation(source=src, predicate=pred, target=tgt)


REVERT = {"predicate": "reverts", "target": "0c071936e94c"}


# ------------------------------------------------------------------ ordering

def test_title_block_prints_before_relations_and_source_events():
    ents = {"feat-brin-index": ent("feat-brin-index", "BRIN index",
                                   source_events=["evt-src"]),
            "svc-repo": ent("svc-repo", "repo")}
    relations = [rel("feat-brin-index", "part_of", "svc-repo")]
    events = {"evt-src": ev("evt-src", "chunk digest"),
              "evt-rev": ev("evt-rev", "Revert changes in HOT handling of BRIN indexes",
                            ts="2026-06-20", derived_relations=[REVERT])}
    out = serialize(ents, relations, events, [ents["feat-brin-index"]],
                    text="BRIN HOT updates")
    i_title = out.index("## Events matched by title")
    assert i_title < out.index("## Relations")
    assert i_title < out.index("## Source events")
    assert "NOT PRESENT (reverted 2026-06-20)" in out


# ------------------------------------------------------------------ caps

def test_relations_capped_with_elision_marker():
    ents = {"feat-hub": ent("feat-hub", "hub feature")}
    relations = [rel("feat-hub", "touches", f"node-{i}") for i in range(15)]
    out = serialize(ents, relations, {}, [ents["feat-hub"]])
    assert sum(1 for ln in out.splitlines() if ln.startswith("(feat-hub)")) == 12
    assert "... +3 more relations" in out


def test_refs_capped_commits_before_files():
    ents = {"feat-x": ent("feat-x", "x", source_events=["evt-1"])}
    events = {"evt-1": ev("evt-1", "chunk digest", refs=[
        Ref("file", "f1"), Ref("doc", "d1"), Ref("commit", "c1"),
        Ref("commit", "c2"), Ref("file", "f2")])}
    out = serialize(ents, [], events, [ents["feat-x"]])
    assert out.index("ref commit: c1") < out.index("ref file: f1")
    assert "ref doc" not in out  # commits and files fill the cap first
    assert "... +2 more refs" in out


def test_source_events_capped_with_elision_marker():
    ids = [f"evt-{i}" for i in range(8)]
    ents = {"feat-x": ent("feat-x", "x", source_events=ids)}
    events = {i: ev(i, "chunk digest", ts=f"2026-07-{n + 1:02d}")
              for n, i in enumerate(ids)}
    out = serialize(ents, [], events, [ents["feat-x"]])
    assert sum(1 for ln in out.splitlines() if ln.startswith("- evt-")) == 5
    assert "... +3 more source events" in out


# ------------------------------------------------------------------ the fold

def test_g9_fold_verdict_within_first_30_lines():
    # the incident shape: a graph big enough that the old layout printed the
    # revert past line 100, behind a full relations and source-events dump
    ents = {f"feat-noise-{i}": ent(f"feat-noise-{i}", f"error wrapper feature {i}",
                                   source_events=[f"evt-src-{j}" for j in range(8)])
            for i in range(5)}
    relations = [rel(f"feat-noise-{i}", "touches", f"node-{i}-{j}")
                 for i in range(5) for j in range(20)]
    events = {f"evt-src-{j}": ev(f"evt-src-{j}", "chunk digest",
                                 ts=f"2026-06-{j + 1:02d}",
                                 summary="digest summary line",
                                 refs=[Ref("commit", f"c{j}{k}") for k in range(6)])
              for j in range(8)}
    events["evt-gold"] = ev(
        "evt-gold", "Revert error-throwing wrappers for the printf family of functions",
        ts="2015-05-19", derived_relations=[REVERT],
        refs=[Ref("commit", "0c071936e94c")])
    matched = [ents[f"feat-noise-{i}"] for i in range(5)]
    out = serialize(ents, relations, events, matched,
                    text="wrapper for formatting functions error handling printf")
    lines = out.splitlines()
    gold = next(i for i, ln in enumerate(lines) if "NOT PRESENT" in ln)
    assert gold < 30
    assert "[0c071936e94c]" in lines[gold]
    # the flood is real, and elided rather than printed
    assert "... +88 more relations" in out
    assert "... +3 more source events" in out


# ------------------------------------------------------------------ graphify

def graph(n_nodes):
    return {"nodes": [{"id": f"n{i}", "name": f"alpha_handler_{i}", "type": "function"}
                      for i in range(n_nodes)],
            "edges": [{"source": "n0", "target": f"n{i}", "label": "calls"}
                      for i in range(1, n_nodes)]}


def test_graphify_nodes_capped_with_pointer(monkeypatch):
    monkeypatch.setattr(graphify_bridge, "load_graphs",
                        lambda root, cfg: [("repoa", graph(12))])
    lines = graphify_bridge.query(Path("."), None, "alpha handler").splitlines()
    assert len(lines) == graphify_bridge.MAX_LINES
    assert sum(1 for ln in lines if ln.startswith("- ")) == graphify_bridge.MAX_LINES - 1
    assert "+5 more nodes" in lines[-1]
    assert "11 edges" in lines[-1]
    assert "graphify explain" in lines[-1]


def test_graphify_pointer_present_even_under_cap(monkeypatch):
    monkeypatch.setattr(graphify_bridge, "load_graphs",
                        lambda root, cfg: [("repoa", graph(2))])
    lines = graphify_bridge.query(Path("."), None, "alpha handler").splitlines()
    assert len(lines) == 3  # two nodes, then the pointer
    assert "more nodes" not in lines[-1]
    assert "graphify explain" in lines[-1]
