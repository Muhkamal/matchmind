-- v1.6: operational run log so the public site can tell "no fixtures" from "job did not run".
-- Holds counts and issue text only (no secrets). Written by the nightly job (owner role); read-only for the API.
CREATE TABLE IF NOT EXISTS job_runs (
    run_id               BIGSERIAL PRIMARY KEY,
    finished_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    league               TEXT NOT NULL,
    ok                   BOOLEAN NOT NULL,
    results_ingested     INT NOT NULL DEFAULT 0,
    fixtures_seen        INT NOT NULL DEFAULT 0,
    predictions_appended INT NOT NULL DEFAULT 0,
    settled              INT NOT NULL DEFAULT 0,
    issues               TEXT
);
CREATE INDEX IF NOT EXISTS job_runs_finished_idx ON job_runs (finished_at DESC);

ALTER TABLE job_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON job_runs FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON job_runs FROM anon; END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON job_runs FROM authenticated; END IF;
END $$;
GRANT SELECT ON job_runs TO matchmind_ro;
DROP POLICY IF EXISTS app_read_job_runs ON job_runs;
CREATE POLICY app_read_job_runs ON job_runs FOR SELECT TO matchmind_ro USING (true);
