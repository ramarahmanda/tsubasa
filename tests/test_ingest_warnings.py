"""A broken source must not look like a quiet one during ingest."""

import subprocess
from types import SimpleNamespace

import pytest

from tsubasa import cli


@pytest.fixture()
def captain_with_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap"])
    repo = tmp_path / "svc"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    cli.main(["source", "add", "github", "svc"])
    return tmp_path


def test_missing_gh_is_a_warning_not_an_empty_source(captain_with_repo, monkeypatch, capsys):
    import tsubasa.adapters.github as gh_mod
    monkeypatch.setattr(gh_mod.shutil, "which", lambda _: None)
    assert cli.main(["ingest", "github"]) == 0
    err = capsys.readouterr().err
    assert "warning: [github:svc]" in err
    assert "gh not on PATH" in err


def test_strict_exits_nonzero_on_warnings(captain_with_repo, monkeypatch):
    import tsubasa.adapters.github as gh_mod
    monkeypatch.setattr(gh_mod.shutil, "which", lambda _: None)
    assert cli.main(["ingest", "github", "--strict"]) == 1


def test_gh_failure_surfaces_its_stderr(captain_with_repo, monkeypatch, capsys):
    import tsubasa.adapters.github as gh_mod
    monkeypatch.setattr(gh_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        gh_mod.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="auth token expired"))
    cli.main(["ingest", "github"])
    assert "auth token expired" in capsys.readouterr().err


def test_healthy_empty_source_stays_quiet(captain_with_repo, monkeypatch, capsys):
    import tsubasa.adapters.github as gh_mod
    monkeypatch.setattr(gh_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        gh_mod.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="[]", stderr=""))
    assert cli.main(["ingest", "github"]) == 0
    assert "warning:" not in capsys.readouterr().err
