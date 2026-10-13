"""The review conversation is where decisions change; the merge fact alone
drops it. ADR-carrying PRs with review pushback get one note event carrying
the changes-requested rationale."""

import json as jsonlib
from types import SimpleNamespace

import pytest

from tsubasa import cli
from tsubasa.storage import Store

PR_LIST = [
    {
        "number": 7, "title": "Ship gateway change adr-gw-rework",
        "mergedAt": "2026-07-30T10:00:00Z", "headRefName": "feat/adr-gw-rework",
        "url": "https://example.test/pr/7", "files": [{"path": "src/a.py"}],
        "author": {"login": "dev1"}, "reviewDecision": "APPROVED",
        "latestReviews": [{"author": {"login": "rev1"}, "state": "APPROVED"}],
    },
    {
        "number": 8, "title": "Plain fix", "mergedAt": "2026-07-30T11:00:00Z",
        "headRefName": "fix/plain", "url": "https://example.test/pr/8", "files": [],
        "author": {"login": "dev2"}, "reviewDecision": "",
        "latestReviews": [{"author": {"login": "rev1"}, "state": "CHANGES_REQUESTED"}],
    },
]
PR7_REVIEWS = {"reviews": [
    {"author": {"login": "rev1"}, "state": "CHANGES_REQUESTED",
     "body": "Sync writes only, the double-write breaks sessions"},
    {"author": {"login": "rev1"}, "state": "APPROVED", "body": ""},
]}


@pytest.fixture()
def captain_with_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap"])
    repo = tmp_path / "svc"
    (repo / ".git").mkdir(parents=True)
    cli.main(["source", "add", "github", "svc"])
    import tsubasa.adapters.github as gh_mod
    monkeypatch.setattr(gh_mod.shutil, "which", lambda _: "/usr/bin/gh")

    def fake_run(argv, **kw):
        if "list" in argv:
            return SimpleNamespace(returncode=0, stdout=jsonlib.dumps(PR_LIST), stderr="")
        if "view" in argv and "7" in argv:
            return SimpleNamespace(returncode=0, stdout=jsonlib.dumps(PR7_REVIEWS), stderr="")
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(gh_mod.subprocess, "run", fake_run)
    return tmp_path


def test_merged_events_carry_reviewers_and_the_arc(captain_with_repo):
    assert cli.main(["ingest", "github"]) == 0
    events = {e.id: e for e in Store(captain_with_repo).load_events()}
    ev = events["evt-20260730-svc-pr7"]
    assert "dev1" in ev.actors and "rev1" in ev.actors
    assert "approved by rev1" in ev.summary


def test_adr_pr_with_pushback_mints_one_review_note(captain_with_repo):
    cli.main(["ingest", "github"])
    events = {e.id: e for e in Store(captain_with_repo).load_events()}
    note = events["evt-20260730-svc-pr7-review"]
    assert note.type == "note"
    assert "changes requested on adr-gw-rework" in note.title
    assert "double-write breaks sessions" in note.body
    assert "changes requested by rev1, then approved by rev1" in note.summary
    assert {r.kind for r in note.refs} >= {"pr", "adr"}


def test_pushback_without_an_adr_stays_un_evented(captain_with_repo):
    cli.main(["ingest", "github"])
    events = {e.id: e for e in Store(captain_with_repo).load_events()}
    assert "evt-20260730-svc-pr8-review" not in events
    assert "evt-20260730-svc-pr8" in events
