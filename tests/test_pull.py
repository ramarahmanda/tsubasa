"""`init` does no git network I/O; `pull` is where fetching lives.

(`init` still makes its one source-classification `claude -p` call unless
`--no-llm`; conftest stubs that suite-wide. Only git network I/O is asserted
away here.)"""

import subprocess

import pytest

from tsubasa import cli
from tsubasa.adapters import gitlog
from tsubasa.storage import Store
from test_study_fast import commit, git, stub_cmd


@pytest.fixture()
def no_network(monkeypatch):
    """Fail loudly on any git subcommand that talks to a remote."""
    seen: list[str] = []
    real = gitlog._git

    def guard(repo, *args):
        if args and args[0] in ("fetch", "pull", "ls-remote", "clone"):
            seen.append(args[0])
            raise AssertionError(f"unexpected git {args[0]} (network I/O)")
        return real(repo, *args)

    monkeypatch.setattr(gitlog, "_git", guard)
    return seen


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A workspace holding a clone with a real (local) origin remote."""
    monkeypatch.chdir(tmp_path)
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    commit(seed, "feat: first", date="2026-01-01", files={"a.go": "package a\n"})
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    svc = tmp_path / "svc"
    subprocess.run(["git", "clone", "-q", str(origin), str(svc)], check=True)
    return tmp_path, seed, origin, svc


def push_new_commit(seed, origin, subject="fix: pushed later"):
    commit(seed, subject, date="2026-02-01", files={"b.go": "package b\n"})
    subprocess.run(["git", "-C", str(seed), "push", "-q", str(origin), "main"], check=True)


# ------------------------------------------------------------------ init

def test_init_does_no_git_network_io(workspace, no_network):
    root, _, _, svc = workspace
    # the guard raises on fetch/pull; init must complete without tripping it
    assert cli.main(["init", "cap", "--domains", "auth"]) == 0
    assert no_network == []
    assert Store(root).load_events()  # the workspace map still landed


def test_init_points_at_pull(workspace, no_network, capsys):
    cli.main(["init", "cap"])
    assert "tsubasa pull" in capsys.readouterr().out


def test_ingest_still_fetches(workspace, monkeypatch):
    root, seed, origin, svc = workspace
    cli.main(["init", "cap"])
    push_new_commit(seed, origin, "fix: adr-login-flow rework")
    fetched: list[str] = []
    real = gitlog._git

    def spy(repo, *args):
        if args and args[0] == "fetch":
            fetched.append(str(repo))
        return real(repo, *args)

    monkeypatch.setattr(gitlog, "_git", spy)
    assert cli.main(["ingest"]) == 0
    assert fetched, "explicit ingest must still refresh from the remote"


# ------------------------------------------------------------------ pull

def test_pull_fetches_and_ingests(workspace, capsys):
    root, seed, origin, svc = workspace
    cli.main(["init", "cap"])
    push_new_commit(seed, origin, "fix: adr-login-flow rework")
    capsys.readouterr()
    assert cli.main(["pull"]) == 0
    out = capsys.readouterr().out
    assert "[pull] svc: 1 new commit(s)" in out
    assert "1 commit(s) fetched" in out


def test_pull_with_nothing_new_says_so(workspace, capsys):
    cli.main(["init", "cap"])
    capsys.readouterr()
    assert cli.main(["pull"]) == 0
    assert "nothing new: no commits fetched, no events added" in capsys.readouterr().out


def test_pull_fast_forwards_when_source_asks(workspace):
    root, seed, origin, svc = workspace
    cli.main(["init", "cap"])
    cfg = root / ".tsubasa/captain.toml"
    cfg.write_text(cfg.read_text().replace('path = "svc"', 'path = "svc"\npull = true'))
    push_new_commit(seed, origin)
    assert cli.main(["pull"]) == 0
    assert (svc / "b.go").is_file()  # working tree moved, not just the remote ref


# ------------------------------------------------- working tree freshness
#
# adr/doc/incident adapters read the WORKING TREE, so a pull that only moves
# origin/<branch> re-ingests stale documents while reporting success.

def test_pull_advances_the_tree_without_pull_true(workspace, capsys):
    root, seed, origin, svc = workspace
    cli.main(["init", "cap"])
    assert "pull = true" not in (root / ".tsubasa/captain.toml").read_text()
    push_new_commit(seed, origin)
    capsys.readouterr()
    assert cli.main(["pull"]) == 0
    assert (svc / "b.go").is_file()
    out = capsys.readouterr().out
    assert "tree at" in out and "(was" in out


def test_doc_source_ingests_the_new_file_after_pull(workspace, capsys):
    root, seed, origin, svc = workspace
    (seed / "docs").mkdir()
    (seed / "docs/old.md").write_text("# Old\n\nThe original note.\n")
    commit(seed, "docs: first note", date="2026-01-02")
    subprocess.run(["git", "-C", str(seed), "push", "-q", str(origin), "main"], check=True)
    subprocess.run(["git", "-C", str(svc), "pull", "-q", "--ff-only"], check=True)
    cli.main(["init", "cap"])
    cli.main(["source", "add", "doc", "svc/docs"])
    cli.main(["ingest"])
    (seed / "docs/fresh.md").write_text("# Fresh\n\nA note written after the clone.\n")
    commit(seed, "docs: second note", date="2026-03-01")
    subprocess.run(["git", "-C", str(seed), "push", "-q", str(origin), "main"], check=True)
    assert not (svc / "docs/fresh.md").exists()
    capsys.readouterr()
    assert cli.main(["pull"]) == 0
    assert (svc / "docs/fresh.md").is_file()
    assert any("fresh.md" in str(r.id) for e in Store(root).load_events() for r in e.refs)


def test_dirty_tree_is_left_alone_and_warns_with_the_lag(workspace, capsys):
    root, seed, origin, svc = workspace
    cli.main(["init", "cap"])
    (svc / "a.go").write_text("package a // uncommitted local edit\n")
    push_new_commit(seed, origin)
    push_new_commit(seed, origin, "fix: another one")
    capsys.readouterr()
    assert cli.main(["pull"]) == 0            # not a failed run
    captured = capsys.readouterr()
    assert "TREE NOT ADVANCED (uncommitted changes)" in captured.out
    assert "2 behind" in captured.out
    assert "stale revision" in captured.out
    assert "working tree not advanced" in captured.err
    assert not (svc / "b.go").exists()        # nothing was clobbered
    assert "uncommitted local edit" in (svc / "a.go").read_text()


def test_detached_head_is_left_alone_and_warns(workspace, capsys):
    root, seed, origin, svc = workspace
    cli.main(["init", "cap"])
    git(svc, "checkout", "-q", "--detach", "HEAD")
    push_new_commit(seed, origin)
    capsys.readouterr()
    assert cli.main(["pull"]) == 0
    assert "TREE NOT ADVANCED (detached HEAD)" in capsys.readouterr().out
    assert not (svc / "b.go").exists()


def test_stale_tree_ingests_the_old_document(workspace, capsys):
    root, seed, origin, svc = workspace
    (seed / "docs").mkdir()
    (seed / "docs/note.md").write_text("# Note\n\nThe original wording.\n")
    commit(seed, "docs: note", date="2026-01-02")
    subprocess.run(["git", "-C", str(seed), "push", "-q", str(origin), "main"], check=True)
    subprocess.run(["git", "-C", str(svc), "pull", "-q", "--ff-only"], check=True)
    cli.main(["init", "cap"])
    cli.main(["source", "add", "doc", "svc/docs"])
    cli.main(["ingest"])
    (svc / "local.txt").write_text("uncommitted\n")          # blocks the ff
    (seed / "docs/note.md").write_text("# Note\n\nThe REWRITTEN wording.\n")
    commit(seed, "docs: rewrite the note", date="2026-03-01")
    subprocess.run(["git", "-C", str(seed), "push", "-q", str(origin), "main"], check=True)
    capsys.readouterr()
    assert cli.main(["pull"]) == 0
    assert "TREE NOT ADVANCED" in capsys.readouterr().out
    # the point of the warning: this is what the captain just re-ingested
    assert "original wording" in (svc / "docs/note.md").read_text()


def test_one_broken_source_does_not_stop_the_rest(workspace, capsys):
    root, seed, origin, svc = workspace
    broken = root / "broken"
    broken.mkdir()
    git(broken, "init", "-q", "-b", "main")
    commit(broken, "feat: local only", date="2026-01-01")
    git(broken, "remote", "add", "origin", str(root / "does-not-exist.git"))
    cli.main(["init", "cap"])
    cli.main(["source", "add", "git", "broken"])
    push_new_commit(seed, origin)
    capsys.readouterr()
    assert cli.main(["pull"]) == 0          # partial failure is not a failed run
    out = capsys.readouterr().out
    assert "[pull] svc: 1 new commit(s)" in out
    assert "[pull] broken: FAILED" in out


def test_pull_fails_only_when_every_source_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    broken = tmp_path / "broken"
    broken.mkdir()
    git(broken, "init", "-q", "-b", "main")
    commit(broken, "feat: local only", date="2026-01-01")
    git(broken, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))
    cli.main(["init", "cap"])
    assert cli.main(["pull"]) == 1


def test_pull_study_resumes_from_the_watermark(workspace, capsys):
    root, seed, origin, svc = workspace
    cli.main(["init", "cap"])
    cmd = stub_cmd(root)
    assert cli.main(["study", "--claude-cmd", cmd, "--no-index"]) == 0
    mark = Store(root).load_state()["study"]["git:svc"]
    push_new_commit(seed, origin, "fix: a genuinely new thing")
    capsys.readouterr()
    assert cli.main(["pull", "--study", "--claude-cmd", cmd, "--jobs", "2"]) == 0
    out = capsys.readouterr().out
    assert f"resuming from {mark['sha']}" in out
    assert "1 commits" in out
    assert Store(root).load_state()["study"]["git:svc"]["sha"] != mark["sha"]


def test_pull_study_since_overrides_the_watermark(workspace, capsys):
    root, seed, origin, svc = workspace
    cli.main(["init", "cap"])
    cmd = stub_cmd(root)
    cli.main(["study", "--claude-cmd", cmd, "--no-index"])
    capsys.readouterr()
    assert cli.main(["pull", "--study", "--claude-cmd", cmd, "--since", "3y"]) == 0
    assert "3y window, 1 commits" in capsys.readouterr().out
