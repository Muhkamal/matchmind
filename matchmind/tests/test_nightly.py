import datetime as dt

import pandas as pd
import psycopg

import nightly
from src import markets, ingest, models
from tests.synth import make_history

UTC = dt.timezone.utc


def _fixtures(rows):
    return pd.DataFrame(rows, columns=["Date", "KickoffUtc", "HomeTeam", "AwayTeam"])


def _kick(hours):
    k = pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=hours)
    return k.tz_localize(None).normalize(), k


def test_only_future_margin_and_empty():
    d1, k1 = _kick(5); d2, k2 = _kick(0.05); d3, k3 = _kick(-3)
    fx = _fixtures([(d1, k1, "arsenal", "chelsea"), (d2, k2, "everton", "fulham"),
                    (d3, k3, "wolves", "burnley")])
    out = ingest.only_future(fx)
    assert list(out.HomeTeam) == ["arsenal"]          # <10 min to kickoff and started: both dropped
    assert ingest.only_future(fx.iloc[0:0]).empty


def test_build_rows_skips_unknown_and_matches_ledger_columns():
    df = make_history(n_seasons=3)
    model = models.EnsembleV1().fit(df)
    d, k = _kick(24)
    fx = _fixtures([(d, k, "arsenal", "chelsea"), (d, k, "arsenal", "bournemouth-xx")])
    rows, skipped = nightly.build_rows(model, "E0", fx, "v-x", "A")
    assert len(rows) == 1 and len(skipped) == 1
    assert len(rows[0]) == 18                          # matches sql.INSERT_PREDICTION placeholders
    assert abs(sum(rows[0][5:8]) - 1) < 1e-6 and rows[0][5:8] == rows[0][8:11]  # raw == published


class FakeFdo:
    def __init__(self, finished, upcoming, unmapped=()):
        self.res = ingest.FdoResult(finished, upcoming, list(unmapped), 0)

    def __call__(self, *a, **k):
        return self.res


def _history_in_db(conn, league="E0"):
    df = make_history(n_seasons=3)
    nightly.upsert_results(conn, league, df)


def test_end_to_end_nightly(clean_db, monkeypatch, tmp_path):
    monkeypatch.setattr(nightly, "LOCAL_HISTORY", tmp_path)          # no CSV: train from the database
    d, k = _kick(30)
    fdo = FakeFdo(pd.DataFrame(columns=ingest.RESULT_COLUMNS),
                  _fixtures([(d, k, "arsenal", "chelsea"), (d, k, "everton", "fulham")]))
    monkeypatch.setattr(ingest, "fetch_football_data_org", fdo)
    with psycopg.connect(clean_db) as conn:
        _history_in_db(conn)
        rep = nightly.run_league(conn, "key", "E0", dt.date.today())
        assert rep.appended == 2 and not rep.issues
        rep2 = nightly.run_league(conn, "key", "E0", dt.date.today())    # second run: nothing new
        assert rep2.appended == 0
        n = conn.execute("SELECT count(*) FROM predictions").fetchone()[0]
        assert n == 2
        # every newly locked pick carries all extra markets; the second run must not add more
        assert conn.execute("SELECT count(*) FROM market_predictions").fetchone()[0] == 2 * len(markets.EXTRA_MARKETS)
        assert nightly.settle(conn) == {"1x2": 0, "markets": 0, "extra": 0, "arena": 0}


def test_unmapped_and_thin_history_make_run_fail(clean_db, monkeypatch, tmp_path):
    monkeypatch.setattr(nightly, "LOCAL_HISTORY", tmp_path)
    d, k = _kick(30)
    empty = pd.DataFrame(columns=ingest.RESULT_COLUMNS)
    monkeypatch.setattr(ingest, "fetch_football_data_org",
                        FakeFdo(empty, _fixtures([(d, k, "arsenal", "chelsea")]), unmapped=["Atlantis FC"]))
    with psycopg.connect(clean_db) as conn:
        rep = nightly.run_league(conn, "key", "E0", dt.date.today())
    assert rep.appended == 0
    assert any("unmapped" in i for i in rep.issues) and any("training matches" in i for i in rep.issues)


def test_unsupported_league_is_reported():
    rep = nightly.run_league(None, "key", "ZZ9", dt.date.today())
    assert rep.issues and "no team registry" in rep.issues[0]


def test_quality_grades():
    assert [nightly.quality_grade(n) for n in (500, 1500, 2500)] == ["C", "B", "A"]


def test_backfill_defaults_include_current_season():
    import datetime as dt
    import backfill
    assert backfill.current_season_start(dt.date(2026, 11, 7)) == 2026
    assert backfill.current_season_start(dt.date(2027, 3, 1)) == 2026
    assert backfill.default_seasons(dt.date(2026, 11, 7))[-1] == 2026


def test_backfill_403_is_a_plan_limit_not_a_failure(monkeypatch, capsys):
    import urllib.error
    import backfill

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 403, "forbidden", {}, None)
    monkeypatch.setattr(backfill.ingest, "fetch_football_data_org", boom)
    total, problems = backfill.backfill_league(None, "k", "E0", [2021])
    assert total == 0 and problems == [] and "plan" in capsys.readouterr().out


def test_log_runs_writes_one_row_per_league_and_never_raises(pg_uri):
    import psycopg
    import nightly
    with psycopg.connect(pg_uri) as conn:
        ok = nightly.LeagueReport("E0"); ok.fixtures_seen = 5
        bad = nightly.LeagueReport("SP1"); bad.issues.append("boom")
        before = conn.execute("SELECT count(*) FROM job_runs").fetchone()[0]
        nightly.log_runs(conn, [ok, bad], 3)
        rows = conn.execute("SELECT league, ok, issues FROM job_runs ORDER BY run_id DESC LIMIT 2").fetchall()
        assert conn.execute("SELECT count(*) FROM job_runs").fetchone()[0] == before + 2
        assert {(r[0], r[1]) for r in rows} == {("E0", True), ("SP1", False)}
        conn.execute("DROP TABLE job_runs CASCADE")        # missing table must not crash the job
        nightly.log_runs(conn, [ok], 0)
        conn.rollback()
