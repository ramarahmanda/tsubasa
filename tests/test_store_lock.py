"""One writer per captain, and no write can tear a file."""

import os
import time

import pytest

from tsubasa import cli, storage
from tsubasa.storage import Store


@pytest.fixture()
def captain(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap"])
    return tmp_path


def test_second_writer_is_refused(captain):
    with Store(captain).write_lock():
        with pytest.raises(RuntimeError, match="another tsubasa command"):
            with Store(captain).write_lock():
                pass


def test_stale_lock_is_stolen(captain):
    lock = captain / ".tsubasa" / ".lock"
    lock.write_text("pid 99999 since 2020-01-01T00:00:00")
    old = time.time() - Store.LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))
    with Store(captain).write_lock():
        assert lock.exists()
    assert not lock.exists()


def test_mutating_command_respects_the_lock(captain, capsys):
    with Store(captain).write_lock():
        rc = cli.main(["event", "add", "--type", "note", "--title", "blocked"])
    assert rc == 1
    assert "another tsubasa command" in capsys.readouterr().err
    assert not [e for e in Store(captain).load_events() if e.title == "blocked"]


def test_read_command_ignores_the_lock(captain):
    with Store(captain).write_lock():
        assert cli.main(["goal", "list"]) == 0


def test_lock_released_after_command(captain):
    assert cli.main(["event", "add", "--type", "note", "--title", "ok"]) == 0
    assert not (captain / ".tsubasa" / ".lock").exists()


def test_atomic_write_keeps_old_content_on_crash(tmp_path, monkeypatch):
    target = tmp_path / "f.toon"
    target.write_text("old")

    def boom(a, b):
        raise OSError("disk full")

    monkeypatch.setattr(storage.os, "replace", boom)
    with pytest.raises(OSError):
        storage.write_atomic(target, "new")
    assert target.read_text() == "old"
