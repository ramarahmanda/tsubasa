"""Captain configuration: .tsubasa/captain.toml.

One file holds the persona, memory weights, and source definitions.
Parsed with stdlib tomllib; written from a template by `tsubasa init`.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TSUBASA_DIR = ".tsubasa"
CONFIG_FILE = "captain.toml"

# Bumped whenever a captain on disk needs `tsubasa upgrade` to gain artifacts a
# fresh `init` would have written.
#   1  pre-versioning: no stamp on disk, task files, no source graph
#   2  task management removed; svc-/corpus- source graph + topology at init
SCHEMA_VERSION = 2

DEFAULT_WEIGHTS = {"recency": 0.4, "impact": 0.3, "domain": 0.2, "access": 0.1}
DEFAULT_HOT_MAX_CONTEXT = 0.25       # ceiling as fraction of the context window
DEFAULT_CONTEXT_WINDOW = 200_000     # tokens, used to size the hot budget
DEFAULT_HALF_LIFE_DAYS = 90.0


@dataclass
class SourceConfig:
    adapter: str
    path: str = "."
    glob: str = ""
    exclude: list[str] = field(default_factory=list)  # globs relative to `path`
    # git history window for `study`. None means "not set, use the default";
    # "" means all history, so absent and empty are deliberately distinct.
    since: str | None = None
    options: dict = field(default_factory=dict)


@dataclass
class CaptainConfig:
    schema_version: int = 1  # absent on disk means pre-versioning, i.e. 1
    name: str = "captain"
    role: str = "Engineering Director"
    domains: dict[str, float] = field(default_factory=dict)  # domain -> weight 0..1
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    hot_max_context: float = DEFAULT_HOT_MAX_CONTEXT
    context_window: int = DEFAULT_CONTEXT_WINDOW
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS
    sources: list[SourceConfig] = field(default_factory=list)

    @property
    def hot_budget_tokens(self) -> int:
        return int(self.hot_max_context * self.context_window)


def _as_list(value) -> list:
    """`exclude` accepts a bare string as well as an array."""
    if value in (None, ""):
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def find_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` to find the directory containing .tsubasa/."""
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / TSUBASA_DIR / CONFIG_FILE).is_file():
            return candidate
    return None


def load(root: Path) -> CaptainConfig:
    path = root / TSUBASA_DIR / CONFIG_FILE
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise RuntimeError(
            f"{path} is not valid TOML: {e}. "
            "Fix it by hand or manage sources with `tsubasa source add` instead of editing."
        ) from e
    cap = data.get("captain", {})
    mem = data.get("memory", {})
    hot_max = mem.get("hot_max_context", DEFAULT_HOT_MAX_CONTEXT)
    if hot_max > 1:  # allow "25" to mean 25%
        hot_max = hot_max / 100.0
    return CaptainConfig(
        schema_version=int(data.get("tsubasa", {}).get("schema_version", 1)),
        name=cap.get("name", "captain"),
        role=cap.get("role", "Engineering Director"),
        domains={k: float(v) for k, v in cap.get("domains", {}).items()},
        weights={**DEFAULT_WEIGHTS, **{k: float(v) for k, v in mem.get("weights", {}).items()}},
        hot_max_context=float(hot_max),
        context_window=int(mem.get("context_window", DEFAULT_CONTEXT_WINDOW)),
        half_life_days=float(mem.get("half_life_days", DEFAULT_HALF_LIFE_DAYS)),
        sources=[
            SourceConfig(
                adapter=s["adapter"],
                path=s.get("path", "."),
                glob=s.get("glob", ""),
                exclude=[str(x) for x in _as_list(s.get("exclude"))],
                since=None if s.get("since") is None else str(s["since"]),
                options={k: v for k, v in s.items()
                         if k not in ("adapter", "path", "glob", "exclude", "since")},
            )
            for s in data.get("sources", [])
        ],
    )


VERSION_BLOCK = "[tsubasa]\nschema_version = {version}\n"
_VERSION_RE = re.compile(r"(?m)^\s*schema_version\s*=\s*\d+\s*$")
_TABLE_RE = re.compile(r"(?m)^\[")


def stamp_version(root: Path, version: int = SCHEMA_VERSION) -> None:
    """Write the schema stamp into captain.toml in place.

    Textual, not a re-serialization: the file is hand-editable and carries
    comments that a tomllib round-trip would silently delete.
    """
    path = root / TSUBASA_DIR / CONFIG_FILE
    text = path.read_text()
    if _VERSION_RE.search(text):
        new = _VERSION_RE.sub(f"schema_version = {version}", text)
    else:
        # above the first table, so `source add`/`_drop_source` (which rewrite
        # only the [[sources]] tail) never touch it
        m = _TABLE_RE.search(text)
        at = m.start() if m else len(text)
        new = text[:at] + VERSION_BLOCK.format(version=version) + "\n" + text[at:]
    if new != text:
        path.write_text(new)
        load(root)  # a stamp that breaks the config is worse than no stamp


CONFIG_TEMPLATE = """\
# Captain configuration — see https://github.com/ramarahmanda/tsubasa
[tsubasa]
schema_version = {schema_version}

[captain]
name = "{name}"
role = "{role}"

# Domains this captain cares about, with weights (0..1) feeding temperature.
[captain.domains]
{domains}

[memory]
hot_max_context = 0.25    # ceiling: fraction of the context window hot may use
context_window = 200000
half_life_days = 90

[memory.weights]          # temperature = weighted sum, see DESIGN.md §3.4
recency = 0.4
impact = 0.3
domain = 0.2
access = 0.1

# Knowledge sources. Each entry maps to an adapter; `path` is relative to the
# repo root, so one captain can span sibling repos in a workspace.
{sources}
"""

SOURCE_TEMPLATE = """\
[[sources]]
adapter = "{adapter}"
path = "{path}"
glob = "{glob}"
"""
