"""Shared fixtures. SQL tests need a real Postgres: set TEST_DATABASE_URL (a throwaway DB!)
or install `pixeltable-pgserver` for an embedded one. Otherwise those tests are skipped."""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MIGRATIONS = ["schema.sql", "migration-2026-11-04.sql", "migration-2026-11-05.sql",
              "migration-2026-11-06.sql", "migration-2026-11-09.sql", "migration-2026-11-10.sql", "migration-2026-11-11.sql"]


def _server_uri():
    import os
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    try:
        import pixeltable_pgserver as pgs
    except ImportError:
        try:
            import pgserver as pgs
        except ImportError:
            return None
    srv = pgs.get_server(Path(tempfile.mkdtemp(prefix="mm_pg_")), cleanup_mode="stop")
    pytest._mm_pg_server = srv  # keep alive
    return srv.get_uri()


@pytest.fixture(scope="session")
def pg_uri():
    import psycopg
    uri = _server_uri()
    if not uri:
        pytest.skip("no Postgres available (set TEST_DATABASE_URL or pip install pixeltable-pgserver)")
    with psycopg.connect(uri, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for role in ("anon", "authenticated"):   # exist on Supabase, not on plain Postgres
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                c.execute(f"CREATE ROLE {role}")
        c.execute("GRANT USAGE ON SCHEMA public TO anon, authenticated")   # as on Supabase
        for f in MIGRATIONS:
            c.execute((ROOT / f).read_text())
    return uri


@pytest.fixture()
def conn(pg_uri):
    import psycopg
    with psycopg.connect(pg_uri) as c:
        yield c
        c.rollback()


@pytest.fixture()
def clean_db(pg_uri):
    """Empty every table (bypassing the immutability triggers: test cleanup only)."""
    import psycopg
    with psycopg.connect(pg_uri, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute("DELETE FROM market_outcomes; DELETE FROM market_predictions; DELETE FROM market_settlements; DELETE FROM settlements; "
                  "DELETE FROM arena_settlements; DELETE FROM arena_predictions; "
                  "DELETE FROM arena_users; DELETE FROM predictions; DELETE FROM results; "
                  "DELETE FROM model_versions")
    return pg_uri
