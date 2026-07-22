"""End-to-end tests over a scaffolded captain in a temp repo."""

import subprocess
from pathlib import Path

import pytest

from tsubasa import cli
from tsubasa.config import load
from tsubasa.storage import Store


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "testcap", "--domains", "auth,payments"]) == 0
    return tmp_path


def run(args):
    return cli.main(args)


def test_init_creates_layout(repo):
    assert (repo / ".tsubasa/captain.toml").is_file()
    assert "hot.md" in (repo / "CLAUDE.md").read_text()
    cfg = load(repo)
    assert cfg.name == "testcap"
    assert cfg.hot_max_context == 0.25
    assert cfg.domains == {"auth": 1.0, "payments": 1.0}


def test_event_and_query(repo, capsys):
    assert run([
        "event", "add", "--type", "incident", "--title", "Session store replication lag",
        "--impact", "high", "--domains", "auth", "--ts", "2026-07-01",
        "--entity", "svc-auth-gateway:service:auth-gateway:Authentication gateway service",
        "--relation", "svc-auth-gateway:suffered:evt-20260701-session-store-replication-lag",
        "--ref", "doc:postmortems/session-store.md",
    ]) == 0
    assert run(["query", "why is auth-gateway slow?"]) == 0
    out = capsys.readouterr().out
    assert "svc-auth-gateway" in out
    assert "suffered" in out
    assert "evt-20260701-session-store-replication-lag" in out


def test_task_lifecycle_via_adr_thread(repo, capsys):
    run(["task", "new", "--title", "Session double write", "--adr", "adr-gw-session-double-write",
         "--domains", "auth"])
    # a merged PR event carrying the ADR id moves the task to done with evidence
    run(["event", "add", "--type", "pr_merged", "--title", "auth-gateway PR-1042: session double write",
         "--ts", "2026-07-14", "--ref", "pr:PR-1042", "--ref", "adr:adr-gw-session-double-write"])
    store = Store(repo)
    task = store.load_tasks()["task-session-double-write"]
    assert task.state == "done"
    assert task.prs == ["PR-1042"]
    assert any("PR-1042" in h.evidence for h in task.history)


def test_reconcile_supersede(repo, capsys):
    run(["event", "add", "--type", "adr", "--title", "Use Kafka for checkout",
         "--ts", "2025-11-01", "--entity", "adr-checkout-kafka:adr:Use Kafka for checkout"])
    run(["event", "add", "--type", "decision", "--title", "Drop Kafka for SQS",
         "--ts", "2026-07-10", "--entity", "adr-checkout-sqs:adr:Use SQS for checkout",
         "--supersedes", "adr-checkout-kafka"])
    store = Store(repo)
    entities = store.load_entities()
    old = entities["adr-checkout-kafka"]
    assert old.status == "superseded"
    assert old.superseded_by == "adr-checkout-sqs"
    rels = {r.key() for r in store.load_relations()}
    assert ("adr-checkout-sqs", "supersedes", "adr-checkout-kafka") in rels


def test_reconcile_low_trust_dispute(repo, capsys):
    run(["event", "add", "--type", "adr", "--title", "Use Kafka",
         "--ts", "2025-11-01", "--entity", "adr-kafka:adr:Use Kafka"])
    run(["event", "add", "--type", "note", "--title", "Someone said kafka is gone",
         "--ts", "2026-07-10", "--trust", "low", "--supersedes", "adr-kafka"])
    store = Store(repo)
    assert store.load_entities()["adr-kafka"].status == "active"  # kept
    capsys.readouterr()
    run(["questions"])
    assert "disputed" in capsys.readouterr().out


def test_rebuild_reproduces_graph(repo):
    run(["event", "add", "--type", "incident", "--title", "Outage A", "--impact", "high",
         "--ts", "2026-06-01", "--entity", "svc-a:service:service-a:Service A"])
    store = Store(repo)
    before = (store.graph_dir / "entities.toon").read_text()
    run(["rebuild"])
    assert (store.graph_dir / "entities.toon").read_text() == before


def test_hot_memory_generation(repo):
    run(["event", "add", "--type", "incident", "--title", "Big outage", "--impact", "high",
         "--domains", "auth", "--entity", "svc-b:service:service-b:Critical service"])
    hot = (repo / ".tsubasa/memory/hot.md").read_text()
    assert "svc-b" in hot
    index = (repo / ".tsubasa/memory/index.md").read_text()
    assert "svc-b" in index


def test_adr_adapter(repo):
    adr_dir = repo / "docs/adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-use-postgres.md").write_text(
        "# Use Postgres\n\nStatus: accepted\nDate: 2026-01-15\n\n"
        "We choose Postgres over MySQL because of the existing operational experience "
        "in the team and better JSONB support.\n"
    )
    assert run(["ingest", "adr"]) == 0
    store = Store(repo)
    entities = store.load_entities()
    assert "adr-use-postgres" in entities
    assert entities["adr-use-postgres"].type == "adr"
    # re-ingest is idempotent (fresh Store per check: the events cache is
    # per-instance, and the CLI builds a new Store every command)
    n_events = len(Store(repo).load_events())
    run(["ingest", "adr"])
    assert len(Store(repo).load_events()) == n_events
    # a touched mtime (file moved / re-checked-out) must NOT mint a new event
    import os, time
    os.utime(adr_dir / "0001-use-postgres.md", (time.time(), time.time()))
    run(["ingest", "adr"])
    assert len(Store(repo).load_events()) == n_events
    # a content edit legitimately does
    (adr_dir / "0001-use-postgres.md").write_text(
        (adr_dir / "0001-use-postgres.md").read_text() + "\nStatus: superseded\n")
    run(["ingest", "adr"])
    assert len(Store(repo).load_events()) == n_events + 1


def test_git_adapter_adr_commits(repo):
    sub = repo / "svc"
    sub.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=sub, check=True)
    (sub / "db.py").write_text("engine = 'postgres'\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "db.py"], cwd=sub, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
                    "-q", "-m", "feat: adr-use-postgres migration"], cwd=sub, check=True)
    cfg_path = repo / ".tsubasa/captain.toml"
    cfg_path.write_text(cfg_path.read_text() + '\n[[sources]]\nadapter = "git"\npath = "svc"\n')
    run(["task", "new", "--title", "Postgres migration", "--adr", "adr-use-postgres"])
    assert run(["ingest", "git"]) == 0
    store = Store(repo)
    assert store.load_tasks()["task-postgres-migration"].state == "in_progress"
    ev = next(e for e in store.load_events() if e.type == "pr_merged")
    # the commit's changed files are cited on the event as file refs, so the
    # graph knows what actually changed, not just which ADR the commit names
    assert any(r.kind == "file" and r.id == "db.py" for r in ev.refs)


def test_doctor_flags_secrets(repo, capsys):
    run(["event", "add", "--type", "note", "--title", "Deploy note",
         "--body", "api_key = sk-live-abcdef1234567890"])
    code = run(["doctor"])
    out = capsys.readouterr().out
    assert code == 1
    assert "SECRET" in out


def test_loose_relation_endpoints_become_key_facts(repo):
    run(["event", "add", "--type", "note", "--title", "logging setup", "--ts", "2026-07-01",
         "--entity", "svc-gw:service:gw:Gateway",
         "--relation", "svc-gw:exports_logs_to:GELF",
         "--relation", "svc-gw:depends_on:svc-db"])
    store = Store(repo)
    keys = {r.key() for r in store.load_relations()}
    assert ("svc-gw", "depends_on", "svc-db") in keys        # id-like target kept
    assert ("svc-gw", "exports_logs_to", "GELF") not in keys  # loose target demoted
    assert any("exports_logs_to: GELF" in f for f in store.load_entities()["svc-gw"].key_facts)
