"""MatchMind Telegram bot: distribution + arena front-end.

Commands: /start /picks /markets /predict /explain /team /pick /nickname /mypicks /leaderboard /record

Design notes
- Blocking work (database, model fits, OpenAI) runs in worker threads, never on the event loop.
- Connects as the least-privilege ``matchmind_bot`` role through BOT_DATABASE_URL.
- /predict and /explain are exploratory estimates, NOT ledger picks. /picks shows ledger picks.
- Arena picks are final, close at kickoff (fail-closed) and are judged head-to-head vs the
  model's top pick when the nightly job settles them.
- Needs an always-on process (see bot-deploy.md); free web services that sleep will miss messages.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

sys.path.insert(0, str(Path(__file__).parent))
from src import botlogic as bl, explain, markets, models, teams  # noqa: E402
from src.dbpool import close_pools, fetch_df, get_pool  # noqa: E402

load_dotenv()
log = logging.getLogger("matchmind.bot")

LEAGUE = "E0"
CACHE = bl.TTLCache(ttl=6 * 3600)
LIMITER = bl.RateLimiter(max_calls=8, per_seconds=30)
EXPLORATORY = "(exploratory estimate, not a ledger pick; nothing here is betting advice)"
HELP = (
    "⚽ MatchMind: transparent football predictions.\n\n"
    "Every published pick is timestamped, can't be edited, and is graded publicly.\n\n"
    "/picks: upcoming published picks\n"
    "/markets: Over 2.5 and BTTS for upcoming matches\n"
    "/predict Arsenal Chelsea: estimate for any two clubs\n"
    "/explain Arsenal Chelsea: why the model leans that way\n"
    "/team Arsenal: what a club has done lately\n"
    "/pick Arsenal Chelsea H: join the arena (H/D/A, final, closes at kickoff)\n"
    "/nickname Name: choose your public name (3-20 letters, digits, _ or space)\n"
    "/mypicks · /leaderboard · /record\n\n"
    "Probabilities, not promises. Football is noisy; this is not betting advice.")


def db(fn, *args):
    """Run a sync DB function on a pooled connection (call via asyncio.to_thread)."""
    with get_pool("BOT_DATABASE_URL").connection() as conn:
        return fn(conn, *args)


def training(conn):
    from nightly import load_training
    return load_training(conn, LEAGUE)


def get_model_and_history():
    def load():
        hist = db(training)
        return models.EnsembleV1().fit(hist), hist
    return CACHE.get("model", load)


async def reply(update: Update, text: str):
    for part in bl.chunk_text(text):
        await update.message.reply_text(part)


def guarded(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None or update.message is None:
            return
        if not LIMITER.allow(user.id):
            await update.message.reply_text("Slow down a little 🙂 try again in a few seconds.")
            return
        try:
            await fn(update, ctx)
        except teams.UnknownTeamError as e:
            await update.message.reply_text(f"I couldn't read the club names: {e}")
        except Exception:
            log.exception("handler %s failed", fn.__name__)
            await update.message.reply_text("Something went wrong on my side. Please try again later.")
    wrapper.__name__ = fn.__name__
    return wrapper


@guarded
async def cmd_start(update, ctx):
    await reply(update, HELP)


@guarded
async def cmd_picks(update, ctx):
    def q(conn):
        return fetch_df(conn, """
            SELECT kickoff_utc, home_team, away_team, p_home, p_draw, p_away, confidence_band
            FROM predictions WHERE provenance='live' AND kickoff_utc > now()
              AND kickoff_utc < now() + interval '4 days' ORDER BY kickoff_utc LIMIT 30""")
    df = await asyncio.to_thread(db, q)
    if df.empty:
        await reply(update, "No upcoming published picks yet. They appear after the nightly run.")
        return
    lines = ["📋 Published picks (locked in the public ledger):\n"]
    for r in df.itertuples():
        when = r.kickoff_utc.strftime("%a %H:%M UTC")
        lines.append(f"{when}  {teams.pretty_fixture(r.home_team, r.away_team)}\n"
                     f"   H {bl.pct(r.p_home)} · D {bl.pct(r.p_draw)} · A {bl.pct(r.p_away)}  [{r.confidence_band}]")
    await reply(update, "\n".join(lines))


@guarded
async def cmd_markets(update, ctx):
    def q(conn):
        return fetch_df(conn, """
            SELECT kickoff_utc, home_team, away_team, p_over25, p_btts, top_scorelines
            FROM predictions WHERE provenance='live' AND p_over25 IS NOT NULL
              AND kickoff_utc > now() AND kickoff_utc < now() + interval '4 days'
            ORDER BY kickoff_utc LIMIT 30""")
    df = await asyncio.to_thread(db, q)
    if df.empty:
        await reply(update, "No goal-market predictions for upcoming matches yet.")
        return
    lines = ["🎯 Goal markets (same model and same scoreline table as the 1X2 pick, so they can't "
             "contradict it). Experimental: see /record for how they are doing.\n"]
    for r in df.itertuples():
        lines.append(f"{teams.pretty_fixture(r.home_team, r.away_team)}\n"
                     f"   Over 2.5 {bl.pct(r.p_over25)} · BTTS {bl.pct(r.p_btts)}\n"
                     f"   likely scores: {r.top_scorelines}")
    await reply(update, "\n".join(lines))


@guarded
async def cmd_team(update, ctx):
    if not ctx.args:
        await reply(update, "Usage: /team Arsenal")
        return
    slug = teams.resolve(" ".join(ctx.args))
    _, hist = await asyncio.to_thread(get_model_and_history)
    prof = markets.team_profile(hist, slug, last_n=30)
    if not prof.get("n"):
        await reply(update, f"No history for {teams.display(slug)} yet.")
        return
    o, h, a = prof["overall"], prof.get("home", {}), prof.get("away", {})
    await reply(update,
        f"📊 {teams.display(slug)}: last {prof['n']} league matches\n"
        f"Goals: {prof['goals_for']:.2f} for / {prof['goals_against']:.2f} against per match\n"
        f"Over 2.5 {bl.pct(o['over25_rate'])} · BTTS {bl.pct(o['btts_rate'])} · "
        f"Clean sheets {bl.pct(o['cs_rate'])}\n"
        f"Home: {h.get('gf', 0):.1f} for / {h.get('ga', 0):.1f} against\n"
        f"Away: {a.get('gf', 0):.1f} for / {a.get('ga', 0):.1f} against\n"
        f"Form (last 5, most recent first): {prof.get('last5', '-')}")


@guarded
async def cmd_predict(update, ctx):
    home, away = bl.parse_fixture(ctx.args)
    model, _ = await asyncio.to_thread(get_model_and_history)
    full = model.predict_full(home, away)
    p, mk = full["p"], full["markets"]
    await reply(update,
        f"{teams.pretty_fixture(home, away)}\n"
        f"H {bl.pct(p[0])} · D {bl.pct(p[1])} · A {bl.pct(p[2])}\n"
        f"Over 2.5 {bl.pct(mk['p_over25'])} · BTTS {bl.pct(mk['p_btts'])}\n"
        f"Likely scores: {mk['top_scorelines']}\n{EXPLORATORY}")


@guarded
async def cmd_explain(update, ctx):
    home, away = bl.parse_fixture(ctx.args)
    model, hist = await asyncio.to_thread(get_model_and_history)

    def build():
        full = model.predict_full(home, away)
        expl = explain.explain_match(model, home, away, full,
                                     markets.team_profile(hist, home, last_n=30),
                                     markets.team_profile(hist, away, last_n=30))
        return explain.polish(expl)          # may call OpenAI: blocking, so it runs in a thread
    text = await asyncio.to_thread(build)
    await reply(update, f"🧠 {text}\n{EXPLORATORY}")


@guarded
async def cmd_pick(update, ctx):
    try:
        home, away, pick = bl.parse_pick(ctx.args)
    except ValueError as e:
        await reply(update, str(e))
        return
    status, _ = await asyncio.to_thread(db, bl.submit_pick, update.effective_user.id, home, away, pick)
    fx = teams.pretty_fixture(home, away)
    await reply(update, {
        "ok": f"✅ Locked: {fx} → {pick}. Picks are final. Settled head-to-head vs the model's top "
              f"pick after full time. See /mypicks.",
        "no_fixture": "No published upcoming pick for that fixture. Check /picks for what is open.",
        "closed": "Picks for that match are closed (kickoff has passed or is unknown).",
        "duplicate": "You already made a pick for that match. Arena picks are final.",
    }[status])


@guarded
async def cmd_nickname(update, ctx):
    name = " ".join(ctx.args).strip()
    if not name:
        _, current = await asyncio.to_thread(db, bl.get_or_create_user, update.effective_user.id)
        await reply(update, f"Your public name is {current}. Change it with /nickname NewName "
                            "(3-20 letters, digits, underscore or space). Never use your real name.")
        return
    res = await asyncio.to_thread(db, bl.set_nickname, update.effective_user.id, name)
    await reply(update, {"ok": f"Done. You are now {name} on the leaderboard.",
                         "invalid": "Use 3-20 characters: letters, digits, underscore or space.",
                         "taken": "That name is taken. Try another."}[res])


@guarded
async def cmd_mypicks(update, ctx):
    rows = await asyncio.to_thread(db, bl.my_picks, update.effective_user.id)
    await reply(update, bl.render_my_picks(rows))


@guarded
async def cmd_leaderboard(update, ctx):
    rows = await asyncio.to_thread(db, bl.leaderboard_rows)
    await reply(update, bl.render_leaderboard(rows))


@guarded
async def cmd_record(update, ctx):
    def q(conn):
        return fetch_df(conn, "SELECT * FROM v_model_record ORDER BY league, provenance")
    df = await asyncio.to_thread(db, q)
    if df.empty:
        await reply(update, "No graded picks yet.")
        return
    lines = ["📜 Public record: every published pick, graded (3-way Brier; a coin flip scores 0.667):"]
    for r in df.itertuples():
        lines.append(f"{r.league} [{r.provenance}] n={r.n} · accuracy {bl.pct(r.accuracy)} · "
                     f"Brier {r.brier:.3f}")
    lines.append("Small samples are noisy. Judge the model over hundreds of picks, not a few.")
    await reply(update, "\n".join(lines))


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("unhandled bot error", exc_info=ctx.error)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    for name, fn in [("start", cmd_start), ("help", cmd_start), ("picks", cmd_picks),
                     ("markets", cmd_markets), ("team", cmd_team), ("explain", cmd_explain),
                     ("predict", cmd_predict), ("pick", cmd_pick), ("nickname", cmd_nickname),
                     ("mypicks", cmd_mypicks), ("leaderboard", cmd_leaderboard),
                     ("record", cmd_record)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_error_handler(on_error)
    try:
        app.run_polling()
    finally:
        close_pools()


if __name__ == "__main__":
    main()
