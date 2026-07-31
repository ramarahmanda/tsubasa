"""`tsubasa init` writes a map of where knowledge lives, with no model and no
`study`, and one optional LLM pass may amend the classification. Everything
here is offline: the pass is stubbed, never invoked."""

import json
import subprocess

import pytest

from tsubasa import cli, llm, topology
from tsubasa.config import load
from tsubasa.storage import Store

ADR = "# Use Postgres for the ledger\n\nStatus: accepted\nDate: 2026-01-15\n\nMulti-row transactions.\n"
POSTMORTEM = "# Write amplification outage\n\nDate: 2026-02-03\nSeverity: sev1\n\nBatch writes fanned out.\n"


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def workspace(root):
    """One repo with decisions, postmortems and a generated-looking appendix."""
    repo = root / "svc"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    git(repo, "remote", "add", "origin", "https://example.invalid/svc.git")
    write(repo / "docs/adr/0001-postgres-for-ledger.md", ADR)
    write(repo / "docs/postmortem/2026-02-03-write-amp.md", POSTMORTEM)
    write(repo / "docs/api-gen/crd.md", "# Generated\n")
    write(repo / "docs/api-gen/crd2.md", "# Generated 2\n")
    write(repo / "docs/api-gen/crd3.md", "# Generated 3\n")
    for i in range(5):
        write(repo / f"ledger{i}.go", "package main\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feat: ledger")
    return repo


def facts(entity):
    return {f.split(":", 1)[0]: f.split(":", 1)[1].strip() for f in entity.key_facts}


# ------------------------------------------------------------------ the map

def test_init_maps_repos_and_corpora_without_a_model(tmp_path, monkeypatch):
    workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap", "--no-llm"]) == 0

    entities = Store(tmp_path).load_entities()
    repo = entities["svc-svc"]
    assert repo.type == "service"
    assert facts(repo)["remote"] == "https://example.invalid/svc.git"
    assert facts(repo)["default branch"] == "main"
    assert facts(repo)["primary language"] == "Go"
    assert any(f.startswith("history: 1 commits") for f in repo.key_facts)

    corpus = entities["corpus-svc-docs-adr"]
    assert corpus.type == "doc"
    got = facts(corpus)
    assert got["adapter"] == "adr"
    assert got["glob"] == "svc/docs/adr/**/*.md"
    assert got["files"] == "1"
    assert "Use Postgres for the ledger" in got["samples"]
    assert any(f.startswith("changed: 2") for f in corpus.key_facts)  # git date range

    rels = {(r.source, r.predicate, r.target) for r in Store(tmp_path).load_relations()}
    assert ("svc-svc", "documented_in", "corpus-svc-docs-adr") in rels
    assert ("svc-svc", "documented_in", "corpus-svc-docs-postmortem") in rels


def test_map_survives_rebuild(tmp_path, monkeypatch, capsys):
    workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap", "--no-llm"]) == 0
    before = Store(tmp_path).load_entities()
    before_rels = {r.key() for r in Store(tmp_path).load_relations()}

    capsys.readouterr()
    assert cli.main(["rebuild"]) == 0
    after = Store(tmp_path).load_entities()
    assert {e.id for e in before.values()} == {e.id for e in after.values()}
    assert after["corpus-svc-docs-adr"].key_facts == before["corpus-svc-docs-adr"].key_facts
    assert {r.key() for r in Store(tmp_path).load_relations()} == before_rels


def test_map_is_one_note_event_not_a_second_write_path(tmp_path, monkeypatch):
    workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap", "--no-llm"]) == 0
    maps = [e for e in Store(tmp_path).load_events() if e.source == "init"]
    assert len(maps) == 1
    assert maps[0].type == "note" and maps[0].trust == "high"
    assert maps[0].derived_entities and maps[0].derived_relations


def test_shared_corpus_aliases_are_dropped_not_queued_as_questions(tmp_path, monkeypatch):
    for name in ("a", "b"):
        write(tmp_path / f"{name}/docs/guide.md", "# Guide\n")
        write(tmp_path / f"{name}/docs/more.md", "# More\n")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap", "--no-llm"]) == 0
    qs = (tmp_path / ".tsubasa/questions.toon")
    assert not qs.exists() or "claimed by both" not in qs.read_text()


# ------------------------------------------------------------------ llm pass

VERDICT = {
    "sources": [
        {"path": "svc/docs/api-gen", "adapter": "drop", "why": "generated CRD reference"},
        {"path": "svc/docs/adr", "adapter": "adr", "kind": "decision", "impact": "high"},
    ],
    "repos": [{"name": "svc", "description": "Ledger service, Postgres-backed"}],
    "externals": [{"id": "ext-postgres", "name": "PostgreSQL", "description": "RDBMS"}],
    "topology": [{"source": "svc-svc", "predicate": "depends_on", "target": "ext-postgres"}],
}


def stub(monkeypatch, payload, calls=None):
    monkeypatch.setattr(llm, "claude_available", lambda *a, **k: True)

    def run(prompt, **kw):
        if calls is not None:
            calls.append((prompt, kw))
        return payload
    monkeypatch.setattr(llm, "run_claude", run)


def test_llm_pass_reclassifies_drops_and_wires_topology(tmp_path, monkeypatch, topology_pass):
    workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []
    stub(monkeypatch, json.dumps(VERDICT), calls)

    assert cli.main(["init", "cap"]) == 0
    assert len(calls) == 1                      # one call for the whole workspace
    assert calls[0][1]["model"] == topology.MODEL

    paths = {s.path for s in load(tmp_path).sources}
    assert "svc/docs/api-gen" not in paths      # dropped as generated
    adr = next(s for s in load(tmp_path).sources if s.path == "svc/docs/adr")
    assert (adr.options.get("kind"), adr.options.get("impact")) == ("decision", "high")

    entities = Store(tmp_path).load_entities()
    assert entities["svc-svc"].description == "Ledger service, Postgres-backed"
    assert entities["ext-postgres"].type == "external"
    rels = {(r.source, r.predicate, r.target) for r in Store(tmp_path).load_relations()}
    assert ("svc-svc", "depends_on", "ext-postgres") in rels

    topo = [e for e in Store(tmp_path).load_events() if e.source == "init-llm"]
    assert len(topo) == 1 and topo[0].trust == "low"  # proposal, not fact


def test_config_header_records_the_proposal(tmp_path, monkeypatch, topology_pass):
    workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    stub(monkeypatch, json.dumps(VERDICT))
    assert cli.main(["init", "cap", "--model", "haiku"]) == 0
    text = (tmp_path / ".tsubasa/captain.toml").read_text()
    assert "MACHINE-PROPOSED SOURCES" in text and "claude (haiku)" in text

    other = tmp_path / "off"
    workspace(other)
    monkeypatch.chdir(other)
    assert cli.main(["init", "cap", "--no-llm"]) == 0
    assert "offline heuristics, no model" in (other / ".tsubasa/captain.toml").read_text()


@pytest.mark.parametrize("payload", ["not json at all", "{}", '{"sources": "nope"}',
                                     '[{"path": "x"}]'])
def test_unusable_output_falls_back_to_the_offline_plan(tmp_path, monkeypatch, capsys,
                                                        topology_pass, payload):
    workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    stub(monkeypatch, payload)
    assert cli.main(["init", "cap"]) == 0
    assert "keeping the offline classification" in capsys.readouterr().out
    assert "svc/docs/api-gen" in {s.path for s in load(tmp_path).sources}  # offline kept it


def test_llm_failure_never_fails_init(tmp_path, monkeypatch, capsys, topology_pass):
    workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm, "claude_available", lambda *a, **k: True)

    def boom(*a, **k):
        raise llm.LLMError("timed out after 240s")
    monkeypatch.setattr(llm, "run_claude", boom)

    assert cli.main(["init", "cap"]) == 0
    out = capsys.readouterr().out
    assert "timed out" in out and "keeping the offline classification" in out
    assert "corpus-svc-docs-adr" in Store(tmp_path).load_entities()  # map still written


def test_missing_claude_binary_falls_back(tmp_path, monkeypatch, capsys, topology_pass):
    workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm, "claude_available", lambda *a, **k: False)
    assert cli.main(["init", "cap", "--claude-cmd", "nope"]) == 0
    assert "'nope' not found" in capsys.readouterr().out


def test_no_llm_skips_the_call(tmp_path, monkeypatch, topology_pass):
    workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []
    stub(monkeypatch, json.dumps(VERDICT), calls)
    assert cli.main(["init", "cap", "--no-llm"]) == 0
    assert calls == []


# ------------------------------------------------------------------ validation

def _validate(data, root):
    candidates = [{"path": "svc/docs/api-gen", "repo": "svc", "bucket": "knowledge",
                   "files": 3, "extensions": {}, "samples": []}]
    planned = [{"path": "svc/docs/adr", "repo": "svc", "adapter": "adr", "glob": "**/*.md",
                "files": 1, "bucket": "knowledge"}]
    return topology._validate(data, candidates, planned, ["svc"], "haiku")


def test_validation_rejects_ungrounded_output(tmp_path):
    assert _validate({"sources": [{"path": "../../etc", "adapter": "doc"}]}, tmp_path) is None
    assert _validate({"sources": [{"path": "svc/docs/adr", "adapter": "shell"}]}, tmp_path) is None
    assert _validate({"topology": [{"source": "svc-svc", "predicate": "pwns",
                                    "target": "svc-ghost"}]}, tmp_path) is None
    assert _validate({"topology": [{"source": "svc-svc", "predicate": "depends_on",
                                    "target": "svc-ghost"}]}, tmp_path) is None
    assert _validate({"repos": [{"name": "ghost", "description": "x"}]}, tmp_path) is None


def test_validation_keeps_grounded_output(tmp_path):
    got = _validate({"sources": [{"path": "svc/docs/adr", "adapter": "doc",
                                  "kind": "Design Notes", "impact": "sky-high",
                                  "glob": "../*"}]}, tmp_path)
    assert got["sources"] == [{"path": "svc/docs/adr", "adapter": "doc", "kind": "design-notes"}]
