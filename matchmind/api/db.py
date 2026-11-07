"""Query layer for the public API.

Read-only SQL over the ledger, run as the ``matchmind_ro`` role (API_DATABASE_URL). That role
cannot write anything and cannot see private arena tables, so even a bug or an injection in
this service cannot alter the record or leak user ids.
All parameters are bound; nothing is formatted into SQL.
"""
from __future__ import annotations

from contextlib import contextmanager

import pandas as pd

from src.dbpool import fetch_df, get_pool


@contextmanager
def connection():
    """Pooled read-only connection. Tests monkeypatch this."""
    with get_pool("API_DATABASE_URL").connection() as conn:
        yield conn


KEYS = "p.league, p.match_date, p.kickoff_utc, p.home_team, p.away_team"
JOIN_RESULT = ("JOIN results r ON (r.league, r.match_date, r.home_team, r.away_team) "
               "= (p.league, p.match_date, p.home_team, p.away_team)")


def query_upcoming(conn, days: int = 14) -> pd.DataFrame:
    """Live picks that have not kicked off yet, next N days."""
    return fetch_df(conn, f"""
        SELECT {KEYS}, p.p_home, p.p_draw, p.p_away, p.p_over25, p.p_btts, p.top_scorelines,
               p.confidence_band, p.data_quality, p.model_version, p.created_at
        FROM predictions p
        WHERE p.provenance = 'live' AND p.kickoff_utc > now()
          AND p.kickoff_utc < now() + make_interval(days => %s)
        ORDER BY p.kickoff_utc, p.league""", (days,))


def query_recent_settled(conn, limit: int = 25) -> pd.DataFrame:
    return fetch_df(conn, f"""
        SELECT {KEYS}, p.p_home, p.p_draw, p.p_away, r.result, r.home_goals, r.away_goals,
               s.hit, s.brier_contrib, s.settled_at
        FROM predictions p JOIN settlements s USING (prediction_id) {JOIN_RESULT}
        WHERE p.provenance = 'live'
        ORDER BY s.settled_at DESC, p.prediction_id DESC LIMIT %s""", (limit,))


def query_record(conn) -> pd.DataFrame:
    return fetch_df(conn, "SELECT * FROM v_model_record ORDER BY league, provenance DESC")


def query_calibration(conn, league: str) -> pd.DataFrame:
    return fetch_df(conn, "SELECT league, market, bin, n, pred, actual FROM v_calibration "
                          "WHERE league = %s ORDER BY market, bin", (league,))


def query_market_record(conn) -> pd.DataFrame:
    return fetch_df(conn, "SELECT * FROM v_market_record ORDER BY league")


def query_worst_misses(conn, limit: int = 10) -> pd.DataFrame:
    return fetch_df(conn, f"""
        SELECT {KEYS}, p.p_home, p.p_draw, p.p_away, r.result, r.home_goals, r.away_goals,
               s.brier_contrib
        FROM predictions p JOIN settlements s USING (prediction_id) {JOIN_RESULT}
        WHERE p.provenance = 'live'
        ORDER BY s.brier_contrib DESC LIMIT %s""", (limit,))


def query_leagues(conn) -> list[str]:
    return [r for r in fetch_df(conn, "SELECT DISTINCT league FROM predictions ORDER BY league")["league"]]


def query_leaderboard(conn, limit: int = 25) -> pd.DataFrame:
    return fetch_df(conn, "SELECT * FROM v_leaderboard ORDER BY net_rate DESC, n DESC LIMIT %s",
                    (limit,))


def query_team_profile(conn, league: str, team: str) -> pd.DataFrame:
    return fetch_df(conn, "SELECT * FROM v_team_profiles WHERE league=%s AND team=%s",
                    (league, team))
