import pandas as pd
import pytest

from src import arena, explain
from tests.synth import make_history
from src import models, markets


@pytest.fixture(scope="module")
def fitted():
    df = make_history(n_seasons=3)
    return models.EnsembleV1().fit(df), df


def test_explain_defence_sign_and_text(fitted):
    model, df = fitted
    ex = explain.explain_match(model, "arsenal", "chelsea", None,
                               markets.team_profile(df, "arsenal", 30), markets.team_profile(df, "chelsea", 30))
    text = explain.render_text(ex)
    assert "higher = concedes more" in text and "Arsenal" in text and "Chelsea" in text
    # edge must equal (atk_h + dfn_a) - (atk_a + dfn_h) as the model builds expected goals
    ah, dh = models._shrunk(model.dc, "arsenal"); aa, da = models._shrunk(model.dc, "chelsea")
    factor = next(f for f in ex["factors"] if f["factor"] == "Attack vs defence")
    assert abs(factor["value"] - round((ah + da) - (aa + dh), 3)) < 1e-9


def test_polish_validation():
    facts = "Arsenal vs Chelsea: 50% / 30% / 20%."
    names = ["Arsenal", "Chelsea"]
    assert explain.validate_polish(facts, "Arsenal are favoured over Chelsea at 50%.", names)
    assert not explain.validate_polish(facts, "Arsenal are 80% to beat Chelsea.", names)   # invented number
    assert not explain.validate_polish(facts, "The home side is favoured at 50%.", names)  # dropped names
    assert not explain.validate_polish(facts, "Arsenal Chelsea 50% " + "x" * 1000, names)


def test_polish_falls_back_without_key(fitted, monkeypatch):
    model, _ = fitted
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ex = explain.explain_match(model, "arsenal", "chelsea")
    assert explain.polish(ex) == explain.render_text(ex)


def test_head_to_head():
    assert arena.model_top_pick(0.5, 0.3, 0.2) == "H"
    assert arena.model_top_pick(0.3, 0.4, 0.3) == "D"
    assert arena.head_to_head("A", "H", "A") == "win"
    assert arena.head_to_head("H", "H", "A") == "tie"       # both wrong
    assert arena.head_to_head("H", "H", "H") == "tie"       # both right
    assert arena.head_to_head("D", "H", "H") == "loss"


def test_leaderboard_min_settled_and_privacy():
    users = pd.DataFrame({"user_id": [1, 2], "display_name": ["Ann", "Bob"], "telegram_id": [11, 22]})
    ap = pd.DataFrame({"arena_prediction_id": range(1, 14), "user_id": [1] * 12 + [2]})
    st = pd.DataFrame({"arena_prediction_id": range(1, 14), "user_hit": True, "model_hit": False,
                       "outcome": ["win"] * 13})
    lb = arena.leaderboard(users, ap, st)
    assert list(lb.display_name) == ["Ann"]                  # Bob has only 1 settled pick
    assert "telegram_id" not in lb.columns
