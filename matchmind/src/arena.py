"""Arena: users vs. the model, graded by the ledger.

Scoring (v2). A user pick is a single outcome (H/D/A), so it is compared with the model's
single TOP pick on the same fixture, never with a probabilistic Brier score (a hard pick has
Brier 0 when right and 2 when wrong, so it would "lose to the model" even when copying it):

    win  = user right, model wrong      loss = user wrong, model right
    tie  = both right or both wrong

Leaderboard: ranked by net wins per pick, (wins - losses) / n, and only users with at least
MIN_SETTLED settled picks appear (one lucky pick must not top the board). Picks are FINAL:
once submitted they cannot be changed, and the database refuses picks made at or after
kickoff. Settlement is SQL (src/sql.py::SETTLE_ARENA) so it inherits ledger immutability.
"""
from __future__ import annotations

import pandas as pd

MIN_SETTLED = 10          # keep in sync with v_leaderboard in migration-2026-11-06.sql
PICKS = ("H", "D", "A")


def model_top_pick(p_home: float, p_draw: float, p_away: float) -> str:
    """Same tie-break as the ledger's SQL: home, then draw, then away."""
    if p_home >= p_draw and p_home >= p_away:
        return "H"
    return "D" if p_draw >= p_away else "A"


def head_to_head(user_pick: str, model_pick: str, result: str) -> str:
    user_right, model_right = user_pick == result, model_pick == result
    if user_right and not model_right:
        return "win"
    if model_right and not user_right:
        return "loss"
    return "tie"


def leaderboard(arena_users: pd.DataFrame, arena_predictions: pd.DataFrame,
                arena_settlements: pd.DataFrame, min_settled: int = MIN_SETTLED) -> pd.DataFrame:
    """Pandas twin of the v_leaderboard SQL view (used by tests and offline reports)."""
    df = arena_predictions.merge(arena_settlements, on="arena_prediction_id") \
                          .merge(arena_users, on="user_id")
    g = df.groupby(["user_id", "display_name"])
    out = g.agg(n=("arena_prediction_id", "size"),
                wins=("outcome", lambda s: int((s == "win").sum())),
                losses=("outcome", lambda s: int((s == "loss").sum())),
                ties=("outcome", lambda s: int((s == "tie").sum())),
                user_hits=("user_hit", "sum"),
                model_hits=("model_hit", "sum")).reset_index()
    out["net_rate"] = (out["wins"] - out["losses"]) / out["n"]
    out["hit_rate"] = out["user_hits"] / out["n"]
    out["model_hit_rate"] = out["model_hits"] / out["n"]
    out = out[out["n"] >= min_settled]
    return out.sort_values(["net_rate", "n"], ascending=False).reset_index(drop=True)
