// Filter controls: vulnerability category, IVI range and road classification.
// The choices themselves come from the API: categories from /meta/config, road classes (with their
// real counts) and the score scale from /analysis/metadata.

import { getTierStyle, tierClass, tierRange } from "./risk-config.js";
import { clear, debounce, el, fmt, titleCase } from "./ui.js";

// Display order of road classes, most important first. Classes not listed here follow, largest first.
const CLASS_ORDER = ["motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
  "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street", "service"];

export class Filters {
  /** @param onChange called (debounced) with the new state whenever a filter changes */
  constructor({ tiers, classes, scaleMin, scaleMax, onChange }) {
    this.tierDefs = tiers;
    this.classDefs = classes
      .filter((item) => item.with_index > 0)
      .sort((a, b) => rank(a.highway_type) - rank(b.highway_type) || b.segments - a.segments);
    this.scaleMin = scaleMin;
    this.scaleMax = scaleMax;
    this.emit = debounce(() => onChange(this.state()), 250);
    this.minInput = document.getElementById("ivi-min");
    this.maxInput = document.getElementById("ivi-max");
    this.renderTiers();
    this.renderClasses();
    this.setupRange();
    document.getElementById("classes-all").addEventListener("click", () => this.setAllClasses(true));
    document.getElementById("classes-none").addEventListener("click", () => this.setAllClasses(false));
    document.getElementById("reset-filters").addEventListener("click", () => this.reset());
  }

  renderTiers() {
    const box = clear(document.getElementById("tier-filters"));
    for (const tier of this.tierDefs) {
      box.append(el("label", { class: "check", title: tier.plain_label },
        el("input", { type: "checkbox", name: "tier", value: tier.tier, checked: true, onchange: this.emit }),
        el("span", { class: `swatch ${tierClass(tier.tier)}`, "aria-hidden": "true" }),
        el("span", {}, tier.tier),
        el("span", { class: "count" }, tierRange(tier, this.scaleMax))));
    }
  }

  renderClasses() {
    const box = clear(document.getElementById("class-filters"));
    if (!this.classDefs.length) {
      box.append(el("p", { class: "state" }, "No road classes have been scored yet."));
      return;
    }
    for (const item of this.classDefs) {
      box.append(el("label", { class: "check" },
        el("input", { type: "checkbox", name: "highway", value: item.highway_type, checked: true, onchange: this.emit }),
        el("span", {}, titleCase(item.highway_type)),
        el("span", { class: "count", title: "Scored road segments in the county" }, fmt.int(item.with_index))));
    }
  }

  setupRange() {
    for (const input of [this.minInput, this.maxInput]) {
      input.min = this.scaleMin;
      input.max = this.scaleMax;
    }
    this.minInput.value = this.scaleMin;
    this.maxInput.value = this.scaleMax;
    // Keep minimum <= maximum by moving the other handle along.
    this.minInput.addEventListener("input", () => {
      if (Number(this.minInput.value) > Number(this.maxInput.value)) this.maxInput.value = this.minInput.value;
      this.showRange();
      this.emit();
    });
    this.maxInput.addEventListener("input", () => {
      if (Number(this.maxInput.value) < Number(this.minInput.value)) this.minInput.value = this.maxInput.value;
      this.showRange();
      this.emit();
    });
    this.showRange();
  }

  showRange() {
    document.getElementById("ivi-min-value").textContent = Number(this.minInput.value).toFixed(1);
    document.getElementById("ivi-max-value").textContent = Number(this.maxInput.value).toFixed(1);
  }

  setAllClasses(checked) {
    document.querySelectorAll('#class-filters input[name="highway"]').forEach((box) => { box.checked = checked; });
    this.emit();
  }

  reset() {
    document.querySelectorAll('#tier-filters input, #class-filters input').forEach((box) => { box.checked = true; });
    this.minInput.value = this.scaleMin;
    this.maxInput.value = this.scaleMax;
    this.showRange();
    this.emit();
  }

  /** Current selection, plus `nothing`: a reason when the selection cannot match any road. */
  state() {
    const checked = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((box) => box.value);
    const tiers = checked("tier");
    const classes = checked("highway");
    const min = Number(this.minInput.value);
    const max = Number(this.maxInput.value);
    let nothing = null;
    if (!tiers.length) nothing = "Select at least one vulnerability category.";
    else if (!classes.length) nothing = "Select at least one road classification.";
    return { tiers, classes, allClasses: classes.length === this.classDefs.length, min, max, nothing };
  }

  /** Query parameters for the list endpoints. Filters left at "everything" are not sent. */
  params({ includeTiers = true } = {}) {
    const s = this.state();
    return {
      tier: includeTiers ? s.tiers : undefined,          // always sent: the map shows scored roads
      highway: s.allClasses ? undefined : s.classes,
      min_ivi: s.min > this.scaleMin ? s.min : undefined,
      max_ivi: s.max < this.scaleMax ? s.max : undefined,
    };
  }
}

function rank(highwayType) {
  const index = CLASS_ORDER.indexOf(highwayType);
  return index === -1 ? CLASS_ORDER.length : index;
}

/** Legend: one row per category with its line sample, name and score range, straight from the API. */
export function renderLegend(container, meta) {
  clear(container);
  const list = el("ul");
  for (const tier of [...meta.tiers].reverse()) {
    const style = getTierStyle(tier.tier);
    list.append(el("li", { title: tier.plain_label },
      el("span", { class: `swatch ${tierClass(tier.tier)}`, "aria-hidden": "true", style: `border-top-color:${style.color}` }),
      el("span", {}, tier.tier),
      el("span", { class: "range" }, tierRange(tier, meta.scale_max))));
  }
  container.append(el("h2", {}, "Vulnerability (IVI)"), list,
    el("p", { class: "note" }, "Relative index, 1 to 5. Not a flood forecast or a flood probability."));
  container.hidden = false;
}
