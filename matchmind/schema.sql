-- MatchMind prediction ledger schema (PostgreSQL / Supabase)
-- Design principles: append-only, every pick publicly gradable, settlement is automated.
-- v1.0 — 2026-11-04

CREATE TABLE IF NOT EXISTS model_versions (
    version_id     TEXT PRIMARY KEY,              -- e.g. 'v1.0-ens-iso'
    league         TEXT NOT NULL,                 -- 'E0', 'SP1', ...
    trained_through DATE NOT NULL,                -- last match date in training data
    params         JSONB NOT NULL,                -- {k, home_adv, xi, ridge, calibrator: 'isotonic'}
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id  BIGSERIAL PRIMARY KEY,
    model_version  TEXT NOT NULL REFERENCES model_versions(version_id),
    league         TEXT NOT NULL,
    match_date     DATE NOT NULL,
    home_team      TEXT NOT NULL,
    away_team      TEXT NOT NULL,
    p_home_raw     REAL NOT NULL,
    p_draw_raw     REAL NOT NULL,
    p_away_raw     REAL NOT NULL,
    p_home         REAL NOT NULL,                 -- calibrated (what we publish)
    p_draw         REAL NOT NULL,
    p_away         REAL NOT NULL,
    confidence_band TEXT NOT NULL,                -- 'high' (top>=0.60) | 'medium' (>=0.50) | 'low'
    data_quality   TEXT NOT NULL DEFAULT 'A',     -- 'A' full history | 'B' limited | 'C' thin data
    provenance     TEXT NOT NULL DEFAULT 'live',  -- 'live' | 'backtest' | 'holdout'
    created_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (model_version, league, match_date, home_team, away_team)
);

CREATE TABLE IF NOT EXISTS results (
    result_id      BIGSERIAL PRIMARY KEY,
    league         TEXT NOT NULL,
    match_date     DATE NOT NULL,
    home_team      TEXT NOT NULL,
    away_team      TEXT NOT NULL,
    home_goals     SMALLINT NOT NULL,
    away_goals     SMALLINT NOT NULL,
    result         CHAR(1) NOT NULL,              -- 'H' | 'D' | 'A'
    settled_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (league, match_date, home_team, away_team)
);

CREATE TABLE IF NOT EXISTS settlements (
    settlement_id  BIGSERIAL PRIMARY KEY,
    prediction_id  BIGINT NOT NULL REFERENCES predictions(prediction_id),
    hit            BOOLEAN NOT NULL,
    brier_contrib  REAL NOT NULL,                 -- this match's contribution to 3-class Brier
    settled_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (prediction_id)
);

-- Weekly transparency snapshot: the table our public dashboard reads.
CREATE TABLE IF NOT EXISTS weekly_reports (
    report_id      BIGSERIAL PRIMARY KEY,
    week_of        DATE NOT NULL,                 -- Monday of report week
    league         TEXT NOT NULL,
    n_predictions  INTEGER NOT NULL,
    n_settled      INTEGER NOT NULL,
    accuracy       REAL,
    brier          REAL,
    log_loss       REAL,
    rps            REAL,
    ece_home       REAL,                          -- expected calibration error per market
    ece_draw       REAL,
    ece_away       REAL,
    generated_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (week_of, league)
);

-- ---------- Dashboard views ----------

-- Model accuracy record, all-time, per league (the public ledger view)
CREATE OR REPLACE VIEW v_model_record AS
SELECT p.league,
       p.model_version,
       p.provenance,
       COUNT(*) AS n,
       AVG(CASE WHEN s.hit THEN 1.0 ELSE 0.0 END) AS accuracy,
       AVG(s.brier_contrib) AS brier,
       MIN(p.match_date) AS first_match,
       MAX(p.match_date) AS last_match
FROM predictions p JOIN settlements s USING (prediction_id)
GROUP BY p.league, p.model_version, p.provenance;

-- Calibration view: predicted vs actual by decile, per market
CREATE OR REPLACE VIEW v_calibration AS
SELECT p.league,
       CASE WHEN p.p_home < 0.1 THEN '00-10' WHEN p.p_home < 0.2 THEN '10-20'
            WHEN p.p_home < 0.3 THEN '20-30' WHEN p.p_home < 0.4 THEN '30-40'
            WHEN p.p_home < 0.5 THEN '40-50' WHEN p.p_home < 0.6 THEN '50-60'
            WHEN p.p_home < 0.7 THEN '60-70' WHEN p.p_home < 0.8 THEN '70-80'
            WHEN p.p_home < 0.9 THEN '80-90' ELSE '90-100' END AS bin,
       COUNT(*) AS n,
       AVG(p.p_home) AS pred_home,
       AVG(CASE WHEN r.result = 'H' THEN 1.0 ELSE 0.0 END) AS actual_home,
       AVG(p.p_draw) AS pred_draw,
       AVG(CASE WHEN r.result = 'D' THEN 1.0 ELSE 0.0 END) AS actual_draw,
       AVG(p.p_away) AS pred_away,
       AVG(CASE WHEN r.result = 'A' THEN 1.0 ELSE 0.0 END) AS actual_away
FROM predictions p
JOIN results r ON (r.league, r.match_date, r.home_team, r.away_team)
              = (p.league, p.match_date, p.home_team, p.away_team)
WHERE p.provenance = 'live'
GROUP BY p.league, bin;

-- Unsettled predictions (the settlement worker's work queue)
CREATE OR REPLACE VIEW v_pending_settlement AS
SELECT p.* FROM predictions p
LEFT JOIN settlements s USING (prediction_id)
JOIN results r ON (r.league, r.match_date, r.home_team, r.away_team)
              = (p.league, p.match_date, p.home_team, p.away_team)
WHERE s.prediction_id IS NULL;
