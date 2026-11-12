# MatchMind v1.3

Transparent football predictions: an **Elo + Dixon-Coles ensemble**, an **append-only prediction ledger enforced by the database**,
automatic grading, a read-only public API and dashboard, and a Telegram bot with an "arena" where people try to beat the model.

Scope today: **English Premier League (E0)**. Other leagues need their clubs added to `src/teams.py` first; the pipeline refuses them with a clear error rather than guessing.

## What is promised, and what is not
- Every published pick is stored with its probabilities **before kickoff**. The database rejects edits, deletions, a second pick for the same fixture, and any live pick created at/after kickoff.
- Picks are graded automatically from real results. The public record includes the misses.
- Over 2.5 / BTTS / likely scorelines come from the same scoreline table as the 1X2 pick (reconciled to it), so they cannot contradict each other. They are **experimental** and are graded against the "always predict the base rate" baseline.
- No probability recalibration is applied (an isotonic calibrator failed honest out-of-sample testing and was removed).
- The model uses results only: no lineups, injuries or odds. It is **not** expected to beat bookmaker closing prices, and nothing here is betting advice.

## Evaluation protocol
Tune on seasons up to 2022-23, select on 2023-24, report on later held-out seasons; never reuse a season for both.
`python backtest.py` runs the production model walk-forward (refit every 30 days, no look-ahead) per season and prints 1X2 accuracy / Brier / log loss against uniform and base-rate references, plus Over 2.5 and BTTS against their base-rate baselines.

## Backtest results (re-run on real data, Nov 2026)
Premier League, walk-forward, results only (no odds, lineups or injuries). Run on `data/history/E0.csv` (2023-24 to 2025-26, 1,140 matches).

| Season | Matches scored | 1X2 accuracy | 1X2 Brier | Base-rate Brier | Uniform Brier | Log loss |
|---|---|---|---|---|---|---|
| 2024-25 | 374 | 50.8% | 0.5975 | 0.6572 | 0.6667 | 1.0006 |
| 2025-26 | 376 | 47.1% | 0.6182 | 0.6584 | 0.6667 | 1.0324 |

- 2023-24 is warm-up (only 44 matches scored) and is not reported.
- 1X2 beats the league base rate in both seasons, a modest edge.
- Over 2.5 and BTTS do **not** reliably beat their base rates (Over 2.5 fails in 2025-26; BTTS fails in both seasons), so they are labelled experimental.
- A single season has an accuracy standard error of about 5 points, and the edge was smaller in 2025-26; do not over-read either season.
- Bookmaker closing prices usually score better than a results-only model. This project's claim is an auditable record, not beating the market.
- A backtest is not a forecast. The live ledger is the real record.

## Layout
```
schema.sql, migration-2026-11-04/05/06.sql   run in this order (see deploy.md)
nightly.py      ingest -> refit -> predict -> append -> settle (exit 1 if anything needs attention)
backfill.py     one-off multi-season results pull
settle.py       settle + weekly reports
backtest.py     walk-forward evaluation
check_teams.py  verify the club registry against football-data.org
check_db.py     verify the database really has the ledger guarantees
reset-matchmind.sql  pre-launch clean slate (destructive)
bot.py          Telegram bot (needs an always-on process: bot-deploy.md)
api/            FastAPI read-only API + dashboard (api-deploy.md)
src/            teams, models, markets, explain, arena, botlogic, ingest, sql, report, ...
tests/          66 tests; SQL/API/bot tests run against a real Postgres
```

## Develop
```bash
pip install -r requirements-dev.txt
python -m pyflakes . && python -m pytest -q
```
SQL tests use an embedded Postgres (`pixeltable-pgserver`) or `TEST_DATABASE_URL` (a throwaway database: the tests drop the `public` schema). They skip if neither is available.

## Security model
| Component | Database role | Can do |
|---|---|---|
| nightly / settle / backfill | owner (`DATABASE_URL`) | write the ledger |
| API | `matchmind_ro` | read public views only |
| bot | `matchmind_bot` | read ledger, write arena tables only |
| browser / anon | Supabase `anon` | read public views only; arena tables private |

Never commit `.env`. If real keys were ever pasted anywhere (chat, issue, screenshot), **rotate them**.

See `CHANGES.md` for what changed in 1.3 and `deploy.md` to run it.
