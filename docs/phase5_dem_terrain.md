# Phase 5 — DEM preprocessing and terrain analysis

```bash
python -m pipeline.prepare_dem            # data/raw/dem/nairobi_srtm_30m.tif -> data/processed/
python -m pipeline.prepare_dem --force    # regenerate (output is bit-for-bit repeatable)
python -m pipeline.prepare_dem --raw-dem path/to/other.tif --resolution 30
```

Outputs (all EPSG:32737, 30 m, float32, nodata −9999, same grid, deflate-compressed GeoTIFF):

| File | Content |
|---|---|
| `data/processed/dem_32737.tif` | Elevation in metres |
| `data/processed/slope_pct_32737.tif` | Slope in **percent** (100 × tan of the slope angle) |
| `data/processed/*.provenance.json` | Source, resampling, parameters, limitations, checksums |
| `data/processed/dem_validation_report.json` | Result of every check below |

Exit status 0 = all blocking checks passed, 1 = failure (reasons printed). The raw DEM is never modified.

## Method

1. **Read and mask.** The declared nodata (SRTM void −32768) and any non-finite cell are masked, never treated as elevation.
2. **Reproject** to EPSG:32737 on a regular 30 m grid whose origin is a multiple of 30 m, so the grid is identical on every run and
   a different DEM tile reprojects onto the same lattice. Output extent is the reprojected extent snapped outward; the rotated
   corners (UTM grid convergence) are nodata.
3. **Resampling: bilinear.** Elevation is continuous, so nearest-neighbour would give stair-steps that distort slope and cubic can
   overshoot. GDAL leaves nodata cells out of the bilinear kernel, so voids are not blended into valid elevation. The source is
   about 30.9 m at Nairobi, so the change of cell size is small.
4. **Metadata.** CRS, geotransform and nodata are written; tags record resampling, source file, units and vertical datum (EGM96).
5. **Slope: Horn's 3×3 method**, `100·sqrt((dz/dx)² + (dz/dy)²)` with the true cell size in metres (same as `gdaldem slope -p`).
   A cell gets a slope only if its whole 3×3 window is valid **and the cell itself is valid**. Cells next to a void, next to the
   nodata border or on the raster edge are nodata, so a void cannot create a fake cliff. Slope is written on the very same grid as
   elevation, so the two rasters align cell for cell.

## Validation

Dimensions, bounds and transform equal between elevation and slope; CRS = EPSG:32737; cell size = 30 m; origin on the grid;
nodata declared; elevation range (error outside −100…6000 m, warning outside ~1300–2700 m); masked cells hold only nodata;
slope exists only where the full 3×3 window is valid (and everywhere it should); slope ≥ 0, finite, not "all ≤ 1" (which would
mean degrees or ratios instead of percent); statistics (min, max, mean, median, p95, p99) are reported; very steep maxima warn.

## Verification done

- Unit tests on planes with known slope (exactly 10 %, 100 %, combined directions), cell-size scaling, voids, edges, tiny
  arrays, alignment/percent/nodata-border failure detection and repeatability (`tests/pipeline/test_prepare_dem.py`, synthetic data).
- The test suite caught one real defect: Horn ignores the centre cell, so a void cell itself received a slope; fixed by masking it.
- Run on the DEM downloaded by Phase 4 on 2026-11-13 (SRTM tiles S02E036 + S02E037, 1729×1167 cells, no voids):
  output 1784×1198 cells, bounds 237870–291390 E, 9837930–9873870 N, elevation 1434–2324 m, 99.66 % valid (the rest is the
  rotated border); slope valid 99.38 %, min 0, median 5.67 %, mean 7.16 %, p95 18.33 %, p99 28.06 %, max 82.5 %.
  Every cell inside the county polygon (773,268 cells) has an elevation and a slope. Two runs give identical files (SHA-256).
  Compared with GDAL's `gdaldem slope -p -alg Horn` on the same elevation raster: the same 2,124,087 valid cells and a
  maximum difference of 0.0009 percentage points.

## Limitations

SRTM is a 2000-era surface model with metres of vertical noise, so slope on a 30 m grid is a coarse, noisy estimate and
smooths road embankments. Cells within one cell of a void or the raster edge have no slope; the scoring step must treat
"no slope" as missing data, not zero.
