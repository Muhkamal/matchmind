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
Premier League, walk-forward (train on the past, predict the next matches), results only: no lineups, injuries or odds feed the model.
Run on `data/history/E0.csv` (2023-24 to 2025-26). 2023-24 is warm-up (44 matches scored) and is not reported.

| Season | Matches | 1X2 accuracy | 1X2 Brier | Base-rate Brier | Uniform Brier | 95% interval, model minus base rate |
|---|---|---|---|---|---|---|
| 2024-25 | 374 | 50.8% | 0.5975 | 0.6572 | 0.6667 | -0.083 to -0.036 |
| 2025-26 | 376 | 47.1% | 0.6182 | 0.6584 | 0.6667 | -0.060 to -0.018 |

- **1X2 has a real but modest edge over league base rates** in both seasons (the interval is entirely below zero).
- **Against the bookmakers it is behind.** Pinnacle closing prices with the margin removed scored 0.5781 vs the model's 0.5975 in 2024-25 (374 matches; accuracy 55.1% vs 50.8%). In 2025-26 Pinnacle prices exist for only 206 of 376 matches; on those the gap was 0.0032 Brier (0.5932 vs 0.5964).
- **No betting edge.** Flat 1-unit bets wherever the model disagreed with Pinnacle closing prices lost money at every threshold tested (ROI -7% to -14%, intervals include zero). Do not use these picks for betting.
- Dixon-Coles alone scored as well as or better than the Elo/Dixon-Coles blend in both seasons (Brier 0.5886 vs 0.5975 and 0.6179 vs 0.6182), and Elo alone was clearly worse. The blend weight is a candidate to revisit with fresh out-of-sample data.
- **Extra markets:** double chance 1X/X2, clean sheets, win to nil, a team scoring 2+, home scoring 3+ and the +/-1.5 handicaps beat the best constant forecast in both seasons. Goal totals (Over 1.5/3.5, goal bands, BTTS with Over, double chance 12, the top-5 score list) did not, and Over 2.5 and BTTS only matched base rates. In short: the model has some team-strength skill and no total-goals skill. The dashboard marks markets without an edge with an asterisk.
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
