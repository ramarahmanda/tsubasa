"""Shared paths, arm definitions and contamination markers."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = REPO_ROOT / "benchmark"
QUESTIONS_DIR = BENCH_DIR / "questions"
FIXTURE_LOCK = BENCH_DIR / "fixture.lock"
DEFAULT_OUT = BENCH_DIR / "results"

# The fixture is READ-ONLY. Nothing in this harness writes below it.
DEFAULT_FIXTURE = Path(
    os.environ.get("TSUBASA_BENCH_FIXTURE", REPO_ROOT.parent / "tsubasa-benchmark")
)

REPOS = ("cloudnative-pg", "etcd", "kubernetes-enhancements", "postgres")

# The vanilla arm needs a working directory with no .tsubasa/, no captain
# CLAUDE.md and no .claude/ settings anywhere above it. Default it outside the
# tsubasa checkout entirely: putting it under benchmark/results/ would place
# the repo's own .claude/ on the ancestor chain for every vanilla session.
DEFAULT_VANILLA_WORKSPACE = Path(
    os.environ.get("TSUBASA_BENCH_VANILLA", Path(tempfile.gettempdir()) / "tsubasa-bench-vanilla")
)

# arm id -> (label, description)
ARMS = {
    "A": ("vanilla", "plain Claude Code, tsubasa plugin off, no .tsubasa/, no captain CLAUDE.md"),
    "B": ("captain", "same workspace with a captain built by `tsubasa init` + `tsubasa study`"),
}
ARM_ALIASES = {"a": "A", "vanilla": "A", "b": "B", "captain": "B"}

# Emitted by plugin/hooks/session_start.sh once it finds .tsubasa/captain.toml.
# Its presence in a vanilla transcript means the vanilla arm was not vanilla.
PERSONA_MARKER = "You are captain-"
PERSONA_MARKERS = (
    PERSONA_MARKER,
    "GRAPH-FIRST (non-negotiable)",
    "MEMORY ROUTING: organizational/system knowledge",
)
# Captain tooling reachable from a session: checked against tool-call inputs,
# not the whole transcript, so quoting the question can never trip it.
TOOLING_MARKER_RE = re.compile(r"(?:^|[\s;|&(])tsubasa\s+(?:query|goal|event|study|init|task)\b")
DOT_TSUBASA_RE = re.compile(r"(?:^|[\s'\"/=])\.tsubasa[/\s'\"]")

# ------------------------------------------------------------- follow-up loop
#
# The nudge sent when a turn is not graded `correct`. It is a CONSTANT, not a
# template: nothing about the question, the gold answer, the locator or the
# trap can reach it, and both arms receive this exact string. A nudge that
# leaked "look at the CRD" or "check the KEP status field" would be coaching,
# and coaching one arm's known weakness is how a benchmark becomes an
# advertisement. It says only that the previous answer was wrong.
NUDGE = "That is not correct. Reconsider and answer again."

DEFAULT_MAX_TURNS = 3
DEFAULT_JOBS = 4

# f-negative items and the two g-git-history honesty probes are correct when
# they ABSTAIN. Nudging an arm that correctly said "the workspace cannot
# answer this" would push it off the right behaviour and measure compliance
# instead of honesty, so those questions are single-shot in both arms.
SINGLE_SHOT_REASON = "expect_abstain: the correct answer is an abstention; a nudge " \
                     "would train the arm out of it"

CATEGORY_ORDER = (
    "a-where", "b-topology", "c-status", "d-supersession", "e-temporal",
    "f-negative", "g-git-history", "h-routing", "i-goal-conflict", "x-cross-repo",
)

# Categories where a tie or a captain loss is the expected, honest outcome.
# Reported at full strength; never omitted, never softened.
EXPECTED_TIE = {
    "f-negative": "both arms should abstain; a confident answer is a confabulation in either arm",
    "g-git-history": "G11 and G12 are honesty probes: the fixture records the reversal, not the reason",
}


def arm_label(arm: str) -> str:
    return ARMS[arm][0]


def normalise_arm(token: str) -> str | None:
    return ARM_ALIASES.get(token.strip().lower())


def read_fixture_lock(path: Path = FIXTURE_LOCK) -> dict[str, str]:
    """repo name -> pinned sha, from benchmark/fixture.lock."""
    pins: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{40}", parts[1]):
            pins[parts[0]] = parts[1]
    return pins
