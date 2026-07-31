"""Plugin hooks: SessionStart persona (#6), SubagentStart house rules (#7),
and PreToolUse delegation enforcement (#5). Each hook is driven as a
subprocess with a synthetic stdin payload."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "plugin" / "hooks"
HOOK = PLUGIN / "session_start.sh"


def run(cwd, source):
    return subprocess.run(
        ["sh", str(HOOK)], cwd=cwd, capture_output=True, text=True,
        input=json.dumps({"hook_event_name": "SessionStart", "source": source}),
    ).stdout


@pytest.fixture()
def captain(tmp_path):
    base = tmp_path / ".tsubasa"
    (base / "memory").mkdir(parents=True)
    (base / "captain.toml").write_text('[captain]\nname = "testcap"\nrole = "Principal Engineer"\n')
    (base / "memory/hot.md").write_text("# hot knowledge\n- HOT_TIER_MARKER\n")
    return tmp_path


def test_matcher_covers_all_four_events():
    cfg = json.loads((PLUGIN / "hooks.json").read_text())
    matcher = cfg["hooks"]["SessionStart"][0]["matcher"]
    assert set(matcher.split("|")) == {"startup", "resume", "clear", "compact"}


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact"])
def test_persona_emitted_on_every_event(captain, source):
    out = run(captain, source)
    assert "You are captain-testcap (Principal Engineer)" in out
    assert "GRAPH-FIRST" in out
    # the standing rules moved to .tsubasa/persona.md (included by CLAUDE.md, so
    # they survive a compaction); the hook points at them instead of restating them
    assert ".tsubasa/persona.md" in out


@pytest.mark.parametrize("source,wanted", [
    ("startup", True), ("resume", True), ("clear", False), ("compact", False),
])
def test_hot_tier_only_on_fresh_sessions(captain, source, wanted):
    # on clear/compact hot.md still reaches the model through CLAUDE.md's import,
    # so re-emitting it here would spend context the compaction just reclaimed
    assert ("HOT_TIER_MARKER" in run(captain, source)) is wanted


def test_found_from_a_subdirectory(captain):
    sub = captain / "repo-a" / "pkg"
    sub.mkdir(parents=True)
    assert "You are captain-testcap" in run(sub, "compact")


def test_silent_outside_a_captain(tmp_path):
    assert run(tmp_path, "startup").strip() == ""


def test_unknown_or_absent_source_keeps_full_output(captain):
    # a future event name must not silently drop the hot tier
    assert "HOT_TIER_MARKER" in run(captain, "somethingelse")
    out = subprocess.run(["sh", str(HOOK)], cwd=captain, capture_output=True,
                         text=True, input="").stdout
    assert "You are captain-testcap" in out and "HOT_TIER_MARKER" in out


# --- SubagentStart: invariant house rules reach Task-spawned workers (issue #7) ---
#
# Payload shape confirmed live against Claude Code 2.1.220: a SubagentStart
# payload carries both `agent_id` and `agent_type` (observed "Explore" and
# "general-purpose"), so scoping on agent_type is safe.

SUBAGENT = PLUGIN / "subagent_start.sh"
DELEGATE_ONLY = PLUGIN / "delegate_only.sh"


def run_hook(hook, cwd, payload, env=None):
    e = dict(os.environ)
    e.pop("TSUBASA_SUBAGENT_MATCHER", None)
    e.update(env or {})
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(["sh", str(hook)], cwd=cwd, capture_output=True,
                          text=True, input=body, env=e)


def subagent_payload(agent_type="general-purpose"):
    return {"hook_event_name": "SubagentStart", "agent_id": "a0723274d63061432",
            "agent_type": agent_type, "session_id": "s", "cwd": "."}


def injected(proc):
    if not proc.stdout.strip():
        return ""
    out = json.loads(proc.stdout)
    return out["hookSpecificOutput"]["additionalContext"]


def test_subagent_hook_registered():
    cfg = json.loads((PLUGIN / "hooks.json").read_text())
    entry = cfg["hooks"]["SubagentStart"][0]
    assert entry["matcher"] == "*"
    assert entry["hooks"][0]["command"].endswith("/hooks/subagent_start.sh")


def test_injects_the_invariant_half(captain):
    ctx = injected(run_hook(SUBAGENT, captain, subagent_payload()))
    assert "I don't know" in ctx                      # citation contract
    assert "Co-Authored-By" in ctx                    # no AI attribution
    assert "Branch names carry the ADR id" in ctx     # branch convention
    assert "escalate to the captain" in ctx.lower()   # escalate, do not guess
    assert "tsubasa query" in ctx


def test_injection_is_tight():
    # it costs tokens on every spawn and competes with the brief for attention
    ctx = injected(run_hook(SUBAGENT, Path(__file__).parent.parent, subagent_payload()))
    assert len(ctx) < 800


def test_silent_outside_a_captain_workspace(tmp_path):
    assert run_hook(SUBAGENT, tmp_path, subagent_payload()).stdout.strip() == ""


@pytest.mark.parametrize("matcher,agent_type,wanted", [
    ("explore|general", "general-purpose", True),   # unanchored
    ("EXPLORE", "Explore", True),                   # case-insensitive
    ("^Explore$", "Explore", True),                 # anchorable
    ("^Explore$", "general-purpose", False),        # definite mismatch: skip
    ("explore", "general-purpose", False),
])
def test_matcher_scopes_on_agent_type(captain, matcher, agent_type, wanted):
    proc = run_hook(SUBAGENT, captain, subagent_payload(agent_type),
                    env={"TSUBASA_SUBAGENT_MATCHER": matcher})
    assert bool(proc.stdout.strip()) is wanted


@pytest.mark.parametrize("payload", [
    "not json at all",
    "",
    '{"hook_event_name": "SubagentStart"}',   # no agent_type to scope on
    '{"agent_type": ',                        # truncated
])
def test_unparseable_payload_fails_open(captain, payload):
    # a worker told the rules twice is harmless; one that silently gets none is the bug
    proc = run_hook(SUBAGENT, captain, payload, env={"TSUBASA_SUBAGENT_MATCHER": "explore"})
    assert "I don't know" in injected(proc)


def test_bad_regex_fails_open(captain):
    proc = run_hook(SUBAGENT, captain, subagent_payload(),
                    env={"TSUBASA_SUBAGENT_MATCHER": "[unclosed("})
    assert "I don't know" in injected(proc)


def test_delegate_skill_no_longer_restates_the_invariants():
    skill = (PLUGIN.parent / "skills" / "delegate" / "SKILL.md").read_text()
    assert "the branch convention: include the ADR id in the branch name" not in skill
    assert "tsubasa task" not in skill and "in_progress" not in skill
    assert "SubagentStart hook" in skill


# --- PreToolUse: the captain delegates, workers implement (issue #5) ---
#
# Confirmed live: a main-thread Write payload has no `agent_id`/`agent_type`,
# a subagent's has both, and session_id/cwd/permission_mode are identical.

def write_payload(path, agent=False):
    p = {"hook_event_name": "PreToolUse", "tool_name": "Write",
         "session_id": "s", "cwd": ".", "tool_input": {"file_path": path}}
    if agent:
        p["agent_id"] = "a575f0364ab27c99e"
        p["agent_type"] = "general-purpose"
    return p


@pytest.fixture()
def armed(captain):
    (captain / ".tsubasa/captain.toml").write_text(
        '[captain]\nname = "testcap"\ndelegate_only = true\n')
    return captain


@pytest.fixture()
def nojq(tmp_path_factory):
    """A PATH with the hook's tools but no jq, to exercise the sed fallback."""
    d = tmp_path_factory.mktemp("nojq")
    for tool in ("sh", "cat", "grep", "sed", "head", "dirname", "env"):
        src = shutil.which(tool)
        if src:
            (d / tool).symlink_to(src)
    return {"PATH": str(d)}


def test_delegate_hook_registered():
    cfg = json.loads((PLUGIN / "hooks.json").read_text())
    entry = cfg["hooks"]["PreToolUse"][0]
    assert set(entry["matcher"].split("|")) == {"Edit", "Write", "NotebookEdit"}
    assert entry["hooks"][0]["command"].endswith("/hooks/delegate_only.sh")


def test_worker_write_is_allowed(armed):
    proc = run_hook(DELEGATE_ONLY, armed, write_payload("/repo/src/app.py", agent=True))
    assert proc.returncode == 0 and proc.stderr == ""


def test_captain_write_to_source_is_blocked(armed):
    proc = run_hook(DELEGATE_ONLY, armed, write_payload("/repo/src/app.py"))
    assert proc.returncode == 2
    assert "src/app.py" in proc.stderr and "Agent tool" in proc.stderr


def test_flag_off_never_blocks(captain):
    # default is off: absent flag must not block anything
    assert run_hook(DELEGATE_ONLY, captain, write_payload("/repo/src/app.py")).returncode == 0
    (captain / ".tsubasa/captain.toml").write_text("[captain]\ndelegate_only = false\n")
    assert run_hook(DELEGATE_ONLY, captain, write_payload("/repo/src/app.py")).returncode == 0


def test_outside_a_captain_workspace_never_blocks(tmp_path):
    assert run_hook(DELEGATE_ONLY, tmp_path, write_payload("/repo/src/app.py")).returncode == 0


@pytest.mark.parametrize("path", [
    "/repo/docs/adr/adr-foo.md",   # captain-capture writes these itself
    "/repo/docs/runbook.txt",
    "/repo/adr/adr-foo.md",
    "/repo/.tsubasa/captain.toml",
    "/repo/.tsubasa/memory/hot.md",
    "/repo/README.md",
    "/repo/src/notes.md",
])
def test_knowledge_paths_stay_writable_while_armed(armed, path):
    assert run_hook(DELEGATE_ONLY, armed, write_payload(path)).returncode == 0


@pytest.mark.parametrize("path", [
    "/repo/src/app.py", "/repo/pyproject.toml", "/repo/tests/test_app.py",
    "/repo/notebook.ipynb", "/repo/Makefile",
])
def test_source_paths_are_blocked_while_armed(armed, path):
    assert run_hook(DELEGATE_ONLY, armed, write_payload(path)).returncode == 2


def test_unreadable_payload_does_not_block(armed):
    # cannot see what is being written: allow rather than block on a payload
    # we do not understand. This is a discipline guard, not a security boundary.
    assert run_hook(DELEGATE_ONLY, armed, "not json").returncode == 0


def test_grep_fallback_without_jq(armed, nojq):
    assert run_hook(DELEGATE_ONLY, armed, write_payload("/repo/src/app.py"),
                    env=nojq).returncode == 2
    assert run_hook(DELEGATE_ONLY, armed, write_payload("/repo/src/app.py", agent=True),
                    env=nojq).returncode == 0
    assert run_hook(DELEGATE_ONLY, armed, write_payload("/repo/docs/x.txt"),
                    env=nojq).returncode == 0


def test_session_start_reports_enforcement_is_live(armed):
    out = run(armed, "startup")
    assert "delegate_only is ON" in out and "WARNING" not in out


def test_session_start_warns_when_the_hook_is_not_installed(armed, tmp_path_factory):
    # hooks bind at session start, so an older plugin build enforces nothing
    old = tmp_path_factory.mktemp("oldplugin")
    shutil.copy(HOOK, old / "session_start.sh")
    (old / "hooks.json").write_text('{"hooks": {"SessionStart": []}}')
    proc = subprocess.run(["sh", str(old / "session_start.sh")], cwd=armed,
                          capture_output=True, text=True,
                          input=json.dumps({"source": "startup"}))
    assert "WARNING: delegate_only = true" in proc.stdout
    assert "restart" in proc.stdout
