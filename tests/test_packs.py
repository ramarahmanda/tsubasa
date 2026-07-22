"""Monthly event packs: fewer files, same knowledge, legacy migration."""

import pytest

from tsubasa import cli, toon
from tsubasa.models import Event
from tsubasa.storage import Store


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap"])
    return tmp_path


def test_events_land_in_monthly_packs(repo):
    cli.main(["event", "add", "--type", "note", "--title", "a", "--ts", "2026-07-01"])
    cli.main(["event", "add", "--type", "note", "--title", "b", "--ts", "2026-07-20"])
    cli.main(["event", "add", "--type", "note", "--title", "c", "--ts", "2026-08-02"])
    store = Store(repo)
    packs = sorted(p.name for p in store.events_dir.glob("*.toon"))
    assert packs == ["2026-07.toon", "2026-08.toon"]
    july = toon.decode((store.events_dir / "2026-07.toon").read_text())["events"]
    assert [e["title"] for e in july] == ["a", "b"]
    assert len(store.load_events()) == 3


def test_legacy_files_still_read_and_pack_migrates(repo):
    store = Store(repo)
    legacy_dir = store.events_dir / "2026" / "06"
    legacy_dir.mkdir(parents=True)
    ev = Event(id="evt-legacy-1", type="note", ts="2026-06-05", title="old style")
    (legacy_dir / "evt-legacy-1.toon").write_text(toon.encode({"event": ev.to_dict()}))
    cli.main(["event", "add", "--type", "note", "--title", "new style", "--ts", "2026-07-01"])
    assert {e.id for e in Store(repo).load_events()} >= {"evt-legacy-1"}
    assert cli.main(["pack"]) == 0
    store = Store(repo)
    assert not (store.events_dir / "2026").exists()          # legacy tree gone
    assert (store.events_dir / "2026-06.toon").is_file()      # packed
    assert "evt-legacy-1" in {e.id for e in store.load_events()}


def test_append_same_id_overwrites_not_duplicates(repo):
    cli.main(["event", "add", "--id", "evt-x", "--type", "note", "--title", "v1", "--ts", "2026-07-01"])
    store = Store(repo)
    ev = [e for e in store.load_events() if e.id == "evt-x"][0]
    ev.title = "v2"
    store.append_event(ev)
    events = [e for e in Store(repo).load_events() if e.id == "evt-x"]
    assert len(events) == 1 and events[0].title == "v2"
