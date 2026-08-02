"""Crash-safety of graph writes.

`Path.write_text` truncates before writing, so a crash or a concurrent reader
sees a partial file. The event log is the durable source of truth — `rebuild`
replays it — so a truncated write there is data loss.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tsubasa import toon
from tsubasa.atomicio import write_text_atomic
from tsubasa.models import Entity, Event
from tsubasa.storage import Store


def test_write_text_atomic_creates_file(tmp_path: Path):
    target = tmp_path / "nested" / "out.toon"
    write_text_atomic(target, "hello")
    assert target.read_text() == "hello"


def test_write_text_atomic_replaces_existing(tmp_path: Path):
    target = tmp_path / "out.toon"
    target.write_text("old")
    write_text_atomic(target, "new")
    assert target.read_text() == "new"


def test_failed_write_leaves_original_intact(tmp_path: Path, monkeypatch):
    """A crash mid-write must not destroy the previous version."""
    target = tmp_path / "graph.toon"
    target.write_text("original")

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_text_atomic(target, "replacement")
    monkeypatch.setattr(os, "replace", real_replace)

    assert target.read_text() == "original"
    # and no debris left behind
    assert [p.name for p in tmp_path.iterdir()] == ["graph.toon"]


def test_no_partial_file_is_ever_observable(tmp_path: Path):
    """The target must never exist in a half-written state.

    Fails against `write_text`, which truncates the target first: a reader
    arriving between truncate and write observes an empty or partial file.
    """
    target = tmp_path / "entities.toon"
    target.write_text("x" * 5000)

    observed: list[int] = []
    real_replace = os.replace

    def watching_replace(src, dst):
        # stand-in for a reader arriving mid-write: the destination must still
        # hold the complete previous version at this point
        observed.append(Path(dst).stat().st_size)
        return real_replace(src, dst)

    import tsubasa.atomicio as atomicio

    orig = atomicio.os.replace
    atomicio.os.replace = watching_replace
    try:
        write_text_atomic(target, "y" * 9000)
    finally:
        atomicio.os.replace = orig

    assert observed == [5000], "target was truncated before the rename"
    assert target.read_text() == "y" * 9000


@pytest.mark.parametrize("mode", [0o644, 0o640])
def test_existing_permissions_are_preserved(tmp_path: Path, mode: int):
    """A rewrite must not export mkstemp's 0600 onto an existing target."""
    target = tmp_path / "out.toon"
    target.write_text("old")
    os.chmod(target, mode)
    write_text_atomic(target, "new")
    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert target.read_text() == "new"


def test_fresh_file_honors_umask_not_mkstemp(tmp_path: Path):
    """A freshly created file gets the process default, not mkstemp's 0600."""
    target = tmp_path / "fresh.toon"
    old_umask = os.umask(0o022)
    try:
        write_text_atomic(target, "hello")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_store_writes_survive_a_failed_append(tmp_path: Path, monkeypatch):
    """An append that dies mid-write leaves the prior event pack readable."""
    store = Store(tmp_path)
    first = Event(id="evt-1", type="note", ts="2026-01-01", title="first")
    store.append_event(first)
    pack = store.event_path(first)
    before = pack.read_text()
    assert "evt-1" in before

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.append_event(Event(id="evt-2", type="note", ts="2026-01-02", title="second"))

    # the log is still valid and still decodable
    assert pack.read_text() == before
    assert toon.decode(pack.read_text())["events"][0]["id"] == "evt-1"


def test_entities_write_is_atomic(tmp_path: Path, monkeypatch):
    store = Store(tmp_path)
    store.save_entities({"a": Entity(id="a", type="service", name="a")})
    path = store.graph_dir / "entities.toon"
    before = path.read_text()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save_entities({"b": Entity(id="b", type="service", name="b")})

    assert path.read_text() == before
