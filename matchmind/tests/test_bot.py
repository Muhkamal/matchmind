import datetime as dt
import random

import pytest

from src import botlogic as bl
from src import sql
from src.teams import UnknownTeamError


def test_parse_multiword_clubs():
    assert bl.parse_fixture("Man City Aston Villa".split()) == ("man-city", "aston-villa")
    assert bl.parse_pick("Arsenal Man City h".split())[2] == "H"


def test_parse_pick_errors():
    with pytest.raises(ValueError):
        bl.parse_pick(["Arsenal", "Chelsea", "X"])
    with pytest.raises(ValueError):
        bl.parse_pick(["Arsenal"])
    with pytest.raises(UnknownTeamError):
        bl.parse_pick(["Foo", "Bar", "H"])


def test_nickname_rules():
    assert bl.valid_nickname("Kay_man 9")
    for bad in ("ab", "x" * 21, "<b>hi</b>", "emoji😀name", ""):
        assert not bl.valid_nickname(bad)
    assert bl.valid_nickname(bl.default_nickname(random.Random(1)))


def test_chunking_respects_limit():
    text = "\n".join(["x" * 90] * 200)
    parts = bl.chunk_text(text, limit=500)
    assert all(len(p) <= 500 for p in parts) and "\n".join(parts) == text
    assert all(len(p) <= 100 for p in bl.chunk_text("y" * 250, limit=100))


def test_rate_limiter_and_cache():
    t = [0.0]
    rl = bl.RateLimiter(2, 10, clock=lambda: t[0])
    assert rl.allow(1) and rl.allow(1) and not rl.allow(1) and rl.allow(2)
    t[0] = 11
    assert rl.allow(1)
    calls = []
    cache = bl.TTLCache(5, clock=lambda: t[0])
    for _ in range(3):
        cache.get("k", lambda: calls.append(1) or 1)
    assert len(calls) == 1
    t[0] = 20
    cache.get("k", lambda: calls.append(1) or 1)
    assert len(calls) == 2


# ---- DB-backed arena operations (real Postgres) ----
NOW = dt.datetime.now(dt.timezone.utc)


def _setup(conn, kickoff):
    conn.execute(sql.INSERT_MODEL_VERSION, ("v-bot", "E0", dt.date(2026, 1, 1), "{}"))
    p = (0.5, 0.3, 0.2)
    conn.execute(sql.INSERT_PREDICTION, ("v-bot", "E0", kickoff.date(), "arsenal", "chelsea",
                                         *p, *p, "medium", "A", "live", kickoff, 0.5, 0.5, "1-1"))


def test_submit_pick_flow(pg_uri):
    import psycopg
    with psycopg.connect(pg_uri) as conn:
        _setup(conn, dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1))
        assert bl.submit_pick(conn, 111, "arsenal", "chelsea", "H")[0] == "ok"
        assert bl.submit_pick(conn, 111, "arsenal", "chelsea", "A")[0] == "duplicate"   # final
        assert bl.submit_pick(conn, 111, "arsenal", "liverpool", "H")[0] == "no_fixture"
        rows = bl.my_picks(conn, 111)
        assert len(rows) == 1 and rows[0][2] == "H"
        assert "pending" in bl.render_my_picks(rows)
        conn.rollback()


def test_submit_pick_closed_when_kickoff_near_or_unknown(pg_uri):
    import psycopg
    with psycopg.connect(pg_uri) as conn:
        _setup(conn, dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=2))
        conn.execute("SELECT pg_sleep(2.5)")
        assert bl.submit_pick(conn, 222, "arsenal", "chelsea", "H")[0] == "closed"
        conn.rollback()


def test_nickname_unique_and_default(pg_uri):
    import psycopg
    with psycopg.connect(pg_uri) as conn:
        uid, name = bl.get_or_create_user(conn, 1)
        assert name.startswith("Player") and bl.get_or_create_user(conn, 1) == (uid, name)
        assert bl.set_nickname(conn, 1, "Kayman") == "ok"
        assert bl.set_nickname(conn, 2, "kayman") == "taken"
        assert bl.set_nickname(conn, 2, "no") == "invalid"
        conn.rollback()


def test_leaderboard_render():
    assert "No one" in bl.render_leaderboard([])
    out = bl.render_leaderboard([("Ann", 12, 6, 3, 3, 0.5, 0.45)])
    assert "Ann" in out and "6W 3L 3T" in out
