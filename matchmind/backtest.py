"""Walk-forward backtest of the exact production model (EnsembleV1).

    python backtest.py                         # seasons 2023, 2024, 2025 on data/history/E0.csv
    python backtest.py --league E0 --seasons 2024 --refit-days 14

For every test season the model only ever sees matches played BEFORE each prediction (it is
refit every ``--refit-days``), so there is no look-ahead. Reported per season:
  * 1X2: accuracy, 3-class Brier, log loss, against two references: uniform (1/3 each) and the
    training-set class frequencies ("home-always-favoured" naive forecast).
  * Over 2.5 and BTTS: Brier against the base-rate forecast (always predict the training
    frequency). A market is only "adding value" if it beats that baseline.
Do not tune the model on seasons you later report as results: tune on the earliest seasons,
select on the next, and treat the rest as held out.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from src import models, teams  # noqa: E402

CLASSES = ("H", "D", "A")


def load_history(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, format="mixed")
    df["HomeTeam"] = df["HomeTeam"].map(lambda s: teams.canon(s, "csv"))
    df["AwayTeam"] = df["AwayTeam"].map(lambda s: teams.canon(s, "csv"))
    out = df[["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]].dropna()
    out[["FTHG", "FTAG"]] = out[["FTHG", "FTAG"]].astype(int)
    return out.sort_values("Date").reset_index(drop=True)


def walk_forward(df: pd.DataFrame, test_start, test_end, refit_days: int = 30) -> pd.DataFrame:
    """One row per test match with the model's probabilities. Refits every ``refit_days``."""
    test_start, test_end = pd.Timestamp(test_start), pd.Timestamp(test_end)
    rows, cursor = [], test_start
    while cursor < test_end:
        chunk_end = min(cursor + pd.Timedelta(days=refit_days), test_end)
        train = df[df.Date < cursor]
        chunk = df[(df.Date >= cursor) & (df.Date < chunk_end)]
        if len(chunk) and len(train) >= 300:
            model = models.EnsembleV1().fit(train)
            for r in chunk.itertuples():
                if not (model.knows(r.HomeTeam) and model.knows(r.AwayTeam)):
                    continue    # promoted club with no history: production skips these too
                full = model.predict_full(r.HomeTeam, r.AwayTeam)
                rows.append({"Date": r.Date, "p_home": full["p"][0], "p_draw": full["p"][1],
                             "p_away": full["p"][2], "p_over25": full["markets"]["p_over25"],
                             "p_btts": full["markets"]["p_btts"], "FTHG": r.FTHG, "FTAG": r.FTAG,
                             "base_h": float((train.FTHG > train.FTAG).mean()),
                             "base_d": float((train.FTHG == train.FTAG).mean()),
                             "base_over": float(((train.FTHG + train.FTAG) >= 3).mean()),
                             "base_btts": float(((train.FTHG > 0) & (train.FTAG > 0)).mean())})
        cursor = chunk_end
    return pd.DataFrame(rows)


def summarize(pred: pd.DataFrame) -> dict:
    if not len(pred):
        return {"n": 0}
    p = pred[["p_home", "p_draw", "p_away"]].to_numpy()
    y_idx = np.where(pred.FTHG > pred.FTAG, 0, np.where(pred.FTHG == pred.FTAG, 1, 2))
    y = np.eye(3)[y_idx]
    base = np.column_stack([pred.base_h, pred.base_d, 1 - pred.base_h - pred.base_d])
    over = ((pred.FTHG + pred.FTAG) >= 3).astype(float).to_numpy()
    btts = ((pred.FTHG > 0) & (pred.FTAG > 0)).astype(float).to_numpy()
    return {
        "n": int(len(pred)),
        "accuracy": float((p.argmax(1) == y_idx).mean()),
        "brier_1x2": float(((p - y) ** 2).sum(1).mean()),
        "brier_uniform": float(((1 / 3 - y) ** 2).sum(1).mean()),
        "brier_base_rates": float(((base - y) ** 2).sum(1).mean()),
        "log_loss": float(-np.log(np.clip(p[np.arange(len(y)), y_idx], 1e-9, 1)).mean()),
        "brier_over25": float(((pred.p_over25 - over) ** 2).mean()),
        "brier_over25_base": float(((pred.base_over - over) ** 2).mean()),
        "brier_btts": float(((pred.p_btts - btts) ** 2).mean()),
        "brier_btts_base": float(((pred.base_btts - btts) ** 2).mean()),
    }


def verdict(s: dict) -> str:
    if not s.get("n"):
        return "no data"
    out = []
    for name, a, b in (("1X2", "brier_1x2", "brier_base_rates"),
                       ("Over 2.5", "brier_over25", "brier_over25_base"),
                       ("BTTS", "brier_btts", "brier_btts_base")):
        out.append(f"{name}: {'beats' if s[a] < s[b] else 'does NOT beat'} base rate")
    return " | ".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league", default="E0")
    ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025],
                    help="start years; season Y runs 1 Jul Y to 30 Jun Y+1")
    ap.add_argument("--refit-days", type=int, default=30)
    ap.add_argument("--history", type=Path, default=None)
    args = ap.parse_args(argv)
    path = args.history or Path(__file__).parent / "data" / "history" / f"{args.league}.csv"
    if not path.exists():
        print(f"No history file at {path}. Download football-data.co.uk/englandm.php (E0 seasons, "
              f"dev use only) and concatenate them into that file.")
        return 1
    df = load_history(path)
    print(f"{args.league}: {len(df)} matches, {df.Date.min().date()} to {df.Date.max().date()}")
    for season in args.seasons:
        pred = walk_forward(df, f"{season}-07-01", f"{season + 1}-07-01", args.refit_days)
        s = summarize(pred)
        if not s["n"]:
            print(f"{season}-{season + 1}: no matches in the data")
            continue
        print(f"\n{season}-{season + 1}  n={s['n']}\n"
              f"  1X2   acc {s['accuracy']:.3f}  Brier {s['brier_1x2']:.4f} "
              f"(uniform {s['brier_uniform']:.4f}, base rates {s['brier_base_rates']:.4f})  "
              f"log loss {s['log_loss']:.4f}\n"
              f"  O2.5  Brier {s['brier_over25']:.4f} (base {s['brier_over25_base']:.4f})\n"
              f"  BTTS  Brier {s['brier_btts']:.4f} (base {s['brier_btts_base']:.4f})\n"
              f"  {verdict(s)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
