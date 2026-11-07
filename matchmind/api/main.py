"""MatchMind public API + transparency dashboard (FastAPI).

Run:  uvicorn api.main:app --host 0.0.0.0 --port 8000
Read-only: this service reads the ledger with a read-only database role and never writes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent))
load_dotenv()

from src import teams  # noqa: E402
from . import db  # noqa: E402

app = FastAPI(title="MatchMind API", version="1.3.0")
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


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/status")
def status(resp: Response):
    with db.connection() as conn:
        leagues = db.query_leagues(conn)
        rec = db.query_record(conn)
    live = rec[rec.provenance == "live"] if len(rec) else rec
    n_live = int(live["n"].sum()) if len(live) else 0
    acc = float((live["accuracy"] * live["n"]).sum() / n_live) if n_live else None  # weighted
    resp.headers["Cache-Control"] = CACHE
    return {"status": "ok", "leagues": leagues, "graded_live_picks": n_live,
            "live_accuracy": acc, "version": app.version}


@app.get("/api/upcoming")
def upcoming(resp: Response, days: int = Query(14, ge=1, le=30)):
    with db.connection() as conn:
        return respond(resp, db.query_upcoming(conn, days))


@app.get("/api/recent")
def recent(resp: Response, limit: int = Query(25, ge=1, le=100)):
    with db.connection() as conn:
        return respond(resp, db.query_recent_settled(conn, limit))


@app.get("/api/record")
def record(resp: Response):
    with db.connection() as conn:
        df = db.query_record(conn)
    if not len(df):
        raise HTTPException(404, "No graded predictions yet.")
    return respond(resp, df)


@app.get("/api/market-record")
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


@app.get("/api/worst-misses")
def worst_misses(resp: Response, limit: int = Query(10, ge=1, le=50)):
    with db.connection() as conn:
        return respond(resp, db.query_worst_misses(conn, limit))


@app.get("/api/leaderboard")
def leaderboard(resp: Response, limit: int = Query(25, ge=1, le=100)):
    with db.connection() as conn:
        df = db.query_leaderboard(conn, limit)
    return respond(resp, df)          # empty list until someone has 10 settled picks


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
