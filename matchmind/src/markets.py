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


def top_cells(matrix: np.ndarray, k: int = 5) -> list[tuple[tuple[int, int], float]]:
    """The k most likely exact scores [((home, away), probability), ...] within the 0-5 goal window."""
    flat = [((i, j), float(matrix[i, j])) for i in range(6) for j in range(6)]
    flat.sort(key=lambda x: -x[1])
    return flat[:k]


def top_scorelines(matrix: np.ndarray, k: int = 5) -> str:
    """Compact "2-1:0.113,1-1:0.096,..." string for the ledger."""
    return ",".join(f"{i}-{j}:{p:.3f}" for (i, j), p in top_cells(matrix, k))


# Match-level markets beyond 1X2, all read off the reconciled scoreline matrix, so none can contradict the 1X2.
# Every market is a yes/no event with no "push" (half-lines only). The SAME rules are written in
# sql.SETTLE_EXTRA_MARKETS; a test keeps the two in step.
EXTRA_MARKETS = {
    "dc_1x": "Home or draw (double chance 1X)",
    "dc_x2": "Draw or away (double chance X2)",
    "dc_12": "Home or away, no draw (double chance 12)",
    "over_1_5": "Over 1.5 goals",
    "over_3_5": "Over 3.5 goals",
    "cs_home": "Home clean sheet",
    "cs_away": "Away clean sheet",
    "score_top1": "Exact score is the single most likely one",
    "score_top5": "Exact score is one of the five most likely",
    "ah_home_p1_5": "Home +1.5 handicap (home does not lose by 2+)",
    "ah_away_p1_5": "Away +1.5 handicap (away does not lose by 2+)",
    "ah_home_m1_5": "Home -1.5 handicap (home wins by 2+)",
    "ah_away_m1_5": "Away -1.5 handicap (away wins by 2+)",
    "wtn_home": "Home win to nil",
    "wtn_away": "Away win to nil",
    "tg_0_1": "Total goals 0-1",
    "tg_2_3": "Total goals 2-3",
    "tg_4_5": "Total goals 4-5",
    "tg_6p": "Total goals 6 or more",
    "ts2_home": "Home team scores 2 or more",
    "ts2_away": "Away team scores 2 or more",
    "ts3_home": "Home team scores 3 or more",
    "ts3_away": "Away team scores 3 or more",
    "btts_over25": "Both teams score and over 2.5 goals",
    "btts_under25": "Both teams score and under 2.5 goals",
}

# Matrix events: (home goals h, away goals a) -> bool. One table drives both the probabilities and the Python grader.
_EVENTS = {
    "dc_1x": lambda h, a: h >= a, "dc_x2": lambda h, a: a >= h, "dc_12": lambda h, a: h != a,
    "over_1_5": lambda h, a: h + a >= 2, "over_3_5": lambda h, a: h + a >= 4,
    "cs_home": lambda h, a: a == 0, "cs_away": lambda h, a: h == 0,
    "ah_home_p1_5": lambda h, a: a - h <= 1, "ah_away_p1_5": lambda h, a: h - a <= 1,
    "ah_home_m1_5": lambda h, a: h - a >= 2, "ah_away_m1_5": lambda h, a: a - h >= 2,
    "wtn_home": lambda h, a: h > a and a == 0, "wtn_away": lambda h, a: a > h and h == 0,
    "tg_0_1": lambda h, a: h + a <= 1, "tg_2_3": lambda h, a: 2 <= h + a <= 3,
    "tg_4_5": lambda h, a: 4 <= h + a <= 5, "tg_6p": lambda h, a: h + a >= 6,
    "ts2_home": lambda h, a: h >= 2, "ts2_away": lambda h, a: a >= 2,
    "ts3_home": lambda h, a: h >= 3, "ts3_away": lambda h, a: a >= 3,
    "btts_over25": lambda h, a: h > 0 and a > 0 and h + a >= 3,
    "btts_under25": lambda h, a: h > 0 and a > 0 and h + a <= 2,
}


def extra_markets(matrix: np.ndarray, p) -> list[tuple[str, str, str | None, float]]:
    """[(market, subject, selection, probability)] for the extra match-level markets. ``p`` = published 1X2."""
    clamp = lambda x: float(min(1.0, max(0.0, x)))  # noqa: E731
    cells = top_cells(matrix, 5)
    h_idx, a_idx = np.indices(matrix.shape)
    out = []
    for market in EXTRA_MARKETS:
        if market == "score_top1":
            out.append((market, "", f"{cells[0][0][0]}-{cells[0][0][1]}", clamp(cells[0][1])))
        elif market == "score_top5":
            out.append((market, "", ",".join(f"{i}-{j}" for (i, j), _ in cells), clamp(sum(c[1] for c in cells))))
        else:
            mask = np.vectorize(_EVENTS[market])(h_idx, a_idx)
            out.append((market, "", None, clamp(matrix[mask].sum())))
    return out


def extra_outcome(market: str, selection: str | None, home_goals: int, away_goals: int) -> bool | None:
    """Did the market happen? None for a market this code does not know (it is then left ungraded, never guessed)."""
    score = f"{home_goals}-{away_goals}"
    if market == "score_top1":
        return score == selection
    if market == "score_top5":
        return selection is not None and score in selection.split(",")
    rule = _EVENTS.get(market)
    return None if rule is None else bool(rule(home_goals, away_goals))


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
