import io
import json
import urllib.error

import pandas as pd
import pytest

import nightly
from src import ingest, report


class Resp(io.BytesIO):
    pass


def _match(status, home, away, hg=None, ag=None, when="2026-11-08T15:00:00Z"):
    return {"status": status, "utcDate": when, "homeTeam": {"name": home}, "awayTeam": {"name": away},
            "score": {"fullTime": {"home": hg, "away": ag}}}


def _patch(monkeypatch, matches):
    monkeypatch.setattr(ingest.urllib.request, "urlopen",
                        lambda req, timeout=0: Resp(json.dumps({"matches": matches}).encode()))
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)


def test_statuses_and_unmapped(monkeypatch):
    _patch(monkeypatch, [
        _match("FINISHED", "Arsenal FC", "Chelsea FC", 2, 1),
        _match("TIMED", "Everton FC", "Fulham FC"),
        _match("IN_PLAY", "Wolverhampton Wanderers FC", "Burnley FC", 1, 0),
        _match("POSTPONED", "Brentford FC", "Newcastle United FC"),
        _match("SCHEDULED", "Atlantis FC", "Arsenal FC"),
        _match("FINISHED", "Arsenal FC", "Everton FC", None, None),
    ])
    r = ingest.fetch_football_data_org("k", "PL", "2026-11-01", "2026-11-10")
    assert len(r.finished) == 1 and r.finished.iloc[0].FTHG == 2
    assert list(r.upcoming.HomeTeam) == ["everton"]
    assert r.unmapped == ["Atlantis FC"] and r.skipped_matches == 2
    assert str(r.upcoming.KickoffUtc.iloc[0].tz) == "UTC"


def test_empty_window(monkeypatch):
    _patch(monkeypatch, [])
    r = ingest.fetch_football_data_org("k", "PL", "2026-11-01", "2026-11-10")
    assert r.finished.empty and r.upcoming.empty and list(r.finished.columns) == ingest.RESULT_COLUMNS
    assert ingest.only_future(r.upcoming).empty


def test_retry_once_on_429(monkeypatch):
    calls = []

    def fake(req, timeout=0):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("u", 429, "slow", {}, None)
        return Resp(b'{"matches": []}')
    monkeypatch.setattr(ingest.urllib.request, "urlopen", fake)
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    assert ingest._get_json("http://x", {}) == {"matches": []} and len(calls) == 2


def test_main_exit_code(monkeypatch):
    class Conn:
        def close(self): pass
        def rollback(self): pass
    monkeypatch.setenv("FOOTBALL_DATA_ORG_KEY", "k")
    monkeypatch.setattr(nightly, "get_conn", lambda: Conn())
    monkeypatch.setattr(nightly, "settle", lambda c: {})
    ok = nightly.LeagueReport("E0")
    monkeypatch.setattr(nightly, "run_league", lambda *a: ok)
    assert nightly.main() == 0
    bad = nightly.LeagueReport("E0"); bad.issues.append("boom")
    monkeypatch.setattr(nightly, "run_league", lambda *a: bad)
    assert nightly.main() == 1
    monkeypatch.setattr(nightly, "run_league", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert nightly.main() == 1


def _frames(date_type):
    d = pd.Timestamp("2026-11-03")
    d = d.date() if date_type == "date" else d
    pred = pd.DataFrame({"prediction_id": [1, 2, 3], "league": ["E0"] * 3, "provenance": ["live", "live", "backtest"],
                         "match_date": [d] * 3, "home_team": ["arsenal", "everton", "wolves"],
                         "away_team": ["chelsea", "fulham", "burnley"],
                         "p_home": [.5, .4, .6], "p_draw": [.3, .3, .2], "p_away": [.2, .3, .2]})
    st = pd.DataFrame({"prediction_id": [1, 2, 3], "hit": [True, False, True], "brier_contrib": [.38, .74, .24]})
    res = pd.DataFrame({"league": ["E0"] * 3, "match_date": [d] * 3, "home_team": pred.home_team,
                        "away_team": pred.away_team, "result": ["H", "A", "H"]})
    return pred, st, res


@pytest.mark.parametrize("kind", ["date", "ts"])
def test_report_live_only_and_date_types(kind):
    pred, st, res = _frames(kind)
    md = report.weekly_report(pred, st, res, "E0", pd.Timestamp("2026-11-02"))
    assert "(this week, live):** 2" in md                       # backtest row excluded
    assert "isotonic" not in md and "not shown" in md           # no false calibration claim; ECE gated


def test_report_empty_week():
    pred, st, res = _frames("ts")
    assert "No live predictions settled" in report.weekly_report(pred, st, res, "E0", pd.Timestamp("2026-12-07"))
