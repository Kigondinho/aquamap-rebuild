// Leaflet map setup. Roads are drawn on a Canvas renderer for performance.

export const NAIROBI_CENTER = [-1.286, 36.82];
export const DEFAULT_ZOOM = 11;

/** Create the base map inside the element with the given id. */
export function createMap(containerId) {
  const map = L.map(containerId, { preferCanvas: true, zoomControl: true })
    .setView(NAIROBI_CENTER, DEFAULT_ZOOM);

  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors",
  }).addTo(map);

  return map;
}

/** Bounding box of the current view as "minLon,minLat,maxLon,maxLat" for the API. */
export function bboxParam(map) {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((n) => n.toFixed(5)).join(",");
}

/** Later phase: fetch roads for the current view and draw them styled by tier. */
export async function loadRoads(_map, _filters) {
  throw new Error("Not implemented yet: loading road data (later phase).");
}
