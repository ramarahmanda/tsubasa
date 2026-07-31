"""`tsubasa init` makes one headless-Claude call to classify sources. No test
may reach a model, so the suite-wide default is the offline fallback — which
is also the path every pre-existing offline assertion depends on. Tests that
exercise the pass ask for the `topology_pass` fixture and stub `llm.run_claude`
themselves."""

import pytest

from tsubasa import topology

_REAL_REFINE = topology.refine


@pytest.fixture(autouse=True)
def offline_init(monkeypatch):
    monkeypatch.setattr(topology, "refine", lambda *a, **k: None)


@pytest.fixture()
def topology_pass(monkeypatch):
    monkeypatch.setattr(topology, "refine", _REAL_REFINE)
