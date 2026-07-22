"""Code snapshot adapter: structure from compose/k8s/build files, rebuilt per ingest."""

import subprocess

import pytest

from tsubasa import cli
from tsubasa.storage import Store

COMPOSE = """\
services:
  gateway:
    image: acme/gateway:1.2
    depends_on: [authdb]
    environment:
      DB_PASSWORD: changeme123456
      LOG_LEVEL: info
  authdb:
    image: postgres:16
"""

K8S = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway
  namespace: staging
spec:
  template:
    spec:
      containers:
        - name: gateway
          env:
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: gateway-db-creds
                  key: password
"""


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap"])
    svc = tmp_path / "gateway"
    svc.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=svc, check=True)
    (svc / "docker-compose.yml").write_text(COMPOSE)
    (svc / "deploy.yaml").write_text(K8S)
    cli.main(["source", "add", "code", "gateway"])
    return tmp_path


def test_snapshot_extracts_structure_and_secret_refs(repo):
    assert cli.main(["ingest", "code"]) == 0
    store = Store(repo)
    entities, relations = store.load_code_graph()
    assert entities["svc-gateway"].type == "service"
    assert entities["secret-db-password"].type == "secret-ref"
    assert entities["secret-gateway-db-creds"].type == "secret-ref"
    assert entities["env-staging"].type == "env"
    keys = {r.key() for r in relations}
    assert ("svc-gateway", "depends_on", "svc-authdb") in keys
    assert ("svc-gateway", "reads_secret", "secret-db-password") in keys
    assert ("svc-gateway", "deployed_to", "env-staging") in keys
    # secret VALUES never stored
    text = (store.graph_dir / "code.toon").read_text()
    assert "changeme123456" not in text


def test_snapshot_is_replaced_not_accumulated(repo):
    cli.main(["ingest", "code"])
    (repo / "gateway/docker-compose.yml").write_text("services:\n  gateway:\n    image: acme/gateway:2.0\n")
    cli.main(["ingest", "code"])
    entities, relations = Store(repo).load_code_graph()
    # authdb + depends_on came from the old compose; a snapshot forgets removed things
    assert "svc-authdb" not in entities
    assert not any(r.predicate == "depends_on" for r in relations)


ARGO_APP = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: identra
spec:
  destination:
    namespace: acme-production
"""

HELM_VALUES = """\
identra:
  secret_key: file:///secrets/key
  postgresql:
    existingSecret: identra-db-creds
"""


def test_gitops_helm_layout(repo):
    gitops = repo / "gateway/apps/identra"
    (gitops / "acme-staging").mkdir(parents=True)
    (gitops / "acme-staging/values.yaml").write_text(HELM_VALUES)
    (repo / "gateway/apps/argocd").mkdir(parents=True)
    (repo / "gateway/apps/argocd/app.yaml").write_text(ARGO_APP)
    cli.main(["ingest", "code"])
    entities, relations = Store(repo).load_code_graph()
    keys = {r.key() for r in relations}
    # helm variant dir -> deployed_to env; argocd app -> destination namespace env
    assert ("svc-identra", "deployed_to", "env-staging") in keys
    assert ("svc-identra", "deployed_to", "env-production") in keys
    assert entities["secret-identra-db-creds"].type == "secret-ref"
    assert ("svc-identra", "reads_secret", "secret-identra-db-creds") in keys
    assert entities["secret-secret-key"].type == "secret-ref"


def test_snapshot_follows_declared_sources_not_stray_repos(repo):
    # a second repo exists in the workspace but is NOT a declared git source
    stray = repo / "scratch-clone"
    stray.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=stray, check=True)
    (stray / "docker-compose.yml").write_text("services:\n  straysvc:\n    image: x:1\n")
    # declare only "gateway" as a git source; code source points at root
    cli.main(["source", "add", "git", "gateway"])
    cfg_path = repo / ".tsubasa/captain.toml"
    cfg_path.write_text(cfg_path.read_text().replace('path = "gateway"\nglob = ""', 'path = "gateway"'))
    cli.main(["source", "add", "code", "."])
    cli.main(["ingest", "code"])
    entities, _ = Store(repo).load_code_graph()
    assert "svc-gateway" in entities       # declared fleet member: scanned
    assert "svc-straysvc" not in entities  # stray repo: ignored


def test_query_merges_code_snapshot(repo, capsys):
    cli.main(["ingest", "code"])
    cli.main(["query", "what secrets does gateway read"])
    out = capsys.readouterr().out
    assert "reads_secret" in out
    assert "secret-gateway-db-creds" in out
