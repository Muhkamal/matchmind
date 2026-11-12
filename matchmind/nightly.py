"""MatchMind nightly pipeline.

Runs every evening (GitHub Actions / cron / Task Scheduler):
  1. Ingest recent results + upcoming fixtures   (football-data.org)
  2. Refit on history, predict the next fixtures (src/models.py::predict_full)
  3. Append predictions to the ledger            (append-only; FIRST live pick per fixture wins)
  4. Settle everything that has finished         (1X2, goal markets, arena; idempotent SQL)

Guarantees
- A fixture is predicted once. The database has a unique index on live picks per fixture,
  and ``ON CONFLICT DO NOTHING`` keeps the first one; refits never add a second row.
- Only fixtures that kick off at least 10 minutes in the future are predicted, and the
  database independently refuses a live pick created at/after kickoff.
- A club the registry does not know is reported and its fixture skipped. It is never
  predicted as an "average team".
- Failures are isolated per league but NOT hidden: the process exits 1 if any league had
  an error, unmapped club, skipped fixture or insufficient history, so CI turns red.

License boundary: football-data.co.uk CSVs are local training history only (non-commercial
R&D). They never enter the production database.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from src import calibrate, ingest, markets, models, sql, teams  # noqa: E402
from src.dbpool import fetch_df  # noqa: E402

load_dotenv()

# Leagues the team registry can serve (see src/teams.py::LEAGUE_TEAMS).
LEAGUE_MAP = {"E0": "PL"}                      # our code -> football-data.org code
LOCAL_HISTORY = Path(__file__).parent / "data" / "history"
MIN_TRAIN_MATCHES = 300


@dataclass
class LeagueReport:
    league: str
    results_ingested: int = 0
    fixtures_seen: int = 0
    appended: int = 0
    skipped: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def log_runs(conn, reports, settled_total: int) -> None:
    """Record one row per league so the public site can show when the job last ran and how it went.
    Best effort: if the table is missing (migration not applied) the job itself must still succeed."""
    try:
        with conn.cursor() as cur:
            for r in reports:
                cur.execute(
                    "INSERT INTO job_runs (league, ok, results_ingested, fixtures_seen, predictions_appended,"
                    " settled, issues) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (r.league, not r.issues, r.results_ingested, r.fixtures_seen, r.appended,
                     settled_total, "; ".join(r.issues)[:500] or None))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print(f"note: could not write job_runs ({type(e).__name__}); apply migration-2026-11-09.sql")


def get_conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"])


# ---------- settlement (idempotent SQL) ----------

def settle(conn) -> dict[str, int]:
    counts = {}
    with conn.cursor() as cur:
        for name, statement in (("1x2", sql.SETTLE_1X2), ("markets", sql.SETTLE_MARKETS),
                                ("extra", sql.SETTLE_EXTRA_MARKETS), ("arena", sql.SETTLE_ARENA)):
            cur.execute(statement)
            counts[name] = max(cur.rowcount or 0, 0)
    conn.commit()
    return counts


# ---------- ingestion ----------

def upsert_results(conn, league: str, df) -> int:
    if not len(df):
        return 0
    rows = [(league, r.Date.date(), r.HomeTeam, r.AwayTeam, int(r.FTHG), int(r.FTAG),
             "H" if r.FTHG > r.FTAG else ("D" if r.FTHG == r.FTAG else "A"))
            for r in df.itertuples()]
    with conn.cursor() as cur:
        cur.executemany(sql.INSERT_RESULT, rows)
        inserted = max(cur.rowcount or 0, 0)
    conn.commit()
    return inserted


# ---------- training data ----------

def load_training(conn, league: str, history_dir: Path | None = None):
    """Training frame with canonical team slugs.

    Local dev CSV (if present) provides deep history; database results newer than the CSV's
    last date are appended, so the model keeps learning even when the CSV is not refreshed.
    """
    import pandas as pd
    cols = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
    frames, csv_max = [], None
    csv_path = Path(history_dir or LOCAL_HISTORY) / f"{league}.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path)
        df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, format="mixed")
        df["HomeTeam"] = df["HomeTeam"].map(lambda s: teams.canon(s, "csv"))
        df["AwayTeam"] = df["AwayTeam"].map(lambda s: teams.canon(s, "csv"))
        frames.append(df[cols])
        csv_max = df["Date"].max()
    query = ("SELECT match_date, home_team, away_team, home_goals, away_goals FROM results "
             "WHERE league=%s" + (" AND match_date > %s" if csv_max is not None else "")
             + " ORDER BY match_date")
    params = (league, csv_max.date()) if csv_max is not None else (league,)
    db = fetch_df(conn, query, params)
    db.columns = cols
    db["Date"] = pd.to_datetime(db["Date"])
    frames.append(db)
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(["Date", "HomeTeam", "AwayTeam"]).sort_values("Date")
    out[["FTHG", "FTAG"]] = out[["FTHG", "FTAG"]].astype(int)
    return out.reset_index(drop=True)


def quality_grade(n_train: int) -> str:
    return "A" if n_train >= 2000 else ("B" if n_train >= 1000 else "C")


def version_id_for(model: "models.EnsembleV1", league: str) -> str:
    """Immutable id: engine version + league + data cut-off + hash of the parameters."""
    digest = hashlib.sha1(json.dumps(model.PARAMS, sort_keys=True, default=str).encode()).hexdigest()[:6]
    return f"{model.VERSION}-{league}-{model.trained_through.date().isoformat()}-{digest}"


# ---------- prediction ----------

def build_rows(model, league: str, fixtures, version_id: str, quality: str):
    """Pure function: fixtures -> (ledger rows, skipped descriptions)."""
    import numpy as np
    rows, skipped = [], []
    for f in fixtures.itertuples():
        try:
            full = model.predict_full(f.HomeTeam, f.AwayTeam)
        except ValueError as e:
            skipped.append(f"{f.HomeTeam} v {f.AwayTeam}: {e}")
            continue
        p, mk = full["p"], full["markets"]
        band = str(calibrate.confidence_band(np.asarray(p).reshape(1, -1))[0])
        rows.append((version_id, league, f.Date.date(), f.HomeTeam, f.AwayTeam,
                     float(p[0]), float(p[1]), float(p[2]),      # raw == published (no calibrator)
                     float(p[0]), float(p[1]), float(p[2]),
                     band, quality, "live", f.KickoffUtc.to_pydatetime(),
                     float(mk["p_over25"]), float(mk["p_btts"]), mk["top_scorelines"]))
    return rows, skipped


def extra_rows_for(model, home: str, away: str):
    """Extra match-level markets for one fixture, from the same fitted model as the 1X2 pick."""
    full = model.predict_full(home, away)
    return markets.extra_markets(full["matrix"], full["p"])


def predict_and_append(conn, league: str, fixtures, report: LeagueReport) -> None:
    fixtures = ingest.only_future(fixtures)
    report.fixtures_seen = len(fixtures)
    if not len(fixtures):
        return
    train = load_training(conn, league)
    if len(train) < MIN_TRAIN_MATCHES:
        report.issues.append(
            f"only {len(train)} training matches (< {MIN_TRAIN_MATCHES}); nothing published. "
            f"Run backfill.py (or add data/history/{league}.csv).")
        return
    model = models.EnsembleV1().fit(train)
    version_id = version_id_for(model, league)
    params = {**models.EnsembleV1.PARAMS, "train_n": len(train)}
    rows, skipped = build_rows(model, league, fixtures, version_id, quality_grade(len(train)))
    report.skipped += skipped
    with conn.cursor() as cur:
        cur.execute(sql.INSERT_MODEL_VERSION,
                    (version_id, league, model.trained_through.date(), json.dumps(params)))
        for row in rows:
            cur.execute(sql.INSERT_PREDICTION_RETURNING, row)
            inserted = cur.fetchone()
            if inserted is None:                      # already locked earlier: leave it, and its markets, alone
                continue
            report.appended += 1
            for market, subject, selection, prob in extra_rows_for(model, row[3], row[4]):
                cur.execute(sql.INSERT_MARKET_PREDICTION, (inserted[0], market, subject, selection, prob))
    conn.commit()


def _season_start(today: dt.date) -> int:
    return today.year if today.month >= 7 else today.year - 1


def run_league(conn, key: str, league: str, today: dt.date) -> LeagueReport:
    report = LeagueReport(league)
    if league not in LEAGUE_MAP:
        report.issues.append(
            f"league {league!r} has no team registry/league map; supported: {sorted(LEAGUE_MAP)}")
        return report
    res = ingest.fetch_football_data_org(
        key, LEAGUE_MAP[league], str(today - dt.timedelta(days=3)), str(today + dt.timedelta(days=7)))
    report.results_ingested = upsert_results(conn, league, res.finished)
    if res.unmapped:
        report.issues.append(f"unmapped clubs (add to src/teams.py): {res.unmapped}")
    predict_and_append(conn, league, res.upcoming, report)
    if report.skipped:
        report.issues.append(
            f"{len(report.skipped)} fixture(s) skipped (a club with no history in the training data, e.g. just "
            f"promoted). Fix: python backfill.py --seasons {_season_start(today)} so this season's results "
            f"are in the database. Never replace the skip with placeholder probabilities.")
    return report


def main() -> int:
    key = os.environ["FOOTBALL_DATA_ORG_KEY"]
    today = dt.date.today()
    leagues = [s.strip() for s in os.environ.get("LEAGUES", "E0").split(",") if s.strip()]
    conn = get_conn()
    reports: list[LeagueReport] = []
    for league in leagues:
        try:
            reports.append(run_league(conn, key, league, today))
        except Exception as e:  # isolate the league, but never hide the failure
            conn.rollback()
            r = LeagueReport(league)
            r.issues.append(f"{type(e).__name__}: {e}")
            reports.append(r)
    settled = settle(conn)
    log_runs(conn, reports, sum(settled.values()))
    conn.close()

    print(f"settled: {settled}")
    bad = 0
    for r in reports:
        print(f"[{r.league}] results ingested: {r.results_ingested} | fixtures seen: "
              f"{r.fixtures_seen} | predictions appended: {r.appended}")
        for line in r.skipped:
            print(f"   skipped: {line}")
        for issue in r.issues:
            print(f"   ISSUE: {issue}")
        bad += bool(r.issues)
    if bad:
        print(f"{bad} league(s) need attention (exit 1)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
