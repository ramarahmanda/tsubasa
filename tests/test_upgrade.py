"""Captains built by an older tsubasa must still load, replay, and upgrade.

The fixture below is a hand-built `.tsubasa/` in the 0.1.1 shape: no schema
stamp, `tasks/task-*.toon` files, `task_update` events and task-typed entities
in the log, hand-written sources, and no svc-/corpus- source graph.

Removing a feature is allowed to stop writing its data. It is never allowed to
stop reading it: a captain that will not load is data loss, not a migration
inconvenience.
"""

import subprocess

import pytest

from tsubasa import cli, toon
from tsubasa.config import SCHEMA_VERSION, load
from tsubasa.graph import assemble
from tsubasa.storage import Store

OLD_CONFIG = """\
# Captain configuration — see https://github.com/ramarahmanda/tsubasa
[captain]
name = "oldcap"
role = "Engineering Director"

[captain.domains]
auth = 1.0

[memory]
hot_max_context = 0.25
context_window = 200000
half_life_days = 90

[memory.weights]
recency = 0.4
impact = 0.3
domain = 0.2
access = 0.1

# hand-written by a human, never auto-detected
[[sources]]
adapter = "adr"
path = "docs/decisions"
glob = "**/*.md"

[[sources]]
adapter = "git"
path = "."
"""

# Schema-1 events, including two shapes this version no longer writes: a
# `task_update` event type and a `task`-typed derived entity with a relation
# whose endpoint is a `task-` id.
OLD_EVENTS = {"events": [
    {"id": "evt-20250110-use-postgres", "type": "adr", "ts": "2025-01-10",
     "title": "Use Postgres for the session store",
     "criticality": {"impact": "high", "domains": ["auth"]},
     "trust": "high", "source": "manual",
     "derived": {"entities": [
         {"id": "adr-use-postgres", "type": "adr",
          "name": "Use Postgres for the session store"}]}},
    {"id": "evt-20250115-task-opened", "type": "task_update", "ts": "2025-01-15",
     "title": "task-session-store -> in_progress",
     "summary": "evt-20250110-use-postgres",
     "refs": [{"kind": "adr", "id": "adr-use-postgres"}],
     "source": "adapter:git",
     "derived": {
         "entities": [{"id": "task-session-store", "type": "task",
                       "name": "Move the session store to Postgres"}],
         "relations": [{"source": "task-session-store", "predicate": "implements",
                        "target": "adr-use-postgres"}]}},
    {"id": "evt-20250220-outage", "type": "incident", "ts": "2025-02-20",
     "title": "Session store replication lag",
     "criticality": {"impact": "high", "domains": ["auth"]},
     "derived": {"entities": [
         {"id": "svc-sessions", "type": "service", "name": "sessions",
          "description": "Session service"}]}},
]}

OLD_TASK = {"task": {
    "id": "task-session-store", "title": "Move the session store to Postgres",
    "state": "in_progress", "adr": "adr-use-postgres", "prs": ["PR-77"],
    "domains": ["auth"], "created": "2025-01-12", "updated": "2025-01-15",
    "history": [{"ts": "2025-01-15", "state": "in_progress", "by": "adapter:git",
                 "evidence": "evt-20250115-task-opened"}],
}}

HISTORICAL_EVENT_IDS = {e["id"] for e in OLD_EVENTS["events"]}


@pytest.fixture()
def old_captain(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# oldcap\n")

    decisions = tmp_path / "docs/decisions"
    decisions.mkdir(parents=True)
    (decisions / "0001-use-postgres.md").write_text(
        "# Use Postgres\n\nStatus: accepted\nDate: 2025-01-10\n\nWe chose Postgres.\n")
    # detected but NOT in captain.toml, so `upgrade --add` has something to add
    postmortems = tmp_path / "docs/postmortems"
    postmortems.mkdir(parents=True)
    for i in range(4):
        (postmortems / f"{i}-outage.md").write_text(f"# Outage {i}\n\nWhat happened.\n")

    base = tmp_path / ".tsubasa"
    (base / "graph/events").mkdir(parents=True)
    (base / "tasks").mkdir(parents=True)
    (base / "memory").mkdir(parents=True)
    (base / "captain.toml").write_text(OLD_CONFIG)
    (base / "graph/events/2025-01.toon").write_text(toon.encode(OLD_EVENTS))
    (base / "tasks/task-session-store.toon").write_text(toon.encode(OLD_TASK))
    return tmp_path


# --------------------------------------------------------------- read tolerance

def test_the_fixture_really_carries_retired_shapes(old_captain):
    pack = (old_captain / ".tsubasa/graph/events/2025-01.toon").read_text()
    assert "type: task_update" in pack
    assert "type: task" in pack
    assert load(old_captain).schema_version == 1  # absent stamp reads as pre-versioning


def test_replay_tolerates_retired_event_types_and_entities(old_captain, capsys):
    """The one that matters: a log holding retired types must replay, not raise."""
    store = Store(old_captain)
    assert len(store.load_events()) == len(HISTORICAL_EVENT_IDS)

    entities, relations, _ = assemble.replay(store)
    assert entities["task-session-store"].type == "task"      # unknown type kept verbatim
    assert entities["adr-use-postgres"].status == "active"
    assert ("task-session-store", "implements", "adr-use-postgres") in {r.key() for r in relations}

    assert cli.main(["rebuild"]) == 0
    assert "rebuilt from event log" in capsys.readouterr().out


def test_unknown_fields_on_an_event_do_not_break_replay(old_captain):
    """A field a future (or retired) version wrote is ignored, not fatal."""
    base = old_captain / ".tsubasa/graph/events"
    (base / "2025-03.toon").write_text(toon.encode({"events": [{
        "id": "evt-20250301-future", "type": "quantum_deploy", "ts": "2025-03-01",
        "title": "From a version that does not exist yet",
        "vibe": "excellent", "confidence_interval": "0.9",
    }]}))
    assert cli.main(["rebuild"]) == 0
    assert Store(old_captain).load_events()[-1].type == "quantum_deploy"


# --------------------------------------------------------------------- upgrade

def test_upgrade_gains_the_source_graph_and_keeps_everything_else(old_captain, capsys):
    assert cli.main(["upgrade"]) == 0
    out = capsys.readouterr().out

    entities = Store(old_captain).load_entities()
    assert any(e.startswith("svc-") and e != "svc-sessions" for e in entities)
    assert any(e.startswith("corpus-") for e in entities)
    # the new map is applied by REPLAY, so the historical graph comes with it
    # rather than being overwritten by the two entities the map event carries
    assert {"adr-use-postgres", "svc-sessions", "task-session-store"} <= set(entities)

    cfg = load(old_captain)
    assert cfg.schema_version == SCHEMA_VERSION
    assert cfg.name == "oldcap" and cfg.domains == {"auth": 1.0}
    # the hand-written source survives verbatim, including its glob
    adr = next(s for s in cfg.sources if s.adapter == "adr")
    assert (adr.path, adr.glob) == ("docs/decisions", "**/*.md")
    # detection reports what is missing but does not write it
    assert "docs/postmortems" in out
    assert not any(s.path == "docs/postmortems" for s in cfg.sources)

    events = {e.id for e in Store(old_captain).load_events()}
    assert HISTORICAL_EVENT_IDS <= events
    assert cli.main(["rebuild"]) == 0


def test_upgrade_retires_task_files_without_deleting_them(old_captain, capsys):
    assert cli.main(["upgrade"]) == 0
    out = capsys.readouterr().out
    base = old_captain / ".tsubasa"
    assert not (base / "tasks").exists()
    retired = base / "retired/tasks/task-session-store.toon"
    assert toon.decode(retired.read_text())["task"]["state"] == "in_progress"
    assert "retired 1 task file(s)" in out
    # the events stay put: append-only is the premise
    assert "left 1 retired task event(s) in the log untouched" in out
    assert "not deleted" in out
    assert any(e.type == "task_update" for e in Store(old_captain).load_events())


def test_upgrade_is_a_no_op_the_second_time(old_captain, capsys):
    assert cli.main(["upgrade"]) == 0
    entities_before = (old_captain / ".tsubasa/graph/entities.toon").read_text()
    config_before = (old_captain / ".tsubasa/captain.toml").read_text()
    capsys.readouterr()

    assert cli.main(["upgrade"]) == 0
    out = capsys.readouterr().out
    assert "nothing to upgrade" in out
    assert "already present" in out
    assert (old_captain / ".tsubasa/graph/entities.toon").read_text() == entities_before
    assert (old_captain / ".tsubasa/captain.toml").read_text() == config_before


def test_upgrade_add_registers_detected_sources(old_captain):
    assert cli.main(["upgrade", "--add"]) == 0
    cfg = load(old_captain)
    assert any(s.path == "docs/postmortems" for s in cfg.sources)
    adr = next(s for s in cfg.sources if s.adapter == "adr" and s.path == "docs/decisions")
    assert adr.glob == "**/*.md"  # hand-written entry untouched


def test_upgrade_maps_hand_written_sources_too(old_captain):
    """The corpus entity must come from captain.toml, not from a fresh scan:
    a source only a human knows about is exactly the one worth mapping."""
    cli.main(["upgrade"])
    corpora = [e for e in Store(old_captain).load_entities().values()
               if e.id.startswith("corpus-")]
    assert any(e.name == "docs/decisions" for e in corpora)


def test_upgrade_adds_delegate_only_when_absent(old_captain, capsys):
    assert cli.main(["upgrade"]) == 0
    assert "delegate_only = true" in capsys.readouterr().out
    text = (old_captain / ".tsubasa/captain.toml").read_text()
    assert "delegate_only = true" in text
    load(old_captain)  # the insert kept the file parseable


def test_upgrade_never_flips_an_explicit_false(old_captain):
    path = old_captain / ".tsubasa/captain.toml"
    path.write_text(path.read_text().replace(
        "[captain]", "[captain]\ndelegate_only = false"))
    assert cli.main(["upgrade"]) == 0
    text = path.read_text()
    assert "delegate_only = false" in text and "delegate_only = true" not in text


# ---------------------------------------------------------------------- doctor

def test_doctor_demands_upgrade_when_the_stamp_is_missing(old_captain, capsys):
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "built before versioning" in out and "tsubasa upgrade" in out


def test_doctor_stops_complaining_after_upgrade(old_captain, capsys):
    cli.main(["upgrade"])
    capsys.readouterr()
    cli.main(["doctor"])
    assert "SCHEMA" not in capsys.readouterr().out


def test_doctor_flags_a_captain_from_the_future(old_captain, capsys):
    cli.main(["upgrade"])
    path = old_captain / ".tsubasa/captain.toml"
    path.write_text(path.read_text().replace(
        f"schema_version = {SCHEMA_VERSION}", f"schema_version = {SCHEMA_VERSION + 99}"))
    capsys.readouterr()
    assert cli.main(["doctor"]) == 1
    assert "newer than this CLI" in capsys.readouterr().out


def test_upgrade_refuses_a_captain_from_the_future(old_captain, capsys):
    cli.main(["upgrade"])
    path = old_captain / ".tsubasa/captain.toml"
    path.write_text(path.read_text().replace(
        f"schema_version = {SCHEMA_VERSION}", f"schema_version = {SCHEMA_VERSION + 99}"))
    assert cli.main(["upgrade"]) == 1
    assert "upgrade tsubasa itself" in capsys.readouterr().err


# ------------------------------------------------------------------------ init

def test_init_stamps_the_schema_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "freshcap"]) == 0
    assert load(tmp_path).schema_version == SCHEMA_VERSION
    assert cli.main(["upgrade"]) == 0  # a fresh captain needs nothing


def test_init_writes_delegate_only_on(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "freshcap"]) == 0
    assert "delegate_only = true" in (tmp_path / ".tsubasa/captain.toml").read_text()


def test_upgrade_on_a_fresh_captain_is_a_no_op(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# fresh\n")
    cli.main(["init", "freshcap"])
    capsys.readouterr()
    assert cli.main(["upgrade"]) == 0
    assert "nothing to upgrade" in capsys.readouterr().out
