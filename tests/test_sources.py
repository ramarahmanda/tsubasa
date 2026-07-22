import pytest

from tsubasa import cli
from tsubasa.config import load


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "testcap"])
    return tmp_path


def test_source_add_and_list(repo, capsys):
    (repo / "svc").mkdir()
    assert cli.main(["source", "add", "git", "svc"]) == 0
    cfg = load(repo)
    assert any(s.adapter == "git" and s.path == "svc" for s in cfg.sources)
    # idempotent
    assert cli.main(["source", "add", "git", "svc"]) == 0
    assert sum(1 for s in load(repo).sources if s.adapter == "git" and s.path == "svc") == 1
    cli.main(["source", "list"])
    assert "svc" in capsys.readouterr().out


def test_source_add_rejects_bad_input(repo, capsys):
    assert cli.main(["source", "add", "nope", "."]) == 1        # unknown adapter
    assert cli.main(["source", "add", "git", "missing-dir"]) == 1  # missing path
    err = capsys.readouterr().err
    assert "unknown adapter" in err and "does not exist" in err


def test_source_no_commit_is_gitignored(repo, capsys):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "postmortems").mkdir()
    assert cli.main(["source", "add", "incident", "postmortems", "--no-commit"]) == 0
    assert "/postmortems/" in (repo / ".gitignore").read_text()
    cfg = load(repo)
    src = next(s for s in cfg.sources if s.path == "postmortems")
    assert src.options.get("commit") is False
    capsys.readouterr()
    cli.main(["source", "list"])
    assert "[local-only]" in capsys.readouterr().out
    # doctor flags a local-only source whose files are actually tracked
    (repo / "postmortems/x.md").write_text("# outage")
    subprocess.run(["git", "add", "-f", "postmortems/x.md"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "x"], cwd=repo, check=True)
    assert cli.main(["doctor"]) == 1
    assert "TRACKED local-only" in capsys.readouterr().out


def test_doc_adapter_principles(repo):
    from tsubasa.storage import Store
    (repo / "principles").mkdir()
    (repo / "principles/boring-tech.md").write_text(
        "# Prefer boring technology\n\nWe choose proven, well-understood tools over novel ones "
        "unless the novel tool solves a problem we actually have.\n")
    assert cli.main(["source", "add", "doc", "principles", "--kind", "principle", "--impact", "high"]) == 0
    assert cli.main(["ingest", "doc"]) == 0
    store = Store(repo)
    ent = store.load_entities()["principle-prefer-boring-technology"]
    assert ent.type == "doc"
    assert ent.impact == "high"  # principles score hot
    hot = (repo / ".tsubasa/memory/hot.md").read_text()
    assert "principle-prefer-boring-technology" in hot


def test_config_parse_error_is_friendly(repo, capsys):
    cfg = repo / ".tsubasa/captain.toml"
    cfg.write_text(cfg.read_text() + "\nadapter = broken [\n")
    assert cli.main(["tiers"]) == 1
    assert "not valid TOML" in capsys.readouterr().err
