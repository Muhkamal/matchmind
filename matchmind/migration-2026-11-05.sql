-- MatchMind migration 2026-11-05 — goal-market columns on the ledger.
-- Run AFTER schema.sql and migration-2026-11-04.sql, BEFORE migration-2026-11-06.sql.
--
-- (The arena tables and views that used to live in this file were rebuilt in
--  migration-2026-11-06.sql: the old v_team_profiles referenced columns that do not exist
--  and the old arena scoring could not work. Nothing else was lost: no arena data existed.)

ALTER TABLE predictions ADD COLUMN IF NOT EXISTS p_over25 REAL;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS p_btts   REAL;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS top_scorelines TEXT;
