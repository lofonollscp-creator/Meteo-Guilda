import { $, $$, api, esc, LS } from "./util.js";
import { icon, hydrate } from "./icons.js";
import { getKey, setKey, syncing, pullSettings, createProfile, call } from "./sync.js";
import * as forecast from "./forecast.js";
import * as routes from "./routes.js";
import * as radio from "./radio.js";
import * as learn from "./learn.js";

const BRAND = { forecast: ["Meteo Guilda", "Datos abiertos y balizas reales"], routes: ["Meteo Guilda", "Rutas con el tiempo a la hora de paso"],
  radio: ["Radio Guilda", "Repetidores, propagación y satélites"], learn: ["Guilda", "Meteo y radio, explicadas"] };
const MARKS = {
  both: `<svg class="mark" viewBox="0 0 32 32" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 26.5 12 11.5l5 8.5 3.5-5 8 11.5Z"/><path d="M8.46 7.46A5 5 0 0 1 15.54 7.46M5.64 4.64A9 9 0 0 1 18.36 4.64"/><circle cx="25" cy="7.2" r="2.6"/></svg>`,
  meteo: `<svg class="mark" viewBox="0 0 32 32" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 26.5 12 11.5l5 8.5 3.5-5 8 11.5Z"/><circle cx="25" cy="7.2" r="2.6"/><path d="M25 1.6v1M25 11.4v1M19.8 7.2h1M29.2 7.2h1M21.4 3.6l.7.7M27.9 10.1l.7.7M21.4 10.8l.7-.7M27.9 4.3l.7-.7"/></svg>`,
  radio: `<svg class="mark" viewBox="0 0 32 32" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 26.5 12 11.5l5 8.5 3.5-5 8 11.5Z"/><path d="M8.46 7.46A5 5 0 0 1 15.54 7.46M5.64 4.64A9 9 0 0 1 18.36 4.64"/></svg>`,
};
const MARK_OF = { forecast: "meteo", routes: "meteo", radio: "radio", learn: "both" };
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
  mapCtl(html) { $("#mapctl").innerHTML = html; hydrate($("#mapctl")); },
  brand(section, sub) { const [n, s] = BRAND[section]; return `<div class="brand">${MARKS[MARK_OF[section]]}<div class="brand-text"><strong>${n}</strong><span>${esc(sub || s)}</span></div></div>`; },
  icon, hydrate,
};

/* ---------- Tema ---------- */
const paintThemeIcon = () => { $("#theme").innerHTML = icon(isDark() ? "sun" : "moon"); };
$("#theme").addEventListener("click", () => { root.dataset.theme = isDark() ? "light" : "dark"; LS.set("mg-theme", root.dataset.theme); applyMapTheme(); paintThemeIcon(); });
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
    document.title = name === "radio" ? "Radio Guilda" : name === "learn" ? "Aprende · Guilda" : "Meteo Guilda";
    $$(".nav .nv").forEach((b) => (b.dataset.s === name ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
    ctx.pane(phone() ? "list" : "detail");
    SECTIONS[name].mount(ctx);
  }
  SECTIONS[name].show?.(rest);
}
$$(".nav .nv").forEach((b) => b.addEventListener("click", () => { location.hash = "#/" + b.dataset.s; }));
$$(".panes button").forEach((b) => b.addEventListener("click", () => ctx.pane(b.dataset.pane)));
window.addEventListener("hashchange", route);
/* ---------- Instalación (webapp) ---------- */
let deferred;
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; $("#install").hidden = false; });
addEventListener("appinstalled", () => { $("#install").hidden = true; ctx.toast("App instalada"); });
const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
const iOS = /iphone|ipad|ipod/i.test(navigator.userAgent) && !standalone;
if (iOS) $("#install").hidden = false;
$("#install").addEventListener("click", async () => {
  if (deferred) { deferred.prompt(); await deferred.userChoice; deferred = null; $("#install").hidden = true; }
  else if (iOS) openDialog(`<h2>Instalar en iPhone o iPad</h2><p>Pulsa el botón Compartir de Safari y elige <b>Añadir a pantalla de inicio</b>. Se abrirá a pantalla completa y funcionará sin conexión con los últimos datos.</p><form method="dialog"><button class="btn">Entendido</button></form>`);
});
if ("serviceWorker" in navigator && location.protocol.startsWith("http")) navigator.serviceWorker.register("/sw.js").catch(() => {});
addEventListener("offline", () => ($("#offline").hidden = false));
addEventListener("online", () => ($("#offline").hidden = true));

/* ---------- Sincronización y API ---------- */
const dlg = $("#dlg");
function openDialog(html) { dlg.innerHTML = html; hydrate(dlg); dlg.showModal?.(); }
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
$("#syncbtn").addEventListener("click", renderSync);
function renderSync(msg = "") {
  const k = getKey(), host = location.origin;
  openDialog(`<h2>Sincronización y acceso por API</h2>
    <p>Guarda favoritos, tu QTH y tu cuaderno de contactos en el servidor con una <b>clave secreta</b>. Con ella los ves desde cualquier dispositivo y también por la API y el MCP. No hay cuentas ni correos.</p>
    ${k ? `<div class="field"><span class="lbl">Tu clave</span><div class="row"><input id="kv" type="password" readonly value="${esc(k)}" style="flex:1"><button class="btn ghost sm" id="kshow" type="button">Ver</button><button class="btn ghost sm" id="kcopy" type="button">Copiar</button></div></div>
      <p class="empty">Quien tenga la clave puede leer y modificar tu cuaderno. Ejemplo de uso:</p>
      <pre class="num code">curl -H "Authorization: Bearer TU_CLAVE" ${esc(host)}/api/qso/stats</pre>
      <div class="row" style="margin-top:14px"><button class="btn ghost sm" id="kout" type="button">Dejar de sincronizar aquí</button><button class="btn ghost sm danger" id="kdel" type="button">Borrar mis datos del servidor</button></div>`
    : `<div class="row"><button class="btn" id="kmk" type="button">Activar sincronización</button></div>
      <div class="field" style="margin-top:16px"><label for="kin">¿Ya tienes una clave?</label><div class="row"><input id="kin" type="password" placeholder="mg_…" style="flex:1"><button class="btn ghost sm" id="kuse" type="button">Usar</button></div></div>`}
    <p class="empty" id="kmsg">${esc(typeof msg === "string" ? msg : "")}</p>
    <form method="dialog" style="margin-top:12px"><button class="btn ghost sm">Cerrar</button></form>`);
  const m = (t) => ($("#kmsg").textContent = t);
  $("#kmk")?.addEventListener("click", async () => { try { m("Creando…"); await createProfile(); renderSync("Sincronización activada. Guarda tu clave."); } catch (e) { m(e.message); } });
  $("#kuse")?.addEventListener("click", async () => { const v = $("#kin").value.trim(); setKey(v); if (await pullSettings()) { renderSync("Clave aceptada. Recarga para ver tus datos."); } else { setKey(null); m("Clave no válida"); } });
  $("#kshow")?.addEventListener("click", () => { const i = $("#kv"); i.type = i.type === "password" ? "text" : "password"; });
  $("#kcopy")?.addEventListener("click", () => navigator.clipboard?.writeText(getKey()).then(() => m("Clave copiada")));
  $("#kout")?.addEventListener("click", () => { setKey(null); renderSync("Este dispositivo ya no sincroniza."); });
  $("#kdel")?.addEventListener("click", async () => { if (confirm("¿Borrar del servidor tu cuaderno y ajustes? No se puede deshacer.")) { try { await call("/api/profile", { method: "DELETE" }); } catch {} setKey(null); renderSync("Datos borrados del servidor."); } });
}
ctx.openDialog = openDialog;

hydrate(document);
paintThemeIcon();
setupMap();
pullSettings().finally(route);
addEventListener("keydown", (e) => { if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { const s = $("#q"); if (s) { e.preventDefault(); s.focus(); } } });
