"""SQL used by the pipeline, kept in one module so tests can run it against real Postgres.

Every statement is idempotent: re-running settlement can never create duplicates.
"""

# 1X2 settlement: grade every ledger prediction that has a result and no settlement yet.
SETTLE_1X2 = """
INSERT INTO settlements (prediction_id, hit, brier_contrib)
SELECT p.prediction_id,
       (CASE WHEN p.p_home >= p.p_draw AND p.p_home >= p.p_away THEN 0
             WHEN p.p_draw >= p.p_away THEN 1 ELSE 2 END)
       = CASE r.result WHEN 'H' THEN 0 WHEN 'D' THEN 1 ELSE 2 END,
       POWER(p.p_home - CASE r.result WHEN 'H' THEN 1 ELSE 0 END, 2)
     + POWER(p.p_draw - CASE r.result WHEN 'D' THEN 1 ELSE 0 END, 2)
     + POWER(p.p_away - CASE r.result WHEN 'A' THEN 1 ELSE 0 END, 2)
FROM predictions p
JOIN results r ON (r.league, r.match_date, r.home_team, r.away_team)
              = (p.league, p.match_date, p.home_team, p.away_team)
LEFT JOIN settlements s ON s.prediction_id = p.prediction_id
WHERE s.prediction_id IS NULL
ON CONFLICT (prediction_id) DO NOTHING;
"""

# Goal markets (Over 2.5, BTTS) are graded too: published picks are always graded picks.
SETTLE_MARKETS = """
INSERT INTO market_settlements
    (prediction_id, over25_actual, btts_actual, brier_over25, brier_btts)
SELECT p.prediction_id,
       (r.home_goals + r.away_goals) >= 3,
       (r.home_goals > 0 AND r.away_goals > 0),
       POWER(p.p_over25 - CASE WHEN r.home_goals + r.away_goals >= 3 THEN 1 ELSE 0 END, 2),
       POWER(p.p_btts   - CASE WHEN r.home_goals > 0 AND r.away_goals > 0 THEN 1 ELSE 0 END, 2)
FROM predictions p
JOIN results r ON (r.league, r.match_date, r.home_team, r.away_team)
              = (p.league, p.match_date, p.home_team, p.away_team)
LEFT JOIN market_settlements ms ON ms.prediction_id = p.prediction_id
WHERE p.p_over25 IS NOT NULL AND p.p_btts IS NOT NULL AND ms.prediction_id IS NULL
ON CONFLICT (prediction_id) DO NOTHING;
"""

# Arena: each user pick is compared head-to-head with the model's TOP pick on the same
# fixture. 'win' = user right and model wrong, 'loss' = the reverse, 'tie' = both right
# or both wrong. (A one-hot pick cannot be compared with a probabilistic Brier score.)
SETTLE_ARENA = """
INSERT INTO arena_settlements (arena_prediction_id, user_hit, model_hit, outcome)
SELECT ap.arena_prediction_id,
       (ap.pick = r.result),
       (mp.top = r.result),
       CASE WHEN ap.pick = r.result AND mp.top <> r.result THEN 'win'
            WHEN ap.pick <> r.result AND mp.top = r.result THEN 'loss'
            ELSE 'tie' END
FROM arena_predictions ap
JOIN predictions p ON p.prediction_id = ap.model_prediction_id
CROSS JOIN LATERAL (
    SELECT CASE WHEN p.p_home >= p.p_draw AND p.p_home >= p.p_away THEN 'H'
                WHEN p.p_draw >= p.p_away THEN 'D' ELSE 'A' END AS top) mp
JOIN results r ON (r.league, r.match_date, r.home_team, r.away_team)
              = (p.league, p.match_date, p.home_team, p.away_team)
LEFT JOIN arena_settlements s ON s.arena_prediction_id = ap.arena_prediction_id
WHERE s.arena_prediction_id IS NULL
ON CONFLICT (arena_prediction_id) DO NOTHING;
"""

INSERT_PREDICTION = """
INSERT INTO predictions
  (model_version, league, match_date, home_team, away_team,
   p_home_raw, p_draw_raw, p_away_raw, p_home, p_draw, p_away,
   confidence_band, data_quality, provenance, kickoff_utc,
   p_over25, p_btts, top_scorelines)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT DO NOTHING
"""  # first live pick per fixture wins (unique index) -- later refits never add a second row

INSERT_RESULT = """
INSERT INTO results (league, match_date, home_team, away_team, home_goals, away_goals, result)
VALUES (%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (league, match_date, home_team, away_team) DO NOTHING
"""

INSERT_MODEL_VERSION = """
INSERT INTO model_versions (version_id, league, trained_through, params)
VALUES (%s,%s,%s,%s::jsonb)
ON CONFLICT (version_id) DO NOTHING
"""
