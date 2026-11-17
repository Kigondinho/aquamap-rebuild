// Dashboard entry point: loads who is signed in and the analysis metadata, then wires the map, filters,
// statistics, search, nearby roads and the road panel together. All data comes from the API.

import { apiFetch } from "./api.js";
import { Filters, renderLegend } from "./filters.js";
import { createMap, RoadLayer } from "./map.js";
import { setupNearby } from "./nearby.js";
import { RoadPanel } from "./popup.js";
import { setupSearch } from "./search.js";
import { loadSummary } from "./summary.js";
import { clear, el, setState } from "./ui.js";

const sidebar = document.getElementById("sidebar");
const toggle = document.getElementById("toggle-sidebar");
const statsBox = document.getElementById("stats");

function setSidebar(open) {
  sidebar.classList.toggle("open", open);
  toggle.setAttribute("aria-expanded", String(open));
}
toggle.addEventListener("click", () => setSidebar(!sidebar.classList.contains("open")));
const closeSidebarOnSmallScreens = () => { if (window.matchMedia("(max-width: 860px)").matches) setSidebar(false); };

document.getElementById("sign-out").addEventListener("click", async () => {
  try {
    await apiFetch("/auth/logout", { method: "POST", redirectOn401: false });
  } finally {
    window.location.assign("/login");
  }
});

const map = createMap("map");
window.addEventListener("resize", () => map.invalidateSize());

async function start() {
  let user, meta, analysis;
  try {
    [user, meta, analysis] = await Promise.all([
      apiFetch("/auth/me"), apiFetch("/meta/config"), apiFetch("/analysis/metadata")]);
  } catch (error) {
    // Without the category definitions nothing can be drawn correctly: say so and offer a retry.
    const line = el("p", { class: "state error" });
    clear(statsBox).append(line);
    setState(line, "error", `The dashboard could not start: ${error.message}`, { label: "Try again", run: () => window.location.reload() });
    for (const id of ["tier-filters", "class-filters"]) clear(document.getElementById(id));
    return;
  }
  document.getElementById("user-name").textContent = user.full_name;
  renderLegend(document.getElementById("legend"), meta);

  let roads, nearby;
  const panel = new RoadPanel(document.getElementById("road-panel"), {
    scaleMax: meta.scale_max,
    onShown: (geometry, options) => roads.showSelection(geometry, options),
    onClose: () => roads.clearSelection(),
  });
  const filters = new Filters({
    tiers: meta.tiers, classes: analysis.coverage.road_classes, scaleMin: meta.scale_min, scaleMax: meta.scale_max,
    onChange: () => refresh(),
  });
  const listParams = () => {
    const state = filters.state();
    return state.nothing ? { nothing: state.nothing } : filters.params();
  };
  roads = new RoadLayer(map, {
    statusEl: document.getElementById("map-status"),
    panelEl: document.getElementById("road-panel"),
    getParams: listParams,
    onSelect: (roadId, latlng) => {
      if (nearby.takeClick(latlng)) return;
      panel.open(roadId);
    },
  });
  nearby = setupNearby({
    map, workspace: document.getElementById("workspace"), getParams: listParams, onNeedMap: closeSidebarOnSmallScreens,
    onPick: (feature) => { closeSidebarOnSmallScreens(); panel.open(feature.properties.id, { fit: true }); },
  });
  setupSearch({
    onPick: (feature) => {
      const p = feature.properties;
      closeSidebarOnSmallScreens();
      const [west, south, east, north] = p.bbox;
      document.getElementById("road-panel").hidden = false;   // so the view is fitted beside the panel, not under it
      roads.fitTo([[south, west], [north, east]]);
      panel.open(p.id, {
        note: p.segments > 1
          ? `“${p.name}” has ${p.segments} segments. This is the one with the highest score; click another segment on the map to see it.`
          : null,
      });
    },
  });

  function refresh() {
    roads.reload();
    nearby.refresh();
    const state = filters.state();
    if (state.classes.length === 0) {
      // No road class selected: nothing can match, so do not show unfiltered totals.
      clear(statsBox).append(el("p", { class: "state" }, "Select at least one road classification to see statistics."));
      return;
    }
    loadSummary(statsBox, filters.params({ includeTiers: false }), analysis);
  }

  // A handle for the browser console and the automated browser tests. It holds no secrets.
  window.aquamap = { map, roads, filters, panel };

  if (!analysis.analysed) {
    roads.status("warn", "No roads have been analysed yet. Run the scoring pipeline, then reload this page.");
    loadSummary(statsBox, {}, analysis);
    return;
  }
  refresh();
}

start();
