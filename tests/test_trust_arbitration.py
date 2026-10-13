"""Entity trust aggregates from source events, so arbitration has teeth:
a lower-trust event cannot silently supersede higher-trust knowledge."""

import pytest

from tsubasa import cli
from tsubasa.storage import Store


@pytest.fixture()
def captain(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap"])
    return tmp_path


def _add(*extra):
    return cli.main(["event", "add", *extra])


def test_normal_event_cannot_supersede_high_trust_entity(captain, capsys):
    _add("--type", "decision", "--title", "adopt X", "--trust", "high",
         "--entity", "feat-x:feature:X:The X path")
    capsys.readouterr()
    _add("--type", "note", "--title", "drop X maybe", "--trust", "normal",
         "--supersedes", "feat-x")
    assert "disputed:" in capsys.readouterr().out
    ents = Store(captain).load_entities()
    assert ents["feat-x"].status == "active"
    assert any(e.disputed for e in Store(captain).load_events())


def test_high_trust_event_supersedes_high_trust_entity(captain):
    _add("--type", "decision", "--title", "adopt Y", "--trust", "high",
         "--entity", "feat-y:feature:Y:The Y path")
    _add("--type", "decision", "--title", "drop Y", "--trust", "high",
         "--supersedes", "feat-y")
    assert Store(captain).load_entities()["feat-y"].status == "superseded"


def test_low_trust_entity_yields_to_normal_event(captain):
    _add("--type", "note", "--title", "rumour of Z", "--trust", "low",
         "--entity", "ext-z:external:Z:rumoured integration")
    _add("--type", "note", "--title", "z confirmed gone", "--trust", "normal",
         "--supersedes", "ext-z")
    assert Store(captain).load_entities()["ext-z"].status == "superseded"


def test_rebuild_reproduces_the_dispute(captain, capsys):
    _add("--type", "decision", "--title", "adopt X", "--trust", "high",
         "--entity", "feat-x:feature:X:The X path")
    _add("--type", "note", "--title", "drop X maybe", "--trust", "normal",
         "--supersedes", "feat-x")
    assert cli.main(["rebuild"]) == 0
    assert Store(captain).load_entities()["feat-x"].status == "active"
