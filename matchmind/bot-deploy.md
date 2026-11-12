# MatchMind Telegram bot: deploy

The bot is a **long-running process** (it polls Telegram). Free web services that sleep when idle will miss messages,
so use something always on: a small VPS, a Raspberry Pi, Fly.io, Railway, or a Render *background worker* (paid).

## Free option: webhook on a second Render free web service
No paid plan needed. Telegram pushes each message to your service; if it is asleep, Telegram retries and the
first reply after idle takes roughly 30-60 seconds. Replies are normal speed after that.
1. Render, New, Web Service, same repo, Root Directory `matchmind`, Build `pip install -r requirements.txt`, Start `python bot.py`.
2. Environment variables: `TELEGRAM_BOT_TOKEN`, `BOT_DATABASE_URL`, `WEBHOOK_SECRET` (random letters and digits only, 30+ characters), `WEBHOOK_BASE_URL` (the service's own `https://....onrender.com` address, no trailing slash; add it after the first deploy gives you the address, then redeploy).
3. Leave the Health Check Path empty for this service.
4. Free instance hours are shared (about 750 a month). Two services that both sleep are fine; do not keep both awake 24/7.
Without `WEBHOOK_BASE_URL` the bot falls back to polling (needs an always-on host).

## Setup
1. Talk to @BotFather, `/newbot`, copy the token.
2. Environment (never commit): `TELEGRAM_BOT_TOKEN`, `BOT_DATABASE_URL` (role `matchmind_bot`; can read predictions/results and write only arena tables), optional `OPENAI_API_KEY`.
3. Training history for `/predict`, `/explain` and `/team`: the bot loads results from the database (and `data/history/E0.csv` if it exists on that machine).
4. Run: `python bot.py`. Use systemd, Docker or your host's process manager to restart it on failure.

## Behaviour you should know
- `/picks` and `/markets` show ledger picks. `/predict` and `/explain` are labelled **exploratory estimates**: they are not ledger picks and are not graded.
- `/pick Arsenal Chelsea H`: arena picks are final, close at kickoff (and when the kickoff time is unknown), and are linked to the locked ledger prediction.
- Arena scoring is head-to-head against the model's top pick: win (you right, model wrong), loss, or tie (both right or both wrong). Nightly settlement does the grading.
- `/nickname` sets the public name (3-20 letters, digits, `_`, space). Everyone starts as `Player####`. Real Telegram names/ids are never shown.
- Per-user rate limit (8 commands / 30 s). Slow work runs off the event loop.
