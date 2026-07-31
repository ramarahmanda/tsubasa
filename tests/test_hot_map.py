"""hot.md is a map, not a cache.

An A/B run on a 4-repo open-source corpus scored 8/8 either way, but the
19-entity map spent 39% fewer input tokens and routed better: it answered
from hot alone where the 172-entity version went hunting with Glob for a
file whose glob it was already holding. These tests pin the shape.
"""

import subprocess

import pytest

from tsubasa import cli
from tsubasa.storage import Store


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """One repo with decisions, design docs, a changelog, principles and a
    postmortem — the mix the fixture has, in miniature."""
    repo = tmp_path / "svc"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    _git(repo, "remote", "add", "origin", "https://example.invalid/svc.git")
    for i in range(6):
        _write(tmp_path, f"svc/mod{i}.go", "package main\n")
    for i in range(4):
        _write(tmp_path, f"svc/docs/adr/000{i}-choice-{i}.md",
               f"# Choice {i}\n\nStatus: accepted\nDate: 2026-01-0{i + 1}\n\n"
               "A decision recorded with prose long enough to be a summary.\n")
    for i in range(3):
        _write(tmp_path, f"svc/docs/design/area-{i}.md",
               f"# Area {i} design\n\nDesign prose for this area, long enough to summarize.\n")
    _write(tmp_path, "svc/docs/changelog/v1.md", "# Release 1.0\n\nWhat changed in this release, at length.\n")
    _write(tmp_path, "svc/docs/principles/boring.md",
           "# Prefer boring technology\n\nWe choose proven tools over novel ones, at length.\n")
    _write(tmp_path, "svc/docs/postmortem/2026-02-03-write-amp.md",
           "# Write amplification outage\n\nDate: 2026-02-03\nSeverity: sev2\n\n"
           "Batch writes fanned out and saturated the disk, at length.\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: svc")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "cap", "--no-llm"]) == 0
    return tmp_path


def hot(root):
    return (root / ".tsubasa/memory/hot.md").read_text()


def entries(text):
    return [l.split("**")[1] for l in text.splitlines() if l.startswith("- **")]


def test_hot_carries_the_map_and_not_the_documents(workspace):
    ids = entries(hot(workspace))
    assert "svc-svc" in ids
    assert any(i.startswith("corpus-") for i in ids)
    assert any(i.startswith("inc-") for i in ids)
    # indexed, read on demand — the graph is an index, not a cache
    assert not [i for i in ids if i.startswith(("adr-", "design-", "changelog-", "principle-", "doc-"))]

    index = (workspace / ".tsubasa/memory/index.md").read_text()
    for i in Store(workspace).load_entities():
        assert i in index  # nothing disappears, it just moves to warm


def test_a_medium_impact_incident_is_still_promoted(workspace):
    inc = next(e for e in Store(workspace).load_entities().values() if e.type == "incident")
    assert inc.impact == "medium"       # would be dropped by an impact==high gate
    assert inc.id in entries(hot(workspace))


def test_corpus_entries_keep_every_locating_fact(workspace):
    block = [l for l in hot(workspace).splitlines()
             if l.startswith("- **corpus-svc-docs-adr**") or "svc/docs/adr" in l]
    text = "\n".join(block)
    assert "glob: svc/docs/adr/**/*.md" in text   # path + pattern
    assert "files: 4" in text                     # count
    assert "changed: " in text                    # date range


def test_repeated_citations_are_hoisted_and_bulk_is_trimmed(workspace):
    text = hot(workspace)
    cite = "evt-" + [l for l in text.splitlines() if l.startswith("- [c1] evt-")][0].split("evt-")[1]
    assert text.count(cite) == 1                  # once in the legend, not once per entry
    assert text.count("[c1]") > 2                 # ...referenced by several entries
    assert "extensions:" not in text
    for line in text.splitlines():
        if line.strip().startswith("- samples:"):
            assert line.count(";") <= 2           # at most 3 titles


def test_topology_relations_are_in_hot(workspace):
    # no LLM pass here, so only the mechanical map exists; the section appears
    # as soon as there are svc/ext edges to show
    text = hot(workspace)
    assert "## Knowledge" in text
    assert "## Topology" not in text or "svc-svc" in text.split("## Topology")[1]


def test_budget_degrades_by_demoting_with_a_pointer(workspace):
    cfg = workspace / ".tsubasa/captain.toml"
    cfg.write_text(cfg.read_text().replace("context_window = 200000", "context_window = 400"))
    assert cli.main(["tiers"]) == 0
    text = hot(workspace)
    assert "demoted to warm by budget" in text     # never silently truncated
    assert "svc-svc" in (workspace / ".tsubasa/memory/index.md").read_text()
