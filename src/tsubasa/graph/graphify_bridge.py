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

# the whole section, across every repo: top node names plus one pointer. The
# anatomy is re-derivable via `graphify explain`, so a pointer beats a dump,
# and a dump here pushes the event graph's verdict past a reader's `| head`
MAX_LINES = 8


def _node_name(n: dict) -> str:
    return str(n.get("name") or n.get("label") or n.get("id") or "")


def _edge_ends(e: dict) -> tuple[str, str, str]:
    src = str(e.get("source") if e.get("source") is not None else e.get("from", ""))
    tgt = str(e.get("target") if e.get("target") is not None else e.get("to", ""))
    label = str(e.get("label") or e.get("relation") or e.get("type") or "related_to")
    return src, tgt, label


def load_graphs(root: Path, cfg: CaptainConfig) -> list[tuple[str, dict]]:
    """[(repo_name, graph_dict)] for every workspace repo with an index.

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
    """Serialize graphify matches for the query text, '' if nothing.

    At most MAX_LINES lines whatever the repo count: the best-matching node
    names, then one pointer carrying the expand command and what was held
    back, so elision is visible without the dump being paid for."""
    words = {w for w in _tokens(text) if len(w) > 2}
    if not words:
        return ""
    scored: list[tuple[int, str, dict]] = []
    touching = 0
    for repo_name, g in load_graphs(root, cfg):
        nodes = g.get("nodes", [])
        edges = g.get("edges") or g.get("links") or []
        by_id = {str(n.get("id", _node_name(n))): n for n in nodes if isinstance(n, dict)}
        matched_ids = set()
        for nid, n in by_id.items():
            hay = set(_tokens(_node_name(n))) | set(_tokens(nid))
            hits = len(words & hay)
            if hits:
                matched_ids.add(nid)
                scored.append((hits, repo_name, n))
        for e in edges:
            if isinstance(e, dict):
                src, tgt, _ = _edge_ends(e)
                if src in matched_ids or tgt in matched_ids:
                    touching += 1
    if not scored:
        return ""
    scored.sort(key=lambda p: -p[0])
    lines: list[str] = []
    for _, repo_name, n in scored[:MAX_LINES - 1]:
        loc = n.get("file") or n.get("path") or n.get("loc") or ""
        ntype = n.get("type") or n.get("kind") or "node"
        lines.append(f"- {_node_name(n)} ({ntype})"
                     + (f" — {loc}" if loc else "") + f"  [graphify:{repo_name}]")
    held = len(scored) - (MAX_LINES - 1)
    top_repo, top = scored[0][1], scored[0][2]
    more = (f"+{held} more nodes, " if held > 0 else "") + f"{touching} edges"
    lines.append(f"({more} — `graphify explain \"{_node_name(top)}\" "
                 f"--graph {top_repo}/graphify-out/graph.json`)")
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
