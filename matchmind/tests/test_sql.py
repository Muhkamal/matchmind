"""Integration tests against real Postgres: migrations, settlement, ledger guarantees, privacy."""
import datetime as dt

import psycopg
import pytest

from src import sql

NOW = dt.datetime.now(dt.timezone.utc)
DAY = dt.timedelta(days=1)


def _version(conn, vid="v-test"):
    conn.execute(sql.INSERT_MODEL_VERSION, (vid, "E0", dt.date(2026, 1, 1), "{}"))


def _pred(conn, home="arsenal", away="chelsea", kickoff=None, p=(0.5, 0.3, 0.2),
          vid="v-test", prov="live", over=0.55, btts=0.5):
    kickoff = kickoff or NOW + DAY
    cur = conn.execute(sql.INSERT_PREDICTION, (
        vid, "E0", kickoff.date(), home, away, *p, *p, "medium", "A", prov, kickoff,
        over, btts, "1-1 (11%)"))
    return cur.rowcount


def _pid(conn, home="arsenal", away="chelsea"):
    return conn.execute("SELECT prediction_id FROM predictions WHERE home_team=%s AND away_team=%s",
                        (home, away)).fetchone()[0]


def _result(conn, date, home, away, hg, ag):
    r = "H" if hg > ag else "D" if hg == ag else "A"
    conn.execute(sql.INSERT_RESULT, ("E0", date, home, away, hg, ag, r))


def test_first_live_pick_per_fixture_wins(conn):
    _version(conn, "v-a"); _version(conn, "v-b")
    assert _pred(conn, vid="v-a") == 1
    assert _pred(conn, vid="v-b", p=(0.1, 0.1, 0.8)) == 0          # refit: ignored
    n, ph = conn.execute("SELECT count(*), max(p_home) FROM predictions WHERE provenance='live'").fetchone()
    assert n == 1 and abs(ph - 0.5) < 1e-6


def test_live_pick_after_kickoff_is_refused(conn):
    _version(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        _pred(conn, kickoff=NOW - dt.timedelta(hours=1))


@pytest.mark.parametrize("stmt", ["UPDATE predictions SET p_home=0.9", "DELETE FROM predictions",
                                  "TRUNCATE predictions"])
def test_ledger_is_append_only(conn, stmt):
    _version(conn); _pred(conn)
    with pytest.raises(psycopg.errors.Error):
        conn.execute(stmt)


def test_settlement_idempotent_and_markets(conn):
    _version(conn)
    kick = NOW + dt.timedelta(hours=2)
    _pred(conn, kickoff=kick, p=(0.6, 0.25, 0.15), over=0.7, btts=0.6)
    _result(conn, kick.date(), "arsenal", "chelsea", 2, 1)
    for _ in range(2):                                           # second run must add nothing
        for s in (sql.SETTLE_1X2, sql.SETTLE_MARKETS):
            conn.execute(s)
    hit, brier = conn.execute("SELECT hit, brier_contrib FROM settlements").fetchone()
    assert hit is True and abs(brier - (0.4**2 + 0.25**2 + 0.15**2)) < 1e-5
    o, b, bo, bb = conn.execute("SELECT over25_actual, btts_actual, brier_over25, brier_btts "
                                "FROM market_settlements").fetchone()
    assert o is True and b is True and abs(bo - 0.09) < 1e-5 and abs(bb - 0.16) < 1e-5
    assert conn.execute("SELECT count(*) FROM settlements").fetchone()[0] == 1


def _user(conn, tg, name):
    return conn.execute("INSERT INTO arena_users (telegram_id, display_name) VALUES (%s,%s) "
                        "RETURNING user_id", (tg, name)).fetchone()[0]


def test_arena_head_to_head(conn):
    _version(conn)
    kick = NOW + dt.timedelta(hours=2)
    _pred(conn, kickoff=kick, p=(0.6, 0.25, 0.15))               # model top pick = H
    pid = _pid(conn)
    u1, u2, u3 = _user(conn, 1, "Alice"), _user(conn, 2, "Bobby"), _user(conn, 3, "Cara")
    for u, pick in ((u1, "A"), (u2, "H"), (u3, "D")):
        conn.execute("INSERT INTO arena_predictions (user_id, model_prediction_id, league, match_date,"
                     " home_team, away_team, pick, kickoff_utc) VALUES (%s,%s,'E0',%s,'arsenal','chelsea',%s,%s)",
                     (u, pid, kick.date(), pick, kick))
    _result(conn, kick.date(), "arsenal", "chelsea", 0, 1)       # away win: model wrong
    conn.execute(sql.SETTLE_ARENA); conn.execute(sql.SETTLE_ARENA)
    out = dict(conn.execute("SELECT u.display_name, s.outcome FROM arena_settlements s "
                            "JOIN arena_predictions a USING (arena_prediction_id) "
                            "JOIN arena_users u USING (user_id)").fetchall())
    assert out == {"Alice": "win", "Bobby": "tie", "Cara": "tie"}   # both wrong = tie


def test_arena_pick_is_final_and_pre_kickoff(conn):
    _version(conn)
    kick = NOW + DAY
    _pred(conn, kickoff=kick); pid = _pid(conn); u = _user(conn, 9, "Dave")
    ins = ("INSERT INTO arena_predictions (user_id, model_prediction_id, league, match_date, home_team,"
           " away_team, pick, kickoff_utc) VALUES (%s,%s,'E0',%s,'arsenal','chelsea',%s,%s)")
    conn.execute(ins, (u, pid, kick.date(), "H", kick))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(ins, (u, pid, kick.date(), "A", kick))
    conn.rollback()
    _version(conn); _pred(conn, kickoff=kick); pid = _pid(conn); u = _user(conn, 9, "Dave")
    conn.execute(ins, (u, pid, kick.date(), "H", kick))
    with pytest.raises(psycopg.errors.Error):
        conn.execute("UPDATE arena_predictions SET pick='D'")


def test_display_name_rules(conn):
    _user(conn, 1, "Alice")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _user(conn, 2, "alice")                                  # case-insensitive unique
    conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        _user(conn, 3, "x")                                      # too short
    conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        _user(conn, 4, "bad<script>")


def test_leaderboard_hides_small_samples_and_private_ids(conn):
    _user(conn, 77, "Erin")
    assert conn.execute("SELECT count(*) FROM v_leaderboard").fetchone()[0] == 0
    cols = [d.name for d in conn.execute("SELECT * FROM v_leaderboard LIMIT 0").description]
    assert "telegram_id" not in cols and "user_id" not in cols and "display_name" in cols


def test_role_privileges(conn):
    _version(conn); _pred(conn); _user(conn, 5, "Finn")
    conn.execute("SET ROLE matchmind_ro")
    assert conn.execute("SELECT count(*) FROM predictions").fetchone()[0] >= 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT telegram_id FROM arena_users")
    conn.rollback(); _version(conn); _pred(conn); conn.execute("SET ROLE matchmind_ro")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM predictions")
    conn.rollback()
    conn.execute("SET ROLE matchmind_bot")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("INSERT INTO results VALUES (default,'E0',now(),'a','b',1,0,'H',now())")
    conn.rollback()
    conn.execute("SET ROLE anon")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM arena_users")
    conn.rollback()
    conn.execute("SET ROLE anon")
    conn.execute("SELECT * FROM v_leaderboard")                  # public views stay readable
    conn.rollback()


def test_views_query(conn):
    for v in ("v_model_record", "v_model_record_by_version", "v_calibration", "v_market_record",
              "v_team_profiles"):
        conn.execute(f"SELECT * FROM {v}").fetchall()
