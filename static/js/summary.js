// Summary statistics, from GET /api/v1/roads/summary. Every number shown is computed by the backend
// from the stored analysis; this file only formats it.

import { apiFetch, isAbort } from "./api.js";
import { getTierStyle, tierClass } from "./risk-config.js";
import { clear, el, fmt, setState } from "./ui.js";

let controller = null;

/**
 * @param params  filter parameters (road class and IVI range; categories are shown side by side)
 * @param meta    analysis metadata, for the analysis date
 */
export async function loadSummary(container, params, meta) {
  controller?.abort();
  controller = new AbortController();
  const stateLine = el("p", { class: "state" });
  clear(container).append(stateLine);
  setState(stateLine, "loading", "Loading statistics…");
  try {
    const stats = await apiFetch("/roads/summary", { params, signal: controller.signal });
    render(container, stats, meta, params);
  } catch (error) {
    if (isAbort(error)) return;
    setState(stateLine, "error", `Statistics are not available: ${error.message}`,
      { label: "Try again", run: () => loadSummary(container, params, meta) });
  }
}

function render(container, stats, meta, params) {
  clear(container);
  const scoredKm = stats.by_tier.reduce((sum, tier) => sum + tier.length_km, 0);
  const scoredSegments = stats.by_tier.reduce((sum, tier) => sum + tier.segments, 0);
  if (scoredSegments === 0) {
    container.append(el("p", { class: "state" }, meta.analysed
      ? "No scored roads match the selected road classes and IVI range."
      : "No roads have been analysed yet. Run the scoring pipeline to see statistics."));
    return;
  }
  container.append(el("div", { class: "stat" },
    el("div", { class: "label" }, "Scored road length"),
    el("div", { class: "value" }, fmt.km(scoredKm)),
    el("div", { class: "sub" }, `${fmt.int(scoredSegments)} road segments`)));
  for (const tier of stats.by_tier) {
    const share = scoredKm > 0 ? (tier.length_km / scoredKm) * 100 : 0;
    container.append(el("div", { class: "stat" },
      el("div", { class: "label" }, el("span", { class: `swatch ${tierClass(tier.tier)}`, "aria-hidden": "true" }), tier.tier),
      el("div", { class: "value" }, fmt.km(tier.length_km)),
      el("div", { class: "sub" }, `${fmt.percent(share)} of length · ${fmt.int(tier.segments)} segments`),
      el("div", { class: "bar", "aria-hidden": "true" },
        el("span", { style: `width:${share.toFixed(1)}%;background:${getTierStyle(tier.tier).color}` }))));
  }
  const filtered = params.highway || params.min_ivi !== undefined || params.max_ivi !== undefined;
  const notes = [filtered ? "County totals for the selected road classes and IVI range." : "Totals for all of Nairobi County."];
  if (stats.unscored_segments > 0) {
    notes.push(`${fmt.int(stats.unscored_segments)} other roads (${fmt.km(stats.unscored_length_km)}) have no score: `
      + "footways, paths and similar are not analysed.");
  }
  if (meta.analysis_date) notes.push(`Analysis of ${fmt.date(meta.analysis_date)}.`);
  container.append(el("p", { class: "stats-note" }, notes.join(" ")));
}
