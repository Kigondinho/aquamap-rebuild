// Road search by name, from GET /api/v1/roads/search.

import { apiFetch, isAbort } from "./api.js";
import { tierClass } from "./risk-config.js";
import { clear, debounce, el, fmt, setState } from "./ui.js";

const MIN_CHARS = 2;

/** @param onPick called with a search result feature when the user chooses it */
export function setupSearch({ onPick }) {
  const input = document.getElementById("search");
  const stateLine = document.getElementById("search-state");
  const list = document.getElementById("search-results");
  let controller = null;

  async function run() {
    controller?.abort();
    const text = input.value.trim();
    clear(list);
    if (!text) return setState(stateLine, null);
    if (text.length < MIN_CHARS) return setState(stateLine, "empty", `Type at least ${MIN_CHARS} characters.`);
    controller = new AbortController();
    setState(stateLine, "loading", "Searching…");
    try {
      const data = await apiFetch("/roads/search", { params: { q: text, limit: 10 }, signal: controller.signal });
      show(data, text);
    } catch (error) {
      if (isAbort(error)) return;
      setState(stateLine, "error", `Search failed: ${error.message}`, { label: "Try again", run });
    }
  }

  function show(data, text) {
    if (!data.features.length) return setState(stateLine, "empty", `No road name contains “${text}”.`);
    const page = data.pagination;
    setState(stateLine, page.has_more ? "empty" : null,
      `Showing the first ${page.returned} of ${fmt.int(page.total)} names. Type more to narrow the search.`);
    for (const feature of data.features) {
      const p = feature.properties;
      const scored = p.vulnerability_index !== null;
      list.append(el("li", {}, el("button", { type: "button", onclick: () => onPick(feature) },
        el("span", { class: "title" }, el("span", {}, p.name),
          el("span", { class: `chip ${tierClass(p.risk_category)}` }, scored ? p.risk_category : "Not scored")),
        el("span", { class: "meta" },
          `${p.segments} ${p.segments === 1 ? "segment" : "segments"} · ${fmt.metres(p.length_m)}`
          + (scored ? ` · highest IVI ${fmt.score(p.vulnerability_index)}` : "")))));
    }
  }

  input.addEventListener("input", debounce(run, 300));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); run(); }
    if (event.key === "Escape") { input.value = ""; run(); }
  });
}
