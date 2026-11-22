import { $, $$, api, esc, LS } from "./util.js";
import * as forecast from "./forecast.js";
import * as routes from "./routes.js";
import * as radio from "./radio.js";
import * as learn from "./learn.js";

const SECTIONS = { forecast, routes, radio, learn };
const app = $("#app"), cola = $("#cola"), colb = $("#detail");
const phone = () => matchMedia("(max-width:760px)").matches;
const root = document.documentElement;
const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);

/* ---------- Contexto compartido con las secciones ---------- */
let mapResolve;
const ctx = {
  app, cola, colb, map: null, markers: new Set(), onMapClick: null, isDark,
  mapReady: new Promise((r) => (mapResolve = r)), mapLoaded: false,
  pane(p) { app.dataset.pane = p; $$(".panes button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.pane === p))); if (p === "map") setTimeout(() => ctx.map?.resize(), 30); },
  hash(path, replace = true) { const h = "#/" + path; if (location.hash !== h) { replace ? history.replaceState(null, "", h) : (location.hash = h); } },
  toast(msg) { const t = Object.assign(document.createElement("div"), { className: "toast", textContent: msg }); document.body.appendChild(t); setTimeout(() => t.remove(), 2200); },
  marker(lon, lat, { label = "", cls = "", onClick, anchor = "left", draggable = false } = {}) {
    if (!ctx.map) return null;
    const el = document.createElement("div");
    el.className = "pin " + cls;
    el.innerHTML = `<i></i>${label ? `<span>${esc(label)}</span>` : ""}`;
    if (onClick) el.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
    const m = new maplibregl.Marker({ element: el, anchor, draggable }).setLngLat([lon, lat]).addTo(ctx.map);
    m.el = el; ctx.markers.add(m);
    return m;
  },
  removeMarker(m) { m?.remove(); ctx.markers.delete(m); },
  clearMarkers() { ctx.markers.forEach((m) => m.remove()); ctx.markers.clear(); },
  fit(points, opts = {}) {
    if (!ctx.map || !points.length) return;
    const b = new maplibregl.LngLatBounds();
    points.forEach(([lo, la]) => b.extend([lo, la]));
    const pad = phone() ? 40 : 70;
    ctx.map.fitBounds(b, { padding: { top: pad + 10, bottom: pad, left: pad, right: pad + 80 }, duration: 600, maxZoom: 12, ...opts });
  },
  fly(lat, lon, zoom = 9) { ctx.map?.easeTo({ center: [lon, lat], zoom: Math.max(ctx.map.getZoom(), zoom), duration: 700 }); },
  stationsOn: false,
  stations(on) { ctx.stationsOn = on; if (ctx.map?.getLayer("st")) ctx.map.setLayoutProperty("st", "visibility", on ? "visible" : "none"); if (on) loadStations(); },
  mapCtl(html) { $("#mapctl").innerHTML = html; },
};

/* ---------- Tema ---------- */
$("#theme").addEventListener("click", () => { root.dataset.theme = isDark() ? "light" : "dark"; LS.set("mg-theme", root.dataset.theme); applyMapTheme(); });
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", applyMapTheme);

/* ---------- Mapa ---------- */
function applyMapTheme() {
  const m = ctx.map; if (!m?.getLayer("osm")) return;
  const d = isDark();
  m.setPaintProperty("osm", "raster-saturation", d ? -0.8 : -0.55);
  m.setPaintProperty("osm", "raster-contrast", d ? 0.1 : -0.05);
  m.setPaintProperty("osm", "raster-brightness-max", d ? 0.42 : 1);
  m.setPaintProperty("osm", "raster-brightness-min", d ? 0 : 0.08);
}
async function loadStations() {
  const m = ctx.map; if (!m || !ctx.stationsOn) return;
  const b = m.getBounds();
  try {
    const st = (await api(`/api/stations?south=${b.getSouth()}&west=${b.getWest()}&north=${b.getNorth()}&east=${b.getEast()}`)).filter((s) => s.quality === "ok");
    m.getSource("st")?.setData({ type: "FeatureCollection", features: st.map((s) => ({ type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] }, properties: s })) });
  } catch {}
}
function setupMap() {
  if (typeof maplibregl === "undefined") { $("#map").innerHTML = '<p class="empty" style="padding:20px">No se pudo cargar el mapa.</p>'; mapResolve(); return; }
  const m = (ctx.map = new maplibregl.Map({ container: "map", center: [-1.5, 41.5], zoom: 5.2, attributionControl: { compact: true },
    style: { version: 8, sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, maxzoom: 18,
      attribution: "© <a href='https://www.openstreetmap.org/copyright' target='_blank' rel='noopener'>OpenStreetMap</a>" } }, layers: [{ id: "osm", type: "raster", source: "osm" }] } }));
  m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  m.on("load", () => {
    applyMapTheme();
    m.addSource("st", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    m.addLayer({ id: "st", type: "circle", source: "st", layout: { visibility: ctx.stationsOn ? "visible" : "none" },
      paint: { "circle-radius": 4.5, "circle-color": "#0f8a82", "circle-stroke-width": 1.5, "circle-stroke-color": "#fff" } });
    m.on("click", "st", (e) => {
      const p = e.features[0].properties, v = (x, f) => (x === "null" || x == null ? "–" : f(x));
      new maplibregl.Popup({ closeButton: false, offset: 8 }).setLngLat(e.lngLat)
        .setHTML(`<b>${esc(p.callsign)}</b><br>${v(p.temp_c, (x) => Math.round(x * 10) / 10 + " °C")} · viento ${v(p.wind_ms, (x) => Math.round(x * 3.6))} km/h`).addTo(m);
      e.originalEvent.__st = true;
    });
    m.on("mouseenter", "st", () => (m.getCanvas().style.cursor = "pointer"));
    m.on("mouseleave", "st", () => (m.getCanvas().style.cursor = ""));
    m.on("moveend", loadStations);
    ctx.mapLoaded = true; mapResolve();
  });
  m.on("click", (e) => { if (e.originalEvent.__st || e.originalEvent.target.closest?.(".pin")) return; ctx.onMapClick?.(e.lngLat); });
}

/* ---------- Router ---------- */
let current = null;
function route() {
  let parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (!SECTIONS[parts[0]]) parts = ["forecast", ...parts];            // enlaces antiguos: #/garos
  const [name, ...rest] = parts;
  if (current !== name) {
    if (current) SECTIONS[current].unmount?.();
    ctx.onMapClick = null; ctx.clearMarkers(); ctx.mapCtl(""); cola.innerHTML = ""; colb.innerHTML = "";
    current = name;
    document.body.dataset.section = name;
    $$(".nav .nv").forEach((b) => (b.dataset.s === name ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
    ctx.pane(phone() ? "list" : "detail");
    SECTIONS[name].mount(ctx);
  }
  SECTIONS[name].show?.(rest);
}
$$(".nav .nv").forEach((b) => b.addEventListener("click", () => { location.hash = "#/" + b.dataset.s; }));
$$(".panes button").forEach((b) => b.addEventListener("click", () => ctx.pane(b.dataset.pane)));
window.addEventListener("hashchange", route);
setupMap();
route();
addEventListener("keydown", (e) => { if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { const s = $("#q"); if (s) { e.preventDefault(); s.focus(); } } });
