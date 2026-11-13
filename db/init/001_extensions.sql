-- AquaMap database initialisation, step 1: extensions.
-- All init scripts are idempotent, so they can be re-applied safely:
--   python -m app.db_tools init
-- (Docker also runs them automatically, once, on the first start of an empty volume.)

CREATE EXTENSION IF NOT EXISTS postgis;
-- Trigram indexes: fast case-insensitive "contains" search on road names (Phase 8).
CREATE EXTENSION IF NOT EXISTS pg_trgm;
