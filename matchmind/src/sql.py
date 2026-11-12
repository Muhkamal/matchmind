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

INSERT_PREDICTION_RETURNING = INSERT_PREDICTION.rstrip() + " RETURNING prediction_id"
# RETURNING gives a row only when this run inserted the pick; an existing (earlier, locked) pick returns nothing, so
# its extra markets are never added later from a different refit.

INSERT_MARKET_PREDICTION = """
INSERT INTO market_predictions (prediction_id, market, subject, selection, probability)
VALUES (%s,%s,%s,%s,%s)
ON CONFLICT (prediction_id, market, subject) DO NOTHING
"""

# Grade every stored extra market whose match has a result. Unknown markets stay ungraded (outcome IS NULL).
SETTLE_EXTRA_MARKETS = """
WITH graded AS (
    SELECT mp.market_pred_id, mp.probability,
           CASE mp.market
               WHEN 'dc_1x'         THEN r.home_goals >= r.away_goals
               WHEN 'dc_x2'         THEN r.away_goals >= r.home_goals
               WHEN 'dc_12'         THEN r.home_goals <> r.away_goals
               WHEN 'over_1_5'      THEN r.home_goals + r.away_goals >= 2
               WHEN 'over_3_5'      THEN r.home_goals + r.away_goals >= 4
               WHEN 'cs_home'       THEN r.away_goals = 0
               WHEN 'cs_away'       THEN r.home_goals = 0
               WHEN 'ah_home_p1_5'  THEN r.away_goals - r.home_goals <= 1
               WHEN 'ah_away_p1_5'  THEN r.home_goals - r.away_goals <= 1
               WHEN 'ah_home_m1_5'  THEN r.home_goals - r.away_goals >= 2
               WHEN 'ah_away_m1_5'  THEN r.away_goals - r.home_goals >= 2
               WHEN 'wtn_home'      THEN r.home_goals > r.away_goals AND r.away_goals = 0
               WHEN 'wtn_away'      THEN r.away_goals > r.home_goals AND r.home_goals = 0
               WHEN 'tg_0_1'        THEN r.home_goals + r.away_goals <= 1
               WHEN 'tg_2_3'        THEN r.home_goals + r.away_goals BETWEEN 2 AND 3
               WHEN 'tg_4_5'        THEN r.home_goals + r.away_goals BETWEEN 4 AND 5
               WHEN 'tg_6p'         THEN r.home_goals + r.away_goals >= 6
               WHEN 'ts2_home'      THEN r.home_goals >= 2
               WHEN 'ts2_away'      THEN r.away_goals >= 2
               WHEN 'ts3_home'      THEN r.home_goals >= 3
               WHEN 'ts3_away'      THEN r.away_goals >= 3
               WHEN 'btts_over25'   THEN r.home_goals > 0 AND r.away_goals > 0 AND r.home_goals + r.away_goals >= 3
               WHEN 'btts_under25'  THEN r.home_goals > 0 AND r.away_goals > 0 AND r.home_goals + r.away_goals <= 2
               WHEN 'score_top1'    THEN (r.home_goals::text || '-' || r.away_goals::text) = mp.selection
               WHEN 'score_top5'    THEN (r.home_goals::text || '-' || r.away_goals::text)
                                         = ANY (string_to_array(mp.selection, ','))
           END AS outcome
    FROM market_predictions mp
    JOIN predictions p ON p.prediction_id = mp.prediction_id
    JOIN results r ON (r.league, r.match_date, r.home_team, r.away_team)
                    = (p.league, p.match_date, p.home_team, p.away_team)
    LEFT JOIN market_outcomes mo ON mo.market_pred_id = mp.market_pred_id
    WHERE mo.market_pred_id IS NULL
)
INSERT INTO market_outcomes (market_pred_id, outcome, brier)
SELECT market_pred_id, outcome, POWER(probability - CASE WHEN outcome THEN 1 ELSE 0 END, 2)
FROM graded WHERE outcome IS NOT NULL
ON CONFLICT (market_pred_id) DO NOTHING;
"""

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
