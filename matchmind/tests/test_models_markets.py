import numpy as np
import pytest

from src import markets, models
from tests.synth import make_history


@pytest.fixture(scope="module")
def fitted():
    df = make_history(n_seasons=3)
    return models.EnsembleV1().fit(df), df


def test_published_1x2_equals_matrix_1x2(fitted):
    model, _ = fitted
    for h, a in [("arsenal", "chelsea"), ("everton", "fulham")]:
        full = model.predict_full(h, a)
        assert np.allclose(full["p"], markets.outcome_probs(full["matrix"]), atol=1e-9)
        assert abs(full["p"].sum() - 1) < 1e-9


def test_unknown_team_raises(fitted):
    model, _ = fitted
    with pytest.raises(ValueError):
        model.predict_full("arsenal", "atlantis")


def test_empty_fit_raises():
    import pandas as pd
    with pytest.raises(ValueError):
        models.EnsembleV1().fit(pd.DataFrame(columns=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]))


def test_market_definitions():
    m = np.zeros((11, 11)); m[0, 0] = 0.5; m[1, 1] = 0.25; m[3, 0] = 0.25
    mk = markets.market_probs(m)
    assert abs(mk["p_btts"] - 0.25) < 1e-12            # only 1-1 has both scoring
    assert abs(mk["p_over25"] - 0.25) < 1e-12          # only 3-0 reaches 3 goals
    # clean sheet = opponent scored zero, including 0-0
    assert mk["p_home_cs"] > 0 and abs(mk["p_home_cs"] - 0.75) < 1e-12


def test_reconcile_hits_target():
    rng = np.random.default_rng(0)
    m = rng.random((11, 11)); m /= m.sum()
    target = np.array([0.5, 0.3, 0.2])
    assert np.allclose(markets.outcome_probs(markets.reconcile(m, target)), target)


def test_team_profile_last_n(fitted):
    _, df = fitted
    full = markets.team_profile(df, "arsenal")
    last = markets.team_profile(df, "arsenal", last_n=30)
    assert last["n"] == 30 and full["n"] > 30
    assert markets.team_profile(df, "nobody")["n"] == 0


def test_version_id_changes_with_params(fitted):
    import nightly
    model, _ = fitted
    a = nightly.version_id_for(model, "E0")
    assert a.startswith("v1.2-ens-E0-")
    old = dict(models.EnsembleV1.PARAMS)
    try:
        models.EnsembleV1.PARAMS["k"] = old.get("k", 16) + 1
        assert nightly.version_id_for(model, "E0") != a
    finally:
        models.EnsembleV1.PARAMS.clear(); models.EnsembleV1.PARAMS.update(old)


def test_extra_markets_are_consistent_with_1x2(fitted):
    model, _ = fitted
    full = model.predict_full("arsenal", "chelsea")
    em = {m: (sel, p) for m, _, sel, p in markets.extra_markets(full["matrix"], full["p"])}
    assert set(em) == set(markets.EXTRA_MARKETS)
    p = full["p"]
    assert abs(em["dc_1x"][1] - (p[0] + p[1])) < 1e-9 and abs(em["dc_12"][1] - (p[0] + p[2])) < 1e-9
    assert abs(em["dc_1x"][1] + em["dc_x2"][1] + em["dc_12"][1] - 2) < 1e-9     # each result counted twice
    assert em["over_3_5"][1] <= full["markets"]["p_over25"] <= em["over_1_5"][1]
    assert em["score_top1"][1] <= em["score_top5"][1] <= 1
    assert len(em["score_top5"][0].split(",")) == 5
    assert all(0 <= v[1] <= 1 for v in em.values())


def test_extra_outcome_rules_and_unknown_market():
    assert markets.extra_outcome("dc_1x", None, 1, 1) is True
    assert markets.extra_outcome("dc_x2", None, 2, 1) is False
    assert markets.extra_outcome("over_1_5", None, 1, 0) is False and markets.extra_outcome("over_3_5", None, 2, 2)
    assert markets.extra_outcome("cs_home", None, 2, 0) and not markets.extra_outcome("cs_away", None, 2, 0)
    assert markets.extra_outcome("score_top5", "1-1,2-1", 2, 1) is True
    assert markets.extra_outcome("score_top1", "1-1", 2, 1) is False
    assert markets.extra_outcome("player_anytime_scorer", None, 2, 1) is None     # unknown: never guessed


def test_matrix_markets_obey_probability_identities(fitted):
    model, _ = fitted
    full = model.predict_full("everton", "fulham")
    em = {m: p for m, _, _, p in markets.extra_markets(full["matrix"], full["p"])}
    assert abs(em["tg_0_1"] + em["tg_2_3"] + em["tg_4_5"] + em["tg_6p"] - 1) < 1e-3       # bands cover every total
    assert abs(em["ah_home_p1_5"] + em["ah_away_m1_5"] - 1) < 1e-9                        # exact complements
    assert abs(em["ah_away_p1_5"] + em["ah_home_m1_5"] - 1) < 1e-9
    assert em["wtn_home"] <= full["p"][0] + 1e-9 and em["wtn_away"] <= full["p"][2] + 1e-9  # a win to nil is a win
    assert em["wtn_home"] <= em["cs_home"] and em["ts3_home"] <= em["ts2_home"]
    assert em["btts_over25"] + em["btts_under25"] <= full["markets"]["p_btts"] + 1e-9
    assert em["ah_home_m1_5"] <= full["p"][0] + 1e-9


def test_backtest_evidence_only_names_real_markets():
    assert markets.BACKTEST_EDGE <= set(markets.EXTRA_MARKETS)
    assert markets.backtest_evidence("dc_12") == "none" and markets.backtest_evidence("dc_1x") == "edge"
