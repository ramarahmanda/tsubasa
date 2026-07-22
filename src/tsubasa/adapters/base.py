"""Adapter contract: anything → Events (with derived entities/relations).

Adapters are deterministic (no LLM calls) so `tsubasa rebuild` is
reproducible; judgment-based extraction happens in the Captain's session
and enters the log through `tsubasa event add`. Each adapter reads its
cursor from state.toon so ingests are incremental.

mode: "pull" adapters scan sources on demand; "push" (post-v0.1, e.g.
chat streams) will deliver events into the same contract.
"""

from __future__ import annotations

from pathlib import Path

from ..config import CaptainConfig, SourceConfig
from ..models import Event


class Adapter:
    name = "base"
    mode = "pull"

    def __init__(self, root: Path, cfg: CaptainConfig, source: SourceConfig, state: dict):
        self.root = root
        self.cfg = cfg
        self.source = source
        self.state = state  # adapter-scoped mutable cursor dict

    def collect(self) -> list[Event]:
        """Return new events since the last cursor. Must be idempotent."""
        raise NotImplementedError


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
