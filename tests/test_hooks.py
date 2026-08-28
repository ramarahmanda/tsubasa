"""Plugin hooks: SessionStart persona (#6), SubagentStart house rules (#7),
PreToolUse delegation enforcement (#5) and UserPromptSubmit context regrouping
(adr-session-context-regrouping). Each hook is driven as a subprocess with a
synthetic stdin payload."""

import json
import os
import shutil
import subprocess
import sys
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


def test_init_scaffolded_config_arms_the_hook(tmp_path):
    # default-on at init: the exact line CONFIG_TEMPLATE writes must match the grep
    from tsubasa import config as cfg_mod
    (tmp_path / ".tsubasa").mkdir()
    (tmp_path / ".tsubasa/captain.toml").write_text(cfg_mod.CONFIG_TEMPLATE.format(
        schema_version=cfg_mod.SCHEMA_VERSION, name="fresh", role="Engineering Director",
        domains="# payments = 1.0", sources=""))
    assert run_hook(DELEGATE_ONLY, tmp_path, write_payload("/repo/src/app.py")).returncode == 2
    assert run_hook(DELEGATE_ONLY, tmp_path,
                    write_payload("/repo/src/app.py", agent=True)).returncode == 0


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


# --- PreToolUse: fleet-watch reminder at Agent spawn time ---
#
# Non-blocking delivery verified against Claude Code 2.1.220: PreToolUse
# hookSpecificOutput.additionalContext is injected into the model context
# independent of the permission flow; plain stdout on exit 0 never reaches
# the model, exit 2 blocks. Hence the hook speaks JSON and only JSON.

AGENT_SPAWN = PLUGIN / "agent_spawn.sh"


def spawn_payload(agent=False):
    p = {"hook_event_name": "PreToolUse", "tool_name": "Agent",
         "session_id": "s", "cwd": ".",
         "tool_input": {"description": "impl", "prompt": "do the thing"}}
    if agent:
        p["agent_id"] = "a575f0364ab27c99e"
    return p


def test_agent_spawn_hook_registered():
    cfg = json.loads((PLUGIN / "hooks.json").read_text())
    entry = cfg["hooks"]["PreToolUse"][1]
    # anchored: a bare "Task" would also match TaskOutput/TaskStop
    assert entry["matcher"] == "^(Agent|Task)$"
    assert entry["hooks"][0]["command"].endswith("/hooks/agent_spawn.sh")


def test_spawn_reminder_reaches_the_captain_without_blocking(captain):
    proc = run_hook(AGENT_SPAWN, captain, spawn_payload())
    assert proc.returncode == 0  # a reminder, never a block
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert "fleet watch" in out["additionalContext"]
    assert "/loop 3m" in out["additionalContext"]
    assert "permissionDecision" not in out  # permission flow stays untouched


def test_spawn_reminder_is_silent_for_workers(captain):
    proc = run_hook(AGENT_SPAWN, captain, spawn_payload(agent=True))
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_spawn_reminder_is_silent_outside_a_captain(tmp_path):
    proc = run_hook(AGENT_SPAWN, tmp_path, spawn_payload())
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_spawn_reminder_sed_fallback_without_jq(captain, nojq):
    proc = run_hook(AGENT_SPAWN, captain, spawn_payload(), env=nojq)
    assert "fleet watch" in proc.stdout
    proc = run_hook(AGENT_SPAWN, captain, spawn_payload(agent=True), env=nojq)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


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


# --- UserPromptSubmit: regroup before acting on an ambiguous prompt ---
#
# adr-session-context-regrouping. The hook decides only *when* to speak; the
# captain groups the session from context it already holds. Nothing on this
# path calls a model, and every failure is a silent exit 0: breaking a prompt
# costs more than missing an ambiguous one.
#
# The fixtures below are recorded Claude Code transcript records (schema as
# observed on 2.1.226). That schema is not a public contract, so these pin the
# shape the extraction was written against.

CONTEXT_CHECK = PLUGIN / "context_check.sh"
CWD = "/Users/dev/work/ops"


def _user(text):
    return {"type": "user", "cwd": CWD, "sessionId": "s", "version": "2.1.226",
            "uuid": "u", "timestamp": "2026-08-29T10:00:00.000Z",
            "message": {"role": "user", "content": text}}


def _tool(name, inp):
    return {"type": "assistant", "cwd": CWD, "sessionId": "s", "version": "2.1.226",
            "uuid": "a", "timestamp": "2026-08-29T10:00:00.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t", "name": name, "input": inp}]}}


# turns 1..5: akasha AB, vault creds read and a chart edited
AB = [
    _user("rotate the akasha AB database password"),
    _tool("Bash", {"command": "vault kv get akasha/ab/db", "description": "AB creds"}),
    _tool("Read", {"file_path": f"{CWD}/deploy/ab/values.yaml"}),
    _tool("Edit", {"file_path": f"{CWD}/deploy/ab/values.yaml",
                   "old_string": "old", "new_string": "new"}),
    _tool("Bash", {"command": "vault kv put akasha/ab/db password=x"}),
]
# turns 6..9: akasha ZY, a different namespace and a different chart
ZY = [
    _user("now check akasha ZY"),
    _tool("Bash", {"command": "kubectl -n akasha-zy get pods"}),
    _tool("Read", {"file_path": f"{CWD}/deploy/zy/values.yaml"}),
    _tool("Bash", {"command": "kubectl -n akasha-zy describe deploy/zy/api"}),
    _tool("Read", {"file_path": f"{CWD}/deploy/zy/values.yaml"}),
]


def transcript(cwd, records, name="transcript.jsonl"):
    path = Path(cwd) / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return str(path)


def prompt_hook(cwd, transcript_path, prompt):
    # the hook shells out to `tsubasa`, so put this interpreter's bin dir first
    path = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    return run_hook(CONTEXT_CHECK, cwd, {
        "hook_event_name": "UserPromptSubmit", "session_id": "s", "cwd": str(cwd),
        "transcript_path": transcript_path, "prompt": prompt}, env={"PATH": path})


def test_context_check_hook_registered():
    cfg = json.loads((PLUGIN / "hooks.json").read_text())
    entry = cfg["hooks"]["UserPromptSubmit"][0]
    assert entry["hooks"][0]["command"].endswith("/hooks/context_check.sh")


def test_two_contexts_and_a_deictic_prompt_asks_which_one(captain):
    # the ADR's own scenario: AB's chart was edited, ZY was read last, and
    # "push the fix" would otherwise resolve to whatever came most recently
    proc = prompt_hook(captain, transcript(captain, AB + ZY), "push the fix")
    assert proc.returncode == 0
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    ctx = out["additionalContext"]
    assert ctx.startswith("CONTEXT CHECK: this session touches 2 contexts")
    assert "deploy/ab" in ctx and "deploy/zy" in ctx   # the mechanical evidence
    assert "doing / access / dev / test / next" in ctx  # the card format travels here
    assert "Do not act until the user answers" in " ".join(ctx.split())


def test_a_prompt_that_names_a_context_is_left_alone(captain):
    # the scope is already unambiguous: asking again is the expensive error
    proc = prompt_hook(captain, transcript(captain, AB + ZY), "push the ab fix")
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_a_single_context_session_is_never_asked(captain):
    # one repo, one namespace, one chart: nothing to disambiguate
    proc = prompt_hook(captain, transcript(captain, AB), "push the fix")
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_a_prompt_with_a_referent_is_not_deictic(captain):
    proc = prompt_hook(captain, transcript(captain, AB + ZY),
                       "compare the two values files and tell me what differs")
    assert proc.returncode == 0 and proc.stdout.strip() == ""


@pytest.mark.parametrize("body", [
    "not a transcript at all\n",
    '{"type": "assistant", "message": {"content": ',   # truncated mid-record
    "",
])
def test_a_malformed_transcript_never_breaks_the_prompt(captain, body):
    # the transcript schema is not a public contract; a format change must
    # disarm the hook, never block the user (ADR, Risks)
    path = captain / "broken.jsonl"
    path.write_text(body)
    proc = prompt_hook(captain, str(path), "push the fix")
    assert proc.returncode == 0 and proc.stdout.strip() == ""


@pytest.mark.parametrize("payload", [
    {"prompt": "push the fix", "transcript_path": "/nonexistent/session.jsonl"},
    {"prompt": "push the fix"},          # no transcript_path at all
    {"transcript_path": "/nonexistent"},  # no prompt
    {},
])
def test_an_unknown_payload_shape_exits_silently(captain, payload):
    proc = run_hook(CONTEXT_CHECK, captain, payload)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_garbage_on_stdin_exits_silently(captain):
    assert run_hook(CONTEXT_CHECK, captain, "not json").stdout.strip() == ""


def test_silent_outside_a_captain(tmp_path):
    proc = prompt_hook(tmp_path, transcript(tmp_path, AB + ZY), "push the fix")
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_emitted_json_survives_the_sed_free_path(captain, nojq):
    # jq is optional; the awk fallback must produce the same escaped string
    tp = transcript(captain, AB + ZY)
    with_jq = prompt_hook(captain, tp, "push the fix").stdout
    d = Path(nojq["PATH"])
    for tool in ("awk", "printf"):
        src = shutil.which(tool)
        if src and not (d / tool).exists():
            (d / tool).symlink_to(src)
    (d / "tsubasa").symlink_to(shutil.which("tsubasa") or Path(sys.executable).parent / "tsubasa")
    without_jq = run_hook(CONTEXT_CHECK, captain, {
        "hook_event_name": "UserPromptSubmit", "transcript_path": tp,
        "prompt": "push the fix"}, env=nojq).stdout
    assert json.loads(without_jq) == json.loads(with_jq)
    assert "additionalContext" in json.loads(without_jq)["hookSpecificOutput"]


# The trigger contract itself, without a subprocess: both halves are required
# and the lists are closed (ADR, Decision b).

@pytest.mark.parametrize("prompt,deictic", [
    ("push the fix", True),
    ("deploy it", True),
    ("run that again", True),
    ("push", False),            # a verb with no dangling reference
    ("what changed?", False),   # neither half
    ("the runner committed", False),  # exact words only: no runner/committed
])
def test_deixis_needs_a_verb_and_a_reference(prompt, deictic):
    from tsubasa import contextcheck
    assert contextcheck.is_deictic(prompt) is deictic


def test_a_dimension_that_does_not_vary_is_ambient():
    from tsubasa import contextcheck
    # one repo mentioned all session cannot tell two contexts apart; two can.
    # A value seen once is noise either way (ADR: no invented contexts).
    assert contextcheck.targets({"repo": {"ops": 40}}) == []
    assert contextcheck.targets({"repo": {"ops": 40, "web": 1}}) == []
    assert contextcheck.targets({"repo": {"ops": 40, "web": 6}}) == ["ops", "web"]


# --- doctor: the half of tsubasa that `tsubasa upgrade` cannot deliver ---
#
# hooks.json ships with the plugin, not with the captain, so a CLI upgrade
# without `/plugin update` leaves the mechanisms absent and silent. That is how
# the delegate guard first shipped disarmed
# (evt-20260802-delegate-only-defaults-on-discipline-guards-ship). Reporting
# only: a stale plugin is not a corrupt graph and must not fail the run.

REPO = PLUGIN.parent.parent


@pytest.fixture()
def scaffolded(tmp_path, monkeypatch):
    from tsubasa import cli
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "plugincap", "--no-detect"]) == 0
    return tmp_path


def doctor(root, capsys, plugin_root):
    from tsubasa import cli
    os.environ["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    try:
        capsys.readouterr()
        code = cli.main(["doctor"])
    finally:
        os.environ.pop("CLAUDE_PLUGIN_ROOT")
    return code, capsys.readouterr().out


def test_doctor_reports_a_plugin_missing_the_prompt_hook(scaffolded, capsys, tmp_path):
    stale = tmp_path / "stale" / "hooks"
    stale.mkdir(parents=True)
    (stale / "hooks.json").write_text('{"hooks": {"SessionStart": []}}')
    code, out = doctor(scaffolded, capsys, stale.parent)
    assert "no UserPromptSubmit hook" in out and "/plugin update" in out
    assert code == 0   # reported, not failed: the graph is fine


def test_doctor_is_quiet_when_the_installed_plugin_is_current(scaffolded, capsys):
    code, out = doctor(scaffolded, capsys, REPO / "plugin")
    assert "UserPromptSubmit" not in out and code == 0


def test_doctor_survives_an_unlocatable_plugin(scaffolded, capsys, tmp_path):
    code, out = doctor(scaffolded, capsys, tmp_path / "gone")
    assert "installed plugin not found" in out and code == 0


def test_doctor_reports_manifest_version_disagreement(scaffolded, capsys):
    for rel, body in (("plugin/.claude-plugin/plugin.json", '{"version": "0.1.19"}'),
                      (".claude-plugin/marketplace.json", '{"metadata": {"version": "0.1.18"}}')):
        (scaffolded / rel).parent.mkdir(parents=True, exist_ok=True)
        (scaffolded / rel).write_text(body)
    code, out = doctor(scaffolded, capsys, REPO / "plugin")
    assert "version disagreement" in out and "0.1.18" in out
    assert code == 0
    (scaffolded / ".claude-plugin/marketplace.json").write_text(
        '{"metadata": {"version": "0.1.19"}}')
    assert "version disagreement" not in doctor(scaffolded, capsys, REPO / "plugin")[1]


def test_the_shipped_manifests_agree():
    # the release pair: plugin.json is what Claude Code installs, marketplace.json
    # is what it resolves the version from
    plug = json.loads((REPO / "plugin/.claude-plugin/plugin.json").read_text())["version"]
    market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
    assert plug == market["metadata"]["version"]
