"""MatchMind public API + transparency dashboard (FastAPI).

Run:  uvicorn api.main:app --host 0.0.0.0 --port 8000
Read-only: this service reads the ledger with a read-only database role and never writes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent))
load_dotenv()

from src import markets, teams  # noqa: E402
from . import db  # noqa: E402

DESCRIPTION = """
Public, read-only football predictions from an append-only ledger. Every pick is stored **before kickoff**
and graded automatically after the match, including the misses.

**Conventions**
- All timestamps are ISO 8601 in **UTC** (`kickoff_utc`, `created_at`, `settled_at`). `match_date` is a calendar date.
- Probabilities are decimals between 0 and 1 and sum to 1 for `p_home + p_draw + p_away`.
  `p_over25` is P(3+ total goals); `p_btts` is P(both teams score). Under 2.5 and "BTTS: No" are `1 - p`.
- `hit` is true when the highest-probability result occurred. `brier_contrib` is the 3-class Brier score for that match
  (0 is perfect; always guessing 1/3 each scores 0.667).
- Goal-market probabilities (`p_over25`, `p_btts`) are **experimental**: in backtests they do not reliably beat base rates.
- `null` means unknown or not yet available, never zero.

**Freshness and empty results.** An empty `/api/upcoming` list is normal when no fixtures are scheduled. Check
`/api/status` -> `freshness`: a recent `last_run` with `open_picks: 0` means nothing to predict; a missing or old
`last_run` means the nightly job has not reported. `next_scheduled_run_utc` is a schedule, not a guarantee.

Responses are cached for 60 seconds. CORS is open for GET. Not betting advice.
"""
app = FastAPI(title="MatchMind API", version="1.7.0", description=DESCRIPTION)
# Public, read-only, GET-only data: safe to read from any website's browser code.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=[])
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

CACHE = "public, max-age=60"
TEAM_COLS = ("home_team", "away_team", "team")


def to_records(df) -> list[dict]:
    """DataFrame -> JSON-safe list (NaN/NaT -> null, timestamps -> ISO) with club display names."""
    rows = json.loads(df.to_json(orient="records", date_format="iso"))
    for row in rows:
        for col in TEAM_COLS:
            if col in row and row[col]:
                row[f"{col}_name"] = teams.display(row[col])
    return rows


def respond(resp: Response, df) -> list[dict]:
    resp.headers["Cache-Control"] = CACHE
    return to_records(df)


@app.get("/", include_in_schema=False)
def landing():
    return FileResponse(BASE / "static" / "landing.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(BASE / "static" / "index.html")


NIGHTLY_UTC = (22, 55)   # keep in step with the cron in .github/workflows/nightly.yml


def next_scheduled_run(now: dt.datetime | None = None) -> str:
    """The next scheduled nightly time (a schedule, not a guarantee: GitHub may start it a little late)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    nxt = now.replace(hour=NIGHTLY_UTC[0], minute=NIGHTLY_UTC[1], second=0, microsecond=0)
    if nxt <= now:
        nxt += dt.timedelta(days=1)
    return nxt.isoformat()


@app.get("/api/export/predictions.csv", summary="Download every live pick as CSV",
         response_class=Response,
         responses={200: {"content": {"text/csv": {"example": "prediction_id,league,match_date,kickoff_utc,home_team,away_team,p_home,..."}}}})
def export_predictions(limit: int = Query(5000, ge=1, le=20000)):
    """One row per live pick with its model version, creation time and (once played) result and grade.
    The `X-Content-SHA256` header is the hash of the body, so you can check a saved copy was not altered after
    download. It does not by itself prove the ledger is untampered; that is enforced by the database."""
    with db.connection() as conn:
        df = db.query_export(conn, limit)
    body = df.to_csv(index=False).encode()
    return Response(body, media_type="text/csv", headers={
        "Cache-Control": CACHE, "X-Content-SHA256": hashlib.sha256(body).hexdigest(),
        "Content-Disposition": 'attachment; filename="matchmind-live-picks.csv"'})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/status", summary="Service status, live record and data freshness",
         responses={200: {"content": {"application/json": {"example": {
             "status": "ok", "leagues": ["E0"], "graded_live_picks": 5, "live_accuracy": 0.6, "version": "1.7.0",
             "freshness": {"last_prediction_at": "2026-11-07T22:56:10+00:00", "last_result_at": "2026-11-08T22:57:02+00:00",
                           "next_kickoff_utc": None, "open_picks": 0,
                           "models": [{"league": "E0", "version_id": "v1.0-ens", "trained_through": "2026-11-07"}],
                           "last_run": {"finished_at": "2026-11-08T22:58:00+00:00", "league": "E0", "ok": True,
                                        "fixtures_seen": 0, "predictions_appended": 0, "issues": None},
                           "next_scheduled_run_utc": "2026-11-09T22:55:00+00:00"}}}}}})
def status(resp: Response):
    with db.connection() as conn:
        leagues = db.query_leagues(conn)
        rec = db.query_record(conn)
        fresh = db.query_freshness(conn)
    live = rec[rec.provenance == "live"] if len(rec) else rec
    n_live = int(live["n"].sum()) if len(live) else 0
    acc = float((live["accuracy"] * live["n"]).sum() / n_live) if n_live else None  # weighted
    resp.headers["Cache-Control"] = CACHE
    return {"status": "ok", "leagues": leagues, "graded_live_picks": n_live,
            "live_accuracy": acc, "version": app.version,
            "freshness": {**fresh, "next_scheduled_run_utc": next_scheduled_run(),
                          "note": "open_picks = 0 with a recent last_run means no fixtures are scheduled, "
                                  "not a failed job. A missing or old last_run means the job has not reported."}}


@app.get("/api/upcoming", summary="Locked picks for matches that have not kicked off",
         responses={200: {"content": {"application/json": {"example": [{
             "league": "E0", "match_date": "2026-11-14", "kickoff_utc": "2026-11-14T15:00:00.000Z",
             "home_team": "arsenal", "away_team": "chelsea", "home_team_name": "Arsenal", "away_team_name": "Chelsea",
             "p_home": 0.5, "p_draw": 0.25, "p_away": 0.25, "p_over25": 0.58, "p_btts": 0.55,
             "confidence_band": "low", "data_quality": "A", "model_version": "v1.0-ens",
             "created_at": "2026-11-12T22:56:00.000Z"}]}}}})
def upcoming(resp: Response, days: int = Query(14, ge=1, le=30)):
    with db.connection() as conn:
        return respond(resp, db.query_upcoming(conn, days))


@app.get("/api/recent", summary="Most recently graded live picks")
def recent(resp: Response, limit: int = Query(25, ge=1, le=100)):
    with db.connection() as conn:
        return respond(resp, db.query_recent_settled(conn, limit))


@app.get("/api/record", summary="Accuracy and Brier score per league (live vs backtest)")
def record(resp: Response):
    with db.connection() as conn:
        df = db.query_record(conn)
    if not len(df):
        raise HTTPException(404, "No graded predictions yet.")
    return respond(resp, df)


@app.get("/api/market-record", summary="Over 2.5 and BTTS Brier scores against base-rate baselines")
def market_record(resp: Response):
    with db.connection() as conn:
        return respond(resp, db.query_market_record(conn))


@app.get("/api/calibration")
def calibration(resp: Response, league: str = Query(..., min_length=1, max_length=8,
                                                     pattern=r"^[A-Za-z0-9]+$")):
    with db.connection() as conn:
        df = db.query_calibration(conn, league)
    if not len(df):
        raise HTTPException(404, f"No calibration data for {league} yet.")
    return respond(resp, df)


@app.get("/api/worst-misses", summary="The graded picks with the largest Brier error")
def worst_misses(resp: Response, limit: int = Query(10, ge=1, le=50)):
    with db.connection() as conn:
        return respond(resp, db.query_worst_misses(conn, limit))


@app.get("/api/leaderboard", summary="Public arena leaderboard (nicknames only)")
def leaderboard(resp: Response, limit: int = Query(25, ge=1, le=100)):
    with db.connection() as conn:
        df = db.query_leaderboard(conn, limit)
    return respond(resp, df)          # empty list until someone has 10 settled picks


@app.get("/api/market-picks", summary="Locked picks for extra markets (double chance, goal lines, clean sheets, scores)")
def market_picks(resp: Response, days: int = Query(7, ge=1, le=30)):
    """Long format: one row per fixture and market. `market` ids are listed in `/api/market-catalog`.
    All are experimental until the record shows they beat the constant-forecast baseline."""
    with db.connection() as conn:
        return respond(resp, db.query_market_picks(conn, days))


@app.get("/api/market-catalog", summary="What each market id means, and whether the backtest showed an edge")
def market_catalog(resp: Response):
    resp.headers["Cache-Control"] = CACHE
    return [{"market": k, "label": v, "backtest": markets.backtest_evidence(k)} for k, v in markets.EXTRA_MARKETS.items()]


@app.get("/api/extra-market-record", summary="Graded record per extra market against the best constant forecast")
def extra_market_record(resp: Response):
    """`brier` is the model's average Brier; `brier_constant` is what always predicting the observed rate would score.
    A market only adds value when `brier` is below `brier_constant` over enough matches."""
    with db.connection() as conn:
        return respond(resp, db.query_extra_market_record(conn))


@app.get("/api/team/{league}/{team}")
def team_profile(resp: Response, league: str, team: str):
    with db.connection() as conn:
        df = db.query_team_profile(conn, league, team)
    if not len(df):
        raise HTTPException(404, f"No profile for {team} in {league} (needs 5+ matches).")
    return respond(resp, df)[0]


@app.get("/api/markets")
def markets_upcoming(resp: Response, days: int = Query(3, ge=1, le=14)):
    """Upcoming Over 2.5 / BTTS picks (same rows as /api/upcoming, goal-market columns)."""
    with db.connection() as conn:
        df = db.query_upcoming(conn, days)
    return respond(resp, df[df["p_over25"].notna()])
