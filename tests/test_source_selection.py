"""What a source actually covers: which files it matches, which it excludes,
and which aliases the corpus is allowed to answer to.
"""

import pytest

from tsubasa import cli, discover, topology
from tsubasa.adapters import base
from tsubasa.config import CaptainConfig, SourceConfig, load
from tsubasa.adapters.docs import DocAdapter
from tsubasa.graph import query
from tsubasa.models import Entity
from tsubasa.storage import Store


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--no-llm"])
    return tmp_path


# ------------------------------------------------------------------ fix 3: aliases

def test_short_and_generic_corpus_aliases_are_not_emitted(tmp_path):
    """`etcd/etcdctl/doc` gave every corpus an alias "doc", which substring
    matching then found inside "dockershim"."""
    for rel in ("x/y/doc/mirror.md", "x/y/payments-ledger/design.md"):
        _write(tmp_path, rel, "# T\n\nprose\n")
    sources = [{"adapter": "doc", "path": "x/y/doc", "repo": "x", "glob": "**/*.md"},
               {"adapter": "doc", "path": "x/y/payments-ledger", "repo": "x", "glob": "**/*.md"}]
    ents = {e["id"]: e for e, _ in topology._corpus_entities(tmp_path, sources)}

    assert "doc" not in [a.lower() for a in ents["corpus-x-y-doc"].get("aliases", [])]
    assert "payments-ledger" in ents["corpus-x-y-payments-ledger"]["aliases"]


@pytest.mark.parametrize("alias,ok", [
    ("doc", False), ("docs", False), ("src", False), ("api", False),
    ("design", False), ("keps", False), ("documentation", False),
    ("payments-ledger", True), ("etcd documentation", True), ("appendixes", True),
])
def test_useful_alias_rule(alias, ok):
    assert topology._useful_alias(alias) is ok


def test_dockershim_outranks_a_corpus_for_the_dockershim_question(tmp_path):
    _write(tmp_path, "etcd/etcdctl/doc/mirror.md", "# Mirror Maker\n\nprose\n")
    corpus = topology._corpus_entities(
        tmp_path, [{"adapter": "doc", "path": "etcd/etcdctl/doc", "repo": "etcd", "glob": "**/*.md"}])
    entities = {e["id"]: Entity.from_dict(e) for e, _ in corpus}
    entities["adr-remove-dockershim"] = Entity(
        id="adr-remove-dockershim", type="adr", name="Removing dockershim from kubelet")

    top = query.match_entities(entities, "what is the status of the dockershim removal KEP?", limit=5)
    assert top and top[0].id == "adr-remove-dockershim"


# ------------------------------------------------------------------ fix 6: case

@pytest.mark.parametrize("name", ["README.MD", "Guide.Md", "notes.markdown", "OTHER.MARKDOWN"])
def test_markdown_is_matched_case_insensitively(repo, name):
    _write(repo, f"knowledge/{name}", "# A title\n\nBody prose for the document, long enough.\n")
    assert cli.main(["source", "add", "doc", "knowledge"]) == 0
    assert cli.main(["ingest", "doc"]) == 0
    assert "doc-a-title" in Store(repo).load_entities()


def test_ci_glob_leaves_character_classes_alone():
    assert base.ci_glob("**/*.md") == "**/*.[mM][dD]"
    assert base.ci_glob("v[0-9]/*.md") == "[vV][0-9]/*.[mM][dD]"


def test_a_custom_glob_still_selects_only_its_own_extension(repo):
    _write(repo, "meta/widgets.TOON", "table: widget_orders\ncolumns[1]{name,type}:\n  email,string\n")
    _write(repo, "meta/prose.md", "# Not structured\n\nBody prose long enough to summarize.\n")
    assert cli.main(["source", "add", "doc", "meta", "--glob", "*.toon"]) == 0
    assert cli.main(["ingest", "doc"]) == 0
    ents = Store(repo).load_entities()
    assert "doc-widget-orders" in ents and "doc-not-structured" not in ents


# ------------------------------------------------------------------ fix 7: exclude

def test_exclude_filters_at_collect_time_and_says_so(tmp_path):
    for name in ("architecture.md", "backup.md", "api.v1.md"):
        _write(tmp_path, f"docs/{name}", f"# {name}\n\nBody prose long enough to summarize it.\n")
    src = SourceConfig(adapter="doc", path="docs", glob="*.md", exclude=["*.v1.md"])
    said = []
    a = DocAdapter(tmp_path, CaptainConfig(), src, {}, log=said.append)

    kept = [p.name for p in a.source_files(tmp_path / "docs")]
    assert kept == ["architecture.md", "backup.md"]
    assert said and "excluded 1 file(s)" in said[0] and "api.v1.md" in said[0]


def test_exclude_round_trips_through_source_add(repo):
    for name in ("architecture.md", "generated.v1.md"):
        _write(repo, f"docs/{name}", f"# {name}\n\nBody prose long enough to summarize it.\n")
    assert cli.main(["source", "add", "doc", "docs", "--exclude", "*.v1.md",
                     "--exclude", "drafts/*"]) == 0
    src = next(s for s in load(repo).sources if s.path == "docs")
    assert src.exclude == ["*.v1.md", "drafts/*"]

    assert cli.main(["ingest", "doc"]) == 0
    ents = Store(repo).load_entities()
    assert "doc-architecture-md" in ents and "doc-generated-v1-md" not in ents


def test_exclude_matches_a_nested_path_as_well_as_a_bare_name():
    assert base.excluded_by("api/gen/crd.md", ["api/gen/*"]) == "api/gen/*"
    assert base.excluded_by("api/gen/crd.md", ["crd.md"]) == "crd.md"
    assert base.excluded_by("api/handwritten.md", ["api/gen/*"]) == ""


def test_discover_proposes_an_exclude_only_for_a_declared_generated_file(tmp_path):
    _write(tmp_path, "docs/architecture.md", "# Arch\n\n" + "prose\n" * 50)
    _write(tmp_path, "docs/backup.md", "# Backup\n\n" + "prose\n" * 50)
    _write(tmp_path, "docs/zz-crd.md", "# CRD reference\n\n<!-- Code generated by controller-gen. DO NOT EDIT. -->\n")
    _write(tmp_path, "docs/api-ref.md", "# API Reference\n\n" + "field\n" * 3000)

    generated, suspect, total = discover.inspect_files(tmp_path / "docs", "**/*.md")
    assert generated == ["zz-crd.md"]                       # banner: safe to exclude offline
    assert any(s.startswith("api-ref.md ") for s in suspect)  # long: evidence only, still ingested
    assert total == 4


@pytest.mark.parametrize("text,generated", [
    ("<!-- Code generated by controller-gen. DO NOT EDIT. -->\n# CRD\n", True),
    ("---\nid: x\ntitle: API\n---\n\nThis file is automatically generated.\n", True),
    ("# Auto-generated API reference\n\nfields\n", True),
    # hand-written design prose that talks *about* generated code:
    # postgres/src/backend/nodes/README is exactly this, and excluding it
    # would be the destructive mistake the heuristic exists to avoid
    ("Node Structures\n===\n\nintro\n\nmore\n\nSome support functions are "
     "automatically generated by gen_node_support.pl from the headers.\n", False),
    ("# Backup\n\nHow to back things up.\n", False),
])
def test_generated_banner_needs_banner_position_and_shape(text, generated):
    assert discover.has_generated_banner(text) is generated


def test_a_model_proposed_exclude_cannot_escape_the_source():
    assert topology._excludes(["../../etc/passwd"]) == []
    assert topology._excludes(["a/../b"]) == []
    assert topology._excludes(["*.v1.md", "*.v1.md", "docs/gen/*"]) == ["*.v1.md", "docs/gen/*"]


def test_apply_sources_folds_in_and_logs_a_model_exclude(tmp_path):
    for name in ("a.md", "b.md", "gen.v1.md"):
        _write(tmp_path, f"docs/{name}", "# T\n\nprose\n")
    planned = [{"adapter": "doc", "path": "docs", "glob": "*.md", "files": 3,
                "bucket": "knowledge", "repo": "."}]
    refined = {"sources": [{"path": "docs", "adapter": "doc", "exclude": ["*.v1.md"]}]}
    said = []
    out = topology.apply_sources(tmp_path, planned, [], refined, log=said.append)
    assert out[0]["exclude"] == ["*.v1.md"] and out[0]["files"] == 2
    assert any("excluded *.v1.md" in m for m in said)


# ------------------------------------------------------------------ fix 5: repo facts

def test_repo_identifying_facts_are_the_first_ones_a_reader_sees(tmp_path, monkeypatch):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       cwd=tmp_path / "svc", check=True, capture_output=True)

    (tmp_path / "svc").mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path / "svc", check=True)
    git("remote", "add", "origin", "https://example.invalid/svc.git")
    for i in range(5):
        _write(tmp_path, f"svc/mod{i}.go", "package main\n")
    _write(tmp_path, "svc/docs/adr/0001-thing.md", "# Thing\n\nA decision with prose long enough to summarize.\n")
    git("add", "-A")
    git("commit", "-q", "-m", "feat: init")
    for tag in ("v0.1.0", "v0.2.0", "v1.0.0"):
        git("tag", tag)

    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap", "--no-llm"]) == 0
    repo = tmp_path
    ent = Store(repo).load_entities()["svc-svc"]

    # `query` prints key_facts[:5] and hot.md folds them onto one line: the
    # identifying ones have to be in that window, not behind 313 release tags
    head = " | ".join(ent.key_facts[:5])
    for expected in ("remote: https://example.invalid/svc.git", "default branch: main",
                     "primary language: Go", "history: 1 commits"):
        assert expected in head
    assert any(f.startswith("releases: 3, v0.1.0..v1.0.0") for f in ent.key_facts)
    assert not any(f.startswith("released: ") for f in ent.key_facts)  # one summary, not N facts

    hot = (repo / ".tsubasa/memory/hot.md").read_text()
    assert "remote: https://example.invalid/svc.git" in hot and "releases: 3, " in hot
