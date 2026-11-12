"""Default scoring parameters and geographic constants (Phase 1, sections 5 and 6).

These are the *initial* values that seed the ``scoring_config`` table. At runtime
the active row in that table is the source of truth, not this module.

Several values are still awaiting confirmation (see docs/phase1_requirements_and_design.md,
section 11): weights (A1), elevation breakpoints (A11) and road classes (A3).
"""

from __future__ import annotations

from typing import Final

# --- Coordinate reference systems --------------------------------------------
STORAGE_SRID: Final[int] = 32737  # WGS 84 / UTM 37S: stored in PostGIS, metric (Nairobi is south of the equator)
API_SRID: Final[int] = 4326  # WGS 84 lon/lat: what Leaflet and GeoJSON clients use

# --- Service area: Nairobi County bounding box (min_lon, min_lat, max_lon, max_lat)
NAIROBI_BBOX: Final[tuple[float, float, float, float]] = (36.60, -1.45, 37.10, -1.15)

# --- Model identity ----------------------------------------------------------
# Bump when the calculation itself changes (formula, sampling, missing-data policy). Changes to weights
# or thresholds are a new ``scoring_config`` row instead; both are stored with every result.
MODEL_VERSION: Final[str] = "ivi-wlc-1.0"

# --- Score scale -------------------------------------------------------------
# Every factor is scored on 1..5 (5 = most vulnerable). Because the weights sum to 1, the IVI is on the
# same 1..5 scale.
SCORE_MIN: Final[float] = 1.0
SCORE_MAX: Final[float] = 5.0

# --- Risk tiers, lowest to highest -------------------------------------------
TIER_NAMES: Final[tuple[str, ...]] = ("Low", "Medium", "High", "Severe")

# Lower bounds of Medium, High and Severe on the 1-5 IVI scale: four equal bands of width 1.
DEFAULT_TIER_THRESHOLDS: Final[dict[str, float]] = {
    "Medium": 2.0,
    "High": 3.0,
    "Severe": 4.0,
}

# --- Weighted Linear Combination weights (proposal, Tariq et al. 2021) -------
DEFAULT_WEIGHTS: Final[dict[str, float]] = {
    "elevation": 0.35,
    "river": 0.30,
    "slope": 0.20,
    "surface": 0.15,
}

# --- Numeric factor breakpoints ----------------------------------------------
# For each factor: ``edges`` are ascending upper bounds, ``scores`` has one more entry than ``edges``.
# A value below edges[i] gets scores[i]; a value at or above the last edge gets scores[-1] (a value
# exactly on an edge belongs to the class above it). In all three factors the lowest bucket is the
# most vulnerable (low ground, short river distance, gentle slope). Justification of each ladder:
# docs/phase7_ivi_engine.md.
DEFAULT_BREAKPOINTS: Final[dict[str, dict[str, tuple[float, ...]]]] = {
    "elevation": {  # metres above sea level (mean along the segment)
        "edges": (1500.0, 1600.0, 1700.0, 1800.0),
        "scores": (5.0, 4.0, 3.0, 2.0, 1.0),
    },
    "river": {  # metres from the segment to the nearest river centreline
        "edges": (50.0, 150.0, 300.0, 500.0),
        "scores": (5.0, 4.0, 3.0, 2.0, 1.0),
    },
    "slope": {  # percent (mean along the segment)
        "edges": (2.0, 5.0, 10.0, 15.0),
        "scores": (5.0, 4.0, 3.0, 2.0, 1.0),
    },
}

# --- Surface susceptibility (OSM ``surface`` tag -> 1..5) --------------------
SURFACE_UNPAVED: Final[tuple[str, ...]] = (
    "unpaved", "dirt", "earth", "ground", "mud", "gravel", "sand",
    "compacted", "fine_gravel", "pebblestone", "clay", "grass",
)
SURFACE_PAVED: Final[tuple[str, ...]] = (
    "paved", "asphalt", "concrete", "paving_stones", "sett",
    "cobblestone", "unhewn_cobblestone", "chipseal", "bricks",
)
SURFACE_SCORE_UNPAVED: Final[float] = 5.0
SURFACE_SCORE_PAVED: Final[float] = 2.0   # sealed, but still floods: not the minimum
SURFACE_SCORE_UNKNOWN: Final[float] = 3.0  # missing or unrecognised tag: scale midpoint, always flagged
SURFACE_UNKNOWN_KEY: Final[str] = "*"      # key of the fallback score in a stored surface map

# --- Road classes kept from OSM (assumption A3, awaiting confirmation) -------
DEFAULT_HIGHWAY_CLASSES: Final[tuple[str, ...]] = (
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "service",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
)
