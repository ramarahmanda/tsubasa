"""`tsubasa init` makes one headless-Claude call to classify sources. No test
may reach a model, so the suite-wide default is the offline fallback — which
is also the path every pre-existing offline assertion depends on. Tests that
exercise the pass ask for the `topology_pass` fixture and stub `llm.run_claude`
themselves."""

import pytest

from tsubasa import semantic, topology

_REAL_REFINE = topology.refine
_REAL_EXPAND = semantic.expand


@pytest.fixture(autouse=True)
def offline_init(monkeypatch):
    monkeypatch.setattr(topology, "refine", lambda *a, **k: None)
    # `tsubasa query` escalates to semantic expansion on every weak lexical
    # result; no test may reach a model, so the suite-wide default is a no-op
    # expansion. Ladder tests ask for `semantic_pass` and stub
    # `llm.run_claude_json` themselves.
    monkeypatch.setattr(semantic, "expand", lambda *a, **k: [])


@pytest.fixture()
def topology_pass(monkeypatch):
    monkeypatch.setattr(topology, "refine", _REAL_REFINE)


@pytest.fixture()
def semantic_pass(monkeypatch):
    monkeypatch.setattr(semantic, "expand", _REAL_EXPAND)
