# MatchMind deployment guide (free tier, about 45 minutes)

Order matters. Do each step once.

## 1. Supabase database
1. supabase.com > New project. Save the database password.
2. SQL Editor: run these files **in this order**, one at a time:
   `schema.sql`, `migration-2026-11-04.sql`, `migration-2026-11-05.sql`, `migration-2026-11-06.sql`, `migration-2026-11-09.sql` (run log for the freshness line on the dashboard), `migration-2026-11-10.sql` (probability sanity checks), `migration-2026-11-11.sql` (extra-markets ledger: double chance, goal lines, clean sheets, scores).
   - `migration-2026-11-06.sql` refuses to run if the old arena already holds data or if there are duplicate live picks for a fixture. If it stops, read the message: it tells you what to fix.
3. Set passwords for the two limited roles the migration created (pick long random ones, run in the SQL editor):
   ```sql
   ALTER ROLE matchmind_ro  PASSWORD '...';
   ALTER ROLE matchmind_bot PASSWORD '...';
   ```
4. Project Settings > Database > Connection string > **Session pooler**. Use the pooler host (`…pooler.supabase.com`), not `db.<ref>.supabase.co`: that host is IPv6-only and GitHub Actions cannot reach it. The pooler username looks like `postgres.<project-ref>`; the limited roles are `matchmind_ro.<project-ref>` and `matchmind_bot.<project-ref>`.

## 2. Local setup
```bash
cd matchmind
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.template .env                                  # fill in; never commit it
python check_teams.py                                  # compares our club registry with football-data.org
python -m pytest                                       # needs requirements-dev.txt
```
`check_teams.py` exits 1 if football-data.org spells a club we do not know. Fix `src/teams.py` first. (The football-data.org spellings in the registry were written without network access and are unverified until you run this.)

## 3. Training history
The model needs several seasons of results.
```bash
python backfill.py                  # football-data.org -> results table (free tier, rate limited)
```
If the free tier returns too little history, `nightly.py` stops with the message "only N training matches (< 300)" and publishes nothing. It never guesses.
For development and backtests you may also place football-data.co.uk CSVs in `data/history/E0.csv` (columns `Date,HomeTeam,AwayTeam,FTHG,FTAG`). **That data is licensed for non-commercial use only and must never go into the database or the repository** (`data/` is git-ignored).

## 4. First run
```bash
python nightly.py     # exit code 0 = clean. 1 = something needs your attention (it tells you what)
python settle.py      # settles finished matches and writes weekly-report-*.md
```
Check `predictions` in Supabase: live rows with `kickoff_utc` set; `model_versions` has an id like `v1.2-ens-E0-2026-11-03-ab12cd`.

## 5. Schedule it
GitHub Actions: `.github/workflows/nightly.yml` is included (22:55 UTC daily, also runnable by hand). Add repository secrets `DATABASE_URL` and `FOOTBALL_DATA_ORG_KEY`; keep the repo private. A **red run is meaningful**: an error, an unmapped club, a skipped fixture or too little history. In Actions there is no CSV, so training comes from the database (that is why step 3 matters).
`.github/workflows/tests.yml` runs the test suite on every push. If your repository root is the `matchmind/` folder itself, move `.github/` into it and delete the `working-directory: matchmind` lines.

## 6. Verify the database
```bash
python check_db.py     # every line must say PASS
```
It checks the append-only triggers, the one-pick-per-fixture index, the kickoff CHECK, private arena tables, role privileges, and that no placeholder predictions are in the ledger. Run it after every migration and before you go public.

## 7. Then deploy the API and the bot
`api-deploy.md` and `bot-deploy.md`.

## What the system guarantees (and what it does not)
- One live pick per fixture, the first one wins (unique index). The database refuses a live pick created at or after kickoff, and refuses UPDATE/DELETE/TRUNCATE on the ledger tables.
- The nightly job only publishes fixtures at least 10 minutes from kickoff.
- A club the registry does not know is reported and its fixture skipped: it is never predicted as an average team.
- It does **not** know about injuries, lineups or odds. Do not present it as beating the market.
- The owner credentials (`DATABASE_URL`) live only in the nightly job. The API and the bot use the limited roles.

## Budget
football-data.org: a few calls per night out of 10 per minute. Supabase free: 500 MB, years of picks fit in a few MB. GitHub Actions: about 2 minutes per run.

## Troubleshooting
**"relation ... does not exist" / "column ... does not exist" from `nightly.py`.** The migrations have not all been run. Do NOT add tables or columns one at a time by hand (each fix exposes the next, and hand-made tables lack the triggers, indexes and privacy rules). Instead:
- Nothing public yet (pre-launch): run `reset-matchmind.sql`, then `schema.sql`, `migration-2026-11-04.sql`, `-05`, `-06` in order, set the two role passwords, `python backfill.py`, `python nightly.py`, `python check_db.py`.
- Already live: do not reset. Ask for help with a targeted migration instead.

**A club is "skipped: not in the training data" (e.g. a promoted club).** The club's name is fine; the model has no matches for it. Run `python backfill.py --seasons <current season start year>` so this season's results are in the database. Never replace the skip with placeholder probabilities: a made-up 0.34/0.28/0.38 in the ledger is fake model output and `check_db.py` flags it.

**`HTTP Error 403` in backfill.** The free football-data.org plan only serves recent seasons. Backfill reports those as "not available on your plan" and carries on. For deeper history use football-data.co.uk CSVs in `data/history/E0.csv` (local, never in the database).

**`numpy.dtype size changed`.** Mixed numpy/pandas/scipy installs. Use a clean virtual environment: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

**Connection strings.** Copy the string from Supabase (Connect > Session pooler) exactly, including the `aws-N-<region>.pooler.supabase.com` host it shows you, remove the `[ ]` around `[YOUR-PASSWORD]`, and keep the username as `<role>.<project-ref>`. URL-encode special characters in passwords (`@` is `%40`).
