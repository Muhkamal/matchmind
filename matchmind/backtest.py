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
from src import markets, models, teams  # noqa: E402

CLASSES = ("H", "D", "A")


def load_history(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, format="mixed")
    df["HomeTeam"] = df["HomeTeam"].map(lambda s: teams.canon(s, "csv"))
    df["AwayTeam"] = df["AwayTeam"].map(lambda s: teams.canon(s, "csv"))
    cols = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
    odds_cols = [c for trio in ODDS_SETS for c in trio[1] if c in df.columns]
    out = df[cols + odds_cols].dropna(subset=cols)
    out[["FTHG", "FTAG"]] = out[["FTHG", "FTAG"]].astype(int)
    return out.sort_values("Date").reset_index(drop=True)


# Closing odds are the sharpest public benchmark; used for comparison ONLY, never as a model input.
ODDS_SETS = (("Pinnacle closing", ("PSCH", "PSCD", "PSCA")),
             ("market-average closing", ("AvgCH", "AvgCD", "AvgCA")),
             ("Bet365 closing", ("B365CH", "B365CD", "B365CA")))


def implied_probs(odds: np.ndarray) -> np.ndarray:
    """Decimal odds -> probabilities with the bookmaker margin removed (proportional normalisation)."""
    inv = 1.0 / odds
    return inv / inv.sum(axis=1, keepdims=True)


def _best_odds_merge(pred: pd.DataFrame, df: pd.DataFrame, min_rows: int = 50):
    """Merge predictions with the closing-odds source that covers the MOST matches (earlier sources win ties).
    Returns (label, trio, merged) or None."""
    best = None
    for label, trio in ODDS_SETS:
        if not all(c in df.columns for c in trio):
            continue
        m = pred.merge(df[["Date", "HomeTeam", "AwayTeam", *trio]], on=["Date", "HomeTeam", "AwayTeam"],
                       how="inner").dropna(subset=list(trio))
        m = m[(m[list(trio)] > 1.0).all(axis=1)]
        if len(m) >= min_rows and (best is None or len(m) > len(best[2])):
            best = (label, trio, m)
    return best


def odds_benchmark(pred: pd.DataFrame, df: pd.DataFrame) -> dict | None:
    """Brier / accuracy of bookmaker closing odds on exactly the matches the model scored."""
    if not len(pred):
        return None
    found = _best_odds_merge(pred, df)
    if not found:
        return None
    label, trio, m = found
    p = implied_probs(m[list(trio)].to_numpy(dtype=float))
    y_idx = np.where(m.FTHG > m.FTAG, 0, np.where(m.FTHG == m.FTAG, 1, 2))
    y = np.eye(3)[y_idx]
    mp = m[["p_home", "p_draw", "p_away"]].to_numpy()
    return {"source": label, "n": int(len(m)), "coverage": float(len(m) / len(pred)),
            "odds_acc": float((p.argmax(1) == y_idx).mean()),
            "odds_brier": float(((p - y) ** 2).sum(1).mean()),
            "model_acc": float((mp.argmax(1) == y_idx).mean()),
            "model_brier": float(((mp - y) ** 2).sum(1).mean())}


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
                extras = {}
                for mkt, _, sel, prob in markets.extra_markets(full["matrix"], full["p"]):
                    extras[f"x_{mkt}"], extras[f"xs_{mkt}"] = prob, sel
                rows.append({**extras, "Date": r.Date, "HomeTeam": r.HomeTeam, "AwayTeam": r.AwayTeam,
                             "p_home": full["p"][0], "p_draw": full["p"][1],
                             "p_away": full["p"][2],
                             "elo_h": full["p_elo"][0], "elo_d": full["p_elo"][1], "elo_a": full["p_elo"][2],
                             "dc_h": full["p_dc"][0], "dc_d": full["p_dc"][1], "dc_a": full["p_dc"][2], "p_over25": full["markets"]["p_over25"],
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


def _brier_rows(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return ((p - y) ** 2).sum(1)


def component_and_ci(pred: pd.DataFrame, n_boot: int = 2000, seed: int = 7) -> dict:
    """Elo-only and Dixon-Coles-only Brier on the same matches, and a paired bootstrap 95% interval for
    (ensemble Brier - base-rate Brier). If the interval is entirely below 0 the edge is unlikely to be luck;
    if it straddles 0 the sample cannot tell the model from the baseline."""
    y_idx = np.where(pred.FTHG > pred.FTAG, 0, np.where(pred.FTHG == pred.FTAG, 1, 2))
    y = np.eye(3)[y_idx]
    ens = _brier_rows(pred[["p_home", "p_draw", "p_away"]].to_numpy(), y)
    base = _brier_rows(np.column_stack([pred.base_h, pred.base_d, 1 - pred.base_h - pred.base_d]), y)
    diff = ens - base
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diff), size=(n_boot, len(diff)))
    boots = diff[idx].mean(1)
    out = {"diff": float(diff.mean()), "ci_low": float(np.percentile(boots, 2.5)),
           "ci_high": float(np.percentile(boots, 97.5))}
    for tag, cols in (("elo", ("elo_h", "elo_d", "elo_a")), ("dc", ("dc_h", "dc_d", "dc_a"))):
        out[f"brier_{tag}"] = float(_brier_rows(pred[list(cols)].to_numpy(), y).mean())
    return out


def value_research(pred: pd.DataFrame, df: pd.DataFrame, thresholds=(0.03, 0.05, 0.10),
                   n_boot: int = 2000, seed: int = 11) -> dict | None:
    """PRIVATE RESEARCH, not advice and never published. Would flat 1-unit bets on the model's biggest disagreements
    with REAL bookmaker closing prices (margin included, as a bettor would face) have made money?
    A bet is taken on an outcome when model_prob * odds - 1 >= threshold. Profit per bet is odds-1 if it won, else -1.
    The bootstrap 95% interval on ROI says whether the result is distinguishable from luck."""
    found = _best_odds_merge(pred, df, min_rows=100)
    if not found:
        return None
    label, trio, m = found
    odds = m[list(trio)].to_numpy(dtype=float)
    probs = m[["p_home", "p_draw", "p_away"]].to_numpy(dtype=float)
    won = np.eye(3)[np.where(m.FTHG > m.FTAG, 0, np.where(m.FTHG == m.FTAG, 1, 2))].astype(bool)
    profit = np.where(won, odds - 1.0, -1.0)
    edge = probs * odds - 1.0
    rng = np.random.default_rng(seed)
    out = {"source": label, "matches": int(len(m)), "rows": []}
    for t in thresholds:
        sel = edge >= t
        n = int(sel.sum())
        if n < 30:
            out["rows"].append({"threshold": t, "n": n, "roi": None, "ci_low": None, "ci_high": None})
            continue
        pl = profit[sel]
        boots = pl[rng.integers(0, n, size=(n_boot, n))].mean(1)
        out["rows"].append({"threshold": t, "n": n, "roi": float(pl.mean()),
                            "ci_low": float(np.percentile(boots, 2.5)), "ci_high": float(np.percentile(boots, 97.5))})
    return out


def extra_market_table(pred: pd.DataFrame) -> list[dict]:
    """Per extra market: Brier vs the best constant forecast (always predict the rate that actually occurred).
    That baseline is strict (it peeks at the test rate), so 'beats' here is a fair claim."""
    out = []
    for mkt in markets.EXTRA_MARKETS:
        if f"x_{mkt}" not in pred.columns:
            continue
        hit = np.array([bool(markets.extra_outcome(mkt, sel if isinstance(sel, str) else None, int(h), int(a)))
                        for sel, h, a in zip(pred[f"xs_{mkt}"], pred.FTHG, pred.FTAG)], dtype=float)
        p = pred[f"x_{mkt}"].to_numpy(dtype=float)
        rate = float(hit.mean())
        out.append({"market": mkt, "n": int(len(hit)), "rate": rate, "pred": float(p.mean()),
                    "brier": float(((p - hit) ** 2).mean()), "constant": rate * (1 - rate)})
    return out


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
        print("  extra markets (Brier vs best constant forecast; lower is better):")
        for e in extra_market_table(pred):
            tag = "beats constant" if e["brier"] < e["constant"] else "does NOT beat constant"
            print(f"    {e['market']:<11} n={e['n']}  predicted {e['pred']:.3f} happened {e['rate']:.3f}  "
                  f"Brier {e['brier']:.4f} vs {e['constant']:.4f}  {tag}")
        vr = value_research(pred, df)
        if vr:
            print(f"  PRIVATE research (not advice): flat bets where model edge >= threshold, {vr['source']} prices "
                  f"(margin included), {vr['matches']} matches")
            for r in vr["rows"]:
                if r["roi"] is None:
                    print(f"    edge >= {r['threshold']:.0%}: {r['n']} bets, too few to judge")
                else:
                    sig = ("profit unlikely to be luck" if r["ci_low"] > 0 else
                           "loss unlikely to be luck" if r["ci_high"] < 0 else "cannot be told from luck")
                    print(f"    edge >= {r['threshold']:.0%}: {r['n']} bets, ROI {r['roi']:+.1%} "
                          f"(95% {r['ci_low']:+.1%} to {r['ci_high']:+.1%}): {sig}")
        c = component_and_ci(pred)
        print(f"  parts: Elo only {c['brier_elo']:.4f} | Dixon-Coles only {c['brier_dc']:.4f} | "
              f"ensemble {s['brier_1x2']:.4f}")
        sig = ("the edge over base rates is unlikely to be luck" if c["ci_high"] < 0 else
               "the sample cannot separate the model from the base rate")
        print(f"  ensemble minus base-rate Brier {c['diff']:+.4f}, 95% interval "
              f"[{c['ci_low']:+.4f}, {c['ci_high']:+.4f}]: {sig}")
        b = odds_benchmark(pred, df)
        if b:
            gap = b["model_brier"] - b["odds_brier"]
            print(f"  vs {b['source']} odds on {b['n']} of {len(pred)} matches that have them (margin removed): "
                  f"odds acc {b['odds_acc']:.3f} Brier {b['odds_brier']:.4f} | "
                  f"model acc {b['model_acc']:.3f} Brier {b['model_brier']:.4f} | "
                  f"model is {'worse' if gap > 0 else 'better'} by {abs(gap):.4f} Brier")
        else:
            print("  (no usable bookmaker odds columns in this file for this season)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
