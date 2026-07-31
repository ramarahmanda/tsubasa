"""A sibling tracker (`kep.yaml`) is edited in place, so its `status` is the
value today, not the value on `creation-date`. Two properties are tested here:
a typo'd date never costs the ingest, and `--as-of` never asserts a status the
graph cannot know held then. Values are the real upstream ones.
"""

import os
import subprocess

import pytest

from tsubasa import cli
from tsubasa.adapters import adr
from tsubasa.adapters.adr import AdrAdapter
from tsubasa.config import CaptainConfig, SourceConfig
from tsubasa.graph import assemble
from tsubasa.models import Ref, parse_ts
from tsubasa.storage import Store

# keps/sig-node/2221-remove-dockershim/kep.yaml, verbatim fields
DOCKERSHIM = (
    "title: Removing dockershim from kubelet\nkep-number: 2221\n"
    "owning-sig: sig-node\nstatus: implemented\ncreation-date: 2020-09-14\n"
    'stage: stable\nlatest-milestone: "v1.24"\n'
)
# real upstream typos: month 14 and day-month swapped
BAD_DATES = {
    "4355-coordinated-leader-election": "2023-14-05",
    "5075-dra-consumable-capacity": "2025-30-01",
}


def _write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--no-llm"])
    return tmp_path


def _ingest(repo, path="keps"):
    assert cli.main(["source", "add", "adr", path]) == 0
    assert cli.main(["ingest", "adr"]) == 0
    return Store(repo)


# ------------------------------------------------------------------ fix 1

@pytest.mark.parametrize("stem,bad", sorted(BAD_DATES.items()))
def test_uncalendar_date_falls_back_instead_of_killing_the_ingest(repo, stem, bad):
    _write(repo, f"keps/{stem}/README.md", "# X\n\nProse long enough to be a summary paragraph here.\n")
    _write(repo, f"keps/{stem}/kep.yaml",
           f"title: Bad date\nstatus: implementable\ncreation-date: {bad}\n")
    _write(repo, "keps/good/README.md", "# Y\n\nProse long enough to be a summary paragraph here.\n")
    _write(repo, "keps/good/kep.yaml", "title: Good one\nstatus: implementable\ncreation-date: 2024-01-05\n")
    store = _ingest(repo)

    for ev in store.load_events():
        parse_ts(ev.ts)  # the whole point: append_event no longer raises
    ent = store.load_entities()[f"adr-{stem.split('-', 1)[1]}"]
    assert any(f.startswith(f"declared date {bad} is not a calendar date") for f in ent.key_facts)
    assert "adr-good-one" in store.load_entities()  # one bad file costs one date, not the corpus


def test_calendar_validation_is_not_just_the_shape():
    assert adr._is_calendar_date("2020-09-14")
    assert not adr._is_calendar_date("2023-14-05")   # month 14
    assert not adr._is_calendar_date("2025-30-01")   # day 30 in position 2
    assert not adr._is_calendar_date("2021-02-30")   # shape-valid, not a date


# ------------------------------------------------------------------ fix 2

def test_as_of_does_not_backdate_a_mutable_status(repo):
    _write(repo, "keps/sig-node/2221-remove-dockershim/README.md",
           "# Whatever\n\nProse about removing dockershim, long enough to summarize.\n")
    _write(repo, "keps/sig-node/2221-remove-dockershim/kep.yaml", DOCKERSHIM)
    store = _ingest(repo)

    creation = next(e for e in store.load_events() if e.id.startswith("evt-adr-removing"))
    status = next(e for e in store.load_events() if e.id.startswith("evt-adr-status-removing"))
    assert creation.ts == "2020-09-14"
    assert status.ts > "2022-01-01"  # observed at ingest, not at creation

    past, _, _ = assemble.replay(store, as_of="2021-01-01")
    ent = past["adr-remove-dockershim"]
    assert ent.status == "active"  # the neutral default, not a claim
    assert not any("implemented" in f for f in ent.key_facts)
    # a pointer at git, not a claim of absence: asserting "not recorded" made an
    # arm discard the historical value it had already read out of git history
    fact = next(f for f in ent.key_facts if "status on 2020-09-14" in f)
    assert "git history" in fact
    assert "not recorded" not in fact

    now, _, _ = assemble.replay(store)
    assert any(f.startswith("status=implemented (kep) as of ") for f in now["adr-remove-dockershim"].key_facts)


def test_last_updated_dates_the_status_observation(repo):
    _write(repo, "keps/753-sidecar/README.md", "# S\n\nProse long enough to be a summary paragraph here.\n")
    _write(repo, "keps/753-sidecar/kep.yaml",
           "title: Sidecar Containers\nstatus: implemented\n"
           "creation-date: 2018-05-14\nlast-updated: 2025-01-23\n")
    store = _ingest(repo)
    status = next(e for e in store.load_events() if e.id.startswith("evt-adr-status-"))
    assert status.ts == "2025-01-23"

    past, _, _ = assemble.replay(store, as_of="2022-06-01")
    assert not any("implemented" in f for f in past["adr-sidecar"].key_facts)


def test_a_plain_markdown_adr_keeps_its_status_on_its_own_dated_event(repo):
    # the status line lives in the record itself, not in a tracker edited later
    _write(repo, "keps/0001-postgres.md",
           "# Use Postgres\n\nStatus: accepted\nDate: 2026-01-15\n\n"
           "A paragraph of prose comfortably long enough to be a summary.\n")
    store = _ingest(repo)
    evs = [e for e in store.load_events() if e.source == "adr"]
    assert len(evs) == 1 and evs[0].ts == "2026-01-15"


# ------------------------------------------------------------------ fix 3
# Most trackers carry no `last-updated` (418 of 656 kep.yaml upstream), and
# dating those at ingest time asserts a status change that never happened
# today. The commit that wrote the status line carries the real date.

KEP = ("title: Declarative Validation\nowning-sig: sig-api-machinery\n"
       "status: {status}\ncreation-date: {created}\n{extra}")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True,
                   env={**os.environ, "GIT_AUTHOR_DATE": "2025-02-13T12:00:00",
                        "GIT_COMMITTER_DATE": "2025-02-13T12:00:00",
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"})


def _record(root, status="superseded", extra="", created="2023-08-20"):
    """A KEP under `root/keps`, README plus tracker."""
    _write(root, "keps/4153-declarative-validation/README.md",
           "# Declarative Validation\n\nProse long enough to serve as a summary paragraph.\n")
    _write(root, "keps/4153-declarative-validation/kep.yaml",
           KEP.format(status=status, extra=extra, created=created))


def _collect(root):
    src = SourceConfig(adapter="adr", path="keps", glob="**/*.md")
    return AdrAdapter(root, CaptainConfig(), src, {}, log=lambda *_: None).collect()


def _status_of(root):
    return next(e for e in _collect(root) if e.id.startswith("evt-adr-status-"))


def _committed(root, status="superseded", extra="", created="2023-08-20"):
    """`root/keps` as a git repo where the status line lands in a dated commit."""
    (root / "keps").mkdir(parents=True, exist_ok=True)
    _git(root / "keps", "init", "-q")
    _record(root, status="provisional", created=created)
    _git(root / "keps", "add", "-A")
    _git(root / "keps", "commit", "-qm", "propose")
    _record(root, status=status, extra=extra, created=created)
    _git(root / "keps", "add", "-A")
    _git(root / "keps", "commit", "-qm", "KEP-5073: supersede 4153")
    return _git_sha(root / "keps")


def _git_sha(repo):
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()[:12]


def test_status_without_last_updated_is_dated_from_the_commit_that_wrote_it(tmp_path):
    sha = _committed(tmp_path)
    ev = _status_of(tmp_path)
    assert ev.ts == "2025-02-13"                                  # not the ingest date
    assert Ref(kind="commit", id=sha) in ev.refs                  # checkable
    fact = ev.derived_entities[0]["profile"]["key_facts"][0]
    assert fact == f"status=superseded (kep) as of 2025-02-13 (commit {sha})"


def test_last_updated_still_wins_over_the_git_date(tmp_path):
    _committed(tmp_path, extra="last-updated: 2026-01-09\n")
    ev = _status_of(tmp_path)
    assert ev.ts == "2026-01-09"
    assert not [r for r in ev.refs if r.kind == "commit"]  # the date is not the commit's
    assert "kep.yaml:last-updated" in ev.derived_entities[0]["profile"]["key_facts"][0]


def test_an_uncalendar_last_updated_falls_back_to_the_git_date(tmp_path):
    sha = _committed(tmp_path, extra="last-updated: 2023-14-05\n")  # real upstream typo
    ev = _status_of(tmp_path)
    assert ev.ts == "2025-02-13"
    assert Ref(kind="commit", id=sha) in ev.refs


def test_a_commit_predating_the_decision_is_not_cited_as_its_status_date(tmp_path):
    _committed(tmp_path, created="2026-01-01")  # tracker committed 2025-02-13
    ev = _status_of(tmp_path)
    assert ev.ts == "2026-01-01"
    assert not [r for r in ev.refs if r.kind == "commit"]
    assert "dated from creation" in ev.derived_entities[0]["profile"]["key_facts"][0]


def test_a_plain_directory_falls_back_to_the_ingest_date(tmp_path):
    _record(tmp_path)  # no git repo anywhere above it
    ev = _status_of(tmp_path)
    assert ev.ts == adr._today()
    assert "as ingested; source has no dated history" in \
        ev.derived_entities[0]["profile"]["key_facts"][0]
    assert not [r for r in ev.refs if r.kind == "commit"]


def test_the_recovered_date_does_not_change_the_event_id(tmp_path):
    """`rebuild` replays the log by id: re-dating an event must not mint a
    second one under a new id."""
    plain, versioned = tmp_path / "plain", tmp_path / "versioned"
    _record(plain)
    _committed(versioned)
    a, b = _status_of(plain), _status_of(versioned)
    assert a.ts != b.ts
    assert a.id == b.id


def test_git_failure_is_not_an_ingest_failure(tmp_path, monkeypatch):
    """Blobless clones cannot run the pickaxe: no date, no crash."""
    _record(tmp_path)
    monkeypatch.setattr(adr.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no git")))
    assert _status_of(tmp_path).ts == adr._today()
