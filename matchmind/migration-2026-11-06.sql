-- MatchMind migration 2026-11-06 — integrity hardening, graded markets, arena v2, least-privilege roles.
-- Run AFTER schema.sql, migration-2026-11-04.sql and migration-2026-11-05.sql.
-- Safe to re-run. It refuses to run if it would destroy data (see section 0 and 1).

-- ============================================================================
-- 0. Guards
-- ============================================================================
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS kickoff_utc TIMESTAMPTZ;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS p_over25 REAL;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS p_btts   REAL;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS top_scorelines TEXT;

DO $$
BEGIN
    -- The arena is rebuilt below. It had no users (the previous arena scoring could not run),
    -- but never drop real data silently.
    IF to_regclass('public.arena_predictions') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM public.arena_predictions) THEN
            RAISE EXCEPTION 'arena_predictions contains rows; export them before running this migration';
        END IF;
    END IF;
END $$;

-- ============================================================================
-- 1. One live prediction per fixture (first pick wins) + verified pre-kickoff
-- ============================================================================
DO $$
DECLARE dupes integer;
BEGIN
    SELECT COUNT(*) INTO dupes FROM (
        SELECT 1 FROM public.predictions WHERE provenance = 'live'
        GROUP BY league, match_date, home_team, away_team HAVING COUNT(*) > 1) d;
    IF dupes > 0 THEN
        RAISE EXCEPTION
          '% fixtures already have several live predictions. Keep the earliest per fixture: '
          'ALTER TABLE predictions DISABLE TRIGGER predictions_immutable; delete the later rows '
          '(and their settlements); ALTER TABLE predictions ENABLE TRIGGER predictions_immutable; '
          'then re-run this migration.', dupes;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS predictions_one_live_per_fixture
    ON predictions (league, match_date, home_team, away_team)
    WHERE provenance = 'live';

-- The database itself refuses a live pick that is not strictly before kickoff.
-- NOT VALID: applies to every NEW row; older rows are not re-checked.
ALTER TABLE predictions DROP CONSTRAINT IF EXISTS predictions_pre_kickoff;
ALTER TABLE predictions ADD CONSTRAINT predictions_pre_kickoff
    CHECK (provenance <> 'live' OR (kickoff_utc IS NOT NULL AND created_at < kickoff_utc)) NOT VALID;

-- ============================================================================
-- 2. Immutability: UPDATE/DELETE and now TRUNCATE are blocked on the ledger tables
-- ============================================================================
CREATE OR REPLACE FUNCTION public.ledger_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    RAISE EXCEPTION 'MatchMind ledger is append-only: % on % is forbidden', TG_OP, TG_TABLE_NAME;
END; $$;

-- graded markets (created here, protected below)
CREATE TABLE IF NOT EXISTS market_settlements (
    prediction_id  BIGINT PRIMARY KEY REFERENCES predictions(prediction_id),
    over25_actual  BOOLEAN NOT NULL,
    btts_actual    BOOLEAN NOT NULL,
    brier_over25   REAL NOT NULL,
    brier_btts     REAL NOT NULL,
    settled_at     TIMESTAMPTZ DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['predictions', 'settlements', 'results', 'market_settlements'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', t || '_immutable', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I '
                       'FOR EACH ROW EXECUTE FUNCTION public.ledger_immutable()', t || '_immutable', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', t || '_no_truncate', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON public.%I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION public.ledger_immutable()', t || '_no_truncate', t);
        EXECUTE format('REVOKE TRUNCATE ON public.%I FROM PUBLIC, anon, authenticated', t);
    END LOOP;
END $$;

-- ============================================================================
-- 3. Least-privilege roles (set their passwords yourself, see api-deploy.md)
--      matchmind_ro  : the public API. Read-only on the ledger.
--      matchmind_bot : the Telegram bot. Read ledger, write ONLY arena tables.
--    The nightly job keeps using the owner connection (DATABASE_URL).
-- ============================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'matchmind_ro')  THEN CREATE ROLE matchmind_ro  LOGIN; END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'matchmind_bot') THEN CREATE ROLE matchmind_bot LOGIN; END IF;
END $$;

GRANT USAGE ON SCHEMA public TO matchmind_ro, matchmind_bot;

-- ============================================================================
-- 4. Arena v2 (head-to-head vs the model's top pick; picks are final; privacy by default)
-- ============================================================================
DROP VIEW IF EXISTS public.v_leaderboard;
DROP VIEW IF EXISTS public.v_team_profiles;
DROP TABLE IF EXISTS arena_settlements;
DROP TABLE IF EXISTS arena_predictions;
DROP TABLE IF EXISTS arena_users;

CREATE TABLE arena_users (
    user_id      BIGSERIAL PRIMARY KEY,
    telegram_id  BIGINT UNIQUE NOT NULL,                 -- private: never exposed publicly
    display_name TEXT NOT NULL
        CONSTRAINT display_name_ok CHECK (display_name ~ '^[A-Za-z0-9_ ]{3,20}$'),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX arena_users_display_name_uniq ON arena_users (lower(display_name));

CREATE TABLE arena_predictions (
    arena_prediction_id BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES arena_users(user_id),
    model_prediction_id BIGINT NOT NULL REFERENCES predictions(prediction_id),
    league              TEXT NOT NULL,
    match_date          DATE NOT NULL,
    home_team           TEXT NOT NULL,
    away_team           TEXT NOT NULL,
    pick                CHAR(1) NOT NULL CHECK (pick IN ('H','D','A')),
    kickoff_utc         TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, model_prediction_id),                -- one pick per user per fixture, final
    CONSTRAINT arena_pre_kickoff CHECK (created_at < kickoff_utc)
);

CREATE TABLE arena_settlements (
    arena_prediction_id BIGINT PRIMARY KEY REFERENCES arena_predictions(arena_prediction_id),
    user_hit   BOOLEAN NOT NULL,
    model_hit  BOOLEAN NOT NULL,
    outcome    TEXT NOT NULL CHECK (outcome IN ('win','loss','tie')),
    settled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['arena_predictions', 'arena_settlements'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I '
                       'FOR EACH ROW EXECUTE FUNCTION public.ledger_immutable()', t || '_immutable', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON public.%I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION public.ledger_immutable()', t || '_no_truncate', t);
    END LOOP;
END $$;

-- Arena tables are PRIVATE (pre-kickoff picks must not be copyable, telegram ids must not leak).
-- Only the aggregated v_leaderboard below is public.
ALTER TABLE arena_users       ENABLE ROW LEVEL SECURITY;
ALTER TABLE arena_predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE arena_settlements ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON arena_users, arena_predictions, arena_settlements FROM PUBLIC, anon, authenticated;

GRANT SELECT, INSERT ON arena_users TO matchmind_bot;
GRANT UPDATE (display_name) ON arena_users TO matchmind_bot;
GRANT SELECT, INSERT ON arena_predictions TO matchmind_bot;
GRANT SELECT ON arena_settlements TO matchmind_bot;
GRANT USAGE ON SEQUENCE arena_users_user_id_seq, arena_predictions_arena_prediction_id_seq TO matchmind_bot;

CREATE POLICY bot_arena_users_select  ON arena_users FOR SELECT TO matchmind_bot USING (true);
CREATE POLICY bot_arena_users_insert  ON arena_users FOR INSERT TO matchmind_bot WITH CHECK (true);
CREATE POLICY bot_arena_users_update  ON arena_users FOR UPDATE TO matchmind_bot USING (true) WITH CHECK (true);
CREATE POLICY bot_arena_pred_select   ON arena_predictions FOR SELECT TO matchmind_bot USING (true);
CREATE POLICY bot_arena_pred_insert   ON arena_predictions FOR INSERT TO matchmind_bot WITH CHECK (true);
CREATE POLICY bot_arena_settle_select ON arena_settlements FOR SELECT TO matchmind_bot USING (true);

-- ============================================================================
-- 5. Public read access to the ledger for the API role (anon policies already exist)
-- ============================================================================
GRANT SELECT ON predictions, results, settlements, market_settlements, model_versions,
                weekly_reports TO matchmind_ro, matchmind_bot;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['predictions', 'results', 'settlements', 'model_versions', 'weekly_reports'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS app_read_%s ON public.%I', t, t);
        EXECUTE format('CREATE POLICY app_read_%s ON public.%I FOR SELECT TO matchmind_ro, matchmind_bot USING (true)', t, t);
    END LOOP;
END $$;

ALTER TABLE market_settlements ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS public_read_market_settlements ON market_settlements;
CREATE POLICY public_read_market_settlements ON market_settlements FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS app_read_market_settlements ON market_settlements;
CREATE POLICY app_read_market_settlements ON market_settlements FOR SELECT TO matchmind_ro, matchmind_bot USING (true);

-- ============================================================================
-- 6. Views
-- ============================================================================
-- Public record per league and provenance (one row per league, not one per daily refit).
DROP VIEW IF EXISTS public.v_model_record;
CREATE VIEW public.v_model_record WITH (security_invoker = true) AS
SELECT p.league, p.provenance,
       COUNT(*) AS n,
       AVG(CASE WHEN s.hit THEN 1.0 ELSE 0.0 END) AS accuracy,
       AVG(s.brier_contrib) AS brier,
       MIN(p.match_date) AS first_match,
       MAX(p.match_date) AS last_match
FROM predictions p JOIN settlements s USING (prediction_id)
GROUP BY p.league, p.provenance;

-- Audit view: the same record split by immutable model version.
DROP VIEW IF EXISTS public.v_model_record_by_version;
CREATE VIEW public.v_model_record_by_version WITH (security_invoker = true) AS
SELECT p.league, p.model_version, p.provenance,
       COUNT(*) AS n,
       AVG(CASE WHEN s.hit THEN 1.0 ELSE 0.0 END) AS accuracy,
       AVG(s.brier_contrib) AS brier
FROM predictions p JOIN settlements s USING (prediction_id)
GROUP BY p.league, p.model_version, p.provenance;

-- Reliability per market, each market binned by ITS OWN probability (live picks only).
DROP VIEW IF EXISTS public.v_calibration;
CREATE VIEW public.v_calibration WITH (security_invoker = true) AS
WITH live AS (
    SELECT p.league, p.p_home, p.p_draw, p.p_away, r.result
    FROM predictions p
    JOIN results r ON (r.league, r.match_date, r.home_team, r.away_team)
                    = (p.league, p.match_date, p.home_team, p.away_team)
    WHERE p.provenance = 'live'
), m AS (
    SELECT league, 'home' AS market, p_home::float8 AS p, (result = 'H')::int AS y FROM live
    UNION ALL SELECT league, 'draw', p_draw::float8, (result = 'D')::int FROM live
    UNION ALL SELECT league, 'away', p_away::float8, (result = 'A')::int FROM live
)
SELECT league, market,
       LEAST(FLOOR(p * 10), 9)::int AS bin,
       COUNT(*) AS n,
       AVG(p) AS pred,
       AVG(y::float8) AS actual
FROM m
GROUP BY league, market, LEAST(FLOOR(p * 10), 9)::int;

-- Goal markets, graded, against the trivial "always predict the base rate" forecast.
DROP VIEW IF EXISTS public.v_market_record;
CREATE VIEW public.v_market_record WITH (security_invoker = true) AS
SELECT p.league,
       COUNT(*) AS n,
       AVG(ms.over25_actual::int::float8) AS over25_rate,
       AVG(ms.brier_over25)               AS brier_over25,
       AVG(ms.over25_actual::int::float8) * (1 - AVG(ms.over25_actual::int::float8)) AS brier_over25_baseline,
       AVG(ms.btts_actual::int::float8)   AS btts_rate,
       AVG(ms.brier_btts)                 AS brier_btts,
       AVG(ms.btts_actual::int::float8) * (1 - AVG(ms.btts_actual::int::float8)) AS brier_btts_baseline
FROM predictions p JOIN market_settlements ms USING (prediction_id)
WHERE p.provenance = 'live'
GROUP BY p.league;

-- Public arena leaderboard. Deliberately a DEFINER view (runs as owner, bypasses RLS) so
-- the underlying private arena tables are never exposed: only display names + aggregates.
-- Users appear only after 10 settled picks (keep in sync with src/arena.py::MIN_SETTLED).
CREATE VIEW public.v_leaderboard WITH (security_invoker = false) AS
SELECT u.display_name,
       COUNT(*) AS n,
       COUNT(*) FILTER (WHERE s.outcome = 'win')  AS wins,
       COUNT(*) FILTER (WHERE s.outcome = 'loss') AS losses,
       COUNT(*) FILTER (WHERE s.outcome = 'tie')  AS ties,
       (COUNT(*) FILTER (WHERE s.outcome = 'win') - COUNT(*) FILTER (WHERE s.outcome = 'loss'))::float8
           / COUNT(*) AS net_rate,
       AVG(s.user_hit::int::float8)  AS hit_rate,
       AVG(s.model_hit::int::float8) AS model_hit_rate,
       MAX(ap.created_at) AS last_pick
FROM arena_predictions ap
JOIN arena_users u ON u.user_id = ap.user_id
JOIN arena_settlements s ON s.arena_prediction_id = ap.arena_prediction_id
GROUP BY u.user_id, u.display_name
HAVING COUNT(*) >= 10;

-- Team tendencies from stored results (home_goals / away_goals; the 11-05 version used
-- columns that do not exist).
CREATE VIEW public.v_team_profiles WITH (security_invoker = true) AS
SELECT league, team, COUNT(*) AS n,
       AVG(gf) AS goals_for, AVG(ga) AS goals_against,
       AVG(CASE WHEN gf + ga >= 3 THEN 1.0 ELSE 0.0 END) AS over25_rate,
       AVG(CASE WHEN gf > 0 AND ga > 0 THEN 1.0 ELSE 0.0 END) AS btts_rate,
       AVG(CASE WHEN ga = 0 THEN 1.0 ELSE 0.0 END) AS clean_sheet_rate,
       SUM(CASE WHEN venue = 'H' THEN 1 ELSE 0 END) AS home_n,
       SUM(CASE WHEN venue = 'A' THEN 1 ELSE 0 END) AS away_n,
       AVG(CASE WHEN venue = 'H' THEN gf END) AS home_gf,
       AVG(CASE WHEN venue = 'H' THEN ga END) AS home_ga,
       AVG(CASE WHEN venue = 'A' THEN gf END) AS away_gf,
       AVG(CASE WHEN venue = 'A' THEN ga END) AS away_ga
FROM (
    SELECT league, home_team AS team, home_goals AS gf, away_goals AS ga, 'H' AS venue FROM results
    UNION ALL
    SELECT league, away_team AS team, away_goals AS gf, home_goals AS ga, 'A' AS venue FROM results
) apps
GROUP BY league, team
HAVING COUNT(*) >= 5;

GRANT SELECT ON v_model_record, v_model_record_by_version, v_calibration, v_market_record,
                v_leaderboard, v_team_profiles TO anon, matchmind_ro, matchmind_bot;
