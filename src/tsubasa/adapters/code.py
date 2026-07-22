"""Code adapter: SNAPSHOT knowledge, rebuilt from the code on every ingest.

Unlike event adapters (append-only history), code-derived knowledge is
current-state: it silently expires with every commit. So this adapter never
appends — it re-derives the whole snapshot and stamps it with the commit it
came from (`code@<repo>:<sha>`). The graph is exactly as fresh as the code,
which makes it the HIGHEST-trust layer: code doesn't lie.

v1 extracts deterministic structure:
  - docker-compose services: service entities, depends_on, secret-ref env keys
  - k8s manifests (Deployment/StatefulSet/DaemonSet/CronJob/Service):
    workload entities, namespaces as envs, secretKeyRef/secret volume names
  - build-file dependencies between workspace repos (pom.xml, package.json,
    go.mod) -> depends_on relations

Secret handling: only NAMES of secrets/env keys are recorded (secret-refs),
values never — and write-time redaction backstops that.
"""

from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from ..models import slugify
from .base import Adapter

SECRET_KEY_RE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)")
K8S_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "CronJob", "Service"}
MAX_FILE_BYTES = 300_000
MAX_YAML_FILES = 400


class CodeAdapter(Adapter):
    name = "code"
    snapshot_mode = True

    def collect(self):
        return []  # snapshot adapters emit no events

    def snapshot(self) -> tuple[list[dict], list[dict], list[str]]:
        base = (self.root / self.source.path).resolve()
        repos = self._fleet_repos(base)
        entities: dict[str, dict] = {}
        relations: list[dict] = []
        provenance: list[str] = []
        workspace_names = {r.name: f"svc-{slugify(r.name)}" for r in repos}
        repo_set = set(repos)
        for repo in repos:
            # a workspace root that is itself a git repo must not re-scan
            # repos nested inside it — those carry their own provenance
            exclude = {r for r in repo_set if r != repo and r.is_relative_to(repo)}
            prov = f"code@{repo.name}:{_head_sha(repo)}"
            provenance.append(prov)
            _scan_compose(repo, prov, entities, relations)
            _scan_k8s(repo, prov, entities, relations, exclude)
            _scan_helm_values(repo, prov, entities, relations, exclude)
            _scan_build_deps(repo, prov, workspace_names, entities, relations)
        return list(entities.values()), _dedupe(relations), provenance


    def _fleet_repos(self, base: Path) -> list[Path]:
        """The fleet is DECLARED, not discovered: when this source points at
        the workspace root and git sources are configured, snapshot exactly
        those repos (plus the root's own manifests). A stray cloned repo in
        the workspace is not fleet knowledge. Directory discovery remains
        only as the fallback when no git sources exist yet (fresh onboard)."""
        if base != self.root.resolve():
            return _candidate_repos(base)  # explicitly scoped code source
        declared = []
        for s in self.cfg.sources:
            if s.adapter != "git":
                continue
            p = (self.root / s.path).resolve()
            if (p / ".git").exists() and p not in declared:
                declared.append(p)
        if not declared:
            return _candidate_repos(base)
        return ([base] if base not in declared else []) + sorted(declared)


def _candidate_repos(base: Path) -> list[Path]:
    subs = [d for d in sorted(base.iterdir()) if d.is_dir() and (d / ".git").exists()]
    if subs:  # workspace mode: nested repos each scan under their own sha
        return ([base] if (base / ".git").exists() else []) + subs
    return [base]


def _head_sha(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or "worktree"
    except Exception:
        return "worktree"


def _ent(entities: dict, eid: str, etype: str, name: str, desc: str, prov: str) -> None:
    cur = entities.get(eid)
    if cur is None:
        entities[eid] = {"id": eid, "type": etype, "name": name, "description": desc,
                         "source_events": [prov]}
    elif prov not in cur["source_events"]:
        cur["source_events"].append(prov)


def _rel(relations: list, src: str, pred: str, tgt: str, prov: str) -> None:
    relations.append({"source": src, "predicate": pred, "target": tgt, "provenance": prov})


def _dedupe(relations: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in relations:
        k = (r["source"], r["predicate"], r["target"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _safe_yaml_docs(path: Path):
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        return [d for d in yaml.safe_load_all(path.read_text(errors="replace")) if isinstance(d, dict)]
    except Exception:
        return []


# ------------------------------------------------------------- docker-compose

def _scan_compose(repo: Path, prov: str, entities: dict, relations: list) -> None:
    for f in list(repo.glob("docker-compose*.y*ml")) + list(repo.glob("compose*.y*ml")):
        for doc in _safe_yaml_docs(f):
            services = doc.get("services")
            if not isinstance(services, dict):
                continue
            for sname, spec in services.items():
                if not isinstance(spec, dict):
                    continue
                sid = f"svc-{slugify(str(sname))}"
                image = spec.get("image", "")
                _ent(entities, sid, "service", str(sname),
                     f"Container service in {repo.name}/{f.name}" + (f" (image {image})" if image else ""), prov)
                deps = spec.get("depends_on")
                dep_names = list(deps) if isinstance(deps, (list, dict)) else []
                for d in dep_names:
                    did = f"svc-{slugify(str(d))}"
                    _ent(entities, did, "service", str(d), f"Container service in {repo.name}/{f.name}", prov)
                    _rel(relations, sid, "depends_on", did, prov)
                env = spec.get("environment")
                keys = list(env) if isinstance(env, dict) else [
                    str(e).split("=", 1)[0] for e in env] if isinstance(env, list) else []
                for key in keys:
                    if SECRET_KEY_RE.search(str(key)):
                        sec_id = f"secret-{slugify(str(key))}"
                        _ent(entities, sec_id, "secret-ref", str(key),
                             f"Secret env key declared in {repo.name}/{f.name} (value not stored)", prov)
                        _rel(relations, sid, "reads_secret", sec_id, prov)


# ------------------------------------------------------------- k8s manifests

def _scan_k8s(repo: Path, prov: str, entities: dict, relations: list,
              exclude: set[Path] = frozenset()) -> None:
    count = 0
    for f in repo.rglob("*.y*ml"):
        if count > MAX_YAML_FILES:
            return
        if any(part in (".git", "node_modules", "target", "vendor") for part in f.parts):
            continue
        if any(f.is_relative_to(x) for x in exclude):
            continue
        count += 1
        for doc in _safe_yaml_docs(f):
            kind = doc.get("kind")
            meta = doc.get("metadata") or {}
            name = meta.get("name")
            if kind == "Application" and name and "argoproj.io" in str(doc.get("apiVersion", "")):
                # ArgoCD Application: the GitOps unit of deployment
                wid = f"svc-{slugify(str(name))}"
                dest_ns = ((doc.get("spec") or {}).get("destination") or {}).get("namespace", "")
                _ent(entities, wid, "service", str(name),
                     f"ArgoCD Application in {repo.name}/{f.relative_to(repo)}", prov)
                if dest_ns:
                    env_id = f"env-{_env_name(str(dest_ns))}"
                    _ent(entities, env_id, "env", str(dest_ns), f"deploy target namespace {dest_ns}", prov)
                    _rel(relations, wid, "deployed_to", env_id, prov)
                continue
            if kind not in K8S_KINDS or not name:
                continue
            wid = f"svc-{slugify(str(name))}"
            ns = meta.get("namespace", "")
            _ent(entities, wid, "service", str(name),
                 f"k8s {kind} defined in {repo.name}/{f.relative_to(repo)}", prov)
            if ns:
                env_id = f"env-{slugify(str(ns))}"
                _ent(entities, env_id, "env", str(ns), f"k8s namespace {ns}", prov)
                _rel(relations, wid, "deployed_to", env_id, prov)
            for sec in _k8s_secret_names(doc):
                sec_id = f"secret-{slugify(sec)}"
                _ent(entities, sec_id, "secret-ref", sec,
                     f"k8s Secret referenced in {repo.name}/{f.relative_to(repo)} (value not stored)", prov)
                _rel(relations, wid, "reads_secret", sec_id, prov)


def _k8s_secret_names(doc: dict) -> set[str]:
    names: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("secretKeyRef", "secretRef") and isinstance(value, dict) and value.get("name"):
                    names.add(str(value["name"]))
                elif key == "secretName" and isinstance(value, str):
                    names.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return names


# ------------------------------------------------------------- helm values

ENV_HINTS = [("production", "production"), ("prd", "production"), ("prod", "production"),
             ("staging", "staging"), ("stg", "staging"), ("dev", "dev")]


def _env_name(raw: str) -> str:
    low = raw.lower()
    for hint, canonical in ENV_HINTS:
        if hint in low:
            return canonical
    return slugify(raw)


def _scan_helm_values(repo: Path, prov: str, entities: dict, relations: list,
                      exclude: set[Path] = frozenset()) -> None:
    """GitOps/helm layout: <...>/apps/<app>/<variant>/values.yaml means
    '<app> is deployed to <variant> with this config'. Secret KEY NAMES and
    referenced Secret names are recorded; values never."""
    count = 0
    for f in repo.rglob("values*.y*ml"):
        if count > MAX_YAML_FILES:
            return
        if any(part in (".git", "node_modules", "target", "vendor", "charts") for part in f.parts):
            continue
        if any(f.is_relative_to(x) for x in exclude):
            continue
        count += 1
        parent = f.parent.name
        grand = f.parent.parent.name if f.parent.parent != repo else ""
        variant_is_env = any(h in parent.lower() for h, _ in ENV_HINTS)
        app = grand if (variant_is_env and grand) else parent
        if not app or app in (".", repo.name):
            continue
        sid = f"svc-{slugify(app)}"
        rel_path = f.relative_to(repo)
        _ent(entities, sid, "service", app, f"Helm-deployed app ({repo.name}/{rel_path})", prov)
        if variant_is_env:
            env_id = f"env-{_env_name(parent)}"
            _ent(entities, env_id, "env", _env_name(parent), f"deploy variant dir {parent}", prov)
            _rel(relations, sid, "deployed_to", env_id, prov)
        for doc in _safe_yaml_docs(f):
            for sec in _helm_secret_names(doc):
                sec_id = f"secret-{slugify(sec)}"
                _ent(entities, sec_id, "secret-ref", sec,
                     f"Secret referenced in {repo.name}/{rel_path} (value not stored)", prov)
                _rel(relations, sid, "reads_secret", sec_id, prov)


def _helm_secret_names(doc) -> set[str]:
    """Names/keys of secrets referenced in helm values: existingSecret:,
    secretName:, secretKeyRef, plus secret-looking KEY names."""
    names: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("existingSecret", "secretName", "secret_name") and isinstance(value, str) and value:
                    names.add(value)
                elif key in ("secretKeyRef", "secretRef") and isinstance(value, dict) and value.get("name"):
                    names.add(str(value["name"]))
                elif SECRET_KEY_RE.search(str(key)) and isinstance(value, (str, int)) and str(value):
                    names.add(str(key))  # the KEY name only — never the value
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return names


# ------------------------------------------------------------- build deps

def _scan_build_deps(repo: Path, prov: str, workspace: dict[str, str],
                     entities: dict, relations: list) -> None:
    me = f"svc-{slugify(repo.name)}"
    dep_names: set[str] = set()

    pom = repo / "pom.xml"
    if pom.is_file():
        _ent(entities, me, "service", repo.name, f"Maven service ({repo.name}/pom.xml)", prov)
        try:
            tree = ET.parse(pom)
            ns = {"m": "http://maven.apache.org/POM/4.0.0"}
            for aid in tree.findall(".//m:dependency/m:artifactId", ns):
                dep_names.add((aid.text or "").strip())
        except ET.ParseError:
            pass

    pkg = repo / "package.json"
    if pkg.is_file():
        _ent(entities, me, "service", repo.name, f"Node service ({repo.name}/package.json)", prov)
        try:
            data = json.loads(pkg.read_text(errors="replace"))
            for section in ("dependencies", "devDependencies"):
                dep_names.update((data.get(section) or {}).keys())
        except json.JSONDecodeError:
            pass

    gomod = repo / "go.mod"
    if gomod.is_file():
        _ent(entities, me, "service", repo.name, f"Go service ({repo.name}/go.mod)", prov)
        for line in gomod.read_text(errors="replace").splitlines():
            m = re.match(r"\s*([\w./-]+)\s+v[\d.]", line)
            if m:
                dep_names.add(m.group(1).rsplit("/", 1)[-1])

    for other_name, other_id in workspace.items():
        if other_id == me:
            continue
        if any(other_name == d or other_name in d for d in dep_names):
            _rel(relations, me, "depends_on", other_id, prov)
