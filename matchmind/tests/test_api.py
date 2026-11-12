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
        c.execute("DELETE FROM market_outcomes; DELETE FROM market_predictions; DELETE FROM market_settlements; DELETE FROM settlements; DELETE FROM predictions;"
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


def test_status_freshness_and_cors(client, pg_uri):
    _seed(pg_uri)
    s = client.get("/api/status", headers={"Origin": "https://example.org"})
    assert s.headers["access-control-allow-origin"] == "*"
    f = s.json()["freshness"]
    assert f["last_run"] is None                         # no job_runs rows yet: say so, never invent one
    assert f["open_picks"] == 1 and f["next_kickoff_utc"] and f["last_prediction_at"]
    assert f["models"][0]["version_id"] == "v-api" and f["next_scheduled_run_utc"]
    with psycopg.connect(pg_uri, autocommit=True) as c:
        c.execute("INSERT INTO job_runs (league, ok, fixtures_seen, predictions_appended) VALUES ('E0', true, 5, 0)")
    run = client.get("/api/status").json()["freshness"]["last_run"]
    assert run["ok"] is True and run["fixtures_seen"] == 5 and run["issues"] is None


def test_next_scheduled_run_rolls_to_tomorrow():
    import datetime as dt
    from api.main import next_scheduled_run
    before = dt.datetime(2026, 11, 9, 20, 0, tzinfo=dt.timezone.utc)
    after = dt.datetime(2026, 11, 9, 23, 30, tzinfo=dt.timezone.utc)
    assert next_scheduled_run(before).startswith("2026-11-09T22:55")
    assert next_scheduled_run(after).startswith("2026-11-10T22:55")


def test_export_csv_and_openapi_docs(client, pg_uri):
    import hashlib
    _seed(pg_uri)
    r = client.get("/api/export/predictions.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["x-content-sha256"] == hashlib.sha256(r.content).hexdigest()
    lines = r.text.strip().splitlines()
    assert len(lines) == 3 and "model_version" in lines[0] and "brier_contrib" in lines[0]
    assert "v-api" in lines[1]
    spec = client.get("/openapi.json").json()
    assert "UTC" in spec["info"]["description"] and "null" in spec["info"]["description"]
    assert "example" in spec["paths"]["/api/upcoming"]["get"]["responses"]["200"]["content"]["application/json"]


def test_extra_market_endpoints(client, pg_uri):
    import psycopg
    from src import sql
    _seed(pg_uri)
    with psycopg.connect(pg_uri, autocommit=True) as c:
        pid = c.execute("SELECT prediction_id FROM predictions WHERE home_team='man-city'").fetchone()[0]
        c.execute(sql.INSERT_MARKET_PREDICTION, (pid, "dc_1x", "", None, 0.8))
        c.execute(sql.INSERT_MARKET_PREDICTION, (pid, "score_top1", "", "2-1", 0.11))
    rows = client.get("/api/market-picks").json()
    assert {r["market"] for r in rows} == {"dc_1x", "score_top1"} and rows[0]["home_team_name"]
    cat = {r["market"] for r in client.get("/api/market-catalog").json()}
    assert "over_3_5" in cat and "cs_home" in cat
    assert client.get("/api/extra-market-record").json() == []            # nothing graded yet: no invented record
