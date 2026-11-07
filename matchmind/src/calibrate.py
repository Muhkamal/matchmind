"""Reliability helpers and public confidence labels.

There is deliberately NO recalibration model here. A per-market isotonic calibrator failed
the honest out-of-sample protocol and was removed (no pickles, no scikit-learn). Recalibration
may return only after validation on accumulated live-ledger history. Published probabilities
are the raw model output, and the ledger records ``calibrator: none``.
"""
from __future__ import annotations

import numpy as np

LABELS = ("H", "D", "A")


def confidence_band(p: np.ndarray) -> np.ndarray:
    """Public confidence label from the top probability of each row of ``p``."""
    top = p.max(axis=1)
    return np.where(top >= 0.60, "high", np.where(top >= 0.50, "medium", "low"))


def reliability_table(p: np.ndarray, y: np.ndarray, lab: str, bins: int = 10) -> np.ndarray:
    """Rows [bin_center, n, pred_mean, actual_rate] for one market."""
    edges = np.linspace(0, 1, bins + 1)
    target = (y == lab).astype(float)
    rows = []
    for i in range(bins):
        m = (p >= edges[i]) & (p <= edges[i + 1] if i == bins - 1 else p < edges[i + 1])
        if m.sum() > 0:
            rows.append([p[m].mean(), m.sum(), p[m].mean(), target[m].mean()])
    return np.array(rows)


def ece(p: np.ndarray, y: np.ndarray, lab: str, bins: int = 10) -> float:
    """Expected calibration error for one market (nan when there is no data)."""
    t = reliability_table(p, y, lab, bins)
    if len(t) == 0:
        return float("nan")
    return float(np.average(np.abs(t[:, 2] - t[:, 3]), weights=t[:, 1]))
