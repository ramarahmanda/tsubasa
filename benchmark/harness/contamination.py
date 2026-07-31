"""Contamination checks.

The vanilla arm is the whole experiment. If a captain persona, a captain
CLAUDE.md, the tsubasa plugin, the tsubasa CLI or carried-over memory reaches
a "vanilla" session, that run measures nothing and the benchmark quietly
becomes a lie. This module refuses to let that happen silently.

Two layers, because no single one is sufficient:

  preflight   filesystem + invocation facts, evaluated BEFORE the model call.
              A fatal failure aborts the run: no tokens are spent.
  postflight  evidence read back out of the recorded session: the persona
              string the SessionStart hook emits, the plugin's skills in the
              session init message, tsubasa CLI calls, .tsubasa/ reads.
              A fatal failure quarantines the run (status="contaminated");
              it is written to disk in full, excluded from headline numbers,
              counted in the summary, and re-run on the next invocation.

Arm B gets the mirror image: a captain arm that did not actually get a captain
is just as useless as a contaminated vanilla one, so the same machinery
asserts the persona IS present.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from config import (DOT_TSUBASA_RE, PERSONA_MARKERS, TOOLING_MARKER_RE,
                    read_fixture_lock)

CAPTAIN_CLAUDE_MD_MARKERS = ("@.tsubasa/", "Captain ", "captain-", "tsubasa")

# The init message's ACTIVE surfaces: what the session can actually invoke.
# `plugins` is deliberately NOT in this list. Measured on claude 2.1.220: a
# --safe-mode session still enumerates every INSTALLED plugin under `plugins`
# while loading none of its skills, commands, agents or hooks. The tsubasa
# plugin is installed globally on the machine this benchmark runs on, so
# keying contamination on the installed listing would quarantine all 72
# vanilla runs and delete the vanilla arm. Keying it on the active surfaces
# catches the thing that actually matters, and the hook/persona checks below
# assert inertness positively rather than merely stopping to look.
ACTIVE_INIT_KEYS = ("slash_commands", "skills", "agents", "tools", "mcp_servers")
INSTALLED_INIT_KEYS = ("plugins",)


def _check(name: str, ok: bool, detail: str = "", severity: str = "fatal") -> dict:
    return {"name": name, "ok": bool(ok), "severity": severity, "detail": detail}


def _ancestors(start: Path) -> list[Path]:
    p = start.resolve()
    return [p, *p.parents]


def _find_captain_tomls(cwd: Path) -> list[str]:
    """Exactly the walk plugin/hooks/session_start.sh does: cwd upward,
    looking for .tsubasa/captain.toml."""
    return [str(d / ".tsubasa" / "captain.toml")
            for d in _ancestors(cwd) if (d / ".tsubasa" / "captain.toml").is_file()]


def _find_claude_mds(cwd: Path) -> list[Path]:
    seen: dict[Path, None] = {}
    for d in [*_ancestors(cwd), Path.home()]:
        for cand in (d / "CLAUDE.md", d / ".claude" / "CLAUDE.md"):
            if cand.is_file():
                seen.setdefault(cand.resolve())
    return list(seen)


def _plugin_enabling_settings(cwd: Path) -> list[str]:
    """Any settings file on the ancestor chain that turns a plugin on. Under
    --safe-mode none of them load, which is exactly why this is asserted
    rather than assumed."""
    hits = []
    for d in _ancestors(cwd):
        for name in ("settings.json", "settings.local.json"):
            path = d / ".claude" / name
            if not path.is_file():
                continue
            try:
                if json.loads(path.read_text()).get("enabledPlugins"):
                    hits.append(str(path))
            except (OSError, ValueError):
                hits.append(f"{path} (unparseable)")
    return hits


def _captain_flavoured(path: Path) -> bool:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False
    return any(m in text for m in CAPTAIN_CLAUDE_MD_MARKERS)


def _project_memory_dirs(cwd: Path) -> list[str]:
    """Claude Code auto-memory is keyed on a slug of the session cwd. A fresh
    workspace has none; a re-used one may carry a prior run's memory."""
    base = Path.home() / ".claude" / "projects"
    raw = str(cwd.resolve())
    slugs = {raw.replace("/", "-"), raw.replace("/", "-").replace(".", "-")}
    return [str(base / s / "memory") for s in sorted(slugs)
            if (base / s / "memory").is_dir()]


def check_fixture_pin(fixture: Path, repos: tuple[str, ...]) -> list[dict]:
    """Every gold answer was written against fixture.lock. A drifted clone
    invalidates the gold set, not the arm, so this is checked for both arms."""
    pins = read_fixture_lock()
    checks = []
    for repo in repos:
        want = pins.get(repo, "")
        path = fixture / repo
        if not path.is_dir():
            checks.append(_check(f"fixture_present[{repo}]", False, f"missing {path}"))
            continue
        try:
            head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError) as e:
            checks.append(_check(f"fixture_pinned[{repo}]", False, f"git failed: {e}"))
            continue
        checks.append(_check(f"fixture_pinned[{repo}]", head == want,
                             f"HEAD={head[:12]} lock={want[:12]}"))

        # HEAD alone does not pin the tree. An uncommitted edit to a fixture
        # file leaves HEAD untouched and silently rewrites the answer the gold
        # was written against: measured here, `status: rejected` became
        # `status: provisional` between the two arms' runs, so each arm quoted
        # its own workspace correctly and only the later one was marked wrong.
        # A dirty tree invalidates the gold set exactly as a drifted HEAD does.
        try:
            dirty = subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                                   capture_output=True, text=True, timeout=60).stdout.strip()
        except (OSError, subprocess.SubprocessError) as e:
            checks.append(_check(f"fixture_clean[{repo}]", False, f"git failed: {e}"))
            continue
        files = [ln[3:] for ln in dirty.splitlines()][:5]
        checks.append(_check(f"fixture_clean[{repo}]", not dirty,
                             f"{len(dirty.splitlines())} modified: {', '.join(files)}"
                             if dirty else ""))
    return checks


def preflight(arm: str, cwd: Path, fixture: Path, repos: tuple[str, ...],
              argv: list[str], env: dict[str, str], allow_unpinned: bool = False) -> dict:
    checks: list[dict] = []

    pin_checks = check_fixture_pin(fixture, repos)
    if allow_unpinned:
        for c in pin_checks:
            c["severity"] = "warn"
    checks += pin_checks

    tomls = _find_captain_tomls(cwd)
    claude_mds = _find_claude_mds(cwd)
    captain_mds = [str(p) for p in claude_mds if _captain_flavoured(p)]
    memories = _project_memory_dirs(cwd)
    path_tsubasa = shutil.which("tsubasa", path=env.get("PATH", ""))

    if arm == "A":
        checks += [
            _check("no_captain_toml_above_cwd", not tomls,
                   f"cwd={cwd}; found={tomls}"),
            _check("no_captain_claude_md", not captain_mds,
                   f"CLAUDE.md on the path: {[str(p) for p in claude_mds]}; "
                   f"captain-flavoured: {captain_mds}"),
            _check("no_carried_project_memory", not memories, f"found={memories}"),
            _check("no_plugin_enabling_settings_above_cwd",
                   not _plugin_enabling_settings(cwd),
                   f"found={_plugin_enabling_settings(cwd)}"),
            _check("safe_mode_requested", "--safe-mode" in argv,
                   "--safe-mode disables CLAUDE.md, skills, plugins, hooks and MCP "
                   "for this session"),
            # Stated, not hidden: the tsubasa plugin IS enabled in user
            # settings on this machine, and --safe-mode is the only thing
            # standing between it and the vanilla arm. That is why
            # safe_mode_requested is fatal here and why the postflight asserts
            # inertness from the recorded session three independent ways.
            _check("plugin_enabled_in_user_settings_is_neutralised_by_safe_mode",
                   (not _plugin_enabled()) or "--safe-mode" in argv,
                   f"tsubasa enabled in ~/.claude/settings.json: {_plugin_enabled()}; "
                   f"--safe-mode in argv: {'--safe-mode' in argv}"),
            _check("session_not_persisted", "--no-session-persistence" in argv,
                   "no session state survives to the next run"),
            _check("tsubasa_cli_not_on_child_path", path_tsubasa is None,
                   f"resolved={path_tsubasa}"),
            _check("workspace_is_repos_only",
                   sorted(p.name for p in cwd.iterdir()) == sorted(repos),
                   f"contents={sorted(p.name for p in cwd.iterdir())}"),
        ]
    else:
        checks += [
            _check("captain_toml_resolves", bool(tomls), f"found={tomls}"),
            _check("captain_claude_md_present", bool(captain_mds), f"found={captain_mds}"),
            _check("graph_populated",
                   (fixture / ".tsubasa" / "graph" / "entities.toon").is_file(),
                   "captain graph must exist before arm B runs"),
            _check("session_not_persisted", "--no-session-persistence" in argv, ""),
            _check("plugin_enabled_in_user_settings", _plugin_enabled(),
                   "tsubasa plugin must be enabled for arm B", severity="warn"),
        ]

    fatal = [c["name"] for c in checks if not c["ok"] and c["severity"] == "fatal"]
    return {"phase": "preflight", "arm": arm, "passed": not fatal,
            "fatal": fatal, "checks": checks}


def _plugin_enabled() -> bool:
    path = Path.home() / ".claude" / "settings.json"
    if not path.is_file():
        return False
    try:
        settings = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return any(k.split("@")[0] == "tsubasa" and v
               for k, v in (settings.get("enabledPlugins") or {}).items())


def _init_hits(init_message: dict, keys: tuple[str, ...]) -> list[str]:
    names = []
    for key in keys:
        value = init_message.get(key) or []
        value = value if isinstance(value, list) else [value]
        for item in value:
            # plugins are dicts ({"name","source","version"}); skills and
            # commands are plain strings
            names.append(json.dumps(item, sort_keys=True) if isinstance(item, dict) else str(item))
    return sorted({n for n in names if "tsubasa" in n.lower()})


def postflight(arm: str, raw_stream: str, init_message: dict, tool_calls: list[dict],
               hook_events: list[dict] | None = None) -> dict:
    """Evidence read back out of the session itself.

    The plugin is installed globally on this machine, so "is the plugin
    installed" is not the question and never was. The question is whether it
    was ACTIVE in this session, and that is asserted three independent ways:
    no tsubasa entry on any invocable surface of the init message, no hook of
    the plugin's ever firing, and no persona string anywhere in the stream.
    All three are fatal for arm A. The installed-but-inert listing is recorded
    separately, as information, so a reader can see the plugin was present and
    see what evidence says it did nothing.
    """
    persona_hits = [m for m in PERSONA_MARKERS if m in raw_stream]
    active_hits = _init_hits(init_message, ACTIVE_INIT_KEYS)
    installed_hits = _init_hits(init_message, INSTALLED_INIT_KEYS)

    hook_events = hook_events or []
    tsubasa_hooks = sorted({str(h.get("hook_name") or h.get("hook_event") or "?")
                            for h in hook_events
                            if "tsubasa" in json.dumps(h, sort_keys=True).lower()
                            or any(m in str(h.get("output") or "") for m in PERSONA_MARKERS)})

    blob = "\n".join(json.dumps(tc.get("input", {}), sort_keys=True) for tc in tool_calls)
    cli_hits = bool(TOOLING_MARKER_RE.search(blob))
    dot_hits = bool(DOT_TSUBASA_RE.search(blob))

    if arm == "A":
        checks = [
            _check("no_persona_in_transcript", not persona_hits, f"markers={persona_hits}"),
            # keyed on the ACTIVE surfaces only: see ACTIVE_INIT_KEYS
            _check("no_tsubasa_plugin_active_in_session", not active_hits,
                   f"active surfaces {list(ACTIVE_INIT_KEYS)} -> tsubasa entries={active_hits}"),
            _check("no_tsubasa_hook_events", not tsubasa_hooks,
                   f"hooks fired={tsubasa_hooks}" if tsubasa_hooks
                   else f"{len(hook_events)} hook event(s) in the stream, none tsubasa's"),
            _check("no_tsubasa_cli_invocation", not cli_hits,
                   "a tool call ran the tsubasa CLI" if cli_hits else ""),
            _check("no_dot_tsubasa_access", not dot_hits,
                   "a tool call touched .tsubasa/" if dot_hits else ""),
            # Information, not a gate. The plugin IS installed globally; under
            # --safe-mode the CLI still enumerates it while loading none of it.
            # ok=True records "listed and demonstrably inert", which is the
            # expected state here and is what the four fatal checks above
            # establish. It is severity=warn so it can never mask them.
            _check("plugin_installed_but_inert",
                   not (installed_hits and (active_hits or tsubasa_hooks or persona_hits)),
                   f"installed listing={installed_hits}; active={active_hits}; "
                   f"tsubasa hooks={tsubasa_hooks}; persona={persona_hits}",
                   severity="warn"),
        ]
    else:
        # Arm B's mirror image, keyed on the same ACTIVE surfaces: a captain arm
        # whose plugin merely appears in the installed listing while loading
        # nothing is as useless as a contaminated vanilla one, and the old
        # installed-listing check could not tell those two apart.
        checks = [
            _check("persona_in_transcript", bool(persona_hits),
                   f"markers={persona_hits}", severity="warn"),
            _check("tsubasa_plugin_active_in_session", bool(active_hits),
                   f"active tsubasa entries={active_hits}", severity="warn"),
            _check("tsubasa_hook_fired", bool(tsubasa_hooks),
                   f"hooks fired={tsubasa_hooks}; {len(hook_events)} hook event(s) total",
                   severity="warn"),
        ]
        if not hook_events and not persona_hits:
            checks.append(_check(
                "hook_events_present_in_stream", False,
                "no hook events in the stream at all: --include-hook-events may not echo "
                "them in this CLI build, which makes the persona check weak evidence "
                "rather than proof", severity="warn"))

    fatal = [c["name"] for c in checks if not c["ok"] and c["severity"] == "fatal"]
    return {"phase": "postflight", "arm": arm, "passed": not fatal,
            "fatal": fatal, "checks": checks}


def merge(pre: dict, post: dict) -> dict:
    return {
        "passed": pre["passed"] and post["passed"],
        "fatal": pre["fatal"] + post["fatal"],
        "preflight": pre["checks"],
        "postflight": post["checks"],
    }


def child_env(arm: str) -> dict[str, str]:
    """Environment for the child session. For the vanilla arm the directory
    holding the tsubasa entry point is stripped from PATH, so the arm cannot
    reach the captain CLI even if it thinks to try."""
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SAFE_MODE", None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if arm != "A":
        return env
    # Drop every PATH entry that holds a tsubasa entry point. Resolving the
    # executable is wrong here: ~/.local/bin/tsubasa is a symlink into the uv
    # tool store, and resolving it would leave ~/.local/bin on PATH.
    keep = []
    for part in env.get("PATH", "").split(os.pathsep):
        if part and not (Path(part) / "tsubasa").exists():
            keep.append(part)
    env["PATH"] = os.pathsep.join(keep)
    return env
