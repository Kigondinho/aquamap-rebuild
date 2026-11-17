// Road information panel, from GET /api/v1/roads/{road_id}. Shows exactly what is stored for the road;
// a value the analysis does not have is shown as missing, never filled in.

import { apiFetch, isAbort } from "./api.js";
import { tierClass } from "./risk-config.js";
import { clear, el, fmt, titleCase } from "./ui.js";

const FLAG_TEXT = {
  surface_missing: "OpenStreetMap has no surface type for this road, so the surface factor uses the neutral middle score.",
  surface_unrecognised: "The surface type is not one the model recognises, so the surface factor uses the neutral middle score.",
  no_dem: "No elevation data under this road, so it has no score.",
  no_slope: "No slope data under this road, so it has no score.",
  no_river: "No waterway is mapped in the data; river distance uses the lowest score.",
};
const FACTORS = [["elevation", "Elevation"], ["slope", "Slope"], ["river", "River proximity"], ["surface", "Road surface"]];

export class RoadPanel {
  /** @param onShown(geometry) and onClose() let the map highlight and clear the selected road */
  constructor(container, { onShown, onClose, scaleMax }) {
    this.container = container;
    this.onShown = onShown;
    this.onClose = onClose;
    this.scaleMax = scaleMax;
    this.controller = null;
    this.currentId = null;
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !this.container.hidden) this.close();
    });
  }

  head(title) {
    return el("div", { class: "panel-head" },
      el("h2", { id: "road-title", tabindex: "-1" }, title),
      el("button", { type: "button", class: "icon", "aria-label": "Close road information", onclick: () => this.close() }, "×"));
  }

  /** @param note optional sentence shown under the title (for example, how the road was found) */
  async open(roadId, { note = null, fit = false } = {}) {
    this.controller?.abort();
    this.controller = new AbortController();
    this.currentId = roadId;
    this.container.hidden = false;
    clear(this.container).append(this.head("Road information"),
      el("p", { class: "state" }, el("span", { class: "spinner", "aria-hidden": "true" }), "Loading road details…"));
    try {
      const feature = await apiFetch(`/roads/${roadId}`, { signal: this.controller.signal });
      this.render(feature, note);
      this.onShown(feature.geometry, { fit });
    } catch (error) {
      if (isAbort(error)) return;
      const text = error.status === 404 ? "This road is no longer in the database." : `Could not load this road: ${error.message}`;
      clear(this.container).append(this.head("Road information"),
        el("p", { class: "state error" }, text, " ",
          error.status === 404 ? null : el("button", { type: "button", class: "link", onclick: () => this.open(roadId, { note, fit }) }, "Try again")));
    }
  }

  close() {
    this.controller?.abort();
    this.currentId = null;
    this.container.hidden = true;
    clear(this.container);
    this.onClose();
  }

  render(feature, note) {
    const p = feature.properties;
    const title = p.name || "Unnamed road";
    const identifier = p.osm_id ? `OpenStreetMap way ${p.osm_id}` : `Road ${p.id}`;
    const value = (text) => el("dd", {}, text);
    const missing = (text) => el("dd", { class: "missing" }, text);

    const parts = [this.head(title), el("p", { class: "id" }, p.name ? identifier : `${identifier} (no name in OpenStreetMap)`)];
    if (note) parts.push(el("p", { class: "msg info" }, note));

    if (p.scored) {
      parts.push(
        el("div", { class: "score" },
          el("span", { class: "number" }, fmt.score(p.vulnerability_index)),
          el("span", { class: "of" }, `IVI, out of ${Number(this.scaleMax).toFixed(0)}`),
          el("span", { class: `chip ${tierClass(p.risk_category)}` }, p.risk_category)),
        el("p", { class: "hint" }, "Higher means more vulnerable. A relative index, not a flood probability."));
    } else {
      parts.push(el("div", { class: "score" }, el("span", { class: "chip none" }, "Not scored")),
        el("p", { class: "hint" }, p.config_version === null
          ? "This road class is not part of the analysis (for example footways and paths)."
          : "The analysis could not score this road; see the notes below."));
    }

    parts.push(el("dl", { class: "facts" },
      el("dt", {}, "Road classification"), value(titleCase(p.highway_type)),
      el("dt", {}, "Surface type"), p.surface ? value(titleCase(p.surface)) : missing("Not recorded"),
      el("dt", {}, "IVI score"), p.scored ? value(fmt.score(p.vulnerability_index)) : missing("None"),
      el("dt", {}, "Vulnerability category"), p.scored ? value(p.risk_category) : missing("None"),
      el("dt", {}, "Elevation"), p.elevation_m !== null ? value(`${fmt.int(p.elevation_m)} m`) : missing("No data"),
      el("dt", {}, "Slope"), p.slope_pct !== null ? value(fmt.percent(p.slope_pct)) : missing("No data"),
      el("dt", {}, "Distance from nearest river"), p.river_dist_m !== null ? value(fmt.metres(p.river_dist_m)) : missing("No data"),
      el("dt", {}, "Length"), p.length_m !== null ? value(fmt.metres(p.length_m)) : missing("No data")));

    if (p.factor_scores) {
      parts.push(el("h3", {}, "How the score is built"));
      for (const [key, label] of FACTORS) {
        const score = p.factor_scores[key];
        const weight = p.weights?.[key];
        parts.push(el("div", { class: "factor" },
          el("span", {}, label),
          el("span", { class: "track", "aria-hidden": "true" },
            el("span", { style: `width:${score === null ? 0 : (score / this.scaleMax) * 100}%` })),
          el("span", { class: "val" }, score === null ? "—" : `${score} / ${Number(this.scaleMax).toFixed(0)}`),
          weight !== null && weight !== undefined
            ? el("span", { class: "weight" }, `Weight ${fmt.percent(weight * 100)}`) : null));
      }
    }

    if (p.data_flags?.length) {
      parts.push(el("h3", {}, "Notes on the data"),
        el("ul", { class: "flags" }, p.data_flags.map((flag) => el("li", {}, FLAG_TEXT[flag] ?? flag))));
    }
    if (p.computed_at) {
      parts.push(el("p", { class: "small" },
        `Analysed ${fmt.date(p.computed_at)} · model ${p.model_version} · configuration ${p.config_version}`
        + (p.terrain_samples ? ` · ${p.terrain_samples} terrain samples` : "")));
    }
    clear(this.container).append(...parts);
    this.container.scrollTop = 0;
    this.container.querySelector("#road-title").focus({ preventScroll: true });
  }
}
