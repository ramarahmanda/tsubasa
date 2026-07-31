"""Adapter contract: anything → Events (with derived entities/relations).

Adapters are deterministic (no LLM calls) so `tsubasa rebuild` is
reproducible; judgment-based extraction happens in the Captain's session
and enters the log through `tsubasa event add`. Each adapter reads its
cursor from state.toon so ingests are incremental.

mode: "pull" adapters scan sources on demand; "push" (post-v0.1, e.g.
chat streams) will deliver events into the same contract.

File selection is shared (`Adapter.source_files`): the configured glob is
matched case-insensitively — a real corpus contains `README.MD` and losing
it silently is worse than a slightly wider match — and a source may carry
`exclude` globs for individual generated files a directory-level
classification cannot reach. Exclusions are always printed.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from ..config import CaptainConfig, SourceConfig
from ..models import Event

DEFAULT_GLOBS = ("**/*.md", "**/*.markdown")
EXCLUDE_REPORT = 10  # names printed before the tail is summarized


def ci_glob(pattern: str) -> str:
    """`**/*.md` -> `**/*.[mM][dD]`: the same pattern, case-insensitive.

    Character classes are left alone so an explicit `[0-9]` keeps working.
    """
    out, in_class = [], False
    for ch in pattern:
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        out.append(f"[{ch.lower()}{ch.upper()}]" if ch.isalpha() and not in_class else ch)
    return "".join(out)


def excluded_by(rel: str, patterns: list[str]) -> str:
    """The first `exclude` pattern matching `rel` (source-relative), or ""."""
    name = rel.rsplit("/", 1)[-1]
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
            return pat
    return ""


class Adapter:
    name = "base"
    mode = "pull"

    def __init__(self, root: Path, cfg: CaptainConfig, source: SourceConfig, state: dict,
                 log=print, offline: bool = False):
        self.root = root
        self.cfg = cfg
        self.source = source
        self.state = state  # adapter-scoped mutable cursor dict
        self.log = log
        self.offline = offline  # no network I/O in this adapter: local refs/files only

    def collect(self) -> list[Event]:
        """Return new events since the last cursor. Must be idempotent."""
        raise NotImplementedError

    def source_files(self, base: Path) -> list[Path]:
        """Files this source covers, case-insensitively, minus `exclude`."""
        patterns = [self.source.glob] if self.source.glob else list(DEFAULT_GLOBS)
        found = {p for pat in patterns for p in base.glob(ci_glob(pat)) if p.is_file()}
        excludes = self.source.exclude
        if not excludes:
            return sorted(found)
        keep, cut = [], []
        for p in sorted(found):
            rel = p.relative_to(base).as_posix()
            hit = excluded_by(rel, excludes)
            (cut if hit else keep).append(f"{rel} ({hit})" if hit else p)
        if cut:  # never drop documents silently
            shown = ", ".join(cut[:EXCLUDE_REPORT])
            more = f", +{len(cut) - EXCLUDE_REPORT} more" if len(cut) > EXCLUDE_REPORT else ""
            self.log(f"[{self.name}:{self.source.path}] excluded {len(cut)} file(s): {shown}{more}")
        return keep


def get_adapter(name: str):
    from . import adr, code, docs, gitlog, github, incident
    registry = {
        "adr": adr.AdrAdapter,
        "code": code.CodeAdapter,
        "doc": docs.DocAdapter,
        "git": gitlog.GitAdapter,
        "github": github.GithubAdapter,
        "incident": incident.IncidentAdapter,
    }
    if name not in registry:
        raise KeyError(f"unknown adapter '{name}' (have: {', '.join(sorted(registry))})")
    return registry[name]
