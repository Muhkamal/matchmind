"""Data ingestion adapters.

License discipline:
- football-data.co.uk CSVs  -> DEV/BACKTEST ONLY (non-commercial). They are used as local
                               training history and never enter the production database.
- football-data.org           -> production results/fixtures (free tier: 10 req/min;
                               pull-and-cache, users read OUR database).
- api-football (API-SPORTS)   -> optional lineups/injuries/odds (free tier: 100 req/day).

Behaviour guarantees:
- Only SCHEDULED/TIMED matches become fixtures (never in-play, postponed, cancelled).
- Team names are canonicalised at the boundary. A match with an unknown club is SKIPPED
  and reported in ``FdoResult.unmapped`` instead of crashing the run or inventing a team.
- Empty windows return empty frames.
"""
from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import pandas as pd

from .teams import UnknownTeamError, canon

USER_AGENT = {"User-Agent": "MatchMind/1.3 (research project)"}
UPCOMING_STATUSES = ("SCHEDULED", "TIMED")
RESULT_COLUMNS = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
FIXTURE_COLUMNS = ["Date", "KickoffUtc", "HomeTeam", "AwayTeam"]
FDO_BASE = "https://api.football-data.org/v4"


@dataclass
class FdoResult:
    finished: pd.DataFrame
    upcoming: pd.DataFrame
    unmapped: list[str] = field(default_factory=list)   # club names we could not map
    skipped_matches: int = 0                              # matches dropped for any reason


def _get_json(url: str, headers: dict, timeout: float = 30.0, retry_wait: float = 65.0,
              transient_wait: float = 5.0, attempts: int = 4):
    """GET JSON. Retries rate limits (HTTP 429, free tier is 10 requests/minute) and transient failures
    (timeouts, connection errors, HTTP 5xx) with a growing pause. Real errors (401, 403, 404) fail at once.
    Retrying is safe: the ledger's unique index means a repeated run can never create a duplicate pick."""
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except urllib.error.HTTPError as e:
            if attempt == attempts or not (e.code == 429 or e.code >= 500):
                raise
            time.sleep(retry_wait if e.code == 429 else transient_wait * attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == attempts:
                raise
            time.sleep(transient_wait * attempt)


def fetch_football_data_org(api_key: str, league_code: str, date_from: str, date_to: str,
                            season: int | None = None, pause: float = 6.5) -> FdoResult:
    """Results and upcoming fixtures from football-data.org with canonical team slugs.
    league_code: 'PL', 'PD', 'CL', ...; season: optional start year (for backfills)."""
    endpoint = f"{FDO_BASE}/competitions/{league_code}/matches?dateFrom={date_from}&dateTo={date_to}"
    if season:
        endpoint += f"&season={season}"
    payload = _get_json(endpoint, {"X-Auth-Token": api_key})
    time.sleep(pause)  # 10 req/min cap

    finished, upcoming, unmapped, skipped = [], [], set(), 0
    for m in payload.get("matches", []):
        status = m.get("status")
        if status != "FINISHED" and status not in UPCOMING_STATUSES:
            continue  # in-play, postponed, cancelled, suspended: never a fixture or a result
        try:
            home, away = canon(m["homeTeam"]["name"], "fdo"), canon(m["awayTeam"]["name"], "fdo")
        except UnknownTeamError:
            for side in ("homeTeam", "awayTeam"):
                try:
                    canon(m[side]["name"], "fdo")
                except UnknownTeamError:
                    unmapped.add(m[side]["name"])
            skipped += 1
            continue
        kickoff = pd.Timestamp(m["utcDate"])
        kickoff = kickoff.tz_localize("UTC") if kickoff.tzinfo is None else kickoff.tz_convert("UTC")
        common = {"Date": kickoff.tz_localize(None).normalize(), "KickoffUtc": kickoff,
                  "HomeTeam": home, "AwayTeam": away}
        if status == "FINISHED":
            ft = (m.get("score") or {}).get("fullTime") or {}
            if ft.get("home") is None or ft.get("away") is None:
                skipped += 1   # e.g. awarded/abandoned with no score: do not guess
                continue
            finished.append({**common, "FTHG": int(ft["home"]), "FTAG": int(ft["away"])})
        else:
            upcoming.append(common)

    done = pd.DataFrame(finished, columns=["Date", "KickoffUtc", *RESULT_COLUMNS[1:]])
    fix = pd.DataFrame(upcoming, columns=FIXTURE_COLUMNS)
    if len(done):
        done = done.sort_values("Date")
    return FdoResult(done[RESULT_COLUMNS] if len(done) else done.reindex(columns=RESULT_COLUMNS),
                     fix, sorted(unmapped), skipped)


def only_future(fixtures: pd.DataFrame, now: pd.Timestamp | None = None,
                margin_minutes: int = 10) -> pd.DataFrame:
    """Keep fixtures that kick off comfortably in the future.

    football-data.org's free tier can lag, so a match may still say TIMED after it started.
    A pick published after kickoff would break the ledger's core promise, so drop those.
    """
    if not len(fixtures):
        return fixtures
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    cutoff = now + pd.Timedelta(minutes=margin_minutes)
    return fixtures[pd.to_datetime(fixtures["KickoffUtc"], utc=True) > cutoff].reset_index(drop=True)


def fetch_fdo_team_names(api_key: str, league_code: str, season: int | None = None) -> list[str]:
    """Club names exactly as football-data.org spells them (used by check_teams.py)."""
    url = f"{FDO_BASE}/competitions/{league_code}/teams"
    if season:
        url += f"?season={season}"
    payload = _get_json(url, {"X-Auth-Token": api_key})
    return sorted(t["name"] for t in payload.get("teams", []))


def fetch_football_data_csv(league: str, season_code: str) -> pd.DataFrame:
    """DEV ONLY: football-data.co.uk season CSV, e.g. ('E0','2526'). Canonical slugs."""
    url = f"https://www.football-data.co.uk/mmz4281/{season_code}/{league}.csv"
    req = urllib.request.Request(url, headers=USER_AGENT)
    raw = urllib.request.urlopen(req, timeout=30).read()
    df = pd.read_csv(io.BytesIO(raw), encoding="latin-1")
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date", "FTHG"])
    df["FTHG"] = df["FTHG"].astype(int)
    df["FTAG"] = df["FTAG"].astype(int)
    df["HomeTeam"] = df["HomeTeam"].map(lambda s: canon(s, "csv"))
    df["AwayTeam"] = df["AwayTeam"].map(lambda s: canon(s, "csv"))
    return df[RESULT_COLUMNS].sort_values("Date")


def fetch_api_football(api_key: str, endpoint: str, params: str = "") -> dict:
    """Optional lineups/injuries/odds. FREE TIER = 100 req/day: nightly batch only."""
    url = f"https://v3.football.api-sports.io/{endpoint}{params}"
    return _get_json(url, {"x-apisports-key": api_key})
