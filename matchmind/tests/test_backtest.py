import pandas as pd

import backtest
from tests.synth import make_history


def test_walk_forward_has_no_lookahead_and_beats_uniform():
    df = make_history(n_seasons=3, start_year=2021)
    start = pd.Timestamp("2023-08-01")
    pred = backtest.walk_forward(df, start, "2024-07-01", refit_days=60)
    assert len(pred) > 200 and pred.Date.min() >= start
    s = backtest.summarize(pred)
    assert 0 < s["brier_1x2"] < s["brier_uniform"]           # signal exists in synthetic data
    assert abs(s["brier_1x2"] - 0.0) > 0 and "beats" in backtest.verdict(s)


def test_summarize_empty():
    assert backtest.summarize(pd.DataFrame()) == {"n": 0}


def test_missing_history_file_exit_code(tmp_path):
    assert backtest.main(["--history", str(tmp_path / "nope.csv")]) == 1


def test_implied_probs_remove_margin():
    import numpy as np
    from backtest import implied_probs
    p = implied_probs(np.array([[2.0, 3.5, 4.0], [1.5, 4.0, 7.0]]))
    assert np.allclose(p.sum(1), 1.0)
    assert p[0, 0] > p[0, 2]            # shorter odds -> higher probability


def test_odds_benchmark_uses_same_matches_and_skips_missing():
    import pandas as pd
    from backtest import odds_benchmark
    n = 80
    dates = pd.date_range("2025-08-01", periods=n)
    pred = pd.DataFrame({"Date": dates, "HomeTeam": "a", "AwayTeam": "b",
                         "p_home": 0.5, "p_draw": 0.25, "p_away": 0.25,
                         "FTHG": [2, 0, 1, 1] * (n // 4), "FTAG": [0, 1, 1, 0] * (n // 4)})
    df = pred[["Date", "HomeTeam", "AwayTeam"]].copy()
    df["PSCH"], df["PSCD"], df["PSCA"] = 2.0, 3.5, 4.0
    b = odds_benchmark(pred, df)
    assert b and b["n"] == n and b["source"].startswith("Pinnacle")
    assert 0 <= b["odds_brier"] <= 2
    # no odds columns -> None, never a made-up benchmark
    assert odds_benchmark(pred, df[["Date", "HomeTeam", "AwayTeam"]]) is None


def test_component_and_ci_runs_and_orders_interval():
    import numpy as np
    import pandas as pd
    from backtest import component_and_ci
    rng = np.random.default_rng(0)
    n = 200
    y = rng.integers(0, 3, n)
    pred = pd.DataFrame({"FTHG": np.where(y == 0, 2, np.where(y == 1, 1, 0)),
                         "FTAG": np.where(y == 0, 0, np.where(y == 1, 1, 2)),
                         "base_h": .45, "base_d": .25})
    for pre, p in (("p_", (.4, .3, .3)), ):
        pred["p_home"], pred["p_draw"], pred["p_away"] = p
    for tag in ("elo", "dc"):
        pred[f"{tag}_h"], pred[f"{tag}_d"], pred[f"{tag}_a"] = .4, .3, .3
    c = component_and_ci(pred, n_boot=300)
    assert c["ci_low"] <= c["diff"] <= c["ci_high"] and "brier_elo" in c and "brier_dc" in c


def test_extra_market_backtest_table_on_synthetic_data():
    df = make_history(n_seasons=3, start_year=2021)
    pred = backtest.walk_forward(df, pd.Timestamp("2023-08-01"), "2024-07-01", refit_days=90)
    table = {e["market"]: e for e in backtest.extra_market_table(pred)}
    assert {"dc_1x", "over_1_5", "cs_home", "score_top5"} <= set(table)
    for e in table.values():
        assert e["n"] == len(pred) and 0 <= e["rate"] <= 1 and e["brier"] >= 0
    assert abs(table["dc_1x"]["rate"] - ((pred.FTHG >= pred.FTAG).mean())) < 1e-9


def test_value_research_finds_no_bets_when_prices_equal_the_model_and_handles_real_disagreement():
    df = make_history(n_seasons=3, start_year=2021)
    pred = backtest.walk_forward(df, pd.Timestamp("2023-08-01"), "2024-07-01", refit_days=90)
    base = pred[["Date", "HomeTeam", "AwayTeam"]].copy()
    # prices = the model's own probabilities with a 6% margin: the model sees no edge anywhere
    probs = pred[["p_home", "p_draw", "p_away"]].to_numpy()
    same = (1 / (probs * 1.06)).clip(1.01)
    agree = base.copy()
    agree["PSCH"], agree["PSCD"], agree["PSCA"] = same[:, 0], same[:, 1], same[:, 2]
    out = backtest.value_research(pred, agree, thresholds=(0.0,))
    assert out["rows"][0]["n"] == 0
    # flat prices (2.83 on everything) disagree with the model, so bets appear and the interval brackets the ROI
    flat = base.copy()
    flat["PSCH"] = flat["PSCD"] = flat["PSCA"] = 2.83
    out = backtest.value_research(pred, flat, thresholds=(0.0, 0.03))
    first = out["rows"][0]
    assert out["matches"] == len(pred) and first["n"] > 30 and first["ci_low"] <= first["roi"] <= first["ci_high"]
    # no odds columns -> None, never an invented result
    assert backtest.value_research(pred, base) is None


def test_odds_source_with_best_coverage_is_used():
    import numpy as np
    df = make_history(n_seasons=3, start_year=2021)
    pred = backtest.walk_forward(df, pd.Timestamp("2023-08-01"), "2024-07-01", refit_days=90)
    odds = pred[["Date", "HomeTeam", "AwayTeam"]].copy()
    odds["PSCH"], odds["PSCD"], odds["PSCA"] = 2.4, 3.3, 3.1
    odds["AvgCH"], odds["AvgCD"], odds["AvgCA"] = 2.3, 3.3, 3.2
    odds.loc[odds.index[: len(odds) // 2], ["PSCH", "PSCD", "PSCA"]] = np.nan        # Pinnacle only half complete
    b = backtest.odds_benchmark(pred, odds)
    assert b["source"].startswith("market-average") and b["n"] == len(pred) and b["coverage"] == 1.0
