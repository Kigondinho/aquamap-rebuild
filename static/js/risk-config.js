// Presentation styles per risk tier. Tier names and thresholds come from the API
// (GET /api/v1/meta/config); this file only decides how each tier looks.
// Colour plus line weight and dash pattern, so the map is not colour-only.

export const TIER_ORDER = ["Low", "Medium", "High", "Severe"];

export const TIER_STYLES = {
  Low:    { color: "#56B4E9", weight: 2,   dashArray: null,   opacity: 0.85 },
  Medium: { color: "#E69F00", weight: 3,   dashArray: null,   opacity: 0.90 },
  High:   { color: "#D55E00", weight: 4.5, dashArray: "8, 6", opacity: 0.95 },
  Severe: { color: "#332288", weight: 6,   dashArray: "2, 6", opacity: 1.0 },
};

const NEUTRAL_STYLE = { color: "#9aa5b1", weight: 2, dashArray: null, opacity: 0.7 };

/** Leaflet path options for a tier name; unknown or missing tiers get a neutral grey. */
export function getTierStyle(tier) {
  return TIER_STYLES[tier] ?? NEUTRAL_STYLE;
}
