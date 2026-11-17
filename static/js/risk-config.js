// How each vulnerability category LOOKS. Category names, score ranges and labels come from the API
// (GET /api/v1/meta/config); nothing here decides which category a road is in.
// Colour plus line weight and dash pattern, so categories are not told apart by colour alone.

export const TIER_STYLES = {
  Low:    { color: "#56B4E9", weight: 2,   dashArray: null,   opacity: 0.9 },
  Medium: { color: "#E69F00", weight: 3,   dashArray: null,   opacity: 0.9 },
  High:   { color: "#D55E00", weight: 4,   dashArray: "8 6",  opacity: 0.95 },
  Severe: { color: "#332288", weight: 5.5, dashArray: "2 7",  opacity: 1.0 },
};

const NEUTRAL_STYLE = { color: "#9aa5b1", weight: 2, dashArray: null, opacity: 0.7 };

/** Leaflet path options for a category name; unknown or missing categories get a neutral grey. */
export function getTierStyle(tier) {
  return TIER_STYLES[tier] ?? NEUTRAL_STYLE;
}

/** CSS class for swatches and chips: the category name, or "none" for a road without one. */
export function tierClass(tier) {
  return tier in TIER_STYLES ? tier : "none";
}

/** "2.0 – below 3.0" / "4.0 – 5.0" from a tier definition of the API. */
export function tierRange(tier, scaleMax) {
  const lower = Number(tier.lower_bound).toFixed(1);
  return tier.upper_bound === null ? `${lower} – ${Number(scaleMax).toFixed(1)}` : `${lower} – below ${Number(tier.upper_bound).toFixed(1)}`;
}
