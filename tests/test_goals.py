"""Future knowledge: goals stay hot until resolved and steer planning."""

import pytest

from tsubasa import cli
from tsubasa.storage import Store


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["init", "cap", "--domains", "identity"])
    # an OLD plan event — recency alone would let it go cold
    cli.main(["event", "add", "--type", "plan", "--ts", "2025-01-10",
              "--title", "Plan: decommission legacy SSO",
              "--entity", "goal-decommission-legacy-sso:goal:Decommission legacy SSO:Retire the legacy SSO stack once the Identra migration completes"])
    return tmp_path


def test_open_goal_never_decays_out_of_hot(repo):
    hot = (repo / ".tsubasa/memory/hot.md").read_text()
    assert "Direction" in hot
    assert "goal-decommission-legacy-sso" in hot


def test_achieved_goal_cools_and_leaves_direction(repo):
    assert cli.main(["goal", "set", "goal-decommission-legacy-sso", "achieved",
                     "--evidence", "legacy SSO shut down"]) == 0
    store = Store(repo)
    assert store.load_entities()["goal-decommission-legacy-sso"].status == "achieved"
    hot = (repo / ".tsubasa/memory/hot.md").read_text()
    assert "Direction" not in hot


def test_goal_status_survives_rebuild(repo):
    cli.main(["goal", "set", "goal-decommission-legacy-sso", "achieved"])
    cli.main(["rebuild"])
    assert Store(repo).load_entities()["goal-decommission-legacy-sso"].status == "achieved"


def test_goal_can_reopen(repo):
    cli.main(["goal", "set", "goal-decommission-legacy-sso", "dropped"])
    cli.main(["goal", "set", "goal-decommission-legacy-sso", "active"])
    assert Store(repo).load_entities()["goal-decommission-legacy-sso"].status == "active"


def test_goal_list(repo, capsys):
    cli.main(["goal", "list"])
    out = capsys.readouterr().out
    assert "goal-decommission-legacy-sso" in out
    assert "active" in out
