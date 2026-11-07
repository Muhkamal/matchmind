"""Check that every club football-data.org lists is in the team registry.

    python check_teams.py              # current season
    python check_teams.py --season 2025

Run this after each promotion/relegation round (and before enabling a new league).
Exits 1 and prints the exact spellings to add to src/teams.py if anything is unmapped.
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightly import LEAGUE_MAP  # noqa: E402
from src import ingest, teams  # noqa: E402

load_dotenv()


def unmapped_names(names: list[str]) -> list[str]:
    bad = []
    for n in names:
        try:
            teams.canon(n, "fdo")
        except teams.UnknownTeamError:
            bad.append(n)
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None, help="start year, e.g. 2026")
    args = ap.parse_args(argv)
    key = os.environ["FOOTBALL_DATA_ORG_KEY"]
    status = 0
    for league, code in LEAGUE_MAP.items():
        names = ingest.fetch_fdo_team_names(key, code, args.season)
        bad = unmapped_names(names)
        print(f"[{league}/{code}] {len(names)} clubs from football-data.org, {len(bad)} unmapped")
        for n in bad:
            print(f"   UNMAPPED: {n!r}")
        status |= bool(bad)
    return status


if __name__ == "__main__":
    sys.exit(main())
