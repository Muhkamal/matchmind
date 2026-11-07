# MatchMind Telegram bot: deploy

The bot is a **long-running process** (it polls Telegram). Free web services that sleep when idle will miss messages,
so use something always on: a small VPS, a Raspberry Pi, Fly.io, Railway, or a Render *background worker* (paid).

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
