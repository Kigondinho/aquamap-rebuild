-- AquaMap database initialisation, step 5: vulnerability classification (Phase 8).
--
-- One rule, in two places that are tested against each other:
--   Python  core.ivi.assign_tier(ivi, config)
--   SQL     ivi_tier(ivi, tier_thresholds)
--
-- The thresholds are the LOWER bounds of Medium, High and Severe, stored per configuration in
-- scoring_config.tier_thresholds, e.g. {"Medium": 2.0, "High": 3.0, "Severe": 4.0}. With those values:
--
--   Low     1.000 <= IVI < 2.000
--   Medium  2.000 <= IVI < 3.000
--   High    3.000 <= IVI < 4.000
--   Severe  4.000 <= IVI <= 5.000
--
-- A value exactly on a threshold belongs to the HIGHER category. The IVI is stored with three
-- decimals (NUMERIC(4,3)) and the category is always read from that stored value. No IVI = no category.
-- The categories rank roads on a relative index; they are not flood probabilities.

CREATE OR REPLACE FUNCTION ivi_tier(ivi NUMERIC, thresholds JSONB) RETURNS VARCHAR
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN ivi IS NULL THEN NULL
        WHEN ivi >= (thresholds ->> 'Severe')::numeric THEN 'Severe'
        WHEN ivi >= (thresholds ->> 'High')::numeric   THEN 'High'
        WHEN ivi >= (thresholds ->> 'Medium')::numeric THEN 'Medium'
        WHEN (thresholds ->> 'Medium') IS NULL OR (thresholds ->> 'High') IS NULL
             OR (thresholds ->> 'Severe') IS NULL      THEN NULL   -- incomplete thresholds: no answer
        ELSE 'Low'
    END
$$;

-- A stored category must be the one its own configuration gives for the stored IVI. This makes it
-- impossible for the table to hold a score and a category that disagree, whoever writes the row.
CREATE OR REPLACE FUNCTION road_vulnerability_check_tier() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    expected VARCHAR;
BEGIN
    IF NEW.vulnerability_index IS NULL THEN
        RETURN NEW;  -- "no index" rows: vuln_ivi_and_tier_together already requires a NULL category
    END IF;
    SELECT ivi_tier(NEW.vulnerability_index, c.tier_thresholds) INTO expected
    FROM scoring_config c WHERE c.config_version = NEW.config_version;
    IF expected IS DISTINCT FROM NEW.risk_category THEN
        RAISE EXCEPTION 'risk_category % does not match IVI % under configuration % (expected %)',
            NEW.risk_category, NEW.vulnerability_index, NEW.config_version, coalesce(expected, 'none')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS road_vulnerability_tier_consistent ON road_vulnerability;
CREATE TRIGGER road_vulnerability_tier_consistent
    BEFORE INSERT OR UPDATE OF vulnerability_index, risk_category, config_version ON road_vulnerability
    FOR EACH ROW EXECUTE FUNCTION road_vulnerability_check_tier();
