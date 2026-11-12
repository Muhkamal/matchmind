"""Bot logic that does not need Telegram: parsing, rate limiting, text, and arena DB operations.

Everything here is synchronous and testable; ``bot.py`` calls it through ``asyncio.to_thread``
so a slow query or a model refit never blocks the Telegram event loop.
"""
from __future__ import annotations

import random
import re
import threading
import time
from collections import defaultdict, deque

import psycopg

from . import teams

NICK_RE = re.compile(r"^[A-Za-z0-9_ ]{3,20}$")
PICKS = ("H", "D", "A")
TG_LIMIT = 4000   # Telegram's hard limit is 4096


# ---------------------------------------------------------------- parsing

def parse_fixture(args: list[str]) -> tuple[str, str]:
    """['man','city','aston','villa'] -> ('man-city', 'aston-villa'). Raises UnknownTeamError."""
    return teams.split_fixture(args)


def parse_pick(args: list[str]) -> tuple[str, str, str]:
    """['Arsenal','Man','City','H'] -> (home, away, 'H'). Raises ValueError/UnknownTeamError."""
    if len(args) < 3:
        raise ValueError("Usage: /pick Arsenal Chelsea H   (H = home win, D = draw, A = away win)")
    pick = args[-1].upper()
    if pick not in PICKS:
        raise ValueError("The last word must be H, D or A (home win / draw / away win).")
    home, away = teams.split_fixture(args[:-1])
    return home, away, pick


def valid_nickname(name: str) -> bool:
    return bool(NICK_RE.match(name))


def default_nickname(rng: random.Random | None = None) -> str:
    return f"Player{(rng or random).randint(1000, 9999)}"


def chunk_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Split on line boundaries so no message exceeds Telegram's limit."""
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                      # pathological single line
            if cur:
                out.append(cur); cur = ""
            out.append(line[:limit]); line = line[limit:]
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out or [""]


def pct(x: float) -> str:
    return f"{x:.0%}"


# ---------------------------------------------------------------- limits & cache

class RateLimiter:
    """At most ``max_calls`` per ``per_seconds`` for each user id (in memory)."""

    def __init__(self, max_calls: int = 8, per_seconds: float = 30.0, clock=time.monotonic):
        self.max_calls, self.per, self.clock = max_calls, per_seconds, clock
        self._calls: dict[int, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, user_id: int) -> bool:
        now = self.clock()
        with self._lock:
            q = self._calls[user_id]
            while q and now - q[0] > self.per:
                q.popleft()
            if len(q) >= self.max_calls:
                return False
            q.append(now)
            return True


class TTLCache:
    """Tiny thread-safe cache; the loader runs under the lock so a refit happens only once."""

    def __init__(self, ttl: float, clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self._data: dict = {}
        self._lock = threading.Lock()

    def get(self, key, loader):
        with self._lock:
            hit = self._data.get(key)
            if hit and self.clock() - hit[0] < self.ttl:
                return hit[1]
            value = loader()
            self._data[key] = (self.clock(), value)
            return value


# ---------------------------------------------------------------- arena DB operations

def get_or_create_user(conn, telegram_id: int, rng: random.Random | None = None) -> tuple[int, str]:
    """Return (user_id, display_name); new users get an anonymous 'Player####' name."""
    row = conn.execute("SELECT user_id, display_name FROM arena_users WHERE telegram_id=%s",
                       (telegram_id,)).fetchone()
    if row:
        return row[0], row[1]
    for _ in range(20):
        name = default_nickname(rng)
        try:
            with conn.transaction():
                row = conn.execute(
                    "INSERT INTO arena_users (telegram_id, display_name) VALUES (%s,%s) "
                    "ON CONFLICT (telegram_id) DO NOTHING RETURNING user_id, display_name",
                    (telegram_id, name)).fetchone()
        except psycopg.errors.UniqueViolation:
            continue                                  # name clash: try another
        if row:
            return row[0], row[1]
        row = conn.execute("SELECT user_id, display_name FROM arena_users WHERE telegram_id=%s",
                           (telegram_id,)).fetchone()
        if row:
            return row[0], row[1]
    raise RuntimeError("could not allocate a display name")


def set_nickname(conn, telegram_id: int, name: str) -> str:
    """Returns 'ok' | 'invalid' | 'taken'."""
    if not valid_nickname(name):
        return "invalid"
    get_or_create_user(conn, telegram_id)
    try:
        with conn.transaction():
            conn.execute("UPDATE arena_users SET display_name=%s WHERE telegram_id=%s",
                         (name, telegram_id))
    except psycopg.errors.UniqueViolation:
        return "taken"
    return "ok"


def find_live_fixture(conn, home: str, away: str):
    """The locked live pick for the next fixture of this pairing, or None.
    Returns (prediction_id, league, match_date, kickoff_utc)."""
    return conn.execute(
        "SELECT prediction_id, league, match_date, kickoff_utc FROM predictions "
        "WHERE provenance='live' AND home_team=%s AND away_team=%s AND match_date >= (now() AT TIME ZONE 'UTC')::date "
        "ORDER BY match_date LIMIT 1", (home, away)).fetchone()


def submit_pick(conn, telegram_id: int, home: str, away: str, pick: str) -> tuple[str, str]:
    """Record a FINAL arena pick. Returns (status, message-key):
    'ok' | 'no_fixture' | 'closed' | 'duplicate'. Fails closed when kickoff is unknown."""
    fx = find_live_fixture(conn, home, away)
    if not fx:
        return "no_fixture", ""
    pid, league, mdate, kickoff = fx
    now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    if kickoff is None or kickoff <= now:             # unknown kickoff == closed
        return "closed", ""
    user_id, _ = get_or_create_user(conn, telegram_id)
    row = conn.execute(
        "INSERT INTO arena_predictions (user_id, model_prediction_id, league, match_date, "
        " home_team, away_team, pick, kickoff_utc) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (user_id, model_prediction_id) DO NOTHING RETURNING arena_prediction_id",
        (user_id, pid, league, mdate, home, away, pick, kickoff)).fetchone()
    return ("ok", "") if row else ("duplicate", "")


def my_picks(conn, telegram_id: int, limit: int = 15):
    return conn.execute(
        "SELECT ap.home_team, ap.away_team, ap.pick, ap.match_date, s.outcome, s.user_hit "
        "FROM arena_users u JOIN arena_predictions ap USING (user_id) "
        "LEFT JOIN arena_settlements s USING (arena_prediction_id) "
        "WHERE u.telegram_id=%s ORDER BY ap.match_date DESC, ap.arena_prediction_id DESC LIMIT %s",
        (telegram_id, limit)).fetchall()


def render_my_picks(rows) -> str:
    if not rows:
        return "You have no arena picks yet. Try /pick Arsenal Chelsea H"
    icon = {"win": "🟢 beat the model", "loss": "🔴 model won", "tie": "⚪ tie with the model"}
    lines = ["🎽 Your arena picks (head-to-head vs the model's top pick):"]
    for home, away, pick, _, outcome, _hit in rows:
        status = icon[outcome] if outcome else "⏳ pending"
        lines.append(f"{teams.pretty_fixture(home, away)} → {pick}  {status}")
    return "\n".join(lines)


def leaderboard_rows(conn, limit: int = 10):
    return conn.execute(
        "SELECT display_name, n, wins, losses, ties, hit_rate, model_hit_rate "
        "FROM v_leaderboard ORDER BY net_rate DESC, n DESC LIMIT %s", (limit,)).fetchall()


def render_leaderboard(rows, min_settled: int = 10) -> str:
    if not rows:
        return (f"🏆 No one is on the leaderboard yet. You appear after {min_settled} settled "
                "picks. Each pick is judged against the model's top pick (win / loss / tie).")
    lines = [f"🏆 Arena leaderboard — head-to-head vs the model (min {min_settled} settled picks)"]
    for i, (name, n, w, l, t, hit, mhit) in enumerate(rows, 1):
        lines.append(f"{i}. {name} — {w}W {l}L {t}T over {n} · hit {pct(hit)} vs model {pct(mhit)}")
    return "\n".join(lines)
