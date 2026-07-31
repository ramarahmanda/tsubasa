import pytest

from tsubasa import cli
from tsubasa.config import load


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "testcap"])
    return tmp_path


def test_source_add_and_list(repo, capsys):
    (repo / "svc").mkdir()
    assert cli.main(["source", "add", "git", "svc"]) == 0
    cfg = load(repo)
    assert any(s.adapter == "git" and s.path == "svc" for s in cfg.sources)
    # idempotent
    assert cli.main(["source", "add", "git", "svc"]) == 0
    assert sum(1 for s in load(repo).sources if s.adapter == "git" and s.path == "svc") == 1
    cli.main(["source", "list"])
    assert "svc" in capsys.readouterr().out


def test_source_add_rejects_bad_input(repo, capsys):
    assert cli.main(["source", "add", "nope", "."]) == 1        # unknown adapter
    assert cli.main(["source", "add", "git", "missing-dir"]) == 1  # missing path
    err = capsys.readouterr().err
    assert "unknown adapter" in err and "does not exist" in err


def test_source_no_commit_is_gitignored(repo, capsys):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "postmortems").mkdir()
    assert cli.main(["source", "add", "incident", "postmortems", "--no-commit"]) == 0
    assert "/postmortems/" in (repo / ".gitignore").read_text()
    cfg = load(repo)
    src = next(s for s in cfg.sources if s.path == "postmortems")
    assert src.options.get("commit") is False
    capsys.readouterr()
    cli.main(["source", "list"])
    assert "[local-only]" in capsys.readouterr().out
    # doctor flags a local-only source whose files are actually tracked
    (repo / "postmortems/x.md").write_text("# outage")
    subprocess.run(["git", "add", "-f", "postmortems/x.md"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "x"], cwd=repo, check=True)
    assert cli.main(["doctor"]) == 1
    assert "TRACKED local-only" in capsys.readouterr().out


def test_doc_adapter_principles(repo):
    from tsubasa.storage import Store
    (repo / "principles").mkdir()
    (repo / "principles/boring-tech.md").write_text(
        "# Prefer boring technology\n\nWe choose proven, well-understood tools over novel ones "
        "unless the novel tool solves a problem we actually have.\n")
    assert cli.main(["source", "add", "doc", "principles", "--kind", "principle", "--impact", "high"]) == 0
    assert cli.main(["ingest", "doc"]) == 0
    store = Store(repo)
    ent = store.load_entities()["principle-prefer-boring-technology"]
    assert ent.type == "doc"
    assert ent.impact == "high"  # principles score hot
    # ...but hot.md is a map, not a cache: document bodies are indexed and read
    # on demand, never carried. See memory/tiers.py.
    assert "principle-prefer-boring-technology" not in (repo / ".tsubasa/memory/hot.md").read_text()
    assert "principle-prefer-boring-technology" in (repo / ".tsubasa/memory/index.md").read_text()


def test_doc_adapter_structured_table_metadata(repo):
    from tsubasa.storage import Store
    (repo / "table-meta").mkdir()
    (repo / "table-meta/widgets.toon").write_text(
        "table: widget_orders\n"
        "description: Orders placed for widgets.\n"
        "columns[2]{name,type,cardinality}:\n"
        "  email,string,0.1\n"
        "  status,string,0.05\n"
    )
    assert cli.main(["source", "add", "doc", "table-meta", "--glob", "*.toon"]) == 0
    assert cli.main(["ingest", "doc"]) == 0
    store = Store(repo)
    ent = store.load_entities()["doc-widget-orders"]
    assert ent.name == "widget_orders"
    assert any("cardinality=0.1" in f for f in ent.key_facts)
    assert any("cardinality=0.05" in f for f in ent.key_facts)


def test_doc_adapter_structured_multi_table(repo):
    from tsubasa.storage import Store
    (repo / "table-meta").mkdir()
    (repo / "table-meta/widgets.toon").write_text(
        "tables[2]:\n"
        "  - table: widget_orders\n"
        "    columns[1]{name,type,cardinality}:\n"
        "      email,string,0.1\n"
        "  - table: widget_customers\n"
        "    columns[1]{name,type,cardinality}:\n"
        "      email,string,0.9\n"
    )
    assert cli.main(["source", "add", "doc", "table-meta", "--glob", "*.toon"]) == 0
    assert cli.main(["ingest", "doc"]) == 0
    store = Store(repo)
    entities = store.load_entities()
    assert "doc-widget-orders" in entities
    assert "doc-widget-customers" in entities


def test_doc_adapter_toon_without_table_key_is_skipped(repo):
    from tsubasa.storage import Store
    (repo / "table-meta").mkdir()
    (repo / "table-meta/notes.toon").write_text("foo: bar\n")
    assert cli.main(["source", "add", "doc", "table-meta", "--glob", "*.toon"]) == 0
    assert cli.main(["ingest", "doc"]) == 0
    store = Store(repo)
    assert store.load_entities() == {}


def _write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _ingest_adr(repo, path="decisions"):
    from tsubasa.storage import Store
    assert cli.main(["source", "add", "adr", path]) == 0
    assert cli.main(["ingest", "adr"]) == 0
    store = Store(repo)
    return store, store.load_entities(), {r.key() for r in store.load_relations()}


def test_adr_sibling_same_stem(repo):
    _write(repo, "decisions/0001-thing.md", "# Ignored heading\n\nBody text that is long enough to be a summary paragraph.\n")
    _write(repo, "decisions/0001-thing.yaml",
           "title: Curated thing\nstatus: implemented\ncreation-date: 2021-03-04\n"
           "owning-sig: sig-node\nauthors:\n  - \"@alice\"\n  - bob\n")
    store, entities, _ = _ingest_adr(repo)
    ent = entities["adr-thing"]
    assert ent.name == "Curated thing"          # sibling title wins over the heading
    assert ent.status == "active"
    assert any(f.startswith("status=implemented (0001-thing) as of ") for f in ent.key_facts)
    ev = next(e for e in store.load_events() if e.source == "adr")
    assert ev.ts == "2021-03-04" and ev.actors == ["alice", "bob"] and ev.domains == ["sig-node"]


def test_adr_kep_yaml_attaches_to_readme(repo):
    _write(repo, "decisions/sig-node/2221-remove-dockershim/README.md", "# Whatever\n\nProse about the dockershim removal, long enough to summarize.\n")
    _write(repo, "decisions/sig-node/2221-remove-dockershim/kep.yaml",
           "title: Removing dockershim from kubelet\nstatus: implemented\ncreation-date: 2020-09-14\n"
           "replaces:\n  - /keps/sig-node/1985-old-shim/README.md\nsee-also:\n  - 0002-runtime-class.md\n")
    _, entities, rels = _ingest_adr(repo)
    assert entities["adr-remove-dockershim"].name == "Removing dockershim from kubelet"
    assert ("adr-remove-dockershim", "supersedes", "adr-old-shim") in rels
    assert ("adr-remove-dockershim", "relates_to", "adr-runtime-class") in rels


def test_adr_generic_sibling_not_attached_to_unrelated_docs(repo):
    _write(repo, "decisions/alpha.md", "# Alpha decision\n\nWe decided alpha, with enough prose to form a summary.\n")
    _write(repo, "decisions/beta.md", "# Beta decision\n\nWe decided beta, with enough prose to form a summary.\n")
    _write(repo, "decisions/kep.yaml", "title: Not mine\nstatus: withdrawn\n")
    _, entities, _ = _ingest_adr(repo)
    assert set(entities) == {"adr-alpha-decision", "adr-beta-decision"}
    assert all(e.status == "active" and not e.key_facts for e in entities.values())


def test_adr_sibling_empty_refs_make_no_relations(repo):
    _write(repo, "decisions/x/README.md", "# X\n\nSome decision prose that is long enough to be a summary here.\n")
    _write(repo, "decisions/x/kep.yaml",
           "title: Decision X\nstatus: provisional\nreplaces:\nsee-also:\nsuperseded-by:\n  - n/a\n")
    _, entities, rels = _ingest_adr(repo)
    assert set(entities) == {"adr-decision-x"}
    assert {r[1] for r in rels} == {"documented_in"}


def test_adr_superseded_by_flips_status(repo):
    _write(repo, "decisions/0009-old.md", "# Old\n\nThe original decision, with a paragraph long enough to summarize.\n")
    _write(repo, "decisions/0009-old.yaml",
           "title: Old way\nstatus: implemented\nsuperseded-by:\n  - /keps/sig-x/0010-new-way\n")
    _, entities, rels = _ingest_adr(repo)
    assert entities["adr-old"].status == "superseded"
    assert entities["adr-old"].superseded_by == "adr-new-way"
    assert ("adr-new-way", "supersedes", "adr-old") in rels


def test_adr_frontmatter_beats_sibling_except_title_and_status(repo):
    _write(repo, "decisions/0007-precedence.md",
           "---\ndate: 2020-01-01\n---\n# Markdown title\n\nStatus: accepted\n\n"
           "A paragraph of prose that is comfortably long enough to be a summary.\n")
    _write(repo, "decisions/0007-precedence.yaml",
           "title: Sibling title\nstatus: withdrawn\ncreation-date: 2021-05-05\n")
    store, entities, _ = _ingest_adr(repo)
    ent = entities["adr-precedence"]
    assert ent.name == "Sibling title" and ent.status == "dropped"
    assert next(e for e in store.load_events() if e.source == "adr").ts == "2020-01-01"


def test_adr_malformed_sibling_is_skipped(repo):
    _write(repo, "decisions/bad/README.md", "# Bad metadata\n\nProse for the doc whose sibling yaml does not parse at all.\n")
    _write(repo, "decisions/bad/kep.yaml", "title: [unclosed\n  - :\n")
    _write(repo, "decisions/good/README.md", "# Ignored\n\nProse for the doc whose sibling yaml parses fine.\n")
    _write(repo, "decisions/good/kep.yaml", "title: Good one\nstatus: implementable\n")
    _, entities, _ = _ingest_adr(repo)
    assert entities["adr-bad-metadata"].status == "active"   # fell back to the markdown
    assert not entities["adr-bad-metadata"].key_facts
    assert entities["adr-good-one"].name == "Good one"


def test_adr_sibling_authors_scalar_or_list(repo):
    _write(repo, "decisions/0011-solo.md", "# Solo\n\nA decision made by exactly one person, prose long enough here.\n")
    _write(repo, "decisions/0011-solo.yaml", "title: Solo\nauthors: \"@solo\"\neditor: \"@ed\"\n")
    store, _, _ = _ingest_adr(repo)
    assert next(e for e in store.load_events() if e.source == "adr").actors == ["solo", "ed"]


def test_config_parse_error_is_friendly(repo, capsys):
    cfg = repo / ".tsubasa/captain.toml"
    cfg.write_text(cfg.read_text() + "\nadapter = broken [\n")
    assert cli.main(["tiers"]) == 1
    assert "not valid TOML" in capsys.readouterr().err
