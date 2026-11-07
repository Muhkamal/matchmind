# MatchMind API and dashboard: run and deploy

Read-only service. It connects as the `matchmind_ro` database role (`API_DATABASE_URL`), which can read
the public ledger views and nothing else: no writes, no private arena tables, no Telegram ids.
No other secret is needed.

## Run locally
```bash
cd matchmind
pip install -r requirements.txt
export API_DATABASE_URL='postgresql://matchmind_ro.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres'
uvicorn api.main:app --host 0.0.0.0 --port 8000
```
Landing page: http://localhost:8000 · dashboard: http://localhost:8000/dashboard · interactive docs: http://localhost:8000/docs · health: `/healthz`.

## Deploy (Render, free web service)
1. Private GitHub repo containing the project.
2. Render > New Web Service. Build: `pip install -r requirements.txt`. Start: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`. If the repo root is above `matchmind/`, set Root Directory to `matchmind`.
3. Environment variable: `API_DATABASE_URL` only. Do **not** give the API the owner `DATABASE_URL`.
4. Health check path: `/healthz`.
Free services sleep when idle, so the first request after a while is slow. That is fine for a read-only dashboard.

## Endpoints
`/api/status` · `/api/upcoming?days=14` · `/api/recent?limit=25` · `/api/record` · `/api/calibration?league=E0` ·
`/api/market-record` · `/api/markets` · `/api/worst-misses` · `/api/leaderboard` · `/api/team/{league}/{team}`.
Numeric parameters are range-checked (422 on bad input) and responses carry `Cache-Control: public, max-age=60`.
The leaderboard shows nicknames only, and only players with 10+ settled picks.
