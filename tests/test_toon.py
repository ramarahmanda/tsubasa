from tsubasa import toon


def roundtrip(obj):
    encoded = toon.encode(obj)
    assert toon.decode(encoded) == obj, encoded
    return encoded


def test_scalars():
    roundtrip({"a": 1, "b": "text", "c": True, "d": None, "e": 1.5})


def test_quoting():
    roundtrip({"a": "has, comma", "b": "colon: inside", "c": "123", "d": "true", "e": " padded "})


def test_nested_and_arrays():
    obj = {
        "top": {"inner": {"deep": "v"}},
        "tags": ["a", "b c", "d,e"],
        "empty": [],
        "nums": [1, 2, 3],
    }
    roundtrip(obj)


def test_tabular():
    obj = {"relations": [
        {"source": "a", "predicate": "p", "target": "b", "ts": "2026-01-01", "provenance": "evt-1"},
        {"source": "c", "predicate": "q", "target": "d", "ts": "", "provenance": ""},
    ]}
    encoded = roundtrip(obj)
    assert "{source,predicate,target,ts,provenance}" in encoded


def test_object_list_non_uniform():
    obj = {"entities": [
        {"id": "x", "type": "service", "aliases": ["y"]},
        {"id": "z", "type": "adr"},
    ]}
    roundtrip(obj)


def test_block_string_with_blanks_and_markdown():
    body = "# Header\n\nline one\n\n  - indented list\n   odd-indent line\ncode:\n    four spaces"
    obj = {"event": {"id": "e1", "body": body, "after": "value"}}
    roundtrip(obj)


def test_unicode():
    roundtrip({"name": "つばさ キャプテン", "note": "émoji ✈"})


def test_yaml_folded_block_accepted():
    # hand-authored files sometimes use YAML's ">" — decode as a block
    text = "event:\n  summary: >\n    line one\n    line two\n  after: ok\n"
    doc = toon.decode(text)
    assert doc["event"]["summary"] == "line one\nline two"
    assert doc["event"]["after"] == "ok"
