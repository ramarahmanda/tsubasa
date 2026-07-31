"""study's speed work: the time window, the noise filter, concurrency (which
must stay bit-for-bit equal to a sequential run) and the incremental watermark."""

import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tsubasa import cli, config as cfg_mod, distill
from tsubasa.storage import Store

# A stub `claude` that answers from the prompt it was handed, with jittered
# latency so a thread pool is all but guaranteed to finish chunks out of order.
# Every chunk also emits one SHARED event id and one SHARED entity id whose
# content differs per chunk: whichever chunk is appended FIRST wins, so the
# graph on disk records the append order and not the completion order.
STUB = r'''#!/usr/bin/env python3
import json, random, re, sys, time
from pathlib import Path
prompt = sys.argv[sys.argv.index("-p") + 1]
Path(__file__ + ".calls").open("a").write("call\n")
rows = re.findall(r"^([0-9a-f]+)\|(\d{4}-\d\d-\d\d)\|(.*)$", prompt, re.M)
if not rows:
    print("[]"); sys.exit(0)
if any("FAIL" in r[2] for r in rows):
    sys.stderr.write("stub refuses this chunk\n"); sys.exit(3)
time.sleep(random.random() * 0.15)
first, last = rows[0], rows[-1]
print(json.dumps([
    {"title": "chunk from %s" % first[0], "summary": " ".join(r[2] for r in rows)[:200],
     "type": "note", "impact": "low", "domains": [], "date": last[1],
     "commits": [r[0] for r in rows[:3]],
     "entities": [{"id": "svc-%s" % first[0], "type": "service", "name": first[0],
                   "description": "chunk service"}],
     "relations": []},
    {"title": "shared theme", "summary": "first appended chunk wins: %s" % first[0],
     "type": "note", "impact": "low", "domains": [], "date": "2026-01-01",
     "commits": [first[0]],
     "entities": [{"id": "svc-shared", "type": "service", "name": "shared",
                   "description": "described by %s" % first[0]}],
     "relations": [{"source": "svc-shared", "predicate": "touched_by",
                    "target": "svc-%s" % first[0]}]},
]))
'''


# these tests count distillation calls, so the rest of the learning pipeline is
# off; the pipeline itself is covered in test_learn_pipeline.py
ONLY_STUDY = ["--no-index", "--no-resolve", "--no-link", "--no-profile"]


def stub_cmd(tmp_path: Path, name: str = "claude-stub") -> str:
    stub = tmp_path / name
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return f"{sys.executable} {stub}"


def calls(claude_cmd: str) -> int:
    log = Path(claude_cmd.split()[-1] + ".calls")
    return len(log.read_text().splitlines()) if log.is_file() else 0


def git(repo: Path, *args: str, date: str = "", author: str = "t <t@t>") -> str:
    name, _, email = author.partition(" <")
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email.rstrip(">"),
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email.rstrip(">")}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{date}T12:00:00"
    import os
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         env={**os.environ, **env}, check=True)
    return out.stdout.strip()


def commit(repo: Path, subject: str, date: str = "", author: str = "t <t@t>",
           files: dict[str, str] | None = None) -> None:
    for rel, body in (files or {}).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", subject, date=date, author=author)


def make_captain(tmp_path: Path, monkeypatch, subjects, name: str = "cap") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    cli.main(["init", name, "--domains", "auth"])
    svc = tmp_path / "svc"
    svc.mkdir()
    git(svc, "init", "-q", "-b", "main")
    for i, subject in enumerate(subjects):
        commit(svc, subject, date=f"2026-0{1 + i // 28}-{1 + i % 28:02d}")
    cli.main(["source", "add", "git", "svc"])
    return tmp_path


# ------------------------------------------------------------------ determinism

def graph_bytes(root: Path) -> dict[str, str]:
    base = root / ".tsubasa/graph"
    return {str(p.relative_to(base)): p.read_text()
            for p in sorted(base.rglob("*.toon"))}


def test_concurrent_study_matches_sequential(tmp_path, monkeypatch):
    """The whole point: --jobs 8 and --jobs 1 must produce the same ordered
    events and the same graph, byte for byte."""
    subjects = [f"feat: change number {i}" for i in range(60)]
    graphs, orders = [], []
    for jobs, name in ((1, "seq"), (8, "par")):
        root = make_captain(tmp_path / name, monkeypatch, subjects, name=name)
        cmd = stub_cmd(root)
        assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "5",
                         "--jobs", str(jobs), *ONLY_STUDY]) == 0
        assert calls(cmd) == 12  # 60 commits / 5 = 12 chunks, one call each
        events = [e for e in Store(root).load_events() if e.source == "study"]
        assert len(events) == 13  # 12 per-chunk + 1 shared
        orders.append([e.id for e in events])
        graphs.append(graph_bytes(root))
    assert orders[0] == orders[1]
    assert graphs[0] == graphs[1]
    # the shared event/entity resolved to the OLDEST chunk in both runs, i.e.
    # append order, not completion order
    shared = next(e for e in Store(tmp_path / "par").load_events() if e.title.endswith("shared theme"))
    first_sha = git(tmp_path / "par" / "svc", "log", "--reverse", "--format=%h").splitlines()[0]
    assert first_sha in shared.summary


def test_failed_chunk_does_not_kill_the_pool(tmp_path, monkeypatch):
    subjects = [f"feat: change {i}" for i in range(20)]
    subjects[5] = "FAIL: stub exits nonzero here"   # lands in chunk 2 of 4
    root = make_captain(tmp_path / "a", monkeypatch, subjects)
    cmd = stub_cmd(root)
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "5", "--jobs", "4",
                     *ONLY_STUDY]) == 0
    events = [e for e in Store(root).load_events() if e.source == "study"]
    assert len(events) == 4  # 3 surviving chunks + the shared event


# ------------------------------------------------------------------ noise filter

def mk(subject="feat: real work", author="Alice <a@x.io>", files=("src/main.go",)):
    return distill._Commit("abc1234", "2026-01-01", author, subject, tuple(files))


@pytest.mark.parametrize("commit_, reason", [
    (mk(author="dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"), "bot"),
    (mk(author="renovate[bot] <bot@renovateapp.com>"), "bot"),
    (mk(author="github-actions <actions@github.com>"), "bot"),
    (mk(files=("go.mod", "go.sum")), "mechanical"),
    (mk(files=("package-lock.json",)), "mechanical"),
    (mk(files=(".github/workflows/ci.yml",)), "mechanical"),
    (mk(files=("vendor/k8s.io/api/types.go",)), "mechanical"),
    (mk(subject="chore(deps): bump x from 1 to 2"), "mechanical"),
    (mk(subject="chore: tidy"), "mechanical"),
    (mk(subject="ci(release): pin runner"), "mechanical"),
    (mk(subject="build(deps): update toolchain"), "mechanical"),
    # a merge duplicates commits that are listed separately in the same log
    (distill._Commit("abc1234", "2026-01-01", "Alice <a@x.io>",
                     "Merge pull request #42 from foo/bar", (), merge=True), "merge"),
])
def test_filter_drops(commit_, reason):
    assert distill._noise(commit_) == reason


@pytest.mark.parametrize("commit_", [
    # a lockfile alongside real source is real work
    mk(files=("go.mod", "go.sum", "pkg/pool/pool.go")),
    # test() carries real knowledge; never filtered
    mk(subject="test(backup): widen backup-cleanup deadline margin against reconcile lag"),
    mk(subject="style: reformat the planner"),
    # free text is never enough on its own
    mk(subject="Revert bump allocator change"),
    mk(subject="Fix typo in the WAL replay bounds check that corrupted recovery"),
    mk(subject="deps are documented in chore.md"),
    # an empty non-merge commit carries no file evidence, so keep it
    mk(subject="fix: revert by empty commit", files=()),
])
def test_filter_keeps(commit_):
    assert distill._noise(commit_) == ""


def test_filter_is_on_by_default_and_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--domains", "auth"])
    svc = tmp_path / "svc"
    svc.mkdir()
    git(svc, "init", "-q", "-b", "main")
    commit(svc, "feat: real work", date="2026-01-01", files={"main.go": "package main\n"})
    commit(svc, "chore(deps): bump x", date="2026-01-02", files={"go.mod": "module x\n"})
    commit(svc, "fix: lockfile only", date="2026-01-03", files={"go.sum": "h1:aaa\n"})
    commit(svc, "feat: bot work", date="2026-01-04", author="dependabot[bot] <b@b>",
           files={"other.go": "package main\n"})
    cli.main(["source", "add", "git", "svc"])
    cmd = stub_cmd(tmp_path)
    assert cli.main(["study", "--claude-cmd", cmd, *ONLY_STUDY]) == 0
    out = capsys.readouterr().out
    assert "1 commits (3 filtered: 1 bot, 0 merge, 2 mechanical)" in out
    # --no-filter keeps everything
    assert cli.main(["study", "--claude-cmd", cmd, "--no-filter", *ONLY_STUDY]) == 0
    assert "4 commits (filter off)" in capsys.readouterr().out


# ------------------------------------------------------------------ window

@pytest.mark.parametrize("given, expected", [
    ("3y", "3 years ago"), ("18 months", "18 months ago"), ("18m", "18 months ago"),
    ("90d", "90 days ago"), ("6w", "6 weeks ago"), ("2015-01-01", "2015-01-01"),
    ("", ""), ("2 years ago", "2 years ago"), ("last friday", "last friday"),
])
def test_since_shorthand_expands(given, expected):
    assert distill.since_arg(given) == expected


def test_since_windows_git_log(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--domains", "auth"])
    svc = tmp_path / "svc"
    svc.mkdir()
    git(svc, "init", "-q", "-b", "main")
    for d in ("2015-03-01", "2015-04-01", "2015-05-01"):
        commit(svc, f"feat: ancient {d}", date=d)
    for d in ("2026-06-01", "2026-06-02"):
        commit(svc, f"feat: recent {d}", date=d)
    cli.main(["source", "add", "git", "svc"])
    assert len(distill._git_log(svc)) == 5
    assert len(distill._git_log(svc, since="3 years ago")) == 2
    assert len(distill._git_log(svc, since="2015-03-15")) == 4
    cmd = stub_cmd(tmp_path)
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "1", *ONLY_STUDY]) == 0
    assert "3y window, 2 commits" in capsys.readouterr().out
    assert calls(cmd) == 2
    # explicit empty window studies all history
    assert cli.main(["study", "--claude-cmd", cmd, "--since", "", "--chunk", "1",
                     *ONLY_STUDY]) == 0
    assert "all history, 5 commits" in capsys.readouterr().out


def test_max_chunks_composes_with_window(tmp_path, monkeypatch, capsys):
    root = make_captain(tmp_path / "a", monkeypatch, [f"feat: c{i}" for i in range(20)])
    cmd = stub_cmd(root)
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "5", "--max-chunks", "2",
                     "--jobs", "2", *ONLY_STUDY]) == 0
    out = capsys.readouterr().out
    assert "20 commits" in out and "2 chunk(s)" in out
    assert calls(cmd) == 2  # newest 2 chunks only


def test_unparseable_since_is_rejected(tmp_path, monkeypatch):
    root = make_captain(tmp_path / "a", monkeypatch, ["feat: one"])
    cmd = stub_cmd(root)
    # git resolves junk to "now" and would silently study nothing
    assert cli.main(["study", "--claude-cmd", cmd, "--since", "garbage",
                     *ONLY_STUDY]) == 1


# ------------------------------------------------------------------ watermark

def test_second_run_over_unchanged_repo_makes_no_llm_calls(tmp_path, monkeypatch, capsys):
    root = make_captain(tmp_path / "a", monkeypatch, [f"feat: c{i}" for i in range(10)])
    cmd = stub_cmd(root)
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "5", *ONLY_STUDY]) == 0
    assert calls(cmd) == 2
    mark = Store(root).load_state()["study"]["git:svc"]
    assert mark["sha"] == git(root / "svc", "log", "-1", "--format=%h")
    capsys.readouterr()
    assert cli.main(["study", "--claude-cmd", cmd, *ONLY_STUDY]) == 0
    assert calls(cmd) == 2                       # zero new calls
    assert "nothing new" in capsys.readouterr().out


def test_watermark_distils_only_the_new_slice(tmp_path, monkeypatch, capsys):
    root = make_captain(tmp_path / "a", monkeypatch, [f"feat: c{i}" for i in range(10)])
    cmd = stub_cmd(root)
    cli.main(["study", "--claude-cmd", cmd, "--chunk", "5", *ONLY_STUDY])
    before = calls(cmd)
    commit(root / "svc", "fix: brand new work", date="2026-03-01")
    capsys.readouterr()
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "5", *ONLY_STUDY]) == 0
    out = capsys.readouterr().out
    assert "resuming from" in out and "1 commits" in out
    assert calls(cmd) == before + 1


def test_failed_chunk_holds_the_watermark(tmp_path, monkeypatch):
    subjects = [f"feat: c{i}" for i in range(20)]
    subjects[12] = "FAIL: chunk 3 of 4 dies here"
    root = make_captain(tmp_path / "a", monkeypatch, subjects)
    cmd = stub_cmd(root)
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "5", "--jobs", "4",
                     *ONLY_STUDY]) == 0
    shas = git(root / "svc", "log", "--reverse", "--format=%h").splitlines()
    # chunks 1-2 are safe (commits 0-9); the watermark stops before the failure
    # even though chunk 4 succeeded
    mark = Store(root).load_state()["study"]["git:svc"]
    assert mark["sha"] == shas[9]
    # chunk 4 succeeded past the failure, so it is recorded by its own boundary
    # rather than re-paid for: the watermark alone cannot express that gap
    assert mark["done"] == [shas[19]]
    # so the next run redoes only the chunk that actually failed
    before = calls(cmd)
    cli.main(["study", "--claude-cmd", cmd, "--chunk", "5", "--jobs", "4", *ONLY_STUDY])
    assert calls(cmd) == before + 1


def test_first_chunk_failure_writes_no_watermark(tmp_path, monkeypatch):
    root = make_captain(tmp_path / "a", monkeypatch, ["FAIL: nothing survives"])
    cmd = stub_cmd(root)
    assert cli.main(["study", "--claude-cmd", cmd, *ONLY_STUDY]) == 0
    assert "git:svc" not in Store(root).load_state().get("study", {})


def test_max_chunks_does_not_advance_the_watermark(tmp_path, monkeypatch):
    root = make_captain(tmp_path / "a", monkeypatch, [f"feat: c{i}" for i in range(20)])
    cmd = stub_cmd(root)
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "5", "--max-chunks", "2",
                     *ONLY_STUDY]) == 0
    # older history was deliberately never distilled, so nothing may claim it was
    assert "git:svc" not in Store(root).load_state().get("study", {})


def test_explicit_since_overrides_the_watermark(tmp_path, monkeypatch, capsys):
    root = make_captain(tmp_path / "a", monkeypatch, [f"feat: c{i}" for i in range(10)])
    cmd = stub_cmd(root)
    cli.main(["study", "--claude-cmd", cmd, "--chunk", "5", *ONLY_STUDY])
    before = calls(cmd)
    capsys.readouterr()
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "5", "--since", "3y",
                     *ONLY_STUDY]) == 0
    assert "3y window, 10 commits" in capsys.readouterr().out
    assert calls(cmd) == before + 2      # re-distilled; event ids dedupe the result
    assert len([e for e in Store(root).load_events() if e.source == "study"]) == 3


def test_rewritten_history_falls_back_to_the_window(tmp_path, monkeypatch, capsys):
    root = make_captain(tmp_path / "a", monkeypatch, [f"feat: c{i}" for i in range(6)])
    cmd = stub_cmd(root)
    cli.main(["study", "--claude-cmd", cmd, "--chunk", "6", *ONLY_STUDY])
    state = Store(root).load_state()
    state["study"]["git:svc"]["sha"] = "deadbee"        # force push / re-clone
    Store(root).save_state(state)
    capsys.readouterr()
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "6", *ONLY_STUDY]) == 0
    out = capsys.readouterr().out
    assert "history rewritten" in out and "3y window, 6 commits" in out


def test_changed_filter_setting_rescans(tmp_path, monkeypatch, capsys):
    root = make_captain(tmp_path / "a", monkeypatch, [f"feat: c{i}" for i in range(6)])
    cmd = stub_cmd(root)
    cli.main(["study", "--claude-cmd", cmd, "--chunk", "6", *ONLY_STUDY])
    capsys.readouterr()
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "6", "--no-filter",
                     *ONLY_STUDY]) == 0
    assert "window/filter changed" in capsys.readouterr().out


def test_watermark_survives_a_run_that_dies_in_a_later_repo(tmp_path, monkeypatch):
    """Resume matters most when a run does not finish, so each repo's progress
    must be on disk before the next repo starts."""
    root = make_captain(tmp_path / "a", monkeypatch, ["feat: a", "feat: b"])
    second = root / "svc2"
    second.mkdir()
    git(second, "init", "-q", "-b", "main")
    commit(second, "FAIL: this repo's only chunk dies", date="2026-01-01")
    assert cli.main(["source", "add", "git", "svc2"]) == 0
    cmd = stub_cmd(root)
    # svc distils fine; svc2's only chunk fails, so it writes no watermark
    assert cli.main(["study", "--claude-cmd", cmd, *ONLY_STUDY]) == 0
    marks = Store(root).load_state()["study"]
    assert "git:svc" in marks, "the repo that finished must be persisted"
    assert "git:svc2" not in marks


# ------------------------------------------------------- per-source window

def make_repo(root: Path, name: str, dates: list[str]) -> Path:
    repo = root / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for i, date in enumerate(dates):
        commit(repo, f"feat: {name} {i}", date=date)
    return repo


def make_workspace(tmp_path: Path, monkeypatch) -> Path:
    """A deep repo whose decisions predate the default window, next to a young
    one for which the default is already everything."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--domains", "auth"])
    make_repo(tmp_path, "deep", ["2015-01-01", "2026-06-01"])
    make_repo(tmp_path, "young", ["2026-06-02", "2026-06-03"])
    assert cli.main(["source", "add", "git", "deep", "--since", "20y"]) == 0
    assert cli.main(["source", "add", "git", "young"]) == 0
    return tmp_path


def test_source_since_is_written_and_parsed(tmp_path, monkeypatch):
    root = make_workspace(tmp_path / "a", monkeypatch)
    by_path = {s.path: s for s in cfg_mod.load(root).sources if s.adapter == "git"}
    assert by_path["deep"].since == "20y"
    assert by_path["young"].since is None   # absent, not empty: means "use the default"
    assert "since" not in by_path["deep"].options


def test_each_source_studies_its_own_window(tmp_path, monkeypatch, capsys):
    root = make_workspace(tmp_path / "a", monkeypatch)
    cmd = stub_cmd(root)
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "10", *ONLY_STUDY]) == 0
    out = capsys.readouterr().out
    assert "deep: 20y window (source since), 2 commits" in out
    assert "young: 3y window, 2 commits" in out   # no `since`, so the default


def test_command_since_overrides_the_source_window(tmp_path, monkeypatch, capsys):
    root = make_workspace(tmp_path / "a", monkeypatch)
    cmd = stub_cmd(root)
    assert cli.main(["study", "--claude-cmd", cmd, "--since", "3y", "--chunk", "10",
                     *ONLY_STUDY]) == 0
    out = capsys.readouterr().out
    assert "deep: 3y window, 1 commits" in out    # the 2015 commit is outside it
    assert "young: 3y window, 2 commits" in out
    assert "(source since)" not in out


def test_source_since_shorthand_expands(tmp_path, monkeypatch, capsys):
    """A bare "10y" must reach git as "10 years ago". Handed through untouched,
    approxidate reads it as ~87 days and the window silently collapses."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--domains", "auth"])
    make_repo(tmp_path, "svc", ["2024-01-01"])     # older than 10 days, newer than 10y
    assert cli.main(["source", "add", "git", "svc", "--since", "10y"]) == 0
    cmd = stub_cmd(tmp_path)
    assert cli.main(["study", "--claude-cmd", cmd, *ONLY_STUDY]) == 0
    assert "10y window (source since), 1 commits" in capsys.readouterr().out
    assert Store(tmp_path).load_state()["study"]["git:svc"]["since"] == "10 years ago"


def test_empty_source_since_studies_all_history(tmp_path, monkeypatch, capsys):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--domains", "auth"])
    make_repo(tmp_path, "svc", ["2001-01-01", "2026-06-01"])
    assert cli.main(["source", "add", "git", "svc", "--since", ""]) == 0
    assert cfg_mod.load(tmp_path).sources[-1].since == ""
    cmd = stub_cmd(tmp_path)
    assert cli.main(["study", "--claude-cmd", cmd, *ONLY_STUDY]) == 0
    assert "all history (source since), 2 commits" in capsys.readouterr().out


def test_unparseable_source_since_is_rejected(tmp_path, monkeypatch, capsys):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--domains", "auth"])
    make_repo(tmp_path, "svc", ["2026-06-01"])
    assert cli.main(["source", "add", "git", "svc", "--since", "garbage"]) == 0
    cmd = stub_cmd(tmp_path)
    assert cli.main(["study", "--claude-cmd", cmd, *ONLY_STUDY]) == 1
    assert "source since 'garbage'" in capsys.readouterr().err


def test_changing_one_window_leaves_other_watermarks_intact(tmp_path, monkeypatch, capsys):
    """A settings change must invalidate the watermark of the source it changed
    and no other: a spurious global rescan re-pays for the whole workspace."""
    root = make_workspace(tmp_path / "a", monkeypatch)
    cmd = stub_cmd(root)
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "10", *ONLY_STUDY]) == 0
    marks = Store(root).load_state()["study"]
    assert marks["git:deep"]["since"] == "20 years ago"   # recorded per source
    assert marks["git:young"]["since"] == "3 years ago"
    young_before = dict(marks["git:young"])
    before = calls(cmd)
    capsys.readouterr()

    assert cli.main(["source", "add", "git", "deep", "--since", "30y"]) == 0
    assert cli.main(["study", "--claude-cmd", cmd, "--chunk", "10", *ONLY_STUDY]) == 0
    out = capsys.readouterr().out
    assert "deep: window/filter changed" in out
    assert "deep: 30y window (source since), 2 commits" in out
    assert "young: window/filter changed" not in out
    assert "young: resuming from" in out and "young: 30y" not in out
    assert Store(root).load_state()["study"]["git:young"] == young_before
    assert calls(cmd) == before + 1   # only the repo whose window moved was re-paid for


def test_pull_study_honours_the_source_window(tmp_path, monkeypatch, capsys):
    root = make_workspace(tmp_path / "a", monkeypatch)
    cmd = stub_cmd(root)
    assert cli.main(["pull", "--study", "--claude-cmd", cmd, *ONLY_STUDY]) == 0
    out = capsys.readouterr().out
    assert "deep: 20y window (source since), 2 commits" in out
    assert "young: 3y window, 2 commits" in out
