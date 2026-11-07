"""MatchMind statistical core: Elo + Dixon-Coles ensemble.

One entry point, ``EnsembleV1.predict_full``, produces EVERYTHING that is published for
a fixture (1X2, Over 2.5, BTTS, scorelines) and always goes through the unknown-team
check. The nightly job, the bot and the backtest all call it, so they cannot drift.

Consistency guarantee: the published 1X2 is the Elo/Dixon-Coles blend; the scoreline
matrix used for the goal markets is *reconciled* to that blend (see
``markets.reconcile``), so Over 2.5 / BTTS / exact scores can never contradict the
published 1X2.

Honest evaluation (see ``backtest.py`` and README): hyperparameters were selected on
seasons <= 2022-23 and checked on 2023-24; seasons after that are held out. No
calibrator ships.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import poisson

from . import markets

LABELS = ("H", "D", "A")
PROMOTED_PRIOR_K = 6.0  # pseudo-matches of prior knowledge toward average


# ---------------- Elo ----------------

class EloModel:
    def __init__(self, k: float = 16.0, home_adv: float = 65.0, start: float = 1500.0,
                 draw_peak: float = 0.30, draw_width: float = 0.45):
        self.k, self.home_adv, self.start = k, home_adv, start
        self.draw_peak, self.draw_width = draw_peak, draw_width
        self.ratings: dict[str, float] = {}

    def _expected(self, home: str, away: str) -> float:
        rh = self.ratings.get(home, self.start)
        ra = self.ratings.get(away, self.start)
        return 1.0 / (1.0 + 10.0 ** (-((rh + self.home_adv - ra) / 400.0)))

    def _draw_prob(self, eh: float) -> float:
        s = 2.0 * eh - 1.0
        return self.draw_peak * np.exp(-((s / self.draw_width) ** 2))

    def predict(self, home: str, away: str) -> np.ndarray:
        eh = self._expected(home, away)
        p_d = self._draw_prob(eh)
        rem = 1.0 - p_d
        return np.array([rem * eh, p_d, rem * (1.0 - eh)])

    def update(self, home: str, away: str, home_goals: int, away_goals: int) -> None:
        eh = self._expected(home, away)
        s_h = 1.0 if home_goals > away_goals else (0.5 if home_goals == away_goals else 0.0)
        self.ratings[home] = self.ratings.get(home, self.start) + self.k * (s_h - eh)
        self.ratings[away] = self.ratings.get(away, self.start) + self.k * ((1.0 - s_h) - (1.0 - eh))

    def fit(self, matches: pd.DataFrame) -> "EloModel":
        for r in matches.sort_values("Date").itertuples():
            self.update(r.HomeTeam, r.AwayTeam, int(r.FTHG), int(r.FTAG))
        return self

    def knows(self, team: str) -> bool:
        return team in self.ratings


# ---------------- Dixon-Coles ----------------
# Parameter convention (important for anything that EXPLAINS the model):
#   expected home goals = exp(atk[home] + dfn[away] + home_adv)
#   expected away goals = exp(atk[away] + dfn[home])
# so a HIGHER dfn means a team CONCEDES MORE (a leakier defence), not a better one.

def fit_dixon_coles(train: pd.DataFrame, xi: float = 0.0035, ridge: float = 0.1,
                    max_iter: int = 500) -> dict:
    """Bounded MLE (L-BFGS-B). Columns: Date, HomeTeam, AwayTeam, FTHG, FTAG."""
    teams = sorted(set(train.HomeTeam) | set(train.AwayTeam))
    n = len(teams)
    idx = {t: i for i, t in enumerate(teams)}
    t_ref = train.Date.max()
    w = np.exp(-xi * (t_ref - train.Date).dt.days.to_numpy())
    hg = train.FTHG.to_numpy(float)
    ag = train.FTAG.to_numpy(float)
    hi = train.HomeTeam.map(idx).to_numpy()
    ai = train.AwayTeam.map(idx).to_numpy()
    games = np.bincount(hi, minlength=n) + np.bincount(ai, minlength=n)

    def nll(p: np.ndarray) -> float:
        atk, dfn, home, rho = p[:n], p[n:2 * n], p[2 * n], p[2 * n + 1]
        lh = np.exp(atk[hi] + dfn[ai] + home)
        la = np.exp(atk[ai] + dfn[hi])
        ll = w * (hg * np.log(lh) - lh - gammaln(hg + 1)
                  + ag * np.log(la) - la - gammaln(ag + 1))
        tau = np.ones(len(hg))
        m00 = (hg == 0) & (ag == 0)
        m01 = (hg == 0) & (ag == 1)
        m10 = (hg == 1) & (ag == 0)
        m11 = (hg == 1) & (ag == 1)
        tau[m00] = 1 - lh[m00] * la[m00] * rho
        tau[m01] = 1 + lh[m01] * rho
        tau[m10] = 1 + la[m10] * rho
        tau[m11] = 1 - rho
        return -(ll + w * np.log(np.clip(tau, 1e-12, None))).sum() \
            + ridge * ((atk ** 2).sum() + (dfn ** 2).sum())

    p0 = np.zeros(2 * n + 2)
    p0[2 * n] = 0.25
    p0[2 * n + 1] = -0.08
    bounds = [(-3, 3)] * (2 * n) + [(0.0, 0.6)] + [(-0.3, 0.3)]
    res = minimize(nll, p0, method="L-BFGS-B", bounds=bounds, options={"maxiter": max_iter})
    if not res.success:
        raise RuntimeError(f"Dixon-Coles fit failed: {res.message}")
    return {"idx": idx, "atk": res.x[:n], "dfn": res.x[n:2 * n],
            "home": res.x[2 * n], "rho": res.x[2 * n + 1], "games": games}


def _shrunk(model: dict, team: str):
    """Empirical-Bayes shrinkage toward average for clubs with little history.
    Returns (attack, defence-leakiness)."""
    if team not in model["idx"]:
        return 0.0, 0.0
    i = model["idx"][team]
    s = model["games"][i] / (model["games"][i] + PROMOTED_PRIOR_K)
    return model["atk"][i] * s, model["dfn"][i] * s


def dc_matrix(model: dict, home: str, away: str, max_goals: int = 10) -> np.ndarray:
    """Normalised scoreline matrix M[i, j] = P(home scores i, away scores j)."""
    a_h, d_h = _shrunk(model, home)
    a_a, d_a = _shrunk(model, away)
    lh = np.exp(np.clip(a_h + d_a + model["home"], -3, 2.2))
    la = np.exp(np.clip(a_a + d_h, -3, 2.2))
    M = np.outer(poisson.pmf(np.arange(max_goals + 1), lh),
                 poisson.pmf(np.arange(max_goals + 1), la))
    M[0, 0] *= max(1 - lh * la * model["rho"], 1e-9)
    M[0, 1] *= max(1 + lh * model["rho"], 1e-9)
    M[1, 0] *= max(1 + la * model["rho"], 1e-9)
    M[1, 1] *= max(1 - model["rho"], 1e-9)
    return M / M.sum()


def dc_predict(model: dict, home: str, away: str, max_goals: int = 10) -> np.ndarray:
    return markets.outcome_probs(dc_matrix(model, home, away, max_goals))


# ---------------- Ensemble ----------------

class EnsembleV1:
    """Weighted Elo + Dixon-Coles blend. Versioned and reproducible."""

    VERSION = "v1.2-ens"
    PARAMS = {"k": 16, "home_adv": 65, "xi": 0.0025, "ridge": 0.05, "weight_elo": 0.4,
              "draw_peak": 0.30, "draw_width": 0.45, "promoted_prior_k": PROMOTED_PRIOR_K,
              "markets": "dc-matrix-reconciled-to-1x2", "calibrator": "none"}

    def __init__(self):
        self.elo = EloModel(k=self.PARAMS["k"], home_adv=self.PARAMS["home_adv"],
                            draw_peak=self.PARAMS["draw_peak"],
                            draw_width=self.PARAMS["draw_width"])
        self.dc: dict | None = None
        self.trained_through: pd.Timestamp | None = None
        self.train_n = 0

    def fit(self, matches: pd.DataFrame) -> "EnsembleV1":
        if not len(matches):
            raise ValueError("Empty training frame — refusing to fit.")
        self.elo.fit(matches)
        self.dc = fit_dixon_coles(matches, xi=self.PARAMS["xi"], ridge=self.PARAMS["ridge"])
        self.trained_through = matches.Date.max()
        self.train_n = len(matches)
        return self

    def knows(self, team: str) -> bool:
        return self.elo.knows(team) or (self.dc is not None and team in self.dc["idx"])

    def _check(self, team: str) -> None:
        if not self.knows(team):
            from .teams import known_display_names
            raise ValueError(
                f"Unknown team {team!r} — not in the training data. "
                f"Known clubs: {', '.join(known_display_names())}")

    def predict_full(self, home: str, away: str) -> dict:
        """Everything published for one fixture. Raises ValueError on an unknown team."""
        self._check(home)
        self._check(away)
        w = self.PARAMS["weight_elo"]
        p_elo = self.elo.predict(home, away)
        matrix_dc = dc_matrix(self.dc, home, away)
        p_dc = markets.outcome_probs(matrix_dc)
        p = w * p_elo + (1 - w) * p_dc
        p = p / p.sum()
        matrix = markets.reconcile(matrix_dc, p)
        return {"p": p, "p_elo": p_elo, "p_dc": p_dc, "matrix": matrix,
                "markets": markets.market_probs(matrix)}

    def predict_raw(self, home: str, away: str) -> np.ndarray:
        return self.predict_full(home, away)["p"]
