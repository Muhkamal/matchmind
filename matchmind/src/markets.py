"""Goal markets and team profiles.

All goal markets come from ONE scoreline matrix M[i, j] = P(home i, away j) that has been
*reconciled* to the published 1X2 probabilities: the home-win cells are rescaled so they
sum to P(H), the draw cells to P(D), the away-win cells to P(A). Over 2.5, BTTS and exact
scores are then read off that matrix, so they cannot contradict the published 1X2.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def outcome_probs(matrix: np.ndarray) -> np.ndarray:
    """[P(home win), P(draw), P(away win)] from a scoreline matrix."""
    return np.array([np.tril(matrix, -1).sum(), np.trace(matrix), np.triu(matrix, 1).sum()])


def reconcile(matrix: np.ndarray, p_target) -> np.ndarray:
    """Rescale the matrix so its 1X2 marginals equal ``p_target`` exactly."""
    p_target = np.asarray(p_target, dtype=float)
    p_target = p_target / p_target.sum()
    cur = outcome_probs(matrix)
    if (cur <= 0).any():
        raise ValueError("Scoreline matrix has an empty outcome class; cannot reconcile.")
    scale = p_target / cur
    out = matrix.copy()
    out[np.tril_indices_from(out, -1)] *= scale[0]
    out[np.diag_indices_from(out)] *= scale[1]
    out[np.triu_indices_from(out, 1)] *= scale[2]
    return out / out.sum()


def market_probs(matrix: np.ndarray) -> dict:
    """All goal markets from a normalised scoreline matrix."""
    n = matrix.shape[0]
    idx = np.arange(n)
    totals = idx[:, None] + idx[None, :]
    return {
        "p_over15": float(matrix[totals >= 2].sum()),
        "p_over25": float(matrix[totals >= 3].sum()),
        "p_over35": float(matrix[totals >= 4].sum()),
        "p_btts": float(matrix[1:, 1:].sum()),
        # clean sheet = the opponent scored nothing (0-0 counts, same as team_profile)
        "p_home_cs": float(matrix[:, 0].sum()),
        "p_away_cs": float(matrix[0, :].sum()),
        "top_scorelines": top_scorelines(matrix, k=5),
    }


def top_scorelines(matrix: np.ndarray, k: int = 5) -> str:
    """Compact "2-1:0.113,1-1:0.096,..." string for the ledger."""
    flat = [((i, j), matrix[i, j]) for i in range(6) for j in range(6)]
    flat.sort(key=lambda x: -x[1])
    return ",".join(f"{i}-{j}:{p:.3f}" for (i, j), p in flat[:k])


def team_profile(history: pd.DataFrame, team: str, last_n: int | None = None) -> dict:
    """Team tendencies from finished matches (columns HomeTeam, AwayTeam, FTHG, FTAG, Date).

    ``last_n`` restricts the profile to the team's most recent N matches (use ~30:
    all-time rates are stale for style-of-play markets).
    """
    games = history[(history.HomeTeam == team) | (history.AwayTeam == team)]
    if "Date" in games.columns:
        games = games.sort_values("Date")
    if last_n:
        games = games.tail(last_n)
    if not len(games):
        return {"team": team, "n": 0}

    is_home = games.HomeTeam == team
    gf = games.FTHG.where(is_home, games.FTAG)
    ga = games.FTAG.where(is_home, games.FTHG)
    frame = pd.DataFrame({"gf": gf.to_numpy(), "ga": ga.to_numpy(),
                          "home": is_home.to_numpy()})

    def _rates(df: pd.DataFrame) -> dict:
        if not len(df):
            return {}
        total = df.gf + df.ga
        return {
            "n": int(len(df)), "gf": float(df.gf.mean()), "ga": float(df.ga.mean()),
            "over25_rate": float((total >= 3).mean()),
            "btts_rate": float(((df.gf > 0) & (df.ga > 0)).mean()),
            "cs_rate": float((df.ga == 0).mean()),
        }

    return {
        "team": team, "n": int(len(frame)),
        "goals_for": float(frame.gf.mean()), "goals_against": float(frame.ga.mean()),
        "overall": _rates(frame),
        "home": _rates(frame[frame.home]),
        "away": _rates(frame[~frame.home]),
        "last5": _last_form(games, team),
    }


def _last_form(games: pd.DataFrame, team: str, k: int = 5) -> str:
    """W/D/L string for the last k matches, most recent first."""
    recent = games.sort_values("Date", ascending=False).head(k) if "Date" in games.columns \
        else games.iloc[::-1].head(k)
    out = []
    for r in recent.itertuples():
        mine, theirs = (r.FTHG, r.FTAG) if r.HomeTeam == team else (r.FTAG, r.FTHG)
        out.append("W" if mine > theirs else ("D" if mine == theirs else "L"))
    return "".join(out)


def market_divergence(profile: dict, model_p_over25: float, model_p_btts: float) -> list[str]:
    """Flags where team history and model disagree materially."""
    flags = []
    ov = (profile.get("overall") or {}).get("over25_rate")
    if ov is not None and abs(ov - model_p_over25) >= 0.15:
        flags.append(f"team history {ov:.0%} Over 2.5 vs model {model_p_over25:.0%}")
    bt = (profile.get("overall") or {}).get("btts_rate")
    if bt is not None and abs(bt - model_p_btts) >= 0.15:
        flags.append(f"team history {bt:.0%} BTTS vs model {model_p_btts:.0%}")
    return flags
