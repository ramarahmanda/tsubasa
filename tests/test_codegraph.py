"""Code-index storage: TOON at rest, JSON at runtime, lossless round-trip."""

from tsubasa import codegraph

GRAPH = {
    "directed": False,
    "nodes": [
        {"id": "a_hello", "label": "hello", "type": "function", "file": "a.py",
         "source_location": None, "community": 0},
        {"id": "a_world", "label": "world()", "type": "function", "file": "a.py",
         "source_location": "a.py:3", "community": 0, "tags": ["core", "hot"]},
    ],
    "edges": [
        {"source": "a_hello", "target": "a_world", "relation": "calls",
         "confidence": "EXTRACTED", "confidence_score": 1.0},
    ],
    "communities": {"0": ["a_hello", "a_world"]},
}


def test_roundtrip_lossless():
    doc = codegraph.graph_to_toon_doc(GRAPH)
    back = codegraph.toon_doc_to_graph(doc)
    assert back["nodes"] == GRAPH["nodes"]
    assert back["edges"] == GRAPH["edges"]
    assert back["directed"] is False
    assert back["communities"] == GRAPH["communities"]


def test_toon_roundtrips_through_encoder(tmp_path):
    path = codegraph.save(tmp_path, "svc", GRAPH)
    assert path.name == "graph.toon"
    loaded = codegraph.load(tmp_path, "svc")
    assert loaded["nodes"] == GRAPH["nodes"]
    assert loaded["edges"] == GRAPH["edges"]
    assert codegraph.repos_with_index(tmp_path) == ["svc"]


def test_toon_smaller_than_json(tmp_path):
    import json
    big = {"nodes": [{"id": f"n{i}", "label": f"Node {i}", "type": "function",
                      "file": "x.py", "source_location": None} for i in range(200)],
           "edges": [{"source": f"n{i}", "target": f"n{i+1}", "relation": "calls",
                      "confidence": "EXTRACTED", "confidence_score": 1.0} for i in range(199)]}
    codegraph.save(tmp_path, "big", big)
    toon_size = (tmp_path / ".tsubasa/code-index/big/graph.toon").stat().st_size
    json_size = len(json.dumps(big))
    assert toon_size < json_size * 0.6  # tabular headers-once beats repeated keys
