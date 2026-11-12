// Dashboard entry point.
import { createMap } from "./map.js";

const map = createMap("map");

// Leaflet needs a size recalculation when the layout changes (e.g. phone rotation).
window.addEventListener("resize", () => map.invalidateSize());
