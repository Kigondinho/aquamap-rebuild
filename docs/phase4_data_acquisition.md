# Phase 4 — Geospatial data acquisition

Scripts download Nairobi County's roads, waterways and elevation, keep the originals untouched in
`data/raw/`, write cleaned copies to `data/processed/`, validate both and record where every file came from.

## Run it

```bash
pip install -r requirements.txt -r requirements-pipeline.txt
python -m pipeline.acquire all            # boundary + roads + rivers + DEM, process, validate
python -m pipeline.acquire fetch dem      # one step (boundary | roads | rivers | dem | all)
python -m pipeline.acquire process        # raw -> processed (EPSG:32737)
python -m pipeline.acquire validate       # re-check what is on disk
python -m pipeline.acquire all --force    # download again
```

Exit status 0 means everything was fetched and passed every blocking check; 1 means something failed, and the
failures are listed at the end (one failing step does not hide the others). Re-running skips files that already
exist, so an interrupted run resumes. Downloads are written to a `.part` file and renamed only when complete.

Needs internet access to `nominatim.openstreetmap.org`, an Overpass server (OSMnx default) and
`elevation-tiles-prod.s3.amazonaws.com`. If your network blocks one, the error names the host.
Fallbacks that keep provenance honest:

- `--boundary-file nairobi.gpkg` uses a polygon you supply instead of the Nominatim lookup.
- `--dem-file my.tif --dem-source "USGS EarthExplorer SRTM 1 Arc-Second Global, public domain"` adopts a DEM you
  downloaded yourself (the source text is mandatory and stored in the provenance record).

## What is acquired

| Dataset | Source | Method | Raw file | Processed file |
|---|---|---|---|---|
| County boundary | OpenStreetMap (Nominatim), query `Nairobi County, Kenya` | `osmnx.geocode_to_gdf` | `data/raw/boundary/nairobi_county_boundary.gpkg` | — |
| Roads | OpenStreetMap, Overpass API | `osmnx.features_from_polygon(boundary, {"highway": True})`, lines only | `data/raw/osm/nairobi_roads.gpkg` | `data/processed/roads.gpkg` |
| Rivers / drainage | OpenStreetMap, Overpass API | `features_from_polygon(boundary, {"waterway": [river, stream, canal, drain]})`, lines only | `data/raw/rivers/nairobi_rivers.gpkg` | `data/processed/rivers.gpkg` |
| Elevation | SRTM 1 arc-second (about 30 m), via Mapzen/Tilezen "skadi" tiles in the AWS Open Data bucket `elevation-tiles-prod` | HTTP download of tiles `S02E036` and `S02E037`, merged and clipped to the county plus a 0.02° margin | `data/raw/dem/tiles/*.hgt.gz` (originals) and `data/raw/dem/nairobi_srtm_30m.tif` (mosaic) | — |

Roads are saved with **all** OSM tag columns exactly as returned (raw). The processed copy keeps `element, osm_id,
name, highway, surface` (waterways: `waterway`), and only does mechanical clean-up: null/empty geometries
dropped, invalid lines repaired where possible (Phase 6; otherwise dropped), repeated OSM ids dropped, MultiLineStrings split, reprojected to **EPSG:32737**. Each
drop is counted in the file's provenance record. No road-class filtering happens here (open assumption A3).
Drainage `drain` is included in addition to river/stream/canal (Phase 1 design); the earlier prototype omitted it.

## Provenance

Every data file has a sidecar `<file>.provenance.json` with: source name and URL, licence, required attribution,
method and parameters (including the exact OSM tag filter and the SRTM tile list), **retrieval time (UTC)**,
SHA-256 and size of each file, feature count, CRS, limitations, processing counts and library versions. The
`validate` step re-hashes files and warns if a file changed after download. These small JSON files are committed;
the data files are not (see `.gitignore`). The pipeline loader of a later phase copies them into `spatial_layers`.

## Licences and attribution

| Data | Licence | Attribution to display |
|---|---|---|
| OpenStreetMap roads, waterways, boundary | Open Database Licence (ODbL) 1.0 | © OpenStreetMap contributors |
| SRTM | Public domain (NASA/NGA, distributed by USGS); USGS asks for credit | SRTM terrain data courtesy of the U.S. Geological Survey |

The SRTM statement and attribution text come from the Tilezen/joerd documentation (`docs/attribution.md`,
`docs/data-sources.md`, `docs/formats.md`), which describes the skadi tiles as SRTMGL1-format, gzip-compressed,
1°×1°, big-endian int16, voids = −32768, EGM96 heights. The web map footer must show both credits.
If you republish derived OSM data, ODbL share-alike applies; read the licence before doing so.

## Validation

`python -m pipeline.acquire validate` writes `data/processed/validation_report.json` and prints a PASS/FAIL line per
dataset. An **error** blocks (do not proceed); a **warning** needs a human look.

| Check | Roads / rivers | DEM |
|---|---|---|
| CRS | present; EPSG:4326 raw, EPSG:32737 processed | present; geographic or projected, ~30 m cells |
| Geographic coverage | within Nairobi bbox ±0.03°; ≤1 % of features more than 2 km outside the county polygon | extent covers the whole county polygon; void cells inside county ≤ 1 % |
| Geometry types | LineString/MultiLineString only | one band |
| Empty / null / invalid | each counted; any = error | NaN/void share (>0.5 % warn, >5 % error) |
| Duplicates | repeated OSM id = error; identical geometry = warning | — |
| Missing attributes | `highway`/`waterway` null = error; `name`, `surface` percentages reported (surface >50 % missing = warning) | nodata value declared |
| Completeness | ≥ 10,000 roads, ≥ 50 waterways; roads: share of 1 km county cells containing a road (<50 % error, <70 % warn) | elevation range plausible (error outside −100…6000 m, warning outside ~1300–2700 m) |
| Boundary | area 550–850 km² (Nairobi County is about 700 km²) and located near Nairobi | — |

The thresholds are sanity checks to catch a wrong place, a truncated download or the wrong layer. They are not
proof that OpenStreetMap is complete.

## Limitations

- **OSM completeness and currency.** Volunteer data; coverage and tagging differ by area. A download is a
  snapshot at the retrieval time recorded in the provenance file.
- **Surface is sparsely tagged.** In the earlier prototype extract about 89 % of roads had no `surface`. Missing
  values are kept as missing and the scoring step uses a documented default; they are never guessed here.
- **Road classes.** Everything tagged `highway` is kept, including footways, paths, tracks and steps; which
  classes count as "roads" is decision A3, applied later.
- **Waterways are centrelines**, not flood extents; piped or seasonal channels may be absent or mapped as `drain`.
- **SRTM** dates from February 2000, is a surface model (trees and buildings included), has vertical error of a few
  metres and does not resolve small channels. Voids stay NODATA.
- **County boundary** comes from OSM, not the official survey line; it can differ by tens of metres.
- **Network access.** Nominatim and Overpass are shared public services with usage policies and rate limits:
  fetch once, then reuse the raw files. OSMnx caches responses in `data/cache/`.

## Verification status (honest record)

What was done when this phase was built, in an environment whose network policy **blocks** Nominatim, Overpass,
Geofabrik and the AWS elevation bucket:

- **Not done:** a live download from those sources. The acquisition code path was exercised only against mocked
  OSMnx and mocked HTTP responses with synthetic data (`tests/pipeline/test_acquisition.py`), and a real attempt
  failed cleanly with the clear error report described above. **Run `python -m pipeline.acquire all` on your own
  machine and read the report before trusting the data.** The skadi URL layout (`/skadi/S02/S02E036.hgt.gz`)
  comes from the documented tile format and was not fetched here.
- **Done:** the validators and the raw→processed step were run on your existing prototype files
  (`nairobi_roads.gpkg`, `nairobi_rivers.gpkg`, `nairobi_srtm_30m.tif`) against an independent county polygon
  (geoBoundaries ADM1 "Nairobi", 705 km²). Results: roads 51,947 LineStrings and rivers 454 LineStrings pass (no
  null/empty/invalid geometries, no duplicate ids or geometries, no missing `highway`/`waterway`, 99 % of 1 km cells
  contain a road); the DEM (1620×1080 cells, 30.9 m, 1,435–2,272 m, no voids) **fails the coverage check**: it
  stops at 37.0999°E and 1.1499°S, so about 0.6 km² of the county's east edge is outside it, and 10 roads and 2
  waterways are partly outside it. The prototype files have no provenance records, so they were not adopted into
  `data/raw/`.
