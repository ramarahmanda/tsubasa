"""`tsubasa init` detects sources and ingests them, so one command yields a
queryable captain."""

import subprocess

from tsubasa import cli, discover
from tsubasa.config import load
from tsubasa.storage import Store

ADR = """\
# Use Postgres for the ledger

Status: accepted
Date: 2026-01-15

We chose Postgres over DynamoDB for the ledger because double-entry bookkeeping
needs multi-row transactions.
"""

POSTMORTEM = """\
# Ledger write amplification outage

Date: 2026-02-03
Severity: sev1

Batch writes fanned out per row and saturated the primary.
"""


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


def make_repo(root, msg="feat: ledger"):
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    git(root, "commit", "--allow-empty", "-q", "-m", msg)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def sources(root):
    return {(s.adapter, s.path) for s in load(root).sources}


def test_single_repo_init_is_queryable_in_one_command(tmp_path, monkeypatch, capsys):
    make_repo(tmp_path)
    write(tmp_path / "docs/adr/0001-postgres-for-ledger.md", ADR)
    write(tmp_path / "docs/postmortem/2026-02-03-write-amp.md", POSTMORTEM)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap"]) == 0
    assert sources(tmp_path) == {("git", "."), ("adr", "docs/adr"), ("incident", "docs/postmortem")}
    entities = Store(tmp_path).load_entities()
    assert "adr-postgres-for-ledger" in entities
    assert any(e.type == "incident" for e in entities.values())
    assert (tmp_path / ".tsubasa/memory/hot.md").is_file()

    capsys.readouterr()
    assert cli.main(["query", "why postgres for the ledger?"]) == 0
    assert "adr-postgres-for-ledger" in capsys.readouterr().out


def test_multi_repo_workspace(tmp_path, monkeypatch):
    for name in ("repo-a", "repo-b"):
        make_repo(tmp_path / name)
        write(tmp_path / name / "docs/adr/0001-postgres-for-ledger.md", ADR)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap"]) == 0
    assert sources(tmp_path) == {
        ("git", "repo-a"), ("git", "repo-b"),
        ("adr", "repo-a/docs/adr"), ("adr", "repo-b/docs/adr"),
    }
    assert "adr-postgres-for-ledger" in Store(tmp_path).load_entities()


def test_submodule_style_repo_is_detected(tmp_path, monkeypatch):
    # submodules and linked worktrees carry a .git FILE, not a directory
    sub = tmp_path / "svc"
    sub.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "--separate-git-dir",
                    str(tmp_path / "gitdir"), "."], cwd=sub, check=True)
    git(sub, "commit", "--allow-empty", "-q", "-m", "feat: ledger")
    assert (sub / ".git").is_file()
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    assert ("git", "svc") in sources(tmp_path)


def test_docs_glob_excludes_adr_subtree(tmp_path, monkeypatch):
    write(tmp_path / "docs/adr/0001-postgres-for-ledger.md", ADR)
    write(tmp_path / "docs/onboarding.md", "# Onboarding\n\nRead the ADRs first.\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    doc = next(s for s in load(tmp_path).sources if s.adapter == "doc")
    assert (doc.path, doc.glob) == ("docs", "*.md")  # ADRs have their own source


def test_docs_glob_is_recursive_without_adrs(tmp_path, monkeypatch):
    write(tmp_path / "docs/onboarding.md", "# Onboarding\n")
    write(tmp_path / "docs/runbooks/deploy.md", "# Deploy\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    doc = next(s for s in load(tmp_path).sources if s.adapter == "doc")
    assert (doc.path, doc.glob) == ("docs", "**/*.md")


def test_no_detect_and_no_ingest(tmp_path, monkeypatch):
    write(tmp_path / "a/docs/adr/0001-postgres-for-ledger.md", ADR)
    write(tmp_path / "b/docs/adr/0001-postgres-for-ledger.md", ADR)

    monkeypatch.chdir(tmp_path / "a")
    assert cli.main(["init", "cap", "--no-detect"]) == 0
    assert load(tmp_path / "a").sources == []
    assert Store(tmp_path / "a").load_entities() == {}

    monkeypatch.chdir(tmp_path / "b")
    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    assert sources(tmp_path / "b") == {("adr", "docs/adr")}
    assert Store(tmp_path / "b").load_entities() == {}


def test_source_add_restates_autodetected_entry(tmp_path, monkeypatch, capsys):
    make_repo(tmp_path / "svc")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    assert sources(tmp_path) == {("git", "svc")}
    # detection writes no options, so an explicit add must replace, not skip
    assert cli.main(["source", "add", "git", "svc", "--pull"]) == 0
    cfg = load(tmp_path)
    assert [s.path for s in cfg.sources] == ["svc"]
    assert cfg.sources[0].options.get("pull") is True

    capsys.readouterr()
    assert cli.main(["source", "add", "git", "svc", "--pull"]) == 0
    assert "already configured" in capsys.readouterr().out
    assert len(load(tmp_path).sources) == 1


def by_path(root):
    return {s.path: s for s in load(root).sources}


def test_capitalized_dir_names_are_matched(tmp_path, monkeypatch):
    # probing lowercase paths walked straight past etcd's Documentation/
    write(tmp_path / "Documentation/etcd-internals/raft.md", "# Raft\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    doc = by_path(tmp_path)["Documentation"]
    assert (doc.adapter, doc.glob) == ("doc", "**/*.md")


def test_keps_are_decisions(tmp_path, monkeypatch):
    write(tmp_path / "keps/sig-node/1234-cpu-manager/README.md", "# CPU manager\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    assert by_path(tmp_path)["keps"].adapter == "adr"


def test_nested_postmortems_beat_a_capitalized_ancestor(tmp_path, monkeypatch):
    write(tmp_path / "Documentation/postmortems/2020-08-data-loss.md", POSTMORTEM)
    write(tmp_path / "Documentation/README.md", "# Docs\n")
    write(tmp_path / "Documentation/dev-guide/api.md", "# API\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    got = by_path(tmp_path)
    assert got["Documentation/postmortems"].adapter == "incident"
    # the ancestor keeps only its own level, so nothing is ingested twice…
    assert (got["Documentation"].adapter, got["Documentation"].glob) == ("doc", "*.md")
    # …and the subtree the child did not claim still gets a source of its own
    assert got["Documentation/dev-guide"].glob == "**/*.md"


def test_extensionless_readmes_are_detected(tmp_path, monkeypatch):
    for name in ("heap", "nbtree", "gin", "gist", "brin", "spgist"):
        write(tmp_path / f"src/backend/access/{name}/README", f"# {name}\n\nDesign notes.\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    src = by_path(tmp_path)["src"]
    assert (src.adapter, src.glob) == ("doc", "**/README")


def test_contribute_is_a_high_impact_principle(tmp_path, monkeypatch):
    write(tmp_path / "contribute/development_environment.md", "# Dev env\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    src = by_path(tmp_path)["contribute"]
    assert (src.adapter, src.options.get("kind"), src.options.get("impact")) == ("doc", "principle", "high")


def test_source_detect_prints_without_writing(tmp_path, monkeypatch, capsys):
    write(tmp_path / "docs/adr/0001-postgres-for-ledger.md", ADR)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap", "--no-detect"]) == 0
    capsys.readouterr()

    assert cli.main(["source", "detect"]) == 0
    out = capsys.readouterr().out
    assert "docs/adr" in out and "read-only" in out
    assert load(tmp_path).sources == []


def test_source_detect_add_is_idempotent(tmp_path, monkeypatch, capsys):
    write(tmp_path / "docs/adr/0001-postgres-for-ledger.md", ADR)
    write(tmp_path / "docs/postmortem/2026-02-03-write-amp.md", POSTMORTEM)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap", "--no-detect"]) == 0

    assert cli.main(["source", "detect", "--add"]) == 0
    first = sources(tmp_path)
    assert ("adr", "docs/adr") in first and ("incident", "docs/postmortem") in first

    capsys.readouterr()
    assert cli.main(["source", "detect", "--add"]) == 0
    assert capsys.readouterr().out.count("already configured") == len(first)
    assert sources(tmp_path) == first


def test_per_repo_cap_reports_what_it_dropped(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(discover, "MAX_SOURCES", 2)
    for name, n in (("adr", 3), ("postmortems", 2), ("guidelines", 1)):
        for i in range(n):
            write(tmp_path / f"{name}/{i}.md", f"# {name} {i}\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "cap", "--no-ingest"]) == 0
    out = capsys.readouterr().out
    assert "dropped (per-repo cap): doc guidelines" in out  # smallest loses
    assert {"adr", "postmortems"} == {s.path for s in load(tmp_path).sources}


def test_empty_dir_still_initializes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap"]) == 0
    assert load(tmp_path).sources == []          # parses, just has nothing to ingest
    assert "no knowledge sources found" in capsys.readouterr().out
