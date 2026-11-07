"""Synthetic league history (latent team strengths + Poisson goals) for tests."""
import numpy as np
import pandas as pd

from src import teams


def make_history(n_seasons=4, start_year=2021, n_clubs=20, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    clubs = sorted(teams.TEAMS)[:n_clubs]
    atk = dict(zip(clubs, rng.normal(0, 0.25, n_clubs)))
    dfn = dict(zip(clubs, rng.normal(0, 0.25, n_clubs)))
    rows = []
    for s in range(n_seasons):
        day = pd.Timestamp(f"{start_year + s}-08-12")
        for h in clubs:
            for a in clubs:
                if h == a:
                    continue
                day += pd.Timedelta(hours=18)
                lh = np.exp(0.3 + atk[h] + dfn[a])
                la = np.exp(0.0 + atk[a] + dfn[h])
                rows.append((day.normalize(), h, a, int(rng.poisson(lh)), int(rng.poisson(la))))
    return pd.DataFrame(rows, columns=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
