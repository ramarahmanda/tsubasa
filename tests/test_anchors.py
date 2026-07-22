"""Anchor layer: entity<->code-node links and repo<->repo edges via graphify indexes."""

import json
import subprocess

import pytest

from tsubasa import cli
from tsubasa.storage import Store

GATEWAY_GRAPH = {
    "nodes": [
        {"id": "n1", "name": "FastLoginValidator", "type": "class", "file": "src/auth/FastLoginValidator.java"},
        {"id": "n2", "name": "IdentraClient", "type": "class", "file": "src/idp/IdentraClient.java"},
        {"id": "n3", "name": "TokenResource", "type": "class"},
    ],
    "edges": [{"source": "n3", "target": "n1", "label": "calls"}],
}
SUPPORT_GRAPH = {
    "nodes": [
        {"id": "m1", "name": "IdentraMirror", "type": "class"},
        {"id": "m2", "name": "RegStepHandler", "type": "class"},
    ],
    "edges": [],
}


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap"])
    for name, graph in (("gateway", GATEWAY_GRAPH), ("support", SUPPORT_GRAPH)):
        d = tmp_path / name
        (d / "graphify-out").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        (d / "graphify-out/graph.json").write_text(json.dumps(graph))
        cli.main(["source", "add", "git", name])
    # fleet entities: the two repo services + a feature + the external target service
    cli.main(["event", "add", "--type", "note", "--title", "seed", "--ts", "2026-07-01",
              "--entity", "svc-gateway:service:gateway:Identity gateway",
              "--entity", "svc-support:service:support:Registration support service",
              "--entity", "svc-identra:service:Identra:Target IdP",
              "--entity", "feat-fast-login:feature:FastLoginValidator:Fast login path"])
    return tmp_path


def test_seed_creates_all_three_link_kinds(repo, capsys):
    assert cli.main(["link"]) == 0
    anchors = Store(repo).load_anchors()
    kinds = {(a["entity"], a["repo"], a["node"], a["by"]) for a in anchors}
    # 1. repo-level anchors
    assert ("svc-gateway", "gateway", "*", "seed") in kinds
    assert ("svc-support", "support", "*", "seed") in kinds
    # 2. name anchor: feature alias matches a code node
    assert ("feat-fast-login", "gateway", "FastLoginValidator", "seed") in kinds
    # 3. cross-repo: Identra referenced in both repos' code
    assert any(a["entity"] == "svc-identra" and a["by"] == "xrepo" and a["repo"] == "gateway" for a in anchors)
    assert any(a["entity"] == "svc-identra" and a["by"] == "xrepo" and a["repo"] == "support" for a in anchors)


def test_query_surfaces_anchors_and_xrepo_edges(repo, capsys):
    cli.main(["link"])
    capsys.readouterr()
    cli.main(["query", "identra"])
    out = capsys.readouterr().out
    assert "Anchors (memory <-> code)" in out
    assert "(svc-gateway) --[references_in_code]--> (svc-identra)" in out
    assert "(svc-support) --[references_in_code]--> (svc-identra)" in out
    capsys.readouterr()
    cli.main(["query", "fast login FastLoginValidator"])
    out = capsys.readouterr().out
    assert "feat-fast-login <=> FastLoginValidator" in out
    assert "Code anatomy (graphify)" in out
    assert "calls" in out  # TokenResource --calls--> FastLoginValidator


def test_link_idempotent(repo):
    cli.main(["link"])
    n = len(Store(repo).load_anchors())
    cli.main(["link"])
    assert len(Store(repo).load_anchors()) == n


def test_link_llm_semantic_pass(repo, tmp_path):
    import stat
    payload = '[{"entity": "feat-fast-login", "nodes": ["TokenResource"]}, {"entity": "feat-fast-login", "nodes": ["InventedNode"]}]'
    stub = tmp_path / "claude-link-stub"
    stub.write_text(f"#!/bin/sh\ncat <<'EOF'\n{payload}\nEOF\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    assert cli.main(["link", "--llm", "--claude-cmd", str(stub)]) == 0
    anchors = Store(repo).load_anchors()
    kinds = {(a["entity"], a["node"], a["by"]) for a in anchors}
    assert ("feat-fast-login", "TokenResource", "link") in kinds   # verbatim node accepted
    assert not any(a["node"] == "InventedNode" for a in anchors)   # invented node rejected
