"""One-off backfill: pull several full seasons per league from football-data.org into the
results table, so models train on real history. Run ONCE locally, then rely on nightly.py:

    python backfill.py                      # last five seasons incl. the current one
    python backfill.py --seasons 2023 2024  # start years

Free-tier budget: one call per league-season, with a pause to stay under 10 requests/min.
Exits 1 if any season failed or contained a club missing from src/teams.py.
"""
import argparse
import os
import sys
import urllib.error
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightly import LEAGUE_MAP, get_conn, upsert_results  # noqa: E402
from src import ingest  # noqa: E402

load_dotenv()

def current_season_start(today=None) -> int:
    """Season start year: Aug 2026 to Jul 2027 is season 2026."""
    import datetime as dt
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def default_seasons(today=None) -> list[int]:
    cur = current_season_start(today)
    return list(range(cur - 4, cur + 1))            # last five seasons including the current one


def backfill_league(conn, key: str, league: str, seasons: list[int]) -> tuple[int, list[str]]:
    total, problems = 0, []
    for season in seasons:
        try:
            res = ingest.fetch_football_data_org(
                key, LEAGUE_MAP[league], f"{season}-07-01", f"{season + 1}-07-15", season=season)
        except urllib.error.HTTPError as e:
            if e.code == 403:   # free tier only serves recent seasons: a plan limit, not a failure
                print(f"  [{league}] {season}-{season + 1}: not available on your football-data.org plan (403), skipped")
                continue
            problems.append(f"{league} {season}-{season + 1}: HTTPError: {e}")
            continue
        except Exception as e:
            problems.append(f"{league} {season}-{season + 1}: {type(e).__name__}: {e}")
            continue
        n = upsert_results(conn, league, res.finished)
        total += n
        print(f"  [{league}] {season}-{season + 1}: {len(res.finished)} matches fetched, {n} new")
        if res.unmapped:
            problems.append(f"{league} {season}-{season + 1}: unmapped clubs {res.unmapped}")
    return total, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=default_seasons())
    args = ap.parse_args(argv)
    key = os.environ["FOOTBALL_DATA_ORG_KEY"]
    leagues = [s.strip() for s in os.environ.get("LEAGUES", "E0").split(",") if s.strip()]
    conn = get_conn()
    problems: list[str] = []
    for league in leagues:
        if league not in LEAGUE_MAP:
            problems.append(f"{league}: no team registry/league map yet")
            continue
        n, probs = backfill_league(conn, key, league, args.seasons)
        print(f"[{league}] backfilled {n} new matches")
        problems += probs
    conn.close()
    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
