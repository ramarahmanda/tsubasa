"""Code-index storage: graphify graphs as TOON at rest, JSON at runtime.

Committed artifact:  .tsubasa/code-index/<repo>/graph.toon   (tabular, compact)
Runtime artifact:    .tsubasa/code-index/<repo>/graph.json   (gitignored, lazy)

graph.json is thousands of uniform node/edge dicts repeating the same keys;
TOON's tabular arrays store the header once. Conversion is lossless for the
fields graphify's own tooling reads; unknown top-level keys are preserved
verbatim in a meta blob.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import toon
from .config import TSUBASA_DIR

CODE_INDEX_DIR = "code-index"
_NULL = "null"
_ABSENT = "__absent__"  # tabular rows need uniform columns; this marks "key not present" (vs null)


def index_dir(root: Path, repo_name: str) -> Path:
    return root / TSUBASA_DIR / CODE_INDEX_DIR / repo_name


def _norm_rows(items: list[dict]) -> tuple[list[str], list[dict]]:
    """Normalize dicts to identical ordered scalar columns (tabular TOON)."""
    keys: list[str] = []
    for it in items:
        for k in it:
            if k not in keys:
                keys.append(k)
    rows = []
    for it in items:
        row = {}
        for k in keys:
            if k not in it:
                row[k] = _ABSENT
                continue
            v = it[k]
            if v is None:
                row[k] = _NULL
            elif isinstance(v, (dict, list)):
                row[k] = json.dumps(v, ensure_ascii=False)
            else:
                row[k] = v
        rows.append(row)
    return keys, rows


def _denorm_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        it = {}
        for k, v in row.items():
            if v == _ABSENT:
                continue
            if v == _NULL:
                it[k] = None
            elif isinstance(v, str) and v[:1] in "[{":
                try:
                    it[k] = json.loads(v)
                except json.JSONDecodeError:
                    it[k] = v
            else:
                it[k] = v
        out.append(it)
    return out


def graph_to_toon_doc(graph: dict) -> dict:
    nodes = [n for n in graph.get("nodes", []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or graph.get("links") or []) if isinstance(e, dict)]
    edge_key = "edges" if "edges" in graph or "links" not in graph else "links"
    meta = {k: v for k, v in graph.items() if k not in ("nodes", "edges", "links")}
    doc: dict = {}
    if meta:
        doc["meta"] = json.dumps(meta, ensure_ascii=False)
    doc["edge_key"] = edge_key
    _, node_rows = _norm_rows(nodes)
    _, edge_rows = _norm_rows(edges)
    doc["nodes"] = node_rows
    doc["edges"] = edge_rows
    return doc


def toon_doc_to_graph(doc: dict) -> dict:
    graph: dict = {}
    if doc.get("meta"):
        try:
            graph.update(json.loads(doc["meta"]))
        except json.JSONDecodeError:
            pass
    graph["nodes"] = _denorm_rows(doc.get("nodes", []))
    graph[doc.get("edge_key", "edges")] = _denorm_rows(doc.get("edges", []))
    return graph


def save(root: Path, repo_name: str, graph: dict) -> Path:
    """Write graph.toon (committed) and graph.json (runtime cache)."""
    d = index_dir(root, repo_name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph.toon").write_text(toon.encode(graph_to_toon_doc(graph)))
    (d / "graph.json").write_text(json.dumps(graph, ensure_ascii=False))
    _ensure_gitignore(root)
    return d / "graph.toon"


def load(root: Path, repo_name: str) -> dict | None:
    d = index_dir(root, repo_name)
    if (d / "graph.toon").is_file():
        return toon_doc_to_graph(toon.decode((d / "graph.toon").read_text()))
    if (d / "graph.json").is_file():  # pre-toon layouts
        try:
            return json.loads((d / "graph.json").read_text(errors="replace"))
        except json.JSONDecodeError:
            return None
    return None


def ensure_json(root: Path, repo_name: str) -> Path | None:
    """Materialize the runtime graph.json from graph.toon if missing/stale —
    graphify's own CLI (path/explain/query --graph) wants JSON."""
    d = index_dir(root, repo_name)
    toon_path, json_path = d / "graph.toon", d / "graph.json"
    if not toon_path.is_file():
        return json_path if json_path.is_file() else None
    if not json_path.is_file() or json_path.stat().st_mtime < toon_path.stat().st_mtime:
        graph = toon_doc_to_graph(toon.decode(toon_path.read_text()))
        json_path.write_text(json.dumps(graph, ensure_ascii=False))
    return json_path


def repos_with_index(root: Path) -> list[str]:
    base = root / TSUBASA_DIR / CODE_INDEX_DIR
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir()
                  if d.is_dir() and ((d / "graph.toon").is_file() or (d / "graph.json").is_file()))


def _ensure_gitignore(root: Path) -> None:
    # written unconditionally: the captain dir may be a SUBDIR of the git
    # repo (nested .gitignore still applies), and the file is harmless
    # without git at all
    gi = root / ".gitignore"
    line = f"{TSUBASA_DIR}/{CODE_INDEX_DIR}/**/graph.json"
    existing = gi.read_text() if gi.exists() else ""
    if line not in existing.splitlines():
        # NB: gitignore has no inline comments — the comment gets its own line
        gi.write_text(existing.rstrip("\n") + ("\n" if existing else "")
                      + f"# tsubasa: runtime cache, regenerated from graph.toon\n{line}\n")
