"""Bridge to graphify code indexes (graphify-out/graph.json per repo).

`tsubasa query` is the ONE query surface: events (why) + code snapshot
(deploy structure) + graphify (symbol-level anatomy) merge into a single
answer, each line carrying its provenance. The captain never has to choose
a tool — this module folds graphify's nodes/edges in when indexes exist.

graph.json shape is treated defensively: nodes under "nodes", edges under
"edges" or "links", with id/name/label and source/target/from/to variants.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import CaptainConfig

MAX_NODES = 12
MAX_EDGES = 20


def _node_name(n: dict) -> str:
    return str(n.get("name") or n.get("label") or n.get("id") or "")


def _edge_ends(e: dict) -> tuple[str, str, str]:
    src = str(e.get("source") if e.get("source") is not None else e.get("from", ""))
    tgt = str(e.get("target") if e.get("target") is not None else e.get("to", ""))
    label = str(e.get("label") or e.get("relation") or e.get("type") or "related_to")
    return src, tgt, label


def load_graphs(root: Path, cfg: CaptainConfig) -> list[tuple[str, dict]]:
    """[(repo_name, graph_dict)] for every fleet repo with an index.

    Primary location: <root>/.tsubasa/code-index/<repo>/graph.toon (committed).
    Legacy fallback: <repo>/graphify-out/graph.json inside the repo."""
    from .. import codegraph
    graphs = []
    seen: set[str] = set()
    for name in codegraph.repos_with_index(root):
        g = codegraph.load(root, name)
        if g is not None:
            graphs.append((name, g))
            seen.add(name)
    for src in cfg.sources:
        if src.adapter != "git":
            continue
        repo = (root / src.path).resolve()
        path = repo / "graphify-out" / "graph.json"
        if repo.name in seen or not path.is_file():
            continue
        seen.add(repo.name)
        try:
            graphs.append((repo.name, json.loads(path.read_text(errors="replace"))))
        except (json.JSONDecodeError, OSError):
            continue
    return graphs


def query(root: Path, cfg: CaptainConfig, text: str) -> str:
    """Serialize graphify matches for the query text, '' if nothing."""
    words = {w for w in _tokens(text) if len(w) > 2}
    if not words:
        return ""
    lines: list[str] = []
    for repo_name, g in load_graphs(root, cfg):
        nodes = g.get("nodes", [])
        edges = g.get("edges") or g.get("links") or []
        by_id = {str(n.get("id", _node_name(n))): n for n in nodes if isinstance(n, dict)}
        matched_ids = set()
        matched_nodes = []
        for nid, n in by_id.items():
            name = _node_name(n)
            hay = set(_tokens(name)) | set(_tokens(nid))
            if words & hay:
                matched_ids.add(nid)
                matched_nodes.append((nid, n))
        if not matched_ids:
            continue
        lines.append(f"### {repo_name} (graphify code graph)")
        for nid, n in matched_nodes[:MAX_NODES]:
            loc = n.get("file") or n.get("path") or n.get("loc") or ""
            ntype = n.get("type") or n.get("kind") or "node"
            lines.append(f"- {_node_name(n)} ({ntype})" + (f" — {loc}" if loc else ""))
        shown = 0
        for e in edges:
            if not isinstance(e, dict):
                continue
            src, tgt, label = _edge_ends(e)
            if src in matched_ids or tgt in matched_ids:
                sname = _node_name(by_id.get(src, {"id": src}))
                tname = _node_name(by_id.get(tgt, {"id": tgt}))
                lines.append(f"({sname}) --[{label}]--> ({tname})  [graphify:{repo_name}]")
                shown += 1
                if shown >= MAX_EDGES:
                    lines.append(f"(+more edges — `graphify explain \"{_node_name(matched_nodes[0][1])}\" "
                                 f"--graph {repo_name}/graphify-out/graph.json`)")
                    break
    return "\n".join(lines)


def _tokens(text: str) -> list[str]:
    out, buf = [], []
    for ch in str(text).lower():
        if ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
            buf = []
    if buf:
        out.append("".join(buf))
    return out
