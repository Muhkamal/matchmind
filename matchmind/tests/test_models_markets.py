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
