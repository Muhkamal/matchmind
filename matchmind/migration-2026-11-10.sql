-- v1.8: the database itself rejects impossible probabilities on every NEW prediction.
-- NOT VALID: older rows are not re-checked (they are what they are); every future row must pass.
ALTER TABLE predictions DROP CONSTRAINT IF EXISTS probs_sane;
ALTER TABLE predictions ADD CONSTRAINT probs_sane CHECK (
    p_home BETWEEN 0 AND 1 AND p_draw BETWEEN 0 AND 1 AND p_away BETWEEN 0 AND 1
    AND abs(p_home + p_draw + p_away - 1) < 0.001
    AND (p_over25 IS NULL OR p_over25 BETWEEN 0 AND 1)
    AND (p_btts   IS NULL OR p_btts   BETWEEN 0 AND 1)
) NOT VALID;
