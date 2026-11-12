# Phase 7 — IVI risk calculation engine

```bash
python -m pipeline.score_roads                # measure, score and store every eligible road
python -m pipeline.score_roads --verify-only  # re-check the stored results, change nothing
python -m pipeline.score_roads --all-classes  # also score footways, paths, tracks ...
python -m pipeline.score_roads --new-config   # store the code defaults as a new active configuration
```

Needs Phase 5 (`data/processed/dem_32737.tif`, `slope_pct_32737.tif`) and Phase 6 (roads and rivers in PostGIS).
A database created before this phase is upgraded by `python -m app.db_tools init`.

## What the index is, and is not

The Infrastructure Vulnerability Index (IVI) is a **relative screening index** from 1 (least) to 5 (most vulnerable).
It ranks road segments by four static characteristics. It is **not** a flood probability, a flood depth, a forecast or a
measured flood risk: it uses no rainfall, drainage-capacity or flood-record data and has **not been validated against
observed flooding**. An IVI of 4 does not mean "80 % chance of flooding" and is not twice as dangerous as 2. This
statement is `core.ivi.INTERPRETATION_NOTE`; it is printed with every run and stored in each run record so the
interface can show it. The tier names (Low, Medium, High, Severe) describe position on this index only.

## Model

`IVI = (WE × E) + (WS × S) + (WD × D) + (WM × M)`

| Factor | Measured from | Weight |
|---|---|---|
| E, elevation | mean DEM elevation along the segment (m) | 0.35 |
| S, slope | mean terrain slope along the segment (%) | 0.20 |
| D, river proximity | distance from the segment to the nearest waterway (m) | 0.30 |
| M, road surface | OSM `surface` tag | 0.15 |

The weights are the documented baseline (P3, after Tariq et al. 2021) and sum to 1.0; the engine refuses any
configuration whose weights do not. Each factor is normalised to a class score 1–5, so the IVI is also 1–5. It is
rounded to three decimals and the tier is read from the rounded value, so the stored number and tier always agree.
This settles design question A2 (the scaffold had assumed a 0–1 scale): scores, database checks and the API's
`min_ivi` parameter are now 1–5.

## Thresholds

A value exactly on a boundary belongs to the class above it (1500 m scores 4, not 5).

| Score | Elevation (m) | Slope (%) | River distance (m) | Surface |
|---|---|---|---|---|
| 5 | < 1500 | < 2 | < 50 | unpaved, dirt, earth, ground, mud, gravel, sand, compacted, fine_gravel, pebblestone, clay, grass |
| 4 | 1500 – < 1600 | 2 – < 5 | 50 – < 150 | |
| 3 | 1600 – < 1700 | 5 – < 10 | 150 – < 300 | missing or unrecognised tag (flagged) |
| 2 | 1700 – < 1800 | 10 – < 15 | 300 – < 500 | paved, asphalt, concrete, paving_stones, sett, cobblestone, chipseal, bricks |
| 1 | ≥ 1800 | ≥ 15 | ≥ 500 | |

Tiers: **Low** < 2.0 ≤ **Medium** < 3.0 ≤ **High** < 4.0 ≤ **Severe** (four equal bands of the scale).

Why these values:

- **Elevation.** Five 100 m bands, the classes of the approved methodology. Water collects on the low ground of the
  Nairobi and Ngong river valleys in the east and south-east of the county. Design question A11 asked whether absolute
  bands separate Nairobi's roads at all; they do: the eligible roads lie between 1,458 and 1,944 m and fall
  5 % / 20 % / 38 % / 23 % / 13 % into the five classes. Elevation is a county-wide proxy for position in the catchment,
  not a local measure; it does not see a local dip on high ground.
- **Slope.** Flat ground drains slowly. The cut-offs follow common terrain classes (under 2 % level, 2–5 % gentle,
  5–10 % moderate, 10–15 % strong, above 15 % steep). Shares: 5 % / 43 % / 40 % / 9 % / 3 %.
- **River distance.** 50 m is on the scale of a riparian reserve and of the positional error of the data; 500 m is
  beyond the direct reach of overbank flow from the small rivers of the county. Shares: 7 % / 12 % / 17 % / 18 % / 46 %.
- **Surface.** Unsealed roads erode and become impassable when wet, so they score 5. Sealed roads still flood, so they
  score 2, not 1. Unknown scores the midpoint 3.

All thresholds are data, not code: they are stored in `scoring_config` with a version number, and each result records
the version that produced it. Changing them means storing a new version and re-running; none of them has been
calibrated against flood records, and a sensitivity analysis of the weights (for example ±0.05) is still to be done.

## Measurements

All in EPSG:32737 (metres); inputs in any other CRS are refused.

- **Terrain sampling.** Each segment is sampled at points at most 30 m apart along the line (the raster cell size),
  always including both ends and at least one interior point: 320,255 points for 47,619 roads. Each point takes the
  value of the cell it falls in. The segment's elevation and slope are the **means of its valid samples**; the number
  of samples is stored with the result (`terrain_samples`). Slope is the slope of the ground under the road from the
  Phase 5 raster, not the gradient of the carriageway.
- **River distance.** Shortest planar distance from the whole road line (not its centroid) to the nearest waterway
  line, 0 where they cross. Checked against PostGIS (`ST_Distance` with a nearest-neighbour search) on 300 random
  roads: largest difference 9 × 10⁻¹⁰ m.

## Missing data

Nothing missing is replaced by an invented measurement. Measurements stay NULL; what happens to the score is fixed
and visible in `data_flags`:

| Case | Outcome | Flag |
|---|---|---|
| No valid elevation sample | No IVI and no tier (NULL). Known factor scores are still stored. | `no_dem` |
| No valid slope sample | No IVI and no tier (NULL). | `no_slope` |
| Some samples on nodata | Mean of the valid samples only. | — |
| No waterway in the data at all | D = 1 (nothing nearby is the farthest class). | `no_river` |
| `surface` tag absent | M = 3 (scale midpoint). | `surface_missing` |
| `surface` tag not in the table | M = 3. | `surface_unrecognised` |
| Negative slope or distance, infinite value | Error; the run stops and stored results are untouched. | — |

Terrain carries 55 % of the weight, so a road without it gets no index instead of a partial one. The surface rule
matters in practice: **89.6 % of the scored roads have no `surface` tag**, so for them M is the neutral 3 and the
surface factor does not separate them. That is a property of OpenStreetMap in Nairobi, shown by the flag, not hidden.

## Storage and metadata

Results go to `road_vulnerability`, one current row per road, written in a single transaction (upsert; results of roads
that are no longer eligible are removed). Each row holds the three measurements, the four factor scores, the IVI, the
tier, the flags and:

| Column | Meaning |
|---|---|
| `model_version` | Version of the calculation (`ivi-wlc-1.0`); bumped when formula, sampling or missing-data rules change |
| `config_version` | Version of weights and thresholds used (`scoring_config`) |
| `run_id` | The run (`pipeline_runs`): start, finish, status, counts and a JSON summary with weights, raster checksums, eligible classes, sample step and the interpretation note |
| `computed_at` | Analysis date and time |
| `terrain_samples` | Number of points sampled along the segment |

A run that fails is recorded as `failed` with the reason, and the previous results stay as they were. After writing,
every stored IVI is recomputed inside PostgreSQL from its factor scores and the stored weights, and every tier from the
thresholds; any mismatch aborts the run. `data/processed/scoring_report.json` holds the run summary.

**Eligible roads** are the vehicular classes of design decision A3 (motorway to residential, unclassified, service and
the link classes). Footways, paths, tracks, steps, cycleways and misspelt classes are not scored and show no result.

## Result on the data of 2026-11-13

52,471 roads in the database, 47,619 eligible, 4,852 not eligible by class. All 47,619 received an index.

| Tier | Segments | Length |
|---|---|---|
| Low | 4,345 | 669 km |
| Medium | 27,641 | 3,715 km |
| High | 14,940 | 2,708 km |
| Severe | 693 | 176 km |

Index: minimum 1.15, median 2.70, mean 2.75, maximum 4.70. Flags: `surface_missing` 42,684, `surface_unrecognised` 8.
The highest-ranked named roads are low-lying stretches next to rivers in the east (for example parts of the Eastern
Bypass and Kangundo Road at about 1,490–1,500 m, 6–26 m from a waterway). That is plausible, and it is not evidence
that the index predicts flooding.

## Tests

- `tests/unit/test_ivi.py` (pure, no files or database): weights; 28 class-boundary cases; ten hand-calculated
  IVI values; "one class up = that factor's weight"; every class combination stays on the scale; tier boundaries;
  a floating-point case that must land exactly on 4.0; every missing-data case; invalid configurations.
- `tests/pipeline/test_compute_metrics.py` (synthetic rasters and lines with known answers): sample spacing, means
  along a gradient and across two slope zones, nodata left out of means, segments with no valid sample, river
  distance from the whole line, and an end-to-end example worked by hand.
- `tests/db/test_scoring.py` (PostGIS): stored values and metadata, NULL handling, re-scoring without duplicates,
  removal of stale results, verification catching edited rows, configuration round trip, scale checks.

## Limitations

- Not validated against flood observations; class thresholds and weights are adopted, not calibrated.
- Step classes: a road at 1,499 m and one at 1,501 m differ by a whole elevation class.
- SRTM is a year-2000 surface model with metres of vertical noise at 30 m; it does not resolve embankments,
  cuttings, bridges or culverts. A bridge over a river scores as the river bank does.
- OSM waterways omit many drains and culverts; distance is planar, not along the flow path.
- Mean elevation hides a short dip in a long segment; OSM ways are not split at junctions.
- Rainfall, drainage condition, land cover and upstream catchment area are not in the model.
