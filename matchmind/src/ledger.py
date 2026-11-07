"""In-memory reference implementation of the ledger rules (used by tests and offline reports).

Production does the same work in SQL (``src/sql.py``) inside the database, which also enforces
append-only behaviour with triggers. ``settle`` here returns the FULL settlements table with
only the new rows added.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

LABELS = ("H", "D", "A")
ONEHOT = {"H": (1, 0, 0), "D": (0, 1, 0), "A": (0, 0, 1)}


def brier_contrib(p: np.ndarray, result: str) -> float:
    return float(((p - np.array(ONEHOT[result])) ** 2).sum())


def append_predictions(predictions: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Append-only insert. Raises on duplicate (model, fixture) — picks are immutable."""
    key = ["model_version", "league", "match_date", "home_team", "away_team"]
    dup = new[key].merge(predictions[key], on=key)
    if len(dup):
        raise ValueError(f"Duplicate predictions rejected: {len(dup)} rows")
    return pd.concat([predictions, new], ignore_index=True)


def settle(predictions: pd.DataFrame, results: pd.DataFrame,
           settlements: pd.DataFrame) -> pd.DataFrame:
    """Settle predictions that have a result but no settlement.
    Returns the full settlements table; only NEW rows were appended."""
    key = ["league", "match_date", "home_team", "away_team"]
    merged = predictions.merge(results, on=key, suffixes=("", "_res"))
    merged = merged.merge(
        settlements[["prediction_id"]], on="prediction_id", how="left", indicator=True)
    merged = merged[merged._merge == "left_only"].drop(columns="_merge")
    if not len(merged):
        return settlements
    p = merged[["p_home", "p_draw", "p_away"]].to_numpy()
    merged["hit"] = p.argmax(axis=1) == merged["result"].map({l: i for i, l in enumerate(LABELS)})
    merged["brier_contrib"] = [
        brier_contrib(p[i], r) for i, r in enumerate(merged["result"])]
    new = merged[["prediction_id", "hit", "brier_contrib"]].copy()
    new["settled_at"] = pd.Timestamp.now()
    if not len(settlements):
        return new.reset_index(drop=True)
    return pd.concat([settlements, new], ignore_index=True)


def score(predictions: pd.DataFrame, settlements: pd.DataFrame) -> pd.DataFrame:
    """All-time public record, per league/provenance/model version."""
    df = predictions.merge(settlements, on="prediction_id")
    out = df.groupby(["league", "model_version", "provenance"]).agg(
        n=("prediction_id", "size"),
        accuracy=("hit", "mean"),
        brier=("brier_contrib", "mean"),
        first_match=("match_date", "min"),
        last_match=("match_date", "max")).reset_index()
    return out
