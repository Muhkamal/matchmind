"""check_db.py must pass on a correctly migrated database and catch the hand-patched state
(missing triggers, bot with ledger write access, placeholder predictions), and the documented
repair path (reset-matchmind.sql + migrations) must turn that state into a passing one."""

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

import check_db
from tests.conftest import MIGRATIONS, ROOT

HAND_PATCHED = """
GRANT USAGE ON SCHEMA public TO matchmind_ro, matchmind_bot;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO matchmind_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO matchmind_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE predictions, results, settlements TO matchmind_bot;
CREATE TABLE market_settlements (settlement_id BIGSERIAL PRIMARY KEY,
  prediction_id BIGINT NOT NULL REFERENCES predictions(prediction_id), over25_actual BOOLEAN,
  btts_actual BOOLEAN, brier_over25 REAL, brier_btts REAL, settled_at TIMESTAMPTZ DEFAULT now(), UNIQUE (prediction_id));
CREATE TABLE arena_users (user_id UUID PRIMARY KEY, telegram_id BIGINT);
CREATE TABLE arena_predictions (arena_prediction_id BIGSERIAL PRIMARY KEY, prediction_id BIGINT, user_id UUID, pick CHAR(1));
CREATE TABLE arena_settlements (arena_settlement_id BIGSERIAL PRIMARY KEY, arena_prediction_id BIGINT UNIQUE, user_hit BOOLEAN);
ALTER TABLE predictions ADD COLUMN p_over25 REAL, ADD COLUMN p_btts REAL, ADD COLUMN kickoff_utc TIMESTAMPTZ,
  ADD COLUMN top_scorelines TEXT;
INSERT INTO model_versions VALUES ('v-x','E0','2026-05-24','{}');
INSERT INTO predictions (model_version, league, match_date, home_team, away_team, p_home_raw, p_draw_raw, p_away_raw,
  p_home, p_draw, p_away, confidence_band, kickoff_utc, p_over25, p_btts, top_scorelines)
VALUES ('v-x','E0','2030-01-01','arsenal','hull-city',.34,.28,.38,.34,.28,.38,'low','2030-01-01 15:00+00',.5,.5,'1-1, 1-2, 2-1');
"""


@pytest.fixture()
def broken_uri(pg_uri):
    base = make_conninfo(pg_uri)
    with psycopg.connect(pg_uri, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS mm_broken WITH (FORCE)")
        c.execute("CREATE DATABASE mm_broken")
    uri = make_conninfo(base, dbname="mm_broken")
    with psycopg.connect(uri, autocommit=True) as c:
        for r in ("anon", "authenticated"):
            c.execute(f"GRANT USAGE ON SCHEMA public TO {r}")
        c.execute((ROOT / "schema.sql").read_text())
        c.execute(HAND_PATCHED)
    return uri


def failures(uri):
    with psycopg.connect(uri) as c:
        return [n for n, ok, _ in check_db.run_checks(c) if not ok]


def test_clean_database_passes(pg_uri):
    assert failures(pg_uri) == []


def test_hand_patched_database_is_caught(broken_uri):
    bad = failures(broken_uri)
    assert any("UPDATE/DELETE blocked" in n for n in bad)
    assert any("matchmind_bot cannot write predictions" in n for n in bad)
    assert any("placeholder" in n for n in bad)
    assert any("arena_users" in n for n in bad)


def test_documented_repair_path_works(broken_uri):
    with psycopg.connect(broken_uri, autocommit=True) as c:
        c.execute((ROOT / "reset-matchmind.sql").read_text())
        for f in MIGRATIONS:
            c.execute((ROOT / f).read_text())
    assert failures(broken_uri) == []
    with psycopg.connect(broken_uri) as c:       # the hand-made fake row is gone
        assert c.execute("SELECT count(*) FROM predictions").fetchone()[0] == 0
