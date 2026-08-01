"""Domain labels become filenames under memory/domains/. Labels arrive from
`event add --domains` (typed) and verbatim from model output during study,
so hostile or sloppy labels must not escape the directory."""

import pytest

from tsubasa import cli
from tsubasa.models import Event
from tsubasa.storage import Store


@pytest.fixture()
def captain(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap"])
    return tmp_path


def test_event_add_rejects_non_kebab_domains(captain, capsys):
    rc = cli.main(["event", "add", "--type", "note", "--title", "bad label",
                   "--domains", "../evil"])
    assert rc == 1
    assert "kebab-case" in capsys.readouterr().err
    assert not [e for e in Store(captain).load_events() if e.title == "bad label"]


def test_hostile_domain_from_the_log_cannot_escape(captain):
    # simulate a label arriving via replay: an old log, an adapter, or a model.
    # The write boundary slugs it instead of trusting it.
    store = Store(captain)
    store.append_event(Event(
        id="evt-20260101-seedx", type="note", ts="2026-01-01", title="seed x",
        domains=["../../evil", "Data Sync"],
        derived_entities=[{"id": "svc-x", "type": "service", "name": "x"}]))
    assert cli.main(["rebuild"]) == 0
    domains_dir = captain / ".tsubasa" / "memory" / "domains"
    names = {p.name for p in domains_dir.iterdir()}
    assert "data-sync.md" in names
    assert "evil.md" in names                      # slugged, kept inside
    assert not (captain / "evil.md").exists()
    assert not (captain / ".tsubasa" / "memory" / "evil.md").exists()
    for p in domains_dir.rglob("*"):
        assert p.parent == domains_dir             # nothing nested, nothing outside


def test_study_domains_are_slugged(tmp_path):
    from tsubasa.distill import _study_event
    ev = _study_event({"title": "t", "date": "2026-01-01",
                       "domains": ["Data Sync", "../evil", ""]}, "repo", tmp_path)
    assert ev.domains == ["data-sync", "evil"]
