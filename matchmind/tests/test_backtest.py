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
