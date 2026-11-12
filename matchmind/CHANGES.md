# Changes in 1.3 (from the v1.2 review findings)

## Correctness
- **Team registry** (`src/teams.py`): 43 clubs, exact + normalised aliases, `split_fixture` for multi-word clubs. Unknown club = error for that fixture (skipped and reported), never an "average team". The old model-version registry mismatch is gone.
- **One pick per fixture**: unique partial index + `ON CONFLICT DO NOTHING`; daily refits no longer create duplicate live rows or double-count the record.
- **Kickoff guard** in code (10-minute margin) and in the database (`created_at < kickoff_utc` CHECK).
- **Failures are loud**: `nightly.py` exits 1 on errors, unmapped clubs, skipped fixtures or thin history; settlement runs after ingestion; exceptions roll back.
- **Markets reconciled to 1X2**: published Over 2.5/BTTS/scorelines derive from the same matrix as the 1X2 pick. Clean sheet = opponent scored zero (0-0 included). Markets are now settled and graded (`market_settlements`).
- **/explain defence sign fixed** (higher defence value = concedes more); LLM polish is validated (no new numbers, names kept, length-capped) with template fallback.
- **Calibration view rebuilt** (per market, own probability bins); report footer no longer claims isotonic calibration; ECE only shown with 100+ settled picks; `datetime.date` crash fixed; empty fixture window / empty fit no longer crash.
- **Immutable model version ids** include a hash of the parameters.

## Arena (rebuilt)
- Head-to-head against the model's top pick (win/loss/tie) instead of an invalid one-hot Brier comparison.
- Picks are final, linked to the locked prediction, closed at kickoff (fail-closed when kickoff unknown).
- Privacy: Telegram usernames/ids are no longer public. Players choose a nickname (default `Player####`); the public view exposes nickname + aggregates only, after 10 settled picks.

## Security
- Triggers block UPDATE/DELETE/TRUNCATE on all ledger tables; RLS; `anon`/`authenticated` revoked from private tables.
- Least-privilege roles: owner for nightly, `matchmind_ro` for the API, `matchmind_bot` for the bot.
- API: range-checked parameters, bound SQL parameters, HTML-escaped dashboard, cache headers, pooled connections.
- `.env.template` is placeholders only; `.gitignore` excludes secrets and licensed data.

## Bot
Non-blocking (worker threads), pooled DB, rate limited, cached model, message chunking, global error handler, `/nickname`, exploratory estimates clearly labelled.

## Dashboard redesign
`DESIGN.md` (data-dense dark, L1 motion) plus a rebuilt `api/static/index.html`: tabular mono numerals, probability bars, per-market SVG calibration chart, goal-market and arena panels, labelled experimental markets, HTML-escaped output, mobile-safe tables, reduced-motion support.

## Landing page
`/` is now a landing page (`api/static/landing.html`, L2 motion, CSS + vanilla JS, spec in DESIGN.md section 10); the dashboard moved to `/dashboard`. Set `TELEGRAM_URL` at the top of the landing script to enable the Telegram buttons.

## Repair kit (from real-world setup)
`reset-matchmind.sql` (clean slate, pre-launch only), `check_db.py` (verifies triggers, indexes, privileges, no placeholder predictions), `backfill.py` now includes the current season and treats free-plan 403s as informational, clearer nightly hint for clubs with no history.

## Added
`backtest.py`, `check_teams.py`, `src/botlogic.py`, `src/dbpool.py`, migration 2026-11-06, CI workflows (tests + nightly), 66 tests, `bot-deploy.md`, rewritten deploy docs.

## Upgrade notes
1. Run `migration-2026-11-06.sql` (after 11-04 and 11-05). It refuses to run if the old arena has data or duplicate live picks exist, and tells you what to fix.
2. Set passwords for `matchmind_ro` and `matchmind_bot`, add `API_DATABASE_URL` and `BOT_DATABASE_URL`.
3. Run `python check_teams.py`, then `python backfill.py`.
4. The calibrator pickle and scikit-learn are no longer used.

## Not verified here
- football-data.org club spellings (no network in the build environment): run `check_teams.py`.
- Real-data backtest numbers (football-data.co.uk unreachable): run `backtest.py`.
- Migrations and all SQL behaviour were tested on Postgres 16, not on Supabase itself.

## v1.5
- bot.py: free webhook mode (set WEBHOOK_BASE_URL and WEBHOOK_SECRET); polling remains the default.
- backtest.py: compares the model with bookmaker closing odds (margin removed) on the same matches; comparison only, never a model input.
- README: real backtest numbers replace the unverified ones.

## v1.6-1.7
- Run log: `migration-2026-11-09.sql` adds `job_runs`; nightly writes one row per league; `/api/status` has a `freshness` block and the dashboard explains an empty pick list.
- CORS open for GET; OpenAPI description (UTC, null, empty-result semantics, field meanings) and examples.
- `/api/export/predictions.csv`: every live pick with model version, creation time, result and grade, plus `X-Content-SHA256`.
- Calibration chart has a plain-language summary; dashboard footer links to the CSV and /docs.
- Telegram `/picks` shows Over/Under 2.5 and BTTS Yes/No (experimental).

## v1.8
- `migration-2026-11-10.sql`: the database rejects any new prediction whose probabilities are outside 0-1 or do not sum to 1.
- API calls retry timeouts, connection errors and HTTP 5xx (not just 429), with growing pauses; real errors (401/403/404) still fail at once.
- Backtest: Elo-only and Dixon-Coles-only Brier, and a paired bootstrap 95% interval for the edge over base rates.
- CSV export includes `data_cutoff` (the training data's last match date).
- Dashboard: "What do these numbers mean?" panel.

## v1.9
- General market ledger: `market_predictions` + `market_outcomes` (append-only, locked before kickoff, graded automatically, Brier-scored). New markets only add rows.
- 25 markets on it, all read off the reconciled scoreline matrix: Double Chance (1X, X2, 12), Over 1.5/3.5, clean sheets, exact score (most likely / in top 5), Asian handicap half-lines (+/-1.5), win to nil, total-goals bands, team to score 2+/3+, BTTS with Over/Under 2.5. Python and SQL grading rules are kept equal by a test over every market and 64 scorelines.
- Extra markets are only written for picks inserted in the same run, never retro-fitted to an older locked pick.
- API: `/api/market-picks`, `/api/market-catalog`, `/api/extra-market-record`. Dashboard: "More markets" section with per-market record vs best constant forecast.
- Backtest prints each extra market against the best constant forecast.
- Fixed: bot fixture lookup used the database's local date; now UTC, so it cannot disagree with the UTC match date near midnight.

- Backtest: private flat-stake research test against real closing prices (margin included) with a bootstrap interval. For research only; nothing about stakes or value bets is published.
