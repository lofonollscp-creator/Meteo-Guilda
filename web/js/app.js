import { $, $$, api, esc, LS } from "./util.js";
import { icon, hydrate } from "./icons.js";
import { getKey, setKey, syncing, pullSettings, createProfile, call } from "./sync.js";
import * as me from "./me.js";
import * as account from "./account.js";
import * as assistant from "./assistant.js";
import { initPantalla } from "./pantalla.js";
import { getQth, hasQth } from "./qth.js";

const BRAND = { forecast: ["Meteo Guilda", "Datos abiertos y balizas reales"], routes: ["Meteo Guilda", "Rutas con el tiempo a la hora de paso"],
  radio: ["Radio Guilda", "Repetidores, propagación y satélites"], learn: ["Guilda", "Meteo y radio, explicadas"] };
const MARKS = {
  both: `<svg class="mark" viewBox="0 0 32 32" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 26.5 12 11.5l5 8.5 3.5-5 8 11.5Z"/><path d="M8.46 7.46A5 5 0 0 1 15.54 7.46M5.64 4.64A9 9 0 0 1 18.36 4.64"/><circle cx="25" cy="7.2" r="2.6"/></svg>`,
  meteo: `<svg class="mark" viewBox="0 0 32 32" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 26.5 12 11.5l5 8.5 3.5-5 8 11.5Z"/><circle cx="25" cy="7.2" r="2.6"/><path d="M25 1.6v1M25 11.4v1M19.8 7.2h1M29.2 7.2h1M21.4 3.6l.7.7M27.9 10.1l.7.7M21.4 10.8l.7-.7M27.9 4.3l.7-.7"/></svg>`,
  radio: `<svg class="mark" viewBox="0 0 32 32" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 26.5 12 11.5l5 8.5 3.5-5 8 11.5Z"/><path d="M8.46 7.46A5 5 0 0 1 15.54 7.46M5.64 4.64A9 9 0 0 1 18.36 4.64"/></svg>`,
};
const MARK_OF = { forecast: "meteo", routes: "meteo", radio: "radio", learn: "both" };
// Las secciones se cargan bajo demanda: al abrir la app solo se descarga la de Previsión (radio, rutas y «Aprende» pesan ~100 KB más).
const LOADERS = { forecast: () => import("./forecast.js"), routes: () => import("./routes.js"), radio: () => import("./radio.js"), learn: () => import("./learn.js") };
const SECTIONS = {};
const app = $("#app"), cola = $("#cola"), colb = $("#detail");
let pendingView = null;
const attach = (h) => { if (h.real || !ctx.map) return; h.real = new maplibregl.Marker({ element: h.el, anchor: h.anchor, draggable: h.draggable }).setLngLat(h.pos).addTo(ctx.map); h.handlers.forEach(([e, f]) => h.real.on(e, f)); };
const phone = () => matchMedia("(max-width:760px)").matches;
const root = document.documentElement;
const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);

/* ---------- Contexto compartido con las secciones ---------- */
let mapResolve;
const ctx = {
  app, cola, colb, map: null, markers: new Set(), onMapClick: null, isDark,
  mapReady: new Promise((r) => (mapResolve = r)), mapLoaded: false,
  pane(p) {
    app.dataset.pane = p; $$(".panes button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.pane === p)));
    if (p === "map") { ensureMap(); setTimeout(() => ctx.map?.resize(), 30); }
    if (p === "detail" && !colb.firstElementChild) {
      colb.innerHTML = `<div class="empty-detail"><div><h2>Nada seleccionado</h2><p>Elige un lugar o una opción de la lista para ver aquí el detalle.</p><button class="btn" type="button" id="to-list">Ir a la lista</button></div></div>`;
      $("#to-list")?.addEventListener("click", () => ctx.pane("list"));
    }
  },
  hash(path, replace = true) { const h = "#/" + path; if (location.hash !== h) { replace ? history.replaceState(null, "", h) : (location.hash = h); } },
  toast(msg) { const t = Object.assign(document.createElement("div"), { className: "toast", textContent: msg }); document.body.appendChild(t); setTimeout(() => t.remove(), 2200); },
  home: () => (hasQth() ? { lat: getQth().lat, lon: getQth().lon, name: getQth().name || (getQth().locator ? `QTH ${getQth().locator}` : "Mi ubicación") } : null),   // «Mi ubicación»
  place: () => null,                  // la sección de Previsión lo sustituye por el lugar que se está viendo
  section: () => current,
  // Los marcadores y encuadres se piden antes de que MapLibre esté cargado (se descarga aparte): se guardan y se colocan al crearse el mapa.
  marker(lon, lat, { label = "", cls = "", onClick, anchor = "left", draggable = false } = {}) {
    const el = document.createElement("div");
    el.className = "pin " + cls;
    el.innerHTML = `<i></i>${label ? `<span>${esc(label)}</span>` : ""}`;
    if (onClick) el.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
    const h = { el, real: null, pos: [lon, lat], anchor, draggable, handlers: [],
      remove() { ctx.markers.delete(h); h.real?.remove(); },
      setLngLat(p) { h.pos = Array.isArray(p) ? p : [p.lng, p.lat]; h.real?.setLngLat(h.pos); return h; },
      getLngLat() { return h.real ? h.real.getLngLat() : { lng: h.pos[0], lat: h.pos[1] }; },
      on(ev, fn) { h.handlers.push([ev, fn]); h.real?.on(ev, fn); return h; } };
    ctx.markers.add(h); attach(h);
    return h;
  },
  removeMarker(m) { m?.remove(); ctx.markers.delete(m); },
  clearMarkers() { ctx.markers.forEach((m) => m.real?.remove()); ctx.markers.clear(); },
  fit(points, opts = {}) {
    if (!points.length) return;
    if (!ctx.map) { pendingView = () => ctx.fit(points, opts); return; }
    const b = new maplibregl.LngLatBounds();
    points.forEach(([lo, la]) => b.extend([lo, la]));
    const pad = phone() ? 40 : 70;
    ctx.map.fitBounds(b, { padding: { top: pad + 10, bottom: pad, left: pad, right: pad + 80 }, duration: 600, maxZoom: 12, ...opts });
  },
  fly(lat, lon, zoom = 9) { if (!ctx.map) { pendingView = () => ctx.fly(lat, lon, zoom); return; } ctx.map.easeTo({ center: [lon, lat], zoom: Math.max(ctx.map.getZoom(), zoom), duration: 700 }); },
  stationsOn: false,
  stations(on) { ctx.stationsOn = on; if (ctx.map?.getLayer("st")) ctx.map.setLayoutProperty("st", "visibility", on ? "visible" : "none"); if (on) loadStations(); },
  mapCtl(html) { $("#mapctl").innerHTML = html; hydrate($("#mapctl")); },
  brand(section, sub) { const [n, s] = BRAND[section]; return `<div class="brand">${MARKS[MARK_OF[section]]}<div class="brand-text"><strong>${n}</strong><span>${esc(sub || s)}</span></div></div>`; },
  icon, hydrate,
  /** Barra superior (escritorio): página actual, frescura de los datos y lugar. Cada sección la rellena con datos reales o la deja vacía. */
  top({ page, fresh, place } = {}) {
    if (page !== undefined) $("#tb-page").textContent = page || TOP[current]?.[1] || "";
    if (fresh !== undefined) { const f = $("#tb-fresh"); f.hidden = !fresh; f.querySelector("span").textContent = fresh || ""; }
    if (place !== undefined) { const b = $("#tb-place"); b.hidden = !place; b.querySelector("span").textContent = place || ""; }
  },
};
const TOP = { forecast: ["Meteo", "Hoy"], routes: ["Planificar", "Rutas"], radio: ["Radio", "Condiciones"], learn: ["Guilda", "Aprende"] };

/* ---------- Tema ---------- */
/* Tema: sistema → claro → oscuro (el modo «sistema» sigue al dispositivo y cambia solo con él) */
const THEMES = [[null, "monitor", "Sistema"], ["light", "sun", "Claro"], ["dark", "moon", "Oscuro"]];
const themeMode = () => { const v = LS.get("mg-theme", null); return v === "light" || v === "dark" ? v : null; };
const paintThemeIcon = () => {
  const [, ic, name] = THEMES.find(([m]) => m === themeMode()), b = $("#theme");
  b.innerHTML = icon(ic); b.title = `Tema: ${name} (pulsa para cambiar)`; b.setAttribute("aria-label", `Tema: ${name}. Pulsa para cambiar entre sistema, claro y oscuro`);
};
$("#theme").addEventListener("click", () => {
  const i = THEMES.findIndex(([m]) => m === themeMode()), [mode, , name] = THEMES[(i + 1) % THEMES.length];
  if (mode) root.dataset.theme = mode; else delete root.dataset.theme;
  mode ? LS.set("mg-theme", mode) : localStorage.removeItem("mg-theme");
  applyMapTheme(); paintThemeIcon(); ctx.toast(`Tema: ${name}`);
});
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
// MapLibre pesa ~800 KB: se descarga cuando el navegador está ocioso (escritorio) o al abrir la pestaña Mapa (móvil), no antes de pintar la app.
let mapStarted = false;
const loadTag = (tag, attrs) => new Promise((ok, ko) => { const e = Object.assign(document.createElement(tag), attrs); e.onload = ok; e.onerror = ko; document.head.appendChild(e); });
function ensureMap() {
  if (mapStarted) return; mapStarted = true;
  Promise.all([loadTag("link", { rel: "stylesheet", href: "vendor/maplibre-gl.css" }), loadTag("script", { src: "vendor/maplibre-gl.js" })]).then(setupMap)
    .catch(() => { $("#map").innerHTML = '<p class="empty" style="padding:20px">No se pudo cargar el mapa.</p>'; mapResolve(); });
}
function setupMap() {
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
    ctx.mapLoaded = true; ctx.markers.forEach(attach); mapResolve();
    if (pendingView) { const f = pendingView; pendingView = null; f(); }
  });
  m.on("click", (e) => { if (e.originalEvent.__st || e.originalEvent.target.closest?.(".pin")) return; ctx.onMapClick?.(e.lngLat); });
}

/* ---------- Router ---------- */
let current = null;
let routeSeq = 0;
async function route() {
  let parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (!LOADERS[parts[0]]) parts = ["forecast", ...parts];             // enlaces antiguos: #/garos
  const [name, ...rest] = parts, seq = ++routeSeq;
  if (!SECTIONS[name]) {
    try { SECTIONS[name] = await LOADERS[name](); }
    catch { colb.innerHTML = '<div class="err"><strong>No se pudo cargar la sección</strong><p class="empty">Comprueba tu conexión y recarga.</p></div>'; return; }
  }
  if (seq !== routeSeq) return;
  if (current !== name) {
    if (current) SECTIONS[current].unmount?.();
    ctx.onMapClick = null; ctx.clearMarkers(); ctx.mapCtl(""); cola.innerHTML = ""; colb.innerHTML = "";
    current = name;
    document.body.dataset.section = name;
    document.title = name === "radio" ? "Radio Guilda" : name === "learn" ? "Aprende · Guilda" : "Meteo Guilda";
    $$(".nav .nv").forEach((b) => (b.dataset.s === name ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
    $("#tb-sec").textContent = TOP[name][0]; ctx.top({ page: "", fresh: "", place: "" });
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
if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
  const hadController = !!navigator.serviceWorker.controller; let reloaded = false;
  navigator.serviceWorker.register("/sw.js").catch(() => {});
  // una versión nueva se aplica sola: al tomar el control el service worker nuevo, la app se recarga una vez (no en la primera instalación)
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (hadController && !reloaded) { reloaded = true; location.reload(); } });
}
addEventListener("offline", () => ($("#offline").hidden = false));
addEventListener("online", () => ($("#offline").hidden = true));

/* ---------- Sincronización y API ---------- */
const dlg = $("#dlg");
function openDialog(html) { dlg.innerHTML = `<div class="dlg-in">${html}</div>`; hydrate(dlg); dlg.showModal?.(); }   // .dlg-in da el margen interior a todos los diálogos
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
/** Pinta la gestión de la clave de sincronización dentro de `box` (pestaña «Sincronización y API» de los ajustes). */
function syncView(box, msg = "") {
  const $ = (s) => box.querySelector(s);
  const k = getKey(), host = location.origin;
  box.innerHTML = (`<p class="note" style="margin-top:0">Sincronización y acceso por API</p>
    <p>Guarda favoritos, tu QTH y tu cuaderno de contactos en el servidor con una <b>clave secreta</b>. Con ella los ves desde cualquier dispositivo y también por la API y el MCP. No hay cuentas ni correos.</p>
    ${k ? `<div class="field"><span class="lbl">Tu clave</span><div class="row"><input id="kv" type="password" readonly value="${esc(k)}" style="flex:1"><button class="btn ghost sm" id="kshow" type="button">Ver</button><button class="btn ghost sm" id="kcopy" type="button">Copiar</button></div></div>
      <p class="empty">Quien tenga la clave puede leer y modificar tu cuaderno. Ejemplo de uso:</p>
      <pre class="num code">curl -H "Authorization: Bearer TU_CLAVE" ${esc(host)}/api/qso/stats</pre>
      <div class="row" style="margin-top:14px"><button class="btn ghost sm" id="kout" type="button">Dejar de sincronizar aquí</button><button class="btn ghost sm danger" id="kdel" type="button">Borrar mis datos del servidor</button></div>`
    : `<div class="row"><button class="btn" id="kmk" type="button">Activar sincronización</button></div>
      <div class="field" style="margin-top:16px"><label for="kin">¿Ya tienes una clave?</label><div class="row"><input id="kin" type="password" placeholder="mg_…" style="flex:1"><button class="btn ghost sm" id="kuse" type="button">Usar</button></div></div>`}
    <p class="empty" id="kmsg">${esc(typeof msg === "string" ? msg : "")}</p>
    `);
  hydrate(box);
  const m = (t) => ($("#kmsg").textContent = t);
  $("#kmk")?.addEventListener("click", async () => { try { m("Creando…"); await createProfile(); syncView(box, "Sincronización activada. Guarda tu clave."); } catch (e) { m(e.message); } });
  $("#kuse")?.addEventListener("click", async () => { const v = $("#kin").value.trim(); setKey(v); if (await pullSettings()) { syncView(box, "Clave aceptada. Recarga para ver tus datos."); } else { setKey(null); m("Clave no válida"); } });
  $("#kshow")?.addEventListener("click", () => { const i = $("#kv"); i.type = i.type === "password" ? "text" : "password"; });
  $("#kcopy")?.addEventListener("click", () => navigator.clipboard?.writeText(getKey()).then(() => m("Clave copiada")));
  $("#kout")?.addEventListener("click", () => { setKey(null); syncView(box, "Este dispositivo ya no sincroniza."); });
  $("#kdel")?.addEventListener("click", async () => { if (confirm("¿Borrar del servidor tu cuaderno y ajustes? No se puede deshacer.")) { try { await call("/api/profile", { method: "DELETE" }); } catch {} setKey(null); syncView(box, "Datos borrados del servidor."); } });
}
ctx.openDialog = openDialog;

hydrate(document);
initPantalla();
caches?.keys().then((k) => { window.__mgv = (k.find((x) => /^guilda-v\d+$/.test(x)) || "").replace("guilda-", ""); }).catch(() => {});
paintThemeIcon();
account.init(ctx, syncView);
assistant.init(ctx, account.open);
assistant.mountAsk(colb);
$("#acctbtn").addEventListener("click", () => account.open("ia"));
$("#chatbtn").addEventListener("click", () => assistant.toggle());
$("#tb-cmd").addEventListener("click", () => assistant.toggle());
$("#tb-place").addEventListener("click", () => { const s = $("#q"); if (s) { s.focus(); s.select(); } });
me.load();
pullSettings().finally(route);
(window.requestIdleCallback || ((f) => setTimeout(f, 800)))(() => { if (!phone()) ensureMap(); else setTimeout(ensureMap, 6000); }, { timeout: 2500 });
addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { const s = $("#q"); if (s) { e.preventDefault(); s.focus(); s.select(); } return; } if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { const s = $("#q"); if (s) { e.preventDefault(); s.focus(); } } });
