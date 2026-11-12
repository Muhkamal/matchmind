-- v1.9: a general, append-only ledger for ANY market on a fixture (not just who wins).
-- Every extra market is stored before kickoff, graded automatically, and scored with Brier like the 1X2 pick.
-- New markets later (team shots, corners, cards, players) only add rows, not new tables.

CREATE TABLE IF NOT EXISTS market_predictions (
    market_pred_id BIGSERIAL PRIMARY KEY,
    prediction_id  BIGINT NOT NULL REFERENCES predictions(prediction_id),
    market         TEXT NOT NULL,                       -- 'dc_1x', 'over_1_5', 'cs_home', 'score_top5', ...
    subject        TEXT NOT NULL DEFAULT '',            -- '' = whole match; a team or player id for those markets
    selection      TEXT,                                -- extra detail, e.g. the scoreline list for score_top5
    probability    REAL NOT NULL CHECK (probability BETWEEN 0 AND 1),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (prediction_id, market, subject)
);
CREATE INDEX IF NOT EXISTS market_predictions_pred_idx ON market_predictions (prediction_id);

CREATE TABLE IF NOT EXISTS market_outcomes (
    market_pred_id BIGINT PRIMARY KEY REFERENCES market_predictions(market_pred_id),
    outcome        BOOLEAN NOT NULL,
    brier          REAL NOT NULL,                       -- (probability - outcome)^2; 0 is perfect
    settled_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A market pick may only be added while its fixture's live pick is still before kickoff.
CREATE OR REPLACE FUNCTION public.market_predictions_lock() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE ko timestamptz; prov text;
BEGIN
    SELECT kickoff_utc, provenance INTO ko, prov FROM public.predictions WHERE prediction_id = NEW.prediction_id;
    IF prov = 'live' AND (ko IS NULL OR ko <= now()) THEN
        RAISE EXCEPTION 'MatchMind: market picks must be locked before kickoff';
    END IF;
    RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS market_predictions_lock ON market_predictions;
CREATE TRIGGER market_predictions_lock BEFORE INSERT ON market_predictions
    FOR EACH ROW EXECUTE FUNCTION public.market_predictions_lock();

-- Append-only, same protection as the rest of the ledger.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['market_predictions', 'market_outcomes'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', t || '_immutable', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I '
                       'FOR EACH ROW EXECUTE FUNCTION public.ledger_immutable()', t || '_immutable', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', t || '_no_truncate', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON public.%I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION public.ledger_immutable()', t || '_no_truncate', t);
        EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC', t);
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN EXECUTE format('REVOKE ALL ON public.%I FROM anon', t); END IF;
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', t); END IF;
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('GRANT SELECT ON public.%I TO matchmind_ro, matchmind_bot', t);
        EXECUTE format('DROP POLICY IF EXISTS app_read_%s ON public.%I', t, t);
        EXECUTE format('CREATE POLICY app_read_%s ON public.%I FOR SELECT TO matchmind_ro, matchmind_bot USING (true)', t, t);
    END LOOP;
END $$;

-- Public record per market. Baseline = the best constant forecast (always predict the observed rate), which is
-- a strict test: a market only adds value if its Brier is below brier_constant.
DROP VIEW IF EXISTS public.v_extra_market_record;
CREATE VIEW public.v_extra_market_record WITH (security_invoker = true) AS
SELECT p.league, mp.market,
       COUNT(*) AS n,
       AVG(mp.probability) AS avg_predicted,
       AVG(CASE WHEN mo.outcome THEN 1.0 ELSE 0.0 END) AS actual_rate,
       AVG(mo.brier) AS brier,
       AVG(CASE WHEN mo.outcome THEN 1.0 ELSE 0.0 END) * (1 - AVG(CASE WHEN mo.outcome THEN 1.0 ELSE 0.0 END)) AS brier_constant
FROM market_predictions mp
JOIN market_outcomes mo USING (market_pred_id)
JOIN predictions p ON p.prediction_id = mp.prediction_id
WHERE p.provenance = 'live'
GROUP BY p.league, mp.market;
GRANT SELECT ON public.v_extra_market_record TO matchmind_ro, matchmind_bot;
