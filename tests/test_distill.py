"""Density passes (study/resolve/profile) with a stubbed claude CLI, and temporal queries."""

import os
import stat
import subprocess

import pytest

from tsubasa import cli
from tsubasa.storage import Store


def make_stub(tmp_path, payload: str) -> str:
    stub = tmp_path / "claude-stub"
    stub.write_text(f"#!/bin/sh\ncat <<'EOF'\n{payload}\nEOF\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return str(stub)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--domains", "auth"])
    svc = tmp_path / "svc"
    svc.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=svc, check=True)
    for msg in ("feat: add login", "fix: retry storm on token refresh"):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "--allow-empty", "-q", "-m", msg], cwd=svc, check=True)
    cli.main(["source", "add", "git", "svc"])
    return tmp_path


STUDY_JSON = """[{"title": "Token refresh retry storm fixed", "summary": "Retries hammered the IdP; capped with backoff.", "type": "incident", "impact": "high", "domains": ["auth"], "date": "2026-01-05", "commits": ["abc123"], "entities": [{"id": "svc-svc", "type": "service", "name": "svc", "description": "Auth service"}], "relations": [{"source": "svc-svc", "predicate": "suffered", "target": "evt-x"}]}]"""


def test_study_writes_events(repo, tmp_path, capsys):
    stub = make_stub(tmp_path, STUDY_JSON)
    assert cli.main(["study", "--claude-cmd", stub]) == 0
    store = Store(repo)
    events = [e for e in store.load_events() if e.source == "study"]
    assert len(events) == 1
    assert events[0].impact == "high"
    assert "svc-svc" in store.load_entities()
    # idempotent: same distilled event id is not duplicated
    cli.main(["study", "--claude-cmd", stub])
    assert len([e for e in Store(repo).load_events() if e.source == "study"]) == 1


def test_study_attaches_file_refs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--domains", "auth"])
    svc = tmp_path / "svc"
    svc.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=svc, check=True)
    (svc / "pool.go").write_text("var poolSize = 10\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "pool.go"], cwd=svc, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
                    "-q", "-m", "fix: connection pool exhaustion"], cwd=svc, check=True)
    sha = subprocess.run(["git", "-C", str(svc), "log", "--format=%h"],
                         capture_output=True, text=True, check=True).stdout.strip()
    cli.main(["source", "add", "git", "svc"])
    study_json = (
        '[{"title": "Connection pool exhaustion fixed", '
        '"summary": "Pool ran out under load; size increased.", '
        '"type": "incident", "impact": "high", "domains": ["auth"], "date": "2026-01-05", '
        f'"commits": ["{sha}"], '
        '"entities": [{"id": "svc-svc", "type": "service", "name": "svc", "description": "Auth service"}], '
        '"relations": []}]'
    )
    stub = make_stub(tmp_path, study_json)
    assert cli.main(["study", "--claude-cmd", stub]) == 0
    ev = next(e for e in Store(tmp_path).load_events() if e.source == "study")
    assert any(r.kind == "file" and r.id == "pool.go" for r in ev.refs)


STUDY_DOC_JSON = """{"title": "Third-party integrations", "summary": "The app sends events to a metrics vendor.", "domains": ["auth"], "entities": [{"id": "ext-metrics-vendor", "type": "external", "name": "Metrics Vendor", "description": "Event pipeline used for product analytics."}], "relations": [{"source": "svc-svc", "predicate": "sends_events_to", "target": "ext-metrics-vendor"}]}"""


def test_study_extracts_entities_from_doc_prose(repo, tmp_path):
    (repo / "docs").mkdir()
    (repo / "docs/integrations.md").write_text(
        "# Third-party integrations\n\nThe app sends events to a metrics vendor.\n")
    (repo / "docs/schema.toon").write_text("table: orders\ncolumns[0]:\n")
    cli.main(["source", "add", "doc", "docs"])
    stub = make_stub(tmp_path, STUDY_DOC_JSON)
    assert cli.main(["study", "--claude-cmd", stub]) == 0
    store = Store(repo)
    doc_events = [e for e in store.load_events() if e.source == "study" and e.trust == "low"]
    # only the .md file is sent through study; the .toon file is left to
    # DocAdapter's deterministic structured parse (see adapters/docs.py)
    assert len(doc_events) == 1
    assert doc_events[0].refs[0].kind == "doc"
    assert doc_events[0].refs[0].id == "docs/integrations.md"
    ent = store.load_entities()["ext-metrics-vendor"]
    assert ent.type == "external"
    assert ent.name == "Metrics Vendor"
    # idempotent: rerunning study doesn't duplicate the event
    cli.main(["study", "--claude-cmd", stub])
    assert len([e for e in Store(repo).load_events() if e.source == "study" and e.trust == "low"]) == 1


def test_resolve_merges_duplicates(repo, tmp_path):
    cli.main(["event", "add", "--type", "note", "--title", "a", "--ts", "2026-01-01",
              "--entity", "svc-gateway:service:gateway:The API gateway"])
    cli.main(["event", "add", "--type", "note", "--title", "b", "--ts", "2026-01-02",
              "--entity", "svc-api-gateway:service:api-gateway:The API gateway service",
              "--relation", "svc-api-gateway:depends_on:svc-gateway"])
    stub = make_stub(tmp_path, '[{"canonical": "svc-gateway", "duplicates": ["svc-api-gateway"]}]')
    assert cli.main(["resolve", "--claude-cmd", stub]) == 0
    store = Store(repo)
    entities = store.load_entities()
    assert "svc-api-gateway" not in entities
    assert "svc-api-gateway" in entities["svc-gateway"].aliases
    # self-relations created by the merge are dropped
    assert all(r.source != r.target for r in store.load_relations())


def test_profile_overlays_and_survives_rebuild(repo, tmp_path):
    for i in range(3):
        cli.main(["event", "add", "--type", "note", "--title", f"evt {i}", "--ts", f"2026-01-0{i+1}",
                  "--entity", "svc-core:service:core:Core service",
                  "--relation", f"svc-core:did_{i}:thing-{i}"])
    stub = make_stub(tmp_path, '{"summary": "Core service, heart of the platform.", "key_facts": ["fact one"]}')
    assert cli.main(["profile", "--claude-cmd", stub, "--top", "3"]) == 0
    store = Store(repo)
    assert store.load_entities()["svc-core"].summary.startswith("Core service")
    cli.main(["rebuild"])
    assert Store(repo).load_entities()["svc-core"].summary.startswith("Core service")


def test_temporal_query_as_of(repo, capsys):
    cli.main(["event", "add", "--type", "adr", "--title", "Use Kafka", "--ts", "2026-01-01",
              "--entity", "adr-kafka:adr:Use Kafka:Kafka for checkout queue"])
    cli.main(["event", "add", "--type", "decision", "--title", "Drop Kafka for SQS", "--ts", "2026-06-01",
              "--entity", "adr-sqs:adr:Use SQS:SQS replaces Kafka", "--supersedes", "adr-kafka"])
    capsys.readouterr()
    cli.main(["query", "kafka", "--as-of", "2026-03-01"])
    then = capsys.readouterr().out
    assert "adr-kafka" in then and "SUPERSEDED" not in then and "adr-sqs" not in then
    cli.main(["query", "kafka"])
    now = capsys.readouterr().out
    assert "SUPERSEDED" in now


def test_index_code_only_deterministic(repo):
    from tsubasa.distill import graphify_python
    if graphify_python(log=lambda *a: None) is None:
        pytest.skip("graphify engine not installed")
    # a code file AND a doc file: code-only indexing must graph the code
    # and never touch the doc
    (repo / "svc/app.py").write_text("def hello():\n    return world()\n\ndef world():\n    return 42\n")
    (repo / "svc/notes.md").write_text("# secret notes\nuser data here\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=repo / "svc")
    subprocess.run(["git", "init", "-q"], cwd=repo)  # captain root is a git repo
    assert cli.main(["index"]) == 0
    # committed artifact: TOON at the captain root; submodule stays pristine
    toon_path = repo / ".tsubasa/code-index/svc/graph.toon"
    assert toon_path.is_file()
    assert not (repo / "svc/graphify-out").exists()
    content = toon_path.read_text()
    assert "hello" in content or "app" in content   # code graphed
    assert "secret notes" not in content            # doc content excluded
    # runtime json is gitignored and reconstructable
    assert (repo / ".tsubasa/code-index/svc/graph.json").is_file()
    assert "code-index/**/graph.json" in (repo / ".gitignore").read_text()
    from tsubasa import codegraph
    (repo / ".tsubasa/code-index/svc/graph.json").unlink()
    assert codegraph.ensure_json(repo, "svc").is_file()
