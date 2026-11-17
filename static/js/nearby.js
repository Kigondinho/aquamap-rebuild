// Nearby roads, from GET /api/v1/roads/nearby: around the user's position or a point picked on the map.

import { apiFetch, isAbort } from "./api.js";
import { tierClass } from "./risk-config.js";
import { clear, el, fmt, setState, titleCase } from "./ui.js";

const LIST_LIMIT = 50;

export function setupNearby({ map, workspace, getParams, onPick, onNeedMap = () => {} }) {
  const stateLine = document.getElementById("nearby-state");
  const list = document.getElementById("nearby-results");
  const radiusSelect = document.getElementById("radius");
  const nearMe = document.getElementById("near-me");
  const pick = document.getElementById("near-pick");
  const clearButton = document.getElementById("nearby-clear");
  const overlay = L.layerGroup().addTo(map);
  let controller = null;
  let point = null;      // {lat, lon, label}
  let picking = false;

  function setPicking(on) {
    picking = on;
    workspace.classList.toggle("pick-mode", on);
    pick.textContent = on ? "Cancel" : "Pick on map";
    pick.setAttribute("aria-pressed", String(on));
    if (on) {
      setState(stateLine, "empty", "Click a place on the map.");
      onNeedMap();   // on a small screen: move the filter drawer out of the way
    }
  }

  function reset() {
    controller?.abort();
    point = null;
    overlay.clearLayers();
    clear(list);
    clearButton.hidden = true;
    setState(stateLine, null);
  }

  /** @param zoomToArea true when the point or the distance changed (not when only the filters did) */
  async function search({ zoomToArea = false } = {}) {
    if (!point) return;
    controller?.abort();
    const filters = getParams();
    const radius = Number(radiusSelect.value);
    overlay.clearLayers();
    const circle = L.circle([point.lat, point.lon], { radius, color: "#0b6e8a", weight: 2, fillOpacity: 0.06, interactive: false }).addTo(overlay);
    if (zoomToArea) map.fitBounds(circle.getBounds(), { padding: [30, 30] });
    L.circleMarker([point.lat, point.lon], { radius: 6, color: "#ffffff", weight: 2, fillColor: "#0b6e8a", fillOpacity: 1, interactive: false }).addTo(overlay);
    clear(list);
    clearButton.hidden = false;
    if (filters.nothing) return setState(stateLine, "empty", filters.nothing);
    controller = new AbortController();
    setState(stateLine, "loading", "Finding nearby roads…");
    try {
      const data = await apiFetch("/roads/nearby", {
        params: { lat: point.lat.toFixed(6), lon: point.lon.toFixed(6), radius_m: radius, limit: LIST_LIMIT, ...filters },
        signal: controller.signal,
      });
      show(data, radius);
    } catch (error) {
      if (isAbort(error)) return;
      setState(stateLine, "error", `Could not search nearby: ${error.message}`, { label: "Try again", run: () => search() });
    }
  }

  function show(data, radius) {
    const page = data.pagination;
    if (!data.features.length) {
      return setState(stateLine, "empty", `No scored roads within ${fmt.metres(radius)} of ${point.label} with the current filters. `
        + "Try a larger distance, or check that the point is inside Nairobi County.");
    }
    setState(stateLine, "empty", page.has_more
      ? `The nearest ${page.returned} of ${fmt.int(page.total)} roads within ${fmt.metres(radius)} of ${point.label}.`
      : `${fmt.int(page.total)} ${page.total === 1 ? "road" : "roads"} within ${fmt.metres(radius)} of ${point.label}.`);
    for (const feature of data.features) {
      const p = feature.properties;
      list.append(el("li", {}, el("button", { type: "button", onclick: () => onPick(feature) },
        el("span", { class: "title" }, el("span", {}, p.name || `Unnamed ${titleCase(p.highway_type).toLowerCase()} road`),
          el("span", { class: `chip ${tierClass(p.risk_category)}` }, p.risk_category ?? "Not scored")),
        el("span", { class: "meta" }, `${fmt.metres(p.distance_m)} away · ${titleCase(p.highway_type)}`
          + (p.vulnerability_index !== null ? ` · IVI ${fmt.score(p.vulnerability_index)}` : "")))));
    }
  }

  nearMe.addEventListener("click", () => {
    setPicking(false);
    if (!("geolocation" in navigator)) {
      return setState(stateLine, "error", "This browser cannot share its location. Use “Pick on map” instead.");
    }
    setState(stateLine, "loading", "Waiting for your location…");
    navigator.geolocation.getCurrentPosition((position) => {
      point = { lat: position.coords.latitude, lon: position.coords.longitude, label: "your location" };
      onNeedMap();
      search({ zoomToArea: true });
    }, (error) => {
      const reason = error.code === error.PERMISSION_DENIED ? "Location access was not allowed."
        : error.code === error.TIMEOUT ? "Finding your location took too long." : "Your location is not available.";
      setState(stateLine, "error", `${reason} Use “Pick on map” instead.`);
    }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
  });

  pick.addEventListener("click", () => setPicking(!picking));
  radiusSelect.addEventListener("change", () => search({ zoomToArea: true }));
  clearButton.addEventListener("click", () => { setPicking(false); reset(); });
  map.on("click", (event) => {
    if (!picking) return;
    setPicking(false);
    point = { lat: event.latlng.lat, lon: event.latlng.lng, label: "the chosen point" };
    search({ zoomToArea: true });
  });

  return {
    /** While picking, a click on a road chooses the point instead of opening the road. */
    takeClick(latlng) {
      if (!picking) return false;
      setPicking(false);
      point = { lat: latlng.lat, lon: latlng.lng, label: "the chosen point" };
      search({ zoomToArea: true });
      return true;
    },
    refresh: () => search(),   // re-run with new filters
  };
}
