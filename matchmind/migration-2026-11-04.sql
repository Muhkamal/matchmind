-- MatchMind migration 2026-11-04 — review-driven hardening.
-- Run in Supabase SQL Editor AFTER the original schema.sql.

-- 1. Verifiable kickoff timestamps (review point 4)
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS kickoff_utc TIMESTAMPTZ;

-- 2. Ledger immutability: block UPDATE/DELETE on ledger tables (review point 6).
--    The nightly job only INSERTs, so this breaks nothing legitimate.
CREATE OR REPLACE FUNCTION ledger_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'MatchMind ledger is append-only: % on % is forbidden', TG_OP, TG_TABLE_NAME;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS predictions_immutable ON predictions;
CREATE TRIGGER predictions_immutable BEFORE UPDATE OR DELETE ON predictions
    FOR EACH ROW EXECUTE FUNCTION ledger_immutable();
DROP TRIGGER IF EXISTS settlements_immutable ON settlements;
CREATE TRIGGER settlements_immutable BEFORE UPDATE OR DELETE ON settlements
    FOR EACH ROW EXECUTE FUNCTION ledger_immutable();
DROP TRIGGER IF EXISTS results_immutable ON results;
CREATE TRIGGER results_immutable BEFORE UPDATE OR DELETE ON results
    FOR EACH ROW EXECUTE FUNCTION ledger_immutable();

-- 3. Row-level security: public read-only via anon key; writes only through the
--    server-side postgres connection (service key bypasses RLS, nightly job).
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE settlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE results ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE weekly_reports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_read_predictions ON predictions;
CREATE POLICY public_read_predictions ON predictions FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS public_read_settlements ON settlements;
CREATE POLICY public_read_settlements ON settlements FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS public_read_results ON results;
CREATE POLICY public_read_results ON results FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS public_read_model_versions ON model_versions;
CREATE POLICY public_read_model_versions ON model_versions FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS public_read_weekly_reports ON weekly_reports;
CREATE POLICY public_read_weekly_reports ON weekly_reports FOR SELECT TO anon USING (true);
-- no INSERT/UPDATE/DELETE policies for anon => writes denied by default

-- 4. Calibration view, fixed: each market binned by ITS OWN probability
--    (was: all three binned by p_home — review point, v_calibration rewrite)
DROP VIEW IF EXISTS public.v_calibration;
CREATE VIEW public.v_calibration
WITH (security_invoker = true) AS
SELECT p.league,
       COUNT(*) AS n,
       -- home market bins
       CASE WHEN p.p_home < 0.1 THEN '00-10' WHEN p.p_home < 0.2 THEN '10-20'
            WHEN p.p_home < 0.3 THEN '20-30' WHEN p.p_home < 0.4 THEN '30-40'
            WHEN p.p_home < 0.5 THEN '40-50' WHEN p.p_home < 0.6 THEN '50-60'
            WHEN p.p_home < 0.7 THEN '60-70' WHEN p.p_home < 0.8 THEN '70-80'
            WHEN p.p_home < 0.9 THEN '80-90' ELSE '90-100' END AS bin_home,
       AVG(p.p_home) AS pred_home,
       AVG(CASE WHEN r.result = 'H' THEN 1.0 ELSE 0.0 END) AS actual_home,
       -- draw market bins
       CASE WHEN p.p_draw < 0.1 THEN '00-10' WHEN p.p_draw < 0.2 THEN '10-20'
            WHEN p.p_draw < 0.3 THEN '20-30' WHEN p.p_draw < 0.4 THEN '30-40'
            WHEN p.p_draw < 0.5 THEN '40-50' WHEN p.p_draw < 0.6 THEN '50-60'
            WHEN p.p_draw < 0.7 THEN '60-70' WHEN p.p_draw < 0.8 THEN '70-80'
            WHEN p.p_draw < 0.9 THEN '80-90' ELSE '90-100' END AS bin_draw,
       AVG(p.p_draw) AS pred_draw,
       AVG(CASE WHEN r.result = 'D' THEN 1.0 ELSE 0.0 END) AS actual_draw,
       -- away market bins
       CASE WHEN p.p_away < 0.1 THEN '00-10' WHEN p.p_away < 0.2 THEN '10-20'
            WHEN p.p_away < 0.3 THEN '20-30' WHEN p.p_away < 0.4 THEN '30-40'
            WHEN p.p_away < 0.5 THEN '40-50' WHEN p.p_away < 0.6 THEN '50-60'
            WHEN p.p_away < 0.7 THEN '60-70' WHEN p.p_away < 0.8 THEN '70-80'
            WHEN p.p_away < 0.9 THEN '80-90' ELSE '90-100' END AS bin_away,
       AVG(p.p_away) AS pred_away,
       AVG(CASE WHEN r.result = 'A' THEN 1.0 ELSE 0.0 END) AS actual_away
FROM predictions p
JOIN results r ON (r.league, r.match_date, r.home_team, r.away_team)
              = (p.league, p.match_date, p.home_team, p.away_team)
WHERE p.provenance = 'live'
GROUP BY p.league,
         bin_home, bin_draw, bin_away;
