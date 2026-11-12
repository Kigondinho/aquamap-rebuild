# Phase 6 — Geospatial database ingestion

```bash
python -m pipeline.load_vectors                 # load roads + rivers into PostGIS, then verify against the files
python -m pipeline.load_vectors --only rivers   # one layer
python -m pipeline.load_vectors --verify-only   # compare database and files, change nothing
python -m pipeline.load_vectors --prune         # also delete rows that are no longer in the file
```

Input: `data/processed/roads.gpkg`, `data/processed/rivers.gpkg` (Phase 4). Target: `road_segments`,
`river_centerlines`, `spatial_layers`. Report: `data/processed/ingestion_report.json`.
Exit status 0 = loaded and verified, 1 = failure (reasons printed). Needs the database from `.env`; a database
created before this phase is upgraded by `python -m app.db_tools init` (adds two columns and three indexes, keeps data).

## What the loader does

| Requirement | How |
|---|---|
| Validate geometries | Shapely validity check before loading; PostGIS checks the staged rows again (`ST_IsValid`, `ST_IsEmpty`, SRID, type) and the table has a `CHECK (ST_IsValid AND NOT ST_IsEmpty)`. |
| Repair where possible | Invalid lines go through `make_valid`; only line parts with a length above zero are kept. A line that cannot be repaired (for example two identical points) is dropped and counted. |
| Remove empty geometries | Null, empty, non-line and zero-length parts are dropped and counted. MultiLineStrings are split into LineStrings. |
| EPSG:32737 | A file without a CRS is refused. Any other CRS is reprojected. The column type `geometry(LineString, 32737)` rejects anything else. Z values are dropped. |
| Keep OSM attributes | Roads: `osm_element`, `osm_id`, `name`, `highway` → `highway_type`, `surface`. Rivers: `osm_element`, `osm_id`, `name`, `waterway`. Missing values stay NULL (never guessed). Values longer than the column are cut and counted. |
| No duplicates | Key `(osm_element, osm_id, part_no)` with a unique index; `part_no` is the position of a segment inside its OSM feature (0 unless a MultiLineString was split). The load is an `INSERT … ON CONFLICT DO UPDATE` that only touches rows that actually differ. Exact repeats inside the file are dropped. |
| Transactions and errors | One transaction per layer: staging, upsert, layer record, index check. Any error rolls all of it back and is reported as one line saying what failed; connection details are never printed. A table lock lets only one loader run at a time; readers are not blocked. |
| Spatial indexes | `CREATE INDEX IF NOT EXISTS … USING GIST (geom)`, then a check that the index is valid, then `ANALYZE`. |

Re-running with the same file inserts 0 and updates 0 rows. If OSM data changes, the row is updated in place and keeps
its `road_id`; a road whose **geometry** changed loses its vulnerability result, because that score was computed for the
old line. Rows that disappeared from the file are kept and reported unless `--prune` is given (pruning only ever
touches the layer being loaded). All road classes are loaded; decision A3 (which classes are scored) is applied later
by `highway_type`. Rows without an OSM id or without `highway` are dropped and counted: without an id a re-run
could not recognise them.

## Verification

After each load (and with `--verify-only`) the database is compared with the cleaned file:

- feature count; every key present; coordinates identical row by row; attributes identical row by row;
- all geometries valid, non-empty, `LINESTRING`, SRID 32737 (data and column definition); no duplicate keys;
- total length and extent equal; NULL counts per attribute;
- spatial queries answered twice, by PostGIS and by GeoPandas on the file, at the CBD (36.82°E, 1.29°S) and at a
  point taken from the data: features intersecting a 2 km box, features within 500 m (`ST_DWithin`), distance to the
  nearest feature (`<->`); the CBD point is also sent as longitude/latitude and must give the same answer;
- GiST index exists, is valid, and is used for a bounding-box query.

## Result on the data downloaded on 2026-11-13

| | Roads | Rivers |
|---|---|---|
| Features in file / in database | 52,471 / 52,471 | 720 / 720 |
| Dropped or repaired while cleaning | 0 | 0 |
| Coordinate / attribute mismatches | 0 / 0 | 0 / 0 |
| Invalid, empty, wrong SRID, duplicate keys | 0 | 0 |
| Total length, file = database | 8,218.489 km | 519.957 km |
| Within 500 m of the CBD, database = file | 311 | 5 |
| Nearest feature to the CBD, database = file | 21.192 m | 154.282 m |
| Second run | 0 inserted, 0 updated, 52,471 unchanged | 0 inserted, 0 updated, 720 unchanged |

Tests: `tests/pipeline/test_load_prepare.py` (cleaning, no database) and `tests/db/test_ingestion.py` (needs
`AQUAMAP_TEST_DATABASE_URL`): first load, re-run, in-place update, stale-score removal, pruning, duplicate rejection,
index re-creation, verification catching edited and deleted rows, and rollback of a load that fails half-way.

## Limitations

- OSM data had no invalid lines in this download, so the repair path is proven by synthetic tests only.
- The key is global: loading the same OSM feature under another layer name moves the row to that layer.
- A re-run advances the id sequence even when nothing is inserted (normal for `ON CONFLICT`); ids have gaps.
- `surface` is NULL on 89 % of roads and `name` on 88 %; that is how OSM is tagged in Nairobi.
