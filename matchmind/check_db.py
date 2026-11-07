"""Verify that the database really has MatchMind's guarantees. Run after migrating, and any time
something looks odd:

    python check_db.py            # uses DATABASE_URL (owner role)

Exit 0 = every check passed. Exit 1 = at least one FAIL (it says how to fix it).
Why this exists: if tables are patched by hand to silence one error after another, the pipeline
can "work" while the append-only ledger, privacy rules and least-privilege roles are missing.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

LEDGER_TABLES = ["predictions", "settlements", "results", "market_settlements"]
ARENA_TABLES = ["arena_users", "arena_predictions", "arena_settlements"]
NEEDED_COLUMNS = {
    "predictions": ["kickoff_utc", "p_over25", "p_btts", "top_scorelines", "provenance", "created_at"],
    "market_settlements": ["prediction_id", "over25_actual", "btts_actual", "brier_over25", "brier_btts"],
    "arena_users": ["user_id", "telegram_id", "display_name"],
    "arena_predictions": ["user_id", "model_prediction_id", "pick", "kickoff_utc", "created_at"],
    "arena_settlements": ["arena_prediction_id", "user_hit", "model_hit", "outcome"],
}
VIEWS = ["v_model_record", "v_model_record_by_version", "v_calibration", "v_market_record",
         "v_leaderboard", "v_team_profiles"]
MIGRATE_HINT = ("Fix: run schema.sql, migration-2026-11-04/05/06.sql in order "
                "(if tables were patched by hand, run reset-matchmind.sql first; see deploy.md).")


def run_checks(conn) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = ""):
        out.append((name, bool(ok), detail))

    def one(sql, *p):
        return conn.execute(sql, p).fetchone()[0]

    tables = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    for t in ["model_versions", *LEDGER_TABLES, *ARENA_TABLES]:
        check(f"table {t} exists", t in tables, MIGRATE_HINT)
    cols: dict[str, set] = {}
    for t, c in conn.execute("SELECT table_name, column_name FROM information_schema.columns "
                             "WHERE table_schema='public'"):
        cols.setdefault(t, set()).add(c)
    for t, need in NEEDED_COLUMNS.items():
        if t in tables:
            missing = [c for c in need if c not in cols.get(t, set())]
            check(f"{t} has the expected columns", not missing, f"missing {missing}. {MIGRATE_HINT}")
    views = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.views WHERE table_schema='public'")}
    for v in VIEWS:
        check(f"view {v} exists", v in views, MIGRATE_HINT)

    # --- ledger guarantees
    trig = {(r[0], r[1]) for r in conn.execute(
        "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "WHERE NOT t.tgisinternal")}
    for t in LEDGER_TABLES:
        if t in tables:
            check(f"{t}: UPDATE/DELETE blocked", (t, f"{t}_immutable") in trig, MIGRATE_HINT)
            check(f"{t}: TRUNCATE blocked", (t, f"{t}_no_truncate") in trig, MIGRATE_HINT)
    if "predictions" in tables:
        check("one live pick per fixture (unique index)",
              one("SELECT count(*) FROM pg_indexes WHERE indexname='predictions_one_live_per_fixture'") == 1,
              MIGRATE_HINT)
        check("live pick must precede kickoff (CHECK)",
              one("SELECT count(*) FROM pg_constraint WHERE conname='predictions_pre_kickoff'") == 1, MIGRATE_HINT)
        n_null = one("SELECT count(*) FROM predictions WHERE provenance='live' AND kickoff_utc IS NULL")
        check("every live pick has a kickoff time", n_null == 0, f"{n_null} live rows have no kickoff_utc")
        # Rows the fallback hack produced: constant neutral numbers instead of model output.
        n_fake = one("SELECT count(*) FROM predictions WHERE provenance='live' AND "
                     "abs(p_home-0.34)<1e-6 AND abs(p_draw-0.28)<1e-6 AND abs(p_away-0.38)<1e-6")
        check("no placeholder 'fallback' predictions in the ledger", n_fake == 0,
              f"{n_fake} live row(s) are the constant 0.34/0.28/0.38 placeholder, not model output. "
              "Use reset-matchmind.sql (pre-launch) and re-run the pipeline without the fallback edit.")
    for t in ARENA_TABLES + ["predictions", "results", "settlements", "market_settlements", "model_versions"]:
        if t in tables:
            check(f"RLS enabled on {t}", one("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass", f"public.{t}"),
                  MIGRATE_HINT)

    # --- least privilege
    roles = {r[0] for r in conn.execute("SELECT rolname FROM pg_roles")}
    for role in ("matchmind_ro", "matchmind_bot"):
        check(f"role {role} exists", role in roles, "run migration-2026-11-06.sql")
    def priv(role, table, p):
        return table in tables and role in roles and one("SELECT has_table_privilege(%s, %s, %s)", role, f"public.{table}", p)
    for t in ["predictions", "results", "settlements", "market_settlements", "model_versions"]:
        check(f"matchmind_bot cannot write {t}", not any(priv("matchmind_bot", t, p) for p in ("INSERT", "UPDATE", "DELETE")),
              "the bot role was granted write access to ledger tables (revoke it, or use reset-matchmind.sql)")
        check(f"matchmind_ro cannot write {t}", not any(priv("matchmind_ro", t, p) for p in ("INSERT", "UPDATE", "DELETE")),
              "the read-only role has write access")
    check("matchmind_ro cannot read private arena_users", not priv("matchmind_ro", "arena_users", "SELECT"),
          "telegram ids would be readable through the public API role")
    check("matchmind_bot can write arena_predictions", priv("matchmind_bot", "arena_predictions", "INSERT"), MIGRATE_HINT)
    n_def = one("SELECT count(*) FROM pg_default_acl d, aclexplode(d.defaclacl) a "
                "JOIN pg_roles r ON r.oid = a.grantee WHERE r.rolname IN ('matchmind_ro','matchmind_bot') "
                "AND d.defaclobjtype = 'r'")
    check("no default grants on future tables", n_def == 0,
          "ALTER DEFAULT PRIVILEGES ... GRANT was used; run reset-matchmind.sql then the migrations")
    return out


def main() -> int:
    import psycopg
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set (use the owner connection string in .env)")
        return 1
    with psycopg.connect(url) as conn:
        results = run_checks(conn)
    bad = 0
    for name, ok, detail in results:
        print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"\n        -> {detail}"))
        bad += not ok
    print(f"\n{len(results) - bad}/{len(results)} checks passed" + ("" if not bad else f"; {bad} FAILED"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
