import { renderCharts } from "./chart.js";

const $ = (s, r = document) => r.querySelector(s);
const nf = new Intl.NumberFormat("es", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("es", { maximumFractionDigits: 1 });
const minus = (s) => String(s).replace("-", "−");
const t0 = (v) => (v == null ? "–" : minus(nf.format(v)));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

const state = { places: [], current: null, custom: null, map: null, pins: {}, customPin: null, req: 0 };
const detail = $("#detail");

/* ---------- Tema ---------- */
const root = document.documentElement;
const isDark = () => root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
$("#theme").addEventListener("click", () => {
  root.dataset.theme = isDark() ? "light" : "dark";
  store.set("mg-theme", root.dataset.theme);
  applyMapTheme();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => applyMapTheme());

/* ---------- Vistas (móvil/tablet) ---------- */
function go(view) {
  document.body.dataset.view = view;
  document.querySelectorAll(".tabs button").forEach((b) => {
    if (b.dataset.go === view) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  if (view === "map") setTimeout(() => state.map?.resize(), 30);
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
const narrow = () => matchMedia("(max-width:1180px)").matches;
const phone = () => matchMedia("(max-width:760px)").matches;

/* ---------- Lugares ---------- */
function spark(vals) {
  const v = vals.filter((x) => x != null);
  if (v.length < 2) return "";
  const lo = Math.min(...v), hi = Math.max(...v), w = 84, h = 20;
  const pts = v.map((y, i) => `${(i / (v.length - 1)) * w},${h - 2 - ((y - lo) / (hi - lo || 1)) * (h - 4)}`);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="M${pts.join("L")}"/></svg>`;
}
function renderPlaces() {
  $("#places").innerHTML = state.places.map((p) => {
    const n = p.now;
    return `<li><button class="place" data-slug="${p.slug}" aria-current="${state.current === p.slug}">
      <span class="place-name">${esc(p.name)}</span>
      <span class="place-temp"><span class="num">${n ? t0(n.temp_c) + "°" : '<span class="skeleton">00°</span>'}</span></span>
      <span class="place-area">${esc(p.area)}</span>
      <span class="place-meta">${n ? spark(n.spark) + `<span class="num">racha ${n.gust_kmh} · lluvia ${nf1.format(n.rain_24h_mm)}</span>` : ""}</span>
    </button></li>`;
  }).join("");
}
$("#places").addEventListener("click", (e) => {
  const b = e.target.closest(".place");
  if (!b) return;
  const h = "#/" + b.dataset.slug;
  if (location.hash === h) show(b.dataset.slug); else location.hash = h;
});

/* ---------- Detalle ---------- */
const skeleton = `<div class="skeleton sk-line" style="width:40%;height:34px"></div><div class="skeleton sk-hero"></div>
  <div class="skeleton sk-line" style="width:92%"></div><div class="skeleton sk-line" style="width:84%"></div><div class="skeleton sk-line" style="width:70%"></div>`;
const confBars = (c) => `<span class="conf" aria-hidden="true">${[1, 2, 3].map((i) => `<i class="${i <= ({ baja: 1, media: 2, alta: 3 }[c] || 1) ? "on" : ""}"></i>`).join("")}</span>`;
const list = (title, a, cls = "", none = "") => `<div class="${cls}"><h3>${title}</h3>${a?.length ? `<ul>${a.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="none">${none}</p>`}</div>`;
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });

async function show(slugOrPoint) {
  const my = ++state.req;
  const isPoint = typeof slugOrPoint === "object";
  state.current = isPoint ? null : slugOrPoint;
  renderPlaces();
  highlightPins();
  detail.innerHTML = skeleton;
  detail.scrollTop = 0;
  if (phone()) go("forecast");
  const url = isPoint ? `/api/forecast?lat=${slugOrPoint.lat.toFixed(4)}&lon=${slugOrPoint.lon.toFixed(4)}` : `/api/forecast?place=${slugOrPoint}`;
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Error ${r.status}`);
    const data = await r.json();
    if (my !== state.req) return;
    render(data, isPoint ? slugOrPoint : null);
  } catch (err) {
    if (my !== state.req) return;
    detail.innerHTML = `<div class="err"><strong>No se pudo cargar el pronóstico</strong><p class="empty">${esc(err.message)}</p><button class="btn" id="retry">Reintentar</button></div>`;
    $("#retry").addEventListener("click", () => show(slugOrPoint));
  }
}

function render(d, point) {
  const lug = d.lugar, p = d.pronostico, c = d.datos, T = c.tramos;
  const day = T.slice(0, 8);
  const max = (arr) => Math.max(...arr.filter((x) => x != null));
  const avg = (arr) => arr.reduce((a, b) => a + b, 0) / (arr.length || 1);
  const now = T[0];
  const rain24 = day.reduce((s, x) => s + (x.lluvia_mm?.mean ?? 0), 0);
  const gust24 = max(day.map((x) => x.racha_kmh?.max ?? 0));
  const wind24 = max(day.map((x) => x.viento_kmh?.mean ?? 0));
  const cloud = avg(day.map((x) => x.nubes_pct?.mean ?? 0));
  const iso = day.map((x) => x.isocero_m?.mean).filter((v) => v != null);
  const near = c.estaciones_cercanas?.find((s) => s.dist_km <= 15 && s.temp_c != null);
  const mountain = lug.kind === "montaña";
  const area = point ? `${lug.lat.toFixed(3)}°, ${lug.lon.toFixed(3)}° · ${nf.format(c.ubicacion.altitud_m ?? 0)} m` : `${esc(lug.area)} · ${esc(lug.region)} · ${nf.format(c.ubicacion.altitud_m ?? 0)} m`;
  const aiBadge = d.origen === "ia" ? `Lectura de IA · ${esc((d.modelo_ia || "").split("/").pop().replace(":free", ""))}` : "Resumen automático (sin IA)";

  detail.innerHTML = `
  <div class="d-head">
    <div><h1>${esc(lug.name)}</h1><p>${area}</p></div>
    <div class="badges"><span class="badge">${confBars(p.confianza)} Confianza ${esc(p.confianza)}</span><span class="badge">Actualizado ${fmtTime(d.generado)}</span></div>
  </div>

  <div class="hero">
    <div class="hero-temp num" aria-label="Temperatura prevista ahora">${t0(now.temp_c.mean)}<sup>°C</sup></div>
    <div class="hero-note">${near ? `Estación <b>${esc(near.id)}</b> a ${near.dist_km} km mide <b class="num">${t0(near.temp_c)} °C</b> ahora.` : "Media de 4 modelos para el tramo actual. Sin balizas cercanas con datos fiables."}</div>
  </div>

  <div class="stats">
    <div class="stat"><span class="label">Viento</span><span class="num">${t0(wind24)}<small>km/h</small></span></div>
    <div class="stat"><span class="label">Racha máx.</span><span class="num">${t0(gust24)}<small>km/h</small></span></div>
    <div class="stat"><span class="label">Lluvia 24 h</span><span class="num">${nf1.format(rain24)}<small>mm</small></span></div>
    <div class="stat"><span class="label">Nubosidad</span><span class="num">${t0(cloud)}<small>%</small></span></div>
    ${mountain && iso.length ? `<div class="stat"><span class="label">Isocero</span><span class="num">${t0(avg(iso))}<small>m</small></span></div>` : ""}
  </div>

  <section class="sec">
    <div class="label">Lectura <em>${aiBadge}</em></div>
    <p class="reading">${esc(p.resumen)}</p>
    <div class="cols">
      ${list("Riesgos", p.riesgos, "risk", "Sin riesgos destacados en las próximas 48 h.")}
      ${list("Mejores ventanas", p.ventanas, "", "No se han identificado ventanas concretas.")}
      ${list("Recomendaciones", p.recomendaciones, "", "—")}
    </div>
    <div class="warn-note"><svg viewBox="0 0 24 24"><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 10v4.5M12 17.5v.01"/></svg>
      <span>Apoyo informativo, no sustituye a los avisos oficiales. Antes de salir al monte o al mar, consulta <a href="${esc(lug.warnings_url || "https://www.aemet.es/es/eltiempo/prediccion/avisos")}" target="_blank" rel="noopener">${esc(lug.warnings_label || "AEMET")}</a>.</span></div>
  </section>

  <section class="sec">
    <div class="label">Próximas 48 h <em>Banda = rango entre modelos · tramos de 3 h</em></div>
    <div id="charts"></div>
    <div class="legend"><span><i style="border-color:var(--accent)"></i>media de modelos</span><span><i style="border-color:var(--wind);border-top-style:dashed"></i>racha</span></div>
  </section>

  <section class="sec">
    <div class="label">Observaciones reales <em>balizas APRS con control de calidad</em></div>
    ${c.estaciones_cercanas?.length ? `<div class="tablewrap"><table><thead><tr><th>Estación</th><th>Dist.</th><th>°C</th><th>Viento</th><th>Racha</th><th>HR</th><th>hPa</th></tr></thead><tbody>
      ${c.estaciones_cercanas.map((s) => `<tr><td>${esc(s.id)}</td><td class="num">${s.dist_km} km</td><td class="num">${s.temp_c != null ? nf1.format(s.temp_c) : "–"}</td><td class="num">${s.viento_kmh ?? "–"}</td><td class="num">${s.racha_kmh ?? "–"}</td><td class="num">${s.humedad_pct != null ? s.humedad_pct + "%" : "–"}</td><td class="num">${s.presion_hpa != null ? nf1.format(s.presion_hpa) : "–"}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">Ninguna baliza APRS con datos fiables en 40 km. El pronóstico se apoya solo en modelos numéricos.</p>`}
  </section>

  <section class="sec">
    <div class="label">Datos de origen</div>
    <p class="empty">Cada cifra del texto se comprueba contra estos datos antes de mostrarse. Modelos: ${esc(c.modelos.join(", "))}.</p>
    <details><summary>Ver tabla de datos crudos</summary><div class="tablewrap"><table><thead><tr><th>Tramo</th><th>°C</th><th>Rango</th><th>Viento</th><th>Racha</th><th>mm</th><th>Conf.</th></tr></thead><tbody>
      ${T.map((s) => `<tr><td>${esc(s.time.slice(5, 10).split("-").reverse().join("/"))} ${s.time.slice(11, 13)}h</td><td class="num">${t0(s.temp_c?.mean)}</td><td class="num">${t0(s.temp_c?.min)}…${t0(s.temp_c?.max)}</td><td class="num">${s.viento_kmh?.mean ?? "–"}</td><td class="num">${s.racha_kmh?.max ?? "–"}</td><td class="num">${nf1.format(s.lluvia_mm?.mean ?? 0)}</td><td>${esc(s.confianza)}</td></tr>`).join("")}</tbody></table></div></details>
  </section>

  <footer class="foot">Meteo Guilda combina modelos numéricos abiertos (ECMWF, DWD ICON, NOAA GFS, Météo-France) con observaciones de estaciones aficionadas. Las estaciones pueden tener errores; el sistema las filtra pero no las garantiza.</footer>`;

  renderCharts($("#charts"), T, [
    { title: "Temperatura", sub: "°C", draw: "band", H: 170, aria: "Gráfico de temperatura próximas 48 horas",
      range: (s) => [Math.min(...s.map((x) => x.temp_c.min)), Math.max(...s.map((x) => x.temp_c.max))],
      mean: (s) => s.temp_c.mean, min: (s) => s.temp_c.min, max: (s) => s.temp_c.max,
      tip: (s) => `<b>${t0(s.temp_c.mean)} °C</b> (${t0(s.temp_c.min)} a ${t0(s.temp_c.max)})` },
    { title: "Precipitación", sub: "mm por tramo de 3 h", draw: "bars", H: 110, ticks: 2, aria: "Gráfico de precipitación",
      range: (s) => [0, Math.max(2, ...s.map((x) => x.lluvia_mm?.mean ?? 0))],
      mean: (s) => s.lluvia_mm?.mean ?? 0, tip: (s) => `<b>${nf1.format(s.lluvia_mm?.mean ?? 0)} mm</b>` },
    { title: "Viento", sub: "km/h", draw: "wind", H: 150, aria: "Gráfico de viento y rachas",
      range: (s) => [0, Math.max(20, ...s.map((x) => x.racha_kmh?.max ?? 0))],
      mean: (s) => s.viento_kmh?.mean ?? 0, gust: (s) => s.racha_kmh?.max ?? 0,
      tip: (s) => `<b>${t0(s.viento_kmh?.mean)}</b> km/h · racha <b>${t0(s.racha_kmh?.max)}</b>` },
  ]);
}

/* ---------- Mapa ---------- */
const STYLE = () => ({
  version: 8,
  sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, maxzoom: 18,
    attribution: "© <a href='https://www.openstreetmap.org/copyright' target='_blank' rel='noopener'>OpenStreetMap</a>" } },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
});
function applyMapTheme() {
  const m = state.map;
  if (!m || !m.getLayer("osm")) return;
  const d = isDark();
  m.setPaintProperty("osm", "raster-saturation", d ? -0.8 : -0.55);
  m.setPaintProperty("osm", "raster-contrast", d ? 0.1 : -0.05);
  m.setPaintProperty("osm", "raster-brightness-max", d ? 0.42 : 1);
  m.setPaintProperty("osm", "raster-brightness-min", d ? 0 : 0.08);
  m.setPaintProperty("osm", "raster-opacity", 1);
}
function highlightPins() {
  for (const [slug, el] of Object.entries(state.pins)) el.classList.toggle("on", slug === state.current);
}
function fitAll() {
  if (!state.map || !state.places.length) return;
  const b = new maplibregl.LngLatBounds();
  state.places.forEach((p) => b.extend([p.lon, p.lat]));
  state.map.fitBounds(b, { padding: { top: 70, bottom: 60, left: 50, right: 50 }, duration: 600, maxZoom: 8 });
}
function setupMap() {
  if (typeof maplibregl === "undefined") { $("#map").innerHTML = '<p class="empty" style="padding:20px">No se pudo cargar el mapa.</p>'; return; }
  const m = (state.map = new maplibregl.Map({ container: "map", style: STYLE(), center: [-1.5, 41.5], zoom: 5.2, attributionControl: { compact: true }, cooperativeGestures: false }));
  m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  m.on("load", () => {
    applyMapTheme();
    m.addSource("st", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    m.addLayer({ id: "st", type: "circle", source: "st", paint: { "circle-radius": 5, "circle-color": "#0f8a82", "circle-stroke-width": 1.5, "circle-stroke-color": "#fff" } });
    m.on("click", "st", (e) => {
      const p = e.features[0].properties;
      new maplibregl.Popup({ closeButton: false, offset: 8 }).setLngLat(e.lngLat)
        .setHTML(`<b>${esc(p.callsign)}</b><br><span class="num">${p.temp_c !== "null" && p.temp_c != null ? nf1.format(p.temp_c) + " °C" : "–"}</span> · viento <span class="num">${p.wind_ms !== "null" && p.wind_ms != null ? nf.format(p.wind_ms * 3.6) : "–"} km/h</span>`).addTo(m);
      e.originalEvent.__st = true;
    });
    m.on("mouseenter", "st", () => (m.getCanvas().style.cursor = "pointer"));
    m.on("mouseleave", "st", () => (m.getCanvas().style.cursor = ""));
    loadStations(); m.on("moveend", loadStations);
    addPins();
  });
  m.on("click", (e) => {
    if (e.originalEvent.__st || e.originalEvent.target.closest?.(".pin")) return;
    const pt = { lat: e.lngLat.lat, lng: e.lngLat.lng };
    state.custom = { lat: pt.lat, lon: pt.lng };
    state.customPin?.remove();
    const el = document.createElement("div"); el.className = "pin pt"; el.innerHTML = "<i></i><span>Punto del mapa</span>";
    state.customPin = new maplibregl.Marker({ element: el, anchor: "left" }).setLngLat([pt.lng, pt.lat]).addTo(m);
    history.replaceState(null, "", `#/punto/${pt.lat.toFixed(4)},${pt.lng.toFixed(4)}`);
    show(state.custom);
    if (narrow()) go("forecast");
  });
  $("#fit").addEventListener("click", fitAll);
}
function addPins() {
  if (!state.map || !state.places.length || Object.keys(state.pins).length) return;
  state.places.forEach((p) => {
    const el = document.createElement("div"); el.className = "pin"; el.innerHTML = `<i></i><span>${esc(p.name)}</span>`;
    el.addEventListener("click", (ev) => { ev.stopPropagation(); location.hash = "#/" + p.slug; if (narrow()) go("forecast"); });
    new maplibregl.Marker({ element: el, anchor: "left" }).setLngLat([p.lon, p.lat]).addTo(state.map);
    state.pins[p.slug] = el;
  });
  highlightPins(); fitAll();
}
async function loadStations() {
  const b = state.map.getBounds();
  try {
    const r = await fetch(`/api/stations?south=${b.getSouth()}&west=${b.getWest()}&north=${b.getNorth()}&east=${b.getEast()}`);
    const st = (await r.json()).filter((s) => s.quality === "ok");
    state.map.getSource("st")?.setData({ type: "FeatureCollection", features: st.map((s) => ({ type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] }, properties: s })) });
  } catch {}
}

/* ---------- Rutas / arranque ---------- */
function route() {
  const h = location.hash.replace(/^#\/?/, "");
  if (h.startsWith("punto/")) {
    const [lat, lon] = h.slice(6).split(",").map(Number);
    if (isFinite(lat) && isFinite(lon)) return show({ lat, lon });
  }
  const slug = state.places.find((p) => p.slug === h)?.slug || state.places[0]?.slug;
  if (slug) { show(slug); if (state.map && state.places.length) { const p = state.places.find((x) => x.slug === slug); state.map.easeTo({ center: [p.lon, p.lat], zoom: Math.max(state.map.getZoom(), 9), duration: 700 }); } }
}
window.addEventListener("hashchange", route);

(async function init() {
  go(phone() ? "places" : "forecast");
  setupMap();
  try {
    state.places = await (await fetch("/api/places")).json();
  } catch {
    detail.innerHTML = `<div class="err"><strong>Sin conexión con el servidor</strong><p class="empty">No se pudo cargar la lista de lugares.</p></div>`;
    return;
  }
  renderPlaces();
  addPins();
  if (!phone() || location.hash) route();
})();
