"""Weekly transparency report generator.

Only LIVE picks are reported (never backtest/holdout), for the chosen week plus a
season-to-date block. Calibration error is shown only with enough data to mean something.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from .calibrate import reliability_table, ece
from .ledger import ONEHOT

LABELS = ("H", "D", "A")
MIN_ECE_N = 100   # below this a calibration error is noise, so it is not reported


def weekly_report(predictions: pd.DataFrame, settlements: pd.DataFrame,
                  results: pd.DataFrame, league: str, week_of: pd.Timestamp) -> str:
    week_start = pd.Timestamp(week_of).normalize()
    week_end = week_start + pd.Timedelta(days=7)
    pred = predictions[(predictions.league == league) & (predictions.provenance == "live")]
    settled = settlements.merge(pred, on="prediction_id")
    settled = settled.merge(
        results.rename(columns={"home": "home_team", "away": "away_team"})[
            ["league", "match_date", "home_team", "away_team", "result"]],
        on=["league", "match_date", "home_team", "away_team"])
    settled["match_date"] = pd.to_datetime(settled["match_date"])
    settled = settled[(settled.match_date >= week_start) & (settled.match_date < week_end)]
    if not len(settled):
        return (f"# Weekly report — week of {week_start.date()} — {league}\n\n"
                f"No live predictions settled this week ({len(pred)} live picks total, "
                f"awaiting settlement).\n")

    p = settled[["p_home", "p_draw", "p_away"]].to_numpy()
    y_idx = settled["result"].map({l: i for i, l in enumerate(LABELS)}).to_numpy()
    y = np.array([ONEHOT[r] for r in settled["result"]])
    acc = (p.argmax(1) == y_idx).mean()
    brier = ((p - y) ** 2).sum(1).mean()
    ll = -np.log(np.clip(p[np.arange(len(y)), y_idx], 1e-9, 1)).mean()
    enough = len(settled) >= MIN_ECE_N
    eces = {lab: ece(p[:, j], settled["result"].to_numpy(), lab) for j, lab in enumerate(LABELS)}

    settled = settled.copy()
    settled["miss"] = ((p - y) ** 2).sum(1)
    worst = settled.nlargest(5, "miss")[
        ["match_date", "home_team", "away_team", "p_home", "p_draw", "p_away", "result", "miss"]]

    lines = [
        f"# Weekly Transparency Report — week of {week_start.date()}",
        f"**League:** {league} · **Settled predictions (this week, live):** {len(settled)}",
        "",
        "## Performance",
        "| Metric | Value |",
        "|---|---|",
        f"| Accuracy | {acc:.1%} |",
        f"| Brier score (3-class, lower=better) | {brier:.4f} |",
        f"| Log loss | {ll:.4f} |",
        (f"| Calibration error — home / draw / away | {eces['H']:.3f} / {eces['D']:.3f} / {eces['A']:.3f} |"
         if enough else f"| Calibration error | not shown (needs {MIN_ECE_N}+ settled picks, have {len(settled)}) |"),
        "| Reference: always-uniform forecast Brier | 0.6667 |",
        "",
    ]
    if enough:
        lines += ["## Reliability check (home win market)", "| Predicted | n | Actual |", "|---|---|---|"]
        for row in reliability_table(p[:, 0], settled["result"].to_numpy(), "H", bins=10):
            lines.append(f"| {row[2]:.2f} | {int(row[1])} | {row[3]:.2f} |")
    lines += ["", "## Biggest misses (we publish these on purpose)", "",
              "| Date | Match | P(H) | P(D) | P(A) | Result | Brier |", "|---|---|---|---|---|---|---|"]
    for r in worst.itertuples():
        lines.append(f"| {pd.Timestamp(r.match_date).date()} | {r.home_team}–{r.away_team} "
                     f"| {r.p_home:.2f} | {r.p_draw:.2f} | {r.p_away:.2f} "
                     f"| {r.result} | {r.miss:.3f} |")
    lines += ["", _to_date_block(pred, settlements, results, week_start)]
    lines += ["", "_Methodology: Elo + Dixon-Coles ensemble (uncalibrated, published as-is). Every pick is "
                  "written to an append-only ledger before kickoff and graded automatically. A single "
                  "week is a small sample; judge the model over hundreds of picks. Not betting advice._"]
    return "\n".join(lines)


def _to_date_block(pred, settlements, results, week_start) -> str:
    """Live record from the first live pick up to the end of the report week."""
    df = settlements.merge(pred, on="prediction_id")
    df["match_date"] = pd.to_datetime(df["match_date"])
    df = df[df.match_date < week_start + pd.Timedelta(days=7)]
    if not len(df):
        return "## Live record to date\nNothing settled yet."
    brier = float(df["brier_contrib"].mean())
    return (f"## Live record to date\n{len(df)} settled live picks · accuracy {df['hit'].mean():.1%} "
            f"· Brier {brier:.4f} (uniform guess = 0.6667)")
