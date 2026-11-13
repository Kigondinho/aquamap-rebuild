# Phase 8 — Vulnerability classification and spatial queries

A database created before this phase is upgraded by `python -m app.db_tools init` (adds one SQL function, one
trigger, the `pg_trgm` extension and one index; keeps all data).

## Classification

Four categories on the 1–5 Infrastructure Vulnerability Index:

| Category | Range | Label shown to users |
|---|---|---|
| Low | 1.000 ≤ IVI < 2.000 | Lower relative vulnerability |
| Medium | 2.000 ≤ IVI < 3.000 | Moderate relative vulnerability |
| High | 3.000 ≤ IVI < 4.000 | High relative vulnerability |
| Severe | 4.000 ≤ IVI ≤ 5.000 | Highest relative vulnerability |

**Boundary behaviour.** The thresholds 2.0, 3.0 and 4.0 are the *lower* bounds of Medium, High and Severe. A value
exactly on a threshold belongs to the higher category: 3.999 is High, 4.000 is Severe. The IVI is stored with three
decimals and the category is always read from that stored value, so the two can never disagree by rounding. A road
without an IVI has no category.

**Why four equal bands.** The scale has no calibrated meaning (it has not been compared with flood records), so there
is no evidence for uneven cut-offs; equal bands are the plainest choice. With the current data they give 9 % Low,
58 % Medium, 31 % High and 1.5 % Severe of the scored roads. The categories rank roads against each other. They are
**not flood probabilities**.

**One rule in two places, kept in step.**

| Place | Implementation |
|---|---|
| Backend | `core.ivi.assign_tier(ivi, config)`; `core.ivi.tier_bounds(config)` lists the ranges |
| Database | `ivi_tier(ivi, thresholds)` in `db/init/005_classification.sql` |
| Stored thresholds | `scoring_config.tier_thresholds`, versioned; each result records its `config_version` |
| Guard | Trigger `road_vulnerability_tier_consistent`: a row whose category is not what its own configuration gives for its IVI is rejected, whoever writes it |
| For the interface | `GET /api/v1/meta/config` returns the ranges, the boundary rule, the weights and the "not a flood probability" note from the active configuration. The frontend never computes a category. |

A test compares the SQL function with the Python function on all 4,001 values a stored IVI can take, for the default
thresholds and for an uneven set.

## Queries

All in `app/services/roads_query.py`, with bound parameters only. Scores, categories and factor values come straight
from `road_vulnerability` joined on `road_id`; nothing is recomputed or re-classified on the way out. Geometry is
stored in EPSG:32737 and returned as GeoJSON in EPSG:4326 with six decimals (about 0.1 m).

| Need | Endpoint | Function | PostGIS / index used |
|---|---|---|---|
| Road segments in a map area | `GET /roads?bbox=` | `query_roads` | `geom && envelope` on the GiST index, then `ST_Intersects` |
| Filter by category | `tier=` (repeatable) | all list queries | `road_vulnerability_tier_idx` |
| Filter by minimum / maximum IVI | `min_ivi=`, `max_ivi=` | all list queries | `road_vulnerability_ivi_idx` |
| Filter by road classification | `highway=` (repeatable) | all list queries | `road_segments_class_idx` |
| Search by road name | `GET /roads/search?q=` | `search_roads` | trigram GIN index `road_segments_name_trgm` |
| Roads within a radius | `GET /roads/nearby?lat=&lon=&radius_m=` | `nearby_roads` | `ST_DWithin` on the GiST index, `ST_Distance` for ordering |
| One road in detail | `GET /roads/{road_id}` | `get_road` | primary keys |
| Totals per category | `GET /roads/summary` | `road_stats` | as above |

Behaviour worth knowing:

- **Coordinates.** Requests use longitude/latitude. The box or point is transformed once to EPSG:32737, so the stored
  geometry is compared directly, the spatial index is used, and all distances are metres.
- **Bounding box.** `minLon,minLat,maxLon,maxLat`. It must overlap Nairobi County and is clipped to the county envelope
  plus 0.05°. A road is returned if any part of it is inside the box.
- **Filters** combine with AND; several values of `tier` or `highway` mean "any of these". `min_ivi` and `max_ivi` are
  both **inclusive**; `min_ivi` greater than `max_ivi` is an error. Any category or IVI filter leaves out roads that
  have no IVI. Road classes are matched exactly (OSM values are lower case).
- **Order and limit.** Highest IVI first, roads without an IVI last. If more roads match than `limit`, the highest
  are kept and the response says `"truncated": true`.
- **Radius.** Distance is from the point to the nearest part of the line, not to its centre. A road exactly
  `radius_m` away is included. Results are nearest first with `distance_m`; radius is at most 2,000 m.
- **Name search.** Case-insensitive; at least two characters; names starting with the text come first, then names
  containing it. `%` and `_` are ordinary characters. One result per name: all segments with that name, their combined
  geometry, a `bbox` to zoom to, segment count, total length, and the highest stored IVI among them with the category
  stored on that same segment. Different roads that share a name (for example two "Church Road") appear as one result.
- **Detail.** The three measurements, four factor scores, weights and thresholds of the configuration used, flags,
  model version, run and analysis date. `scored` is false when the road is not eligible, not yet analysed, or has no
  terrain data.
- **Geometry** is the stored line exactly, unless the map passes `zoom` below 15; then it is simplified (5 m at zoom
  13–14, 20 m at 11–12, 60 m below) to keep county-wide responses small. Scores are never affected.

Access: every `/roads` and `/meta` route requires a signed-in user. Authentication is a later phase, so over HTTP
these routes still answer 501 for now; the tests exercise them with a stand-in user.

## Measured on the loaded data (52,471 roads)

| Query | Time | Plan |
|---|---|---|
| Map view of the city centre, 5,000 roads returned | about 200 ms (mostly building GeoJSON) | GiST bitmap scan |
| All Severe roads in the county (693) | 30 ms | category index |
| Primary and trunk roads with IVI 3.5–4.0 | 20 ms | IVI and class indexes |
| Roads within 500 m of the CBD (311) | 11 ms | GiST via `ST_DWithin` |
| Name search "bypass" (105 segments, 7 names) | 7 ms | trigram index |
| One road | under 1 ms | primary keys |

## Tests

- `tests/db/test_classification.py`: SQL and Python agree on every storable value; boundary values; the trigger
  rejects contradicting inserts and updates; categories follow the row's own configuration.
- `tests/db/test_road_queries.py`: a small synthetic network with known answers for every filter, including values
  exactly on a threshold and roads 499.9 m and 500.1 m from the point; 150 random lines where the radius and
  bounding-box answers must equal an independent pyproj + Shapely calculation (distances to a millimetre); returned
  values and coordinates compared with the stored rows; the HTTP layer including bad requests.
- `tests/unit/test_roads_query_parsing.py`: bounding-box parsing, range checks, wildcard escaping, category ranges.

## Limitations

- Substring search finds text inside names only; it does not correct spelling or match alternative names.
- A road counts toward `/roads/summary` with its whole length when any part of it is inside the box.
- The export endpoint, authentication and the admin endpoints are not part of this phase.
