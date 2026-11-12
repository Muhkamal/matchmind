-- MatchMind RESET (pre-launch only).
-- Drops every MatchMind table/view/function in the public schema and removes any grants given to the
-- matchmind_ro / matchmind_bot roles, so the migrations can be applied cleanly from scratch.
--
--   !!  THIS DELETES ALL MATCHMIND DATA (predictions, results, arena).  !!
--   Use it only while nothing public depends on the ledger. After launch the ledger is append-only
--   on purpose and you should never reset it.
--
-- After running this, run in order:
--   schema.sql, migration-2026-11-04.sql, migration-2026-11-05.sql, migration-2026-11-06.sql
-- then:  ALTER ROLE matchmind_ro PASSWORD '...';  ALTER ROLE matchmind_bot PASSWORD '...';
-- then:  python backfill.py && python nightly.py && python check_db.py

DROP VIEW IF EXISTS public.v_leaderboard, public.v_team_profiles, public.v_market_record,
                    public.v_model_record_by_version, public.v_model_record, public.v_calibration,
                    public.v_pending_settlement CASCADE;

-- Ledger triggers refuse DELETE/TRUNCATE; DROP TABLE is not blocked by them.
DROP VIEW IF EXISTS public.v_extra_market_record;
DROP TABLE IF EXISTS public.market_outcomes, public.market_predictions;
DROP FUNCTION IF EXISTS public.market_predictions_lock();
DROP TABLE IF EXISTS public.job_runs, public.arena_settlements, public.arena_predictions, public.arena_users,
                     public.market_settlements, public.settlements, public.predictions,
                     public.weekly_reports, public.results, public.model_versions CASCADE;
DROP FUNCTION IF EXISTS public.ledger_immutable() CASCADE;

-- Remove anything that was granted by hand (including default privileges on FUTURE tables).
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'matchmind_ro') THEN
    REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM matchmind_ro;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM matchmind_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM matchmind_ro;
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'matchmind_bot') THEN
    REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM matchmind_bot;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM matchmind_bot;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM matchmind_bot;
  END IF;
END $$;
