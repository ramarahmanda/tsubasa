"""Git adapter reads the latest on the configured branch via fetch/pull."""

import subprocess

import pytest

from tsubasa import cli
from tsubasa.storage import Store


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    # origin bare repo with one adr commit; local clone that will fall behind
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(origin, "init", "-q", "--bare", "-b", "main")
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "clone", "-q", str(origin), ".")
    git(seed, "commit", "--allow-empty", "-q", "-m", "feat: adr-first work")
    git(seed, "push", "-q", "origin", "main")

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)
    git(ws, "clone", "-q", str(origin), "svc")
    cli.main(["init", "cap"])
    assert cli.main(["source", "add", "git", "svc", "--branch", "main", "--pull"]) == 0

    # origin moves ahead AFTER the clone
    git(seed, "commit", "--allow-empty", "-q", "-m", "feat: adr-second work")
    git(seed, "push", "-q", "origin", "main")
    return ws


def test_ingest_sees_commits_pushed_after_clone(workspace):
    assert cli.main(["ingest", "git"]) == 0
    events = Store(workspace).load_events()
    titles = " ".join(e.title for e in events)
    assert "adr-first" in titles
    assert "adr-second" in titles  # only reachable because ingest pulled latest


def test_pull_fast_forwards_local_branch(workspace):
    cli.main(["ingest", "git"])
    head = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=workspace / "svc",
                          capture_output=True, text=True).stdout
    assert "adr-second" in head
