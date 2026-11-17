// Leaflet map: base tiles, the road layer (reloaded for the current view and filters), the
// highlight of the selected road, and the status line over the map.

import { apiFetch, isAbort } from "./api.js";
import { getTierStyle } from "./risk-config.js";
import { clear, debounce, el, fmt } from "./ui.js";

export const NAIROBI_CENTER = [-1.286, 36.82];
export const DEFAULT_ZOOM = 12;
const PAGE_LIMIT = 5000;     // the API's largest page
const MAX_FEATURES = 15000;  // pages are added until the view is complete or this many roads are drawn

/** Thinner lines when zoomed out, so a dense city view stays readable; dashes only when lines are long enough to show them. */
function styleForZoom(tier, zoom) {
  const style = getTierStyle(tier);
  const scale = zoom >= 15 ? 1 : zoom === 14 ? 0.8 : zoom === 13 ? 0.6 : 0.45;
  return { ...style, weight: Math.max(1, style.weight * scale), dashArray: zoom >= 15 ? style.dashArray : null };
}

/** Create the base map inside the element with the given id. */
export function createMap(containerId) {
  const map = L.map(containerId, { zoomControl: true, minZoom: 10, maxZoom: 19 })
    .setView(NAIROBI_CENTER, DEFAULT_ZOOM);
  // Drawings that are only for show (the selected-road outline, the nearby-search circle) go on their own
  // canvas above the roads, in a pane that ignores the mouse. A canvas takes every click that lands on it,
  // so without this the outline's canvas would sit on top of the roads and swallow all later clicks.
  const decor = map.createPane("decor");
  decor.style.zIndex = 450;            // above roads (overlayPane, 400), below markers and popups
  decor.style.pointerEvents = "none";
  map.options.renderer = L.canvas({ pane: "decor", padding: 0.3 });   // default for every vector layer not given its own
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | '
      + "SRTM terrain data courtesy of the U.S. Geological Survey",
  }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);
  return map;
}

/** Bounding box of the current view as "minLon,minLat,maxLon,maxLat" for the API. */
export function bboxParam(map) {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((n) => n.toFixed(5)).join(",");
}

export class RoadLayer {
  /**
   * @param getParams returns the filter query parameters, or {nothing: "reason"} when nothing can match
   * @param onSelect  called with (roadId, latlng) when a road is clicked
   */
  constructor(map, { getParams, onSelect, statusEl, panelEl }) {
    this.map = map;
    this.panelEl = panelEl;
    this.getParams = getParams;
    this.onSelect = onSelect;
    this.statusEl = statusEl;
    // The only canvas that takes clicks. tolerance: thin lines stay easy to tap.
    this.renderer = L.canvas({ pane: "overlayPane", padding: 0.3, tolerance: 8 });
    this.layer = L.layerGroup().addTo(map);
    this.highlight = L.layerGroup().addTo(map);
    this.controller = null;
    this.lastCount = null;
    // What is on the map right now (also read by the automated browser tests).
    this.loading = false;
    this.drawn = { total: 0, matching: 0, byCategory: {}, ids: [] };
    map.on("moveend", debounce(() => this.reload(), 200));
  }

  status(kind, text, action) {
    clear(this.statusEl);
    this.statusEl.hidden = !kind;
    if (!kind) return;
    this.statusEl.className = `map-status${kind === "error" ? " error" : kind === "warn" ? " warn" : ""}`;
    if (kind === "loading") this.statusEl.append(el("span", { class: "spinner", "aria-hidden": "true" }));
    this.statusEl.append(text);
    if (action) this.statusEl.append(" ", el("button", { type: "button", class: "link", onclick: action.run }, action.label));
  }

  /** Fetch the roads of the current view with the current filters and draw them. */
  async reload() {
    this.controller?.abort();
    const filters = this.getParams();
    if (filters.nothing) {
      this.layer.clearLayers();
      this.drawn = { total: 0, matching: 0, byCategory: {}, ids: [] };
      this.loading = false;
      this.status("warn", filters.nothing);
      return;
    }
    const controller = new AbortController();
    this.controller = controller;
    this.loading = true;
    this.status("loading", "Loading roads…");
    const query = { bbox: bboxParam(this.map), zoom: this.map.getZoom(), limit: PAGE_LIMIT, ...filters };
    try {
      // The first page (the highest scores) is drawn at once; further pages are added underneath it.
      let data = await apiFetch("/roads", { params: query, signal: controller.signal });
      this.layer.clearLayers();
      this.drawn = { total: 0, matching: data.pagination.total, byCategory: {}, ids: [] };
      let drawn = this.draw(data.features, query.zoom);
      while (data.pagination.has_more && drawn < MAX_FEATURES) {
        this.status("loading", `Loading roads… ${fmt.int(drawn)} of ${fmt.int(data.pagination.total)}`);
        data = await apiFetch("/roads", { params: { ...query, offset: data.pagination.next_offset }, signal: controller.signal });
        drawn += this.draw(data.features, query.zoom, { underneath: true });
      }
      this.report(drawn, data.pagination.total);
      this.loading = false;
    } catch (error) {
      if (isAbort(error)) return;   // a newer reload has taken over and owns the loading flag
      this.loading = false;
      this.drawn = { total: 0, matching: 0, byCategory: {}, ids: [] };
      this.layer.clearLayers();
      if (error.status === 422 && /overlap/i.test(error.message)) {
        this.status("warn", "This view is outside Nairobi County.", {
          label: "Back to Nairobi", run: () => this.map.setView(NAIROBI_CENTER, DEFAULT_ZOOM) });
      } else {
        this.status("error", `Could not load roads: ${error.message}`, { label: "Try again", run: () => this.reload() });
      }
    }
  }

  /** Draw one page of features. Returns how many were drawn. */
  draw(features, zoom, { underneath = false } = {}) {
    // The API returns the highest scores first; draw them last so they end up on top.
    const page = L.geoJSON([...features].reverse(), {
      renderer: this.renderer,
      style: (feature) => styleForZoom(feature.properties.risk_category, zoom),
      onEachFeature: (feature, layer) => {
        layer.on("click", (event) => {
          L.DomEvent.stopPropagation(event);
          this.onSelect(feature.properties.id, event.latlng);
        });
      },
    }).addTo(this.layer);
    if (underneath) {
      // A later page holds lower scores: put it below what is already drawn (lowest scores at the very bottom).
      const lines = page.getLayers();
      for (let i = lines.length - 1; i >= 0; i -= 1) lines[i].bringToBack();
    }
    for (const feature of features) {
      const category = feature.properties.risk_category ?? "none";
      this.drawn.byCategory[category] = (this.drawn.byCategory[category] ?? 0) + 1;
      this.drawn.ids.push(feature.properties.id);
    }
    this.drawn.total += features.length;
    return features.length;
  }

  report(drawn, total) {
    this.lastCount = total;
    if (total === 0) {
      this.status("warn", "No scored roads match the filters in this area.");
    } else if (drawn < total) {
      this.status("warn", `Showing the ${fmt.int(drawn)} highest-scoring of ${fmt.int(total)} roads in view. `
        + "Zoom in or narrow the filters to see the rest.");
    } else {
      this.status(null);
    }
  }

  /** Padding for fitBounds that keeps the target clear of the road panel when it covers the right side. */
  fitOptions(maxZoom = 17) {
    const wide = window.matchMedia("(min-width: 861px)").matches;
    const open = this.panelEl && !this.panelEl.hidden;
    const right = open && wide ? this.panelEl.offsetWidth : 0;        // side panel on wide screens
    const bottom = open && !wide ? this.panelEl.offsetHeight : 0;     // bottom sheet on small screens
    return { paddingTopLeft: [50, 50], paddingBottomRight: [50 + right, 50 + bottom], maxZoom };
  }

  fitTo(bounds) {
    this.map.fitBounds(bounds, this.fitOptions());
  }

  /** Outline a geometry (GeoJSON) as the current selection. */
  showSelection(geometry, { fit = false } = {}) {
    this.highlight.clearLayers();
    const halo = L.geoJSON(geometry, { style: { color: "#ffffff", weight: 11, opacity: 0.95 }, interactive: false });
    const line = L.geoJSON(geometry, { style: { color: "#111827", weight: 4, opacity: 1 }, interactive: false });
    this.highlight.addLayer(halo).addLayer(line);
    if (fit) this.fitTo(line.getBounds());
  }

  clearSelection() {
    this.highlight.clearLayers();
  }
}
