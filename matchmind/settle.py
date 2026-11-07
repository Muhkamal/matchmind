"""Settlement + weekly report runner. Idempotent: safe to run any number of times.

    python settle.py          # settle everything finished, write this week's reports
Exit code 0 = ok, 1 = failure.
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightly import get_conn, settle  # noqa: E402
from src import report  # noqa: E402
from src.dbpool import fetch_df  # noqa: E402

load_dotenv()


def build_frames(conn):
    return (fetch_df(conn, "SELECT * FROM predictions"), fetch_df(conn, "SELECT * FROM settlements"),
            fetch_df(conn, "SELECT * FROM results"))


def main() -> int:
    conn = get_conn()
    try:
        counts = settle(conn)
        predictions, settlements, results = build_frames(conn)
    finally:
        conn.close()
    monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    for league in sorted(predictions.league.unique()) if len(predictions) else []:
        md = report.weekly_report(predictions, settlements, results, league, pd.Timestamp(monday))
        path = Path(__file__).parent / f"weekly-report-{league}-{monday}.md"
        path.write_text(md)
        print(f"report written: {path}")
    print(f"settled: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
