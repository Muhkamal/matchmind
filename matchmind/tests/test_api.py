import datetime as dt
from contextlib import contextmanager

import psycopg
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from src import sql  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


@pytest.fixture()
def client(pg_uri, monkeypatch):
    from api import db, main

    @contextmanager
    def fake():
        with psycopg.connect(pg_uri) as c:
            c.execute("SET ROLE matchmind_ro")          # run exactly as the production API role
            yield c
    monkeypatch.setattr(db, "connection", fake)
    with psycopg.connect(pg_uri, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")   # skip immutability triggers: test cleanup only
        c.execute("DELETE FROM market_settlements; DELETE FROM settlements; DELETE FROM predictions;"
                  "DELETE FROM results; DELETE FROM arena_settlements; DELETE FROM arena_predictions;"
                  "DELETE FROM arena_users")
    return TestClient(main.app)


def _seed(pg_uri):
    with psycopg.connect(pg_uri, autocommit=True) as c:
        c.execute(sql.INSERT_MODEL_VERSION, ("v-api", "E0", dt.date(2026, 1, 1), "{}"))
        p = (0.5, 0.3, 0.2)
        fut = NOW + dt.timedelta(days=2)
        c.execute(sql.INSERT_PREDICTION, ("v-api", "E0", fut.date(), "man-city", "arsenal", *p, *p,
                                          "medium", "A", "live", fut, 0.6, 0.55, "2-1:0.100"))
        past = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)
        c.execute(sql.INSERT_PREDICTION, ("v-api", "E0", past.date(), "chelsea", "fulham", *p, *p,
                                          "medium", "A", "live", past, 0.6, 0.55, "1-1:0.1"))
        c.execute("SELECT pg_sleep(1.3)")                    # kickoff now in the past
        c.execute(sql.INSERT_RESULT, ("E0", past.date(), "chelsea", "fulham", 2, 0, "H"))
        c.execute(sql.SETTLE_1X2); c.execute(sql.SETTLE_MARKETS)


def test_empty_state(client):
    assert client.get("/api/upcoming").json() == []
    assert client.get("/api/leaderboard").json() == []
    assert client.get("/api/record").status_code == 404
    s = client.get("/api/status").json()
    assert s["graded_live_picks"] == 0 and s["live_accuracy"] is None


def test_endpoints_with_data(client, pg_uri):
    _seed(pg_uri)
    up = client.get("/api/upcoming")
    assert up.headers["cache-control"].startswith("public")
    rows = up.json()
    assert len(rows) == 1 and rows[0]["home_team_name"] == "Man City"
    assert client.get("/api/status").json()["graded_live_picks"] == 1
    assert client.get("/api/recent").json()[0]["result"] == "H"
    cal = client.get("/api/calibration", params={"league": "E0"}).json()
    assert {r["market"] for r in cal} == {"home", "draw", "away"}
    assert client.get("/api/market-record").json()[0]["n"] == 1
    assert len(client.get("/api/markets").json()) == 1


@pytest.mark.parametrize("url", ["/api/upcoming?days=0", "/api/upcoming?days=9999",
                                 "/api/recent?limit=-1", "/api/recent?limit=100000",
                                 "/api/calibration?league=E0;DROP", "/api/calibration"])
def test_bad_params_rejected(client, url):
    assert client.get(url).status_code == 422


def test_api_role_cannot_write(pg_uri):
    with psycopg.connect(pg_uri) as c:
        c.execute("SET ROLE matchmind_ro")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO results VALUES (default,'E0',now(),'a','b',1,0,'H',now())")


def test_pages_served(client):
    land = client.get("/")
    dash = client.get("/dashboard")
    assert land.status_code == 200 and "audit" in land.text
    assert dash.status_code == 200 and "Upcoming picks" in dash.text
