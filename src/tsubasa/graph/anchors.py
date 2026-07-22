"""Anchor layer: links between tsubasa entities and graphify code nodes.

The two graphs stay separate artifacts (memory vs anatomy) — anchors are
the committed join table that lets one query traverse both. Anchored by
node NAME, not internal id, so graphify rebuilds don't break links.

Link kinds (`by`):
    seed   deterministic: repo-level + strong name matches
    xrepo  cross-repo: fleet service names found in another repo's nodes
    link   proposed by the LLM pass (tsubasa link --llm)
    recall saved ambiently when the captain used both layers together
"""

from __future__ import annotations

from pathlib import Path

from ..config import CaptainConfig
from ..models import Entity, slugify
from ..storage import Store
from . import graphify_bridge


def seed(store: Store, root: Path, cfg: CaptainConfig, log=print) -> int:
    """Deterministic anchor seeding across all fleet graphify indexes."""
    entities = store.load_entities()
    anchors = store.load_anchors()
    graphs = graphify_bridge.load_graphs(root, cfg)
    if not graphs:
        log("[link] no graphify indexes found — build them first (graphify skill / tsubasa index)")
        return 0
    before = len(anchors)

    svc_by_repo = {slugify(repo_name): f"svc-{slugify(repo_name)}" for repo_name, _ in graphs}
    fleet_names = _fleet_service_names(entities)

    for repo_name, g in graphs:
        repo_before = len(anchors)
        nodes = [n for n in g.get("nodes", []) if isinstance(n, dict)]
        node_names = {graphify_bridge._node_name(n) for n in nodes} - {""}

        # 1. repo-level anchor: the service entity that IS this repo
        repo_svc = svc_by_repo.get(slugify(repo_name))
        if repo_svc and repo_svc in entities:
            anchors.append({"entity": repo_svc, "repo": repo_name, "node": "*", "by": "seed"})

        # 2. name anchors: entity names/aliases matching node names
        lowered = {n.lower(): n for n in node_names}
        for e in entities.values():
            if e.type in ("secret-ref", "env"):
                continue
            for name in e.all_names():
                if len(name) < 5:
                    continue
                hit = lowered.get(name.lower())
                if hit:
                    anchors.append({"entity": e.id, "repo": repo_name, "node": hit, "by": "seed"})

        # 3. cross-repo: another fleet service's name inside THIS repo's nodes
        for other_id, tokens in fleet_names.items():
            if other_id == repo_svc:
                continue
            for node_name in node_names:
                nl = node_name.lower()
                if any(t in nl for t in tokens):
                    anchors.append({"entity": other_id, "repo": repo_name,
                                    "node": node_name, "by": "xrepo"})
                    break  # one witness node per (entity, repo) is enough
        log(f"[link] {repo_name}: {len(node_names)} code nodes, +{len(anchors) - repo_before} anchor(s)")

    store.save_anchors(anchors)
    added = len(store.load_anchors()) - min(before, len(store.load_anchors()))
    log(f"[link] {len(store.load_anchors())} anchor(s) across {len(graphs)} index(es)")
    return added


def _fleet_service_names(entities: dict[str, Entity]) -> dict[str, list[str]]:
    """service entity id -> distinctive lowercase name tokens (len >= 5)."""
    out: dict[str, list[str]] = {}
    for e in entities.values():
        if e.type != "service":
            continue
        tokens = {n.lower() for n in e.all_names() if len(n) >= 5}
        if tokens:
            out[e.id] = sorted(tokens)
    return out


def cross_repo_edges(anchors: list[dict], entities: dict[str, Entity]) -> list[tuple[str, str, str]]:
    """(source_entity, predicate, target_entity) derived from xrepo anchors:
    entity A anchored in repo B's code => A is referenced by B's service."""
    edges = []
    for a in anchors:
        if a.get("by") != "xrepo":
            continue
        host_svc = f"svc-{slugify(a['repo'])}"
        if host_svc in entities and a["entity"] in entities and host_svc != a["entity"]:
            edges.append((host_svc, "references_in_code", a["entity"]))
    return sorted(set(edges))


def for_entities(anchors: list[dict], entity_ids: set[str]) -> list[dict]:
    return [a for a in anchors if a["entity"] in entity_ids]
