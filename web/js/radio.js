import { $, $$, api, esc, nf, nf1, t0, LS, debounce, toLocator, fromLocator, distKm, bearing, compass, wd, hh } from "./util.js";
import { renderCharts, renderStrip, sparkline } from "./charts.js";
import { icon } from "./icons.js";
import { pushSettings } from "./sync.js";
import * as qsl from "./qsl.js";
import * as aprsdx from "./aprsdx.js";
import { seal as hoySeal, tmo } from "./hoy.js";

let ctx, sub = "resumen", places = [], placesP = Promise.resolve([]), loc = null, req = 0;
const MENU = [
  ["resumen", "Resumen", "Tu radio de un vistazo", "home"],
  ["ventanas", "Ventanas", "Cuándo operar: HF, tropo, satélites y tiempo", "clock"],
  ["repetidores", "Repetidores", "FM, DMR, D-STAR y C4FM con cobertura", "tower"],
  ["propagacion", "Propagación", "Tropo, HF y línea gris", "wave"],
  ["satelites", "Satélites", "Pasos de la ISS y satélites de aficionados", "satellite"],
  ["aprs", "APRS y clúster DX", "Estaciones en vivo y spots DX", "antenna"],
  ["portatil", "Operación portátil", "Mejor ventana para salir a operar", "tent"],
  ["qsl", "Cuaderno QSL", "Contactos, confirmaciones y tarjetas", "log"],
  ["herramientas", "Herramientas", "Locator, antenas, Morse, código Q…", "tools"],
];
import { getQth, setQth, qthName } from "./qth.js";   // el QTH es el mismo que «Mi ubicación» de Previsión (y llega al asistente y al MCP)
export { getQth };
const MODE_COL = { FM: "#3a7bd5", DMR: "#c4501b", "D-STAR": "#7a5bd6", C4FM: "#2c8a5a" };
const fmtF = (f) => f.toFixed(4).replace(".", ",");
const here = () => loc || (getQth() ? { ...getQth(), name: qthName() } : { lat: 42.7, lon: 0.847, name: "Garòs" });
const bmLink = (r) => (r.dmr_id ? `https://brandmeister.network/?page=repeater&id=${r.dmr_id}` : null);

export function mount(c) {
  ctx = c;
  const q = getQth();
  ctx.cola.innerHTML = `${ctx.brand("radio")}
    <div class="form" style="padding-bottom:6px">
      <div class="field"><label for="qth">Mi locator (QTH)</label><div class="row" style="flex-wrap:nowrap"><input id="qth" type="text" maxlength="8" placeholder="p. ej. JN02ac" value="${esc(q?.locator || "")}" style="flex:1"><button class="icon-btn" id="geo" type="button" title="Usar mi ubicación" aria-label="Usar mi ubicación">${icon("compass")}</button></div></div>
      <div class="field"><label for="mycall">Mi indicativo</label><input id="mycall" type="text" maxlength="14" placeholder="opcional" style="text-transform:uppercase" value="${esc(q?.call || "")}"></div>
      <p class="empty" id="qthinfo" style="font-size:12.5px"></p></div>
    <div class="menu" id="menu">${MENU.map(([id, t, s, ic]) => `<button type="button" data-sub="${id}">${icon(ic)}<span>${t}<small>${s}</small></span></button>`).join("")}</div>
    <p class="cola-note">Cuaderno y ajustes se guardan en este dispositivo (o en tu clave, si activas la sincronización). Datos de repetidores: BrandMeister y HearHam.</p>`;
  $("#menu").addEventListener("click", (e) => { const b = e.target.closest("[data-sub]"); if (b) location.hash = `#/radio/${b.dataset.sub}`; });
  const save = () => { const p = fromLocator($("#qth").value); setQth(p ? { locator: $("#qth").value.toUpperCase(), ...p, call: $("#mycall").value.toUpperCase() } : $("#mycall").value ? { ...(getQth() || {}), call: $("#mycall").value.toUpperCase() } : null); qthInfo(); };
  $("#qth").addEventListener("input", save); $("#mycall").addEventListener("input", save);
  $("#qth").addEventListener("change", () => { if (getQth()?.lat != null) show([sub]); });
  $("#geo").addEventListener("click", () => navigator.geolocation?.getCurrentPosition((p) => {
    const l = toLocator(p.coords.latitude, p.coords.longitude); $("#qth").value = l.slice(0, 4).toUpperCase() + l.slice(4); // sin precisión engañosa: 6 caracteres
    setQth({ locator: $("#qth").value, lat: p.coords.latitude, lon: p.coords.longitude, call: $("#mycall").value.toUpperCase() }); qthInfo(); ctx.toast("QTH actualizado"); show([sub]);
  }, () => ctx.toast("No se pudo obtener la ubicación"), { timeout: 8000 }));
  qthInfo();
  ctx.stations(false);
  placesP = api("/api/places").then((p) => (places = p)).catch(() => []);
}
function qthInfo() { const i = $("#qthinfo"), q = getQth(); if (i) i.textContent = q?.lat != null ? `${q.lat.toFixed(3)}°, ${q.lon.toFixed(3)}°` : "Indica tu locator para personalizar la sección."; }
export function unmount() { clearLayers(); stopSat(); aprsdx.stop(); }

const LAYERS = ["rep-cl", "rep-pt", "rep-pt-sel", "rep-cov", "rep-cov-l", "night", "night-l", "qso-lines", "qso-grids"];
const SOURCES = ["reps", "rep-c", "night", "qso-l", "qso-g"];
export function clearLayers() {
  const m = ctx?.map; if (!m || !ctx.mapLoaded) return;
  LAYERS.forEach((l) => m.getLayer(l) && m.removeLayer(l)); SOURCES.forEach((s) => m.getSource(s) && m.removeSource(s));
  aprsdx.clear(m);
}
const onMap = (fn) => (ctx.mapLoaded ? fn() : ctx.mapReady.then(fn));

export function show(parts) {
  const id = parts[0] || "resumen", bare = !parts[0];
  sub = MENU.some((m) => m[0] === id) ? id : "resumen";
  ctx.top?.({ page: sub === "resumen" ? "Condiciones" : MENU.find((m) => m[0] === sub)[1] });
  req++;
  $$("#menu [data-sub]").forEach((b) => b.setAttribute("aria-current", String(b.dataset.sub === sub)));
  ctx.clearMarkers(); clearLayers(); ctx.mapCtl(""); ctx.onMapClick = null; stopSat(); aprsdx.stop(); ctx.pane(bare && matchMedia("(max-width:760px)").matches ? "list" : "detail");
  ({ resumen, ventanas, repetidores: () => repetidores(parts[1]), propagacion, satelites, aprs: () => aprsdx.view(ctx, { onMap, here, head }), portatil, qsl: () => qsl.view(ctx, { getQth, clearLayers, onMap }), herramientas })[sub]();
}
const head = (t, p, extra = "") => `<div class="d-head"><div><h1>${t}</h1><p>${p}</p></div>${extra}</div>`;
const where = () => `<div class="row" style="margin:16px 0"><select id="where" style="width:auto;max-width:100%"></select><span class="chip chip-ghost">o toca el mapa</span></div>`;
async function fillWhere(onChange) {
  await placesP;
  const q = getQth(), opts = [...(q?.lat != null ? [{ name: qthName(q), lat: q.lat, lon: q.lon }] : []), ...places.map((p) => ({ name: p.name, lat: p.lat, lon: p.lon }))];
  if (loc?.name === "Punto del mapa") opts.push(loc);
  const sel = $("#where"); if (!sel) return;
  sel.innerHTML = opts.map((o, i) => `<option value="${i}">${esc(o.name)}</option>`).join("");
  sel.value = String(Math.max(0, opts.findIndex((o) => o.name === here().name)));
  sel.addEventListener("change", () => { loc = opts[+sel.value]; onChange(); });
  ctx.onMapClick = ({ lat, lng }) => { loc = { lat, lon: lng, name: "Punto del mapa" }; onChange(); };
}

/* ---------- Resumen ---------- */
async function resumen() {
  const my = req, at = here(), q = getQth();
  ctx.top?.({ page: "Condiciones", place: at.name || "" });
  ctx.colb.innerHTML = `${head("Radio Guilda", "Meteorología y radio, juntas: lo que importa ahora mismo " + (q?.lat != null ? "en tu QTH" : "(indica tu locator para personalizarlo)"))}
    <section class="rbrief" id="rbrief" aria-label="Para operar hoy"><div class="skeleton" style="height:90px"></div></section>
    <section class="hoy-kpis" id="rkpis" aria-label="Clima espacial y condiciones">${Array.from({ length: 4 }, () => '<div class="kpi"><div class="skeleton" style="height:62px"></div></div>').join("")}</section>
    <section class="card rheat" id="rheat" aria-label="Bandas por tramo"><div class="skeleton" style="height:220px"></div></section>
    <div class="pgrid" id="rg" style="margin-top:22px">${Array.from({ length: 4 }, () => '<div class="skeleton" style="height:150px;border-radius:12px"></div>').join("")}</div>
    <section class="sec"><div class="label">Qué puedes hacer</div><div class="quick">
      <a href="#/radio/repetidores">${icon("tower")}Buscar repetidores</a><a href="#/radio/satelites">${icon("satellite")}Pasos de satélites</a><a href="#/radio/portatil">${icon("tent")}Operar en portátil</a>
      <a href="#/radio/qsl">${icon("log")}Cuaderno QSL</a><a href="#/radio/herramientas">${icon("tools")}Herramientas</a><a href="#/learn/tropo">${icon("book")}Guías</a></div></section>
    <p class="empty" style="margin-top:20px">Todo lo que ves aquí está disponible por la <a href="/api/openapi.json">API</a> y el <a href="#/learn/mcp">servidor MCP</a>.</p>`;
  ctx.marker(at.lon, at.lat, { label: at.name, cls: "on" }); ctx.fly(at.lat, at.lon, 6);
  const [solar, prop, astro, sats, reps, port, st, ven] = await Promise.all([
    api("/api/solar").catch(() => null), api(`/api/propagation?lat=${at.lat}&lon=${at.lon}`).catch(() => null), api(`/api/astro?lat=${at.lat}&lon=${at.lon}`).catch(() => null),
    tmo(api(`/api/satellites/passes?lat=${at.lat}&lon=${at.lon}&hours=24&min_el=15`)).catch(() => null), api(`/api/repeaters/near?lat=${at.lat}&lon=${at.lon}&km=70&limit=4`).catch(() => []),
    api(`/api/portable?lat=${at.lat}&lon=${at.lon}`).catch(() => null), qsl.quickStats().catch(() => null), api(`/api/radio/ventanas?lat=${at.lat}&lon=${at.lon}`).catch(() => null)]);
  if (my !== req || !$("#rg")) return;
  const w = prop?.peor, time = (iso) => new Date(iso).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
  const next = sats?.pasos?.[0], win = port?.mejores_ventanas?.[0], T = (ven?.tramos || []).slice(0, 16);
  const dayH = (iso) => { const d = new Date(iso), td = new Date().toDateString() === d.toDateString(); return `${td ? "" : d.toLocaleDateString("es", { weekday: "short" }).replace(".", "") + " "}${String(d.getHours()).padStart(2, "0")} h`; };
  // titular: el mejor tramo de HF, la tropo y el próximo pase (todo de la API; nada inventado)
  const best = T.reduce((m, x) => (x.hf?.nota > (m?.hf?.nota ?? -1) ? x : m), null), bits = [];
  if (best) bits.push(`Mejor rato para HF: ${dayH(best.desde)}–${dayH(best.hasta).replace(/^\D+ /, "")}, con ${best.hf.bandas.slice(0, 3).join(", ")} (${best.hf.motivo.charAt(0).toLowerCase() + best.hf.motivo.slice(1)})`);
  if (w) bits.push(`tropo en VHF/UHF: ${w.cat}`);
  if (next) bits.push(`${next.satelite} pasa a las ${time(next.aos)} a ${next.max_el}°`);
  $("#rbrief").innerHTML = `${hoySeal("Para operar hoy", "", "NOAA SWPC · modelo de Guilda por tramos")}<h2 class="hoy-h1">${esc(bits.length ? bits.join(". ") + "." : "Sin datos suficientes para recomendar ahora mismo.")}</h2>`;
  const k = [["Índice Kp", solar?.kp != null ? nf1.format(solar.kp) : "–", solar?.resumen?.[0] || "NOAA SWPC"], ["Flujo solar (SFI)", solar?.sfi ?? "–", solar?.sfi == null ? "sin dato ahora" : "unidades de flujo solar"],
    ["Tropo 72 h", w ? w.cat : "–", w ? `peor tramo: ${time(w.time)}` : "modelo de gradiente"], ["Próximo pase", next ? time(next.aos) : "–", next ? `${next.satelite} · ${next.max_el}° máx.` : "sin pasos > 15° en 24 h"]];
  $("#rkpis").innerHTML = k.map(([l, v, s]) => `<div class="kpi"><span class="kpl">${l}</span><span class="kpv">${esc(String(v))}</span><span class="kps">${esc(s)}</span></div>`).join("");
  // mapa de calor: bandas recomendadas por tramo de 3 h (luz, línea gris y flujo solar) y tropo en VHF/UHF
  const BANDS = ["160 m", "80 m", "60 m", "40 m", "30 m", "20 m", "17 m", "15 m", "12 m", "10 m", "6 m"];
  const lvlHf = (x, b) => (!x.hf?.bandas?.includes(b) ? 0 : x.hf.nota >= 70 ? 3 : x.hf.nota >= 50 ? 2 : 1);
  const lvlTr = (x) => (x.tropo?.nota >= 70 ? 3 : x.tropo?.nota >= 50 ? 2 : x.tropo?.nota >= 35 ? 1 : 0);
  const used = BANDS.filter((b) => T.some((x) => x.hf?.bandas?.includes(b)));
  const rows = [...used.map((b) => [b, T.map((x) => [lvlHf(x, b), `${b} · ${dayH(x.desde)} · ${x.hf.motivo}`])]), ["VHF tropo", T.map((x) => [lvlTr(x), `Tropo · ${dayH(x.desde)} · ${x.tropo?.motivo || ""}`])]];
  $("#rheat").innerHTML = T.length ? `<div class="hc-head"><div><h2>Bandas por tramo · próximas ${T.length * 3} h</h2><p>Bandas recomendadas en cada tramo de 3 h según luz, línea gris y flujo solar; tropo según el gradiente de refractividad</p></div>
    <div class="hm-leg"><span>Cerrada</span><i class="l0"></i><i class="l1"></i><i class="l2"></i><i class="l3"></i><span>Buena</span></div></div>
    <div class="tablewrap"><div class="hm" style="--n:${T.length}">${rows.map(([n, cells]) => `<span class="hm-n num">${esc(n)}</span>${cells.map(([l, t]) => `<span class="hm-c l${l}" title="${esc(t)}"></span>`).join("")}`).join("")}
      <span></span>${T.map((x, i) => `<span class="hm-x num">${i % 2 ? "" : String(new Date(x.desde).getHours()).padStart(2, "0")}</span>`).join("")}</div></div>
    <p class="hc-src">Modelo simplificado de Guilda (no es VOACAP). Para predicciones finas usa VOACAP o DX Toolbox.</p>` : '<p class="empty">Sin datos de ventanas ahora mismo.</p>';
  const card = (ic, t, link, body) => `<a class="card rcard" href="${link}"><h3>${icon(ic, 16)} ${t}</h3>${body}</a>`;
  $("#rg").innerHTML = [
    card("tower", "Repetidores cerca", "#/radio/repetidores", reps?.length ? reps.map((r) => `<div class="kv"><span>${esc(r.callsign)} <span class="tag">${esc(r.mode)}</span></span><span>${fmtF(r.freq_mhz)}</span></div>`).join("") : '<p class="empty">Sin repetidores en 70 km (o catálogo aún cargando).</p>'),
    card("tent", "Salir a operar", "#/radio/portatil", win ? `<div class="big" style="font-size:22px">${time(win.desde)}–${time(win.hasta)}</div><p class="empty">Mejor ventana de ${win.horas} h. ${port.horas_con_tormenta.length ? "Hay riesgo de tormenta: revisa." : "Sin tormentas previstas."}</p>` : '<p class="empty">Sin ventanas limpias en 48 h.</p>'),
    card("sun", "Sol y Luna", "#/radio/propagacion", astro ? `<div class="kv"><span>Orto / ocaso</span><span>${time(astro.orto)} / ${time(astro.ocaso)}</span></div><div class="kv"><span>Luna</span><span>${esc(astro.luna.fase)}</span></div><div class="kv"><span>Iluminación</span><span>${astro.luna.iluminacion_pct}%</span></div>` : ""),
    card("log", "Mi cuaderno", "#/radio/qsl", st ? `<div class="big">${st.total}</div><p class="empty">contactos · ${st.confirmados} confirmados · ${st.indicativos} indicativos</p>` : '<p class="empty">Aún no hay contactos.</p>'),
  ].join("");
}

/* ---------- Repetidores ---------- */
let rep = { band: "", mode: "", q: "", km: 0, reach: false, reachIds: new Set(), rows: [], sel: null, cov: true, ctl: null };
async function repetidores(selId) {
  const my = req;
  ctx.colb.innerHTML = `${head("Repetidores", "FM, DMR, D-STAR y C4FM de BrandMeister y listados abiertos")}
    <div class="row" style="margin:18px 0"><input id="rq" type="search" placeholder="Indicativo o ciudad…" style="max-width:220px" value="${esc(rep.q)}">
      <select id="rb" style="width:auto"><option value="">Todas las bandas</option><option>10m</option><option>6m</option><option>2m</option><option>70cm</option><option>23cm</option></select>
      <span class="seg" id="rm"><button type="button" data-m="" aria-pressed="true">Todos</button>${Object.keys(MODE_COL).map((m) => `<button type="button" data-m="${m}" aria-pressed="false">${m}</button>`).join("")}</span></div>
    <div class="row" style="margin:-6px 0 14px"><select id="rd" style="width:auto" aria-label="Distancia máxima">${[[0, "Cualquier distancia"], [25, "Hasta 25 km"], [50, "Hasta 50 km"], [100, "Hasta 100 km"], [200, "Hasta 200 km"]].map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select>
      <button class="chip" id="rreach" type="button" aria-pressed="false" title="Solo repetidores cuya cobertura estimada (con el relieve) llega a tu QTH">Llegan a mi QTH</button><span class="empty" id="rreachmsg" style="font-size:12.5px"></span></div>
    <div class="legend" id="rleg" style="margin:0 0 12px">${Object.entries(MODE_COL).map(([m, c]) => `<span><i style="border-color:${c}"></i>${m}</span>`).join("")}<span id="rstat"></span></div>
    <div id="rdetail"></div><div id="rlist"><div class="skeleton" style="height:90px"></div></div>
    <p class="empty" style="margin-top:18px">Datos de <a href="https://brandmeister.network" target="_blank" rel="noopener">BrandMeister</a> y <a href="https://hearham.com" target="_blank" rel="noopener">HearHam</a>. Pueden contener errores o estar desactualizados: confirma en la web del repetidor o de tu asociación. La cobertura es una <b>estimación por línea de vista</b>, no una garantía.</p>`;
  $("#rd").value = String(rep.km); $("#rd").addEventListener("change", (e) => { rep.km = +e.target.value; paintReps(); });
  $("#rreach").setAttribute("aria-pressed", String(rep.reach));
  $("#rreach").addEventListener("click", async () => {
    if (getQth()?.lat == null) { ctx.toast("Indica primero tu locator (QTH)"); return; }
    rep.reach = !rep.reach; $("#rreach").setAttribute("aria-pressed", String(rep.reach)); await loadReach(); paintReps();
  });
  $("#rb").value = rep.band; $$("#rm button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.m === rep.mode)));
  const reload = debounce(() => loadReps(), 250);
  $("#rq").addEventListener("input", (e) => { rep.q = e.target.value; reload(); });
  $("#rb").addEventListener("change", (e) => { rep.band = e.target.value; loadReps(); });
  $("#rm").addEventListener("click", (e) => { const b = e.target.closest("[data-m]"); if (!b) return; rep.mode = b.dataset.m; $$("#rm button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); loadReps(); });
  api("/api/repeaters/stats").then((s) => { if ($("#rstat")) $("#rstat").textContent = `${nf.format(s.total)} en el catálogo`; }).catch(() => {});
  ctx.mapCtl('<button class="chip" id="rcov" type="button" aria-pressed="true">Cobertura</button><button class="chip" id="rchirp" type="button">Exportar FM (CHIRP)</button>');
  $("#rcov").addEventListener("click", () => { rep.cov = !rep.cov; $("#rcov").setAttribute("aria-pressed", String(rep.cov)); drawCov(); });
  $("#rchirp").addEventListener("click", exportChirp);
  const at = here(); ctx.marker(at.lon, at.lat, { label: at.name, cls: "fav" });
  await ctx.mapReady; if (my !== req) return;
  const m = ctx.map;
  m.addSource("reps", { type: "geojson", data: { type: "FeatureCollection", features: [] }, cluster: true, clusterRadius: 38, clusterMaxZoom: 11 });
  m.addLayer({ id: "rep-cl", type: "circle", source: "reps", filter: ["has", "point_count"], paint: { "circle-color": "#5c6670", "circle-radius": ["step", ["get", "point_count"], 9, 10, 13, 40, 18, 120, 24], "circle-opacity": 0.78, "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
  m.addLayer({ id: "rep-pt", type: "circle", source: "reps", filter: ["!", ["has", "point_count"]], paint: { "circle-radius": 6, "circle-stroke-width": 2, "circle-stroke-color": "#fff",
    "circle-color": ["match", ["get", "mode"], "DMR", MODE_COL.DMR, "D-STAR", MODE_COL["D-STAR"], "C4FM", MODE_COL.C4FM, MODE_COL.FM] } });
  m.on("click", "rep-cl", (e) => { m.easeTo({ center: e.features[0].geometry.coordinates, zoom: m.getZoom() + 2 }); e.originalEvent.__st = true; });
  m.on("click", "rep-pt", (e) => { const id = e.features[0].properties.id; e.originalEvent.__st = true; location.hash = `#/radio/repetidores/${id}`; });
  ["rep-cl", "rep-pt"].forEach((l) => { m.on("mouseenter", l, () => (m.getCanvas().style.cursor = "pointer")); m.on("mouseleave", l, () => (m.getCanvas().style.cursor = "")); });
  if (rep.ctl) m.off("moveend", rep.ctl);
  rep.ctl = debounce(() => { if (sub === "repetidores") loadReps(true); }, 350); m.on("moveend", rep.ctl);
  m.easeTo({ center: [at.lon, at.lat], zoom: Math.max(m.getZoom(), 7), duration: 0 });
  await loadReps(); if (selId) pick(+selId, true);
}
async function loadReach() {
  const msg = $("#rreachmsg"); if (msg) msg.textContent = "";
  if (!rep.reach) { rep.reachIds = new Set(); return; }
  const at = here();
  try {
    const p = new URLSearchParams({ lat: at.lat, lon: at.lon, km: "200", limit: "300" }); if (rep.band) p.set("band", rep.band); if (rep.mode) p.set("mode", rep.mode);
    const near = await api(`/api/repeaters/near?${p}`);
    rep.reachIds = new Set(near.filter((r) => r.covers_point).map((r) => r.id));
    if (msg) msg.textContent = `${rep.reachIds.size} con cobertura calculada que llega a ${at.name}. Los que aún no la tienen no aparecen: ábrelos y pulsa «Calcular cobertura».`;
  } catch (e) { if (msg) msg.textContent = e.message; }
}
async function loadReps(fromMove) {
  const m = ctx.map; if (!m || !m.getSource("reps")) return;
  const b = m.getBounds(), p = new URLSearchParams({ bbox: `${b.getSouth()},${b.getWest()},${b.getNorth()},${b.getEast()}`, limit: "2500" });
  if (rep.band) p.set("band", rep.band); if (rep.mode) p.set("mode", rep.mode); if (rep.q) p.set("q", rep.q);
  let rows; try { rows = await api(`/api/repeaters?${p}`); } catch (e) { if ($("#rlist")) $("#rlist").innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  rep.rows = rows;
  m.getSource("reps")?.setData({ type: "FeatureCollection", features: rows.map((r) => ({ type: "Feature", geometry: { type: "Point", coordinates: [r.lon, r.lat] }, properties: { id: r.id, mode: r.mode, call: r.callsign } })) });
  if (rep.q && rows.length && !fromMove) ctx.fit(rows.slice(0, 50).map((r) => [r.lon, r.lat]), { maxZoom: 10 });
  paintReps();
}
function paintReps() {
  const box = $("#rlist"); if (!box) return;
  const at = here(), all = rep.rows.map((r) => ({ ...r, d: distKm(at, r) })), list = all.filter((r) => (!rep.km || r.d <= rep.km) && (!rep.reach || rep.reachIds.has(r.id))).sort((a, b) => a.d - b.d).slice(0, 60);
  if (all.length && !list.length) { box.innerHTML = `<p class="empty">Ninguno cumple ${rep.reach ? "«Llegan a mi QTH»" : ""}${rep.reach && rep.km ? " y " : ""}${rep.km ? `estar a menos de ${rep.km} km de ${esc(at.name)}` : ""}. Quita un filtro o aleja el mapa.</p>`; return; }
  if (!rep.rows.length) { box.innerHTML = '<p class="empty">No hay repetidores en esta zona con esos filtros. Prueba a alejar el mapa o a quitar filtros. Si el catálogo está vacío, el servidor aún no lo ha sincronizado.</p>'; return; }
  box.innerHTML = `<p class="empty" style="margin-bottom:4px">${nf.format(list.length)}${list.length === 60 ? "+" : ""} de ${nf.format(rep.rows.length)} en el área visible, de más cerca a más lejos de ${esc(at.name)}.</p>` + list.map((r) => `<button class="rep" data-id="${r.id}" type="button"><span class="f">${fmtF(r.freq_mhz)} MHz</span>
    <span class="meta"><span class="tag" style="background:${MODE_COL[r.mode] || "#888"}22;color:${MODE_COL[r.mode] || "inherit"}">${esc(r.mode)}</span>${nf.format(r.d)} km ${compass(bearing(at, r))}</span>
    <span class="meta"><b>${esc(r.callsign || "")}</b> ${esc(r.city || r.name)}${r.tone_hz ? ` · CTCSS ${r.tone_hz} Hz` : ""}${r.color_code != null ? ` · CC${r.color_code}` : ""}${r.shift_khz ? ` · ${r.shift_khz > 0 ? "+" : "−"}${Math.abs(r.shift_khz) >= 1000 ? Math.abs(r.shift_khz) / 1000 + " MHz" : Math.abs(r.shift_khz) + " kHz"}` : ""}</span></button>`).join("");
  box.onclick = (e) => { const b = e.target.closest("[data-id]"); if (b) location.hash = `#/radio/repetidores/${b.dataset.id}`; };
}
async function pick(id, fly) {
  let r = rep.rows.find((x) => x.id === id) || await api(`/api/repeaters/${id}`).catch(() => null);
  if (!r) return;
  rep.sel = r;
  const d = $("#rdetail"); if (!d) return;
  const tx = r.freq_mhz + (r.shift_khz || 0) / 1000, at = here(), dist = distKm(at, r), bm = bmLink(r);
  const shift = (k) => (k ? (k > 0 ? "+" : "−") + (Math.abs(k) >= 1000 ? `${nf.format(Math.abs(k) / 1000)} MHz` : `${Math.abs(k)} kHz`) : "simplex");
  const chip = (t) => `<span class="rchip">${t}</span>`;
  d.innerHTML = `<div class="card rsel"><div class="rs-head"><div><div class="f big" style="font-size:30px">${fmtF(r.freq_mhz)} <small style="font-size:14px">MHz</small></div>
    <div class="meta"><b>${esc(r.callsign || "")}</b> · ${esc(r.city || r.name)} · locator ${toLocator(r.lat, r.lon)}</div></div>
    <button class="icon-btn" id="rclose" aria-label="Cerrar ficha">${icon("x")}</button></div>
    <div class="rchips">${chip(esc(r.mode) + (r.network ? ` · ${esc(r.network)}` : ""))}${chip("TX " + fmtF(tx) + " MHz")}${chip("Desplaz. " + shift(r.shift_khz))}${r.tone_hz ? chip("CTCSS " + r.tone_hz + " Hz") : ""}${r.color_code != null ? chip("CC " + r.color_code) : ""}${chip(nf.format(dist) + " km " + compass(bearing(at, r)))}</div>
    <div class="row rs-acts"><button class="btn sm" id="rcalc" type="button">${icon("antenna", 14)} ${r.has_coverage ? "Ver cobertura" : "Calcular cobertura"}</button>
      <button class="btn ghost sm" id="rcopy" type="button">${icon("code", 14)} Copiar ajustes</button>
      ${bm ? `<a class="btn ghost sm" href="${bm}" target="_blank" rel="noopener">${icon("globe", 14)} BrandMeister</a>` : ""}${r.website ? `<a class="btn ghost sm" href="${esc(r.website)}" target="_blank" rel="noopener">${icon("link", 14)} Web</a>` : ""}</div>
    <p class="empty" id="rmsg" style="margin:8px 0 0"></p>
    <div class="grid2" style="margin-top:8px"><div>
      <div class="kv"><span>Modo</span><span>${esc(r.mode)}${r.network ? ` · ${esc(r.network)}` : ""}</span></div>
      <div class="kv"><span>Tú transmites en</span><span>${fmtF(tx)} MHz</span></div>
      <div class="kv"><span>Desplazamiento</span><span>${shift(r.shift_khz)}</span></div>
      ${r.tone_hz ? `<div class="kv"><span>Subtono CTCSS</span><span>${r.tone_hz} Hz</span></div>` : ""}${r.color_code != null ? `<div class="kv"><span>Color code</span><span>${r.color_code}</span></div>` : ""}</div><div>
      ${r.dmr_id ? `<div class="kv"><span>ID DMR</span><span>${r.dmr_id}</span></div>` : ""}${r.power_w ? `<div class="kv"><span>Potencia</span><span>${r.power_w} W</span></div>` : ""}
      <div class="kv"><span>Altura antena</span><span>${nf.format(r.height_m || 10)} m</span></div><div class="kv"><span>Distancia a ${esc(at.name)}</span><span>${nf.format(dist)} km ${compass(bearing(at, r))}</span></div>
      <div class="kv"><span>Fuente</span><span>${esc(r.source || "")}</span></div></div></div></div>`;
  $("#rclose").addEventListener("click", () => { rep.sel = null; location.hash = "#/radio/repetidores"; });
  $("#rcalc").addEventListener("click", async () => {
    $("#rmsg").textContent = "Calculando línea de vista con el relieve (unos segundos)…";
    try { rep.sel = await api(`/api/repeaters/${id}/coverage`, { method: "POST" }); $("#rmsg").textContent = ""; drawCov(); const c = rep.sel.coverage_geojson, q = getQth(); if (c && q?.lat != null) $("#rmsg").textContent = inPoly(c, q) ? "Tu QTH queda dentro de la cobertura estimada." : "Tu QTH queda fuera de la cobertura estimada."; } catch (e) { $("#rmsg").textContent = e.message; }
  });
  $("#rcopy").addEventListener("click", () => navigator.clipboard?.writeText(`RX ${fmtF(r.freq_mhz)} MHz | TX ${fmtF(tx)} MHz | ${r.mode}${r.tone_hz ? ` | CTCSS ${r.tone_hz} Hz` : ""}${r.color_code != null ? ` | CC${r.color_code}` : ""} | ${r.callsign || r.name}`).then(() => ctx.toast("Ajustes copiados")));
  if (fly) ctx.fly(r.lat, r.lon, 10);
  ctx.markers.forEach((x) => x.kind === "sel" && ctx.removeMarker(x));
  const mk = ctx.marker(r.lon, r.lat, { label: r.callsign || r.name, cls: "on rep" }); if (mk) mk.kind = "sel";
  drawCov();
}
function inPoly(geo, p) { const ring = geo.coordinates[0]; let inside = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const [xi, yi] = ring[i], [xj, yj] = ring[j]; if (yi > p.lat !== yj > p.lat && p.lon < ((xj - xi) * (p.lat - yi)) / (yj - yi) + xi) inside = !inside; } return inside; }
function drawCov() {
  const m = ctx.map; if (!m || !ctx.mapLoaded) return;
  ["rep-cov", "rep-cov-l"].forEach((l) => m.getLayer(l) && m.removeLayer(l)); m.getSource("rep-c") && m.removeSource("rep-c");
  const g = rep.sel?.coverage_geojson; if (!g || !rep.cov) return;
  const c = MODE_COL[rep.sel.mode] || "#3a7bd5";
  m.addSource("rep-c", { type: "geojson", data: { type: "Feature", properties: {}, geometry: g } });
  m.addLayer({ id: "rep-cov", type: "fill", source: "rep-c", paint: { "fill-color": c, "fill-opacity": 0.16 } }, "rep-cl");
  m.addLayer({ id: "rep-cov-l", type: "line", source: "rep-c", paint: { "line-color": c, "line-width": 1.6, "line-opacity": 0.8 } }, "rep-cl");
}
function exportChirp() {
  const fm = rep.rows.filter((r) => r.mode === "FM" && r.shift_khz != null && r.freq_mhz).slice(0, 200);
  if (!fm.length) return ctx.toast("No hay repetidores FM en el área visible");
  const head = "Location,Name,Frequency,Duplex,Offset,Tone,rToneFreq,cToneFreq,DtcsCode,DtcsPolarity,RxDtcsCode,CrossMode,Mode,TStep,Skip,Power,Comment,URCALL,RPT1CALL,RPT2CALL,DVCODE";
  const rows = fm.map((r, i) => `${i},${(r.callsign || r.name).slice(0, 8).replace(/,/g, "")},${r.freq_mhz.toFixed(6)},${r.shift_khz > 0 ? "+" : r.shift_khz < 0 ? "-" : ""},${(Math.abs(r.shift_khz) / 1000).toFixed(6)},${r.tone_hz ? "Tone" : ""},${(r.tone_hz || 88.5).toFixed(1)},${(r.tone_hz || 88.5).toFixed(1)},023,NN,023,Tone->Tone,FM,5.00,,,${(r.city || "").replace(/,/g, " ")},,,,`);
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([[head, ...rows].join("\n")], { type: "text/csv" })), download: "radioguilda-repetidores-chirp.csv" }); a.click(); URL.revokeObjectURL(a.href);
  ctx.toast(`${fm.length} repetidores FM exportados`);
}

/* ---------- Propagación + línea gris ---------- */
const CAT = { normal: "var(--ok)", "sub-refracción": "var(--ink-3)", "super-refracción": "var(--warn)", conducto: "var(--bad)" };
export function terminator(date = new Date()) {
  const rad = Math.PI / 180, n = date.getTime() / 86400000 + 2440587.5 - 2451545.0;
  const L = (280.46 + 0.9856474 * n) % 360, g = ((357.528 + 0.9856003 * n) % 360) * rad;
  const lam = (L + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * rad, eps = (23.439 - 0.0000004 * n) * rad;
  const dec = Math.asin(Math.sin(eps) * Math.sin(lam)), ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam)) / rad;
  const gmst = (280.46061837 + 360.98564736629 * n) % 360, ssLon = ((ra - gmst + 540) % 360) - 180;
  const td = Math.abs(dec) < 1e-4 ? 1e-4 : Math.tan(dec), pts = [];
  for (let lon = -180; lon <= 180; lon += 2) pts.push([lon, Math.atan(-Math.cos((lon - ssLon) * rad) / td) / rad]);
  const pole = dec >= 0 ? -90 : 90;                                      // el polo opuesto al Sol está en la noche
  return { poly: [...pts, [180, pole], [-180, pole], pts[0]], ssLon, dec: dec / rad };
}
async function propagacion() {
  const my = req;
  ctx.colb.innerHTML = `${head("Propagación", "Cómo se comportará la señal en VHF/UHF y HF")}${where()}<div id="prop"><div class="skeleton" style="height:200px"></div></div>`;
  const go = () => propagacion(); await fillWhere(go);
  const at = here();
  ctx.marker(at.lon, at.lat, { label: at.name, cls: "on" });
  ctx.mapCtl('<button class="chip" id="gl" type="button">Ver línea gris (mundo)</button>');
  const night = () => onMap(() => { const m = ctx.map, t = terminator(); clearLayers();
    m.addSource("night", { type: "geojson", data: { type: "Feature", geometry: { type: "Polygon", coordinates: [t.poly] } } });
    m.addLayer({ id: "night", type: "fill", source: "night", paint: { "fill-color": "#0b1320", "fill-opacity": 0.28 } });
    m.addLayer({ id: "night-l", type: "line", source: "night", paint: { "line-color": "#e2a548", "line-width": 2, "line-opacity": 0.9 } });
    m.easeTo({ center: [at.lon, 30], zoom: 1.6, duration: 700 }); });
  $("#gl").addEventListener("click", night);
  ctx.fly(at.lat, at.lon, 6);
  try {
    const [p, s] = await Promise.all([api(`/api/propagation?lat=${at.lat}&lon=${at.lon}`), api("/api/solar").catch(() => null)]);
    if (my !== req) return;
    const S = p.serie, w = p.peor;
    $("#prop").innerHTML = `<div class="grid2">
      <div class="card"><h3>${icon("wave", 16)} Troposférica · VHF/UHF/microondas</h3><div class="big" style="color:${w ? CAT[w.cat] : "inherit"}">${w ? esc(w.cat) : "–"}</div>
        <p class="empty" style="margin-top:8px">${w ? `Peor momento previsto: <b>${new Date(w.time).toLocaleString("es", { weekday: "short", hour: "2-digit", minute: "2-digit" })}</b> (${w.gradient} N/km). ` : ""}Gradiente de refractividad entre superficie y ${S[0]?.level_hpa ?? 850} hPa.</p></div>
      <div class="card"><h3>${icon("sun", 16)} HF · actividad solar</h3>${s && (s.kp != null || s.sfi != null) ? `<div class="kv"><span>Índice Kp</span><span>${s.kp ?? "–"}</span></div><div class="kv"><span>Flujo solar (SFI)</span><span>${s.sfi ?? "–"}</span></div>${(s.resumen || []).map((t) => `<p class="empty" style="margin-top:8px">${esc(t)}</p>`).join("")}` : '<p class="empty">Datos de NOAA SWPC no disponibles ahora.</p>'}</div>
      <div class="card"><h3>${icon("globe", 16)} Ubicación</h3><div class="kv"><span>Locator</span><span>${esc(p.locator)}</span></div><div class="kv"><span>Coordenadas</span><span>${at.lat.toFixed(3)}, ${at.lon.toFixed(3)}</span></div></div></div>
    <section class="sec"><div class="label">Gradiente de refractividad, 72 h <em>más negativo = más alcance</em></div><div class="strip" id="tstrip"></div><div id="tchart"></div>
      <div class="legend">${Object.entries(CAT).map(([k, c]) => `<span><i style="border-color:${c}"></i>${k}</span>`).join("")}</div>
      <p class="empty" style="margin-top:10px">${esc(p.nota)}</p><p class="empty" style="margin-top:8px">Más información: <a href="#/learn/tropo">propagación troposférica</a> · <a href="#/learn/hf">HF y actividad solar</a>. La <b>línea gris</b> (botón del mapa) marca el amanecer/atardecer, cuando los contactos DX en HF suelen mejorar.</p></section>`;
    const ax = (it) => it.map((x, i) => ({ i, label: x.time.slice(11, 13) + "h", major: false })).filter((a) => a.i % 6 === 0);
    renderStrip($("#tstrip"), S, [{ label: "Tropo", val: (i) => S[i].gradient, color: (v) => (v <= -157 ? CAT.conducto : v <= -79 ? CAT["super-refracción"] : v > 0 ? CAT["sub-refracción"] : CAT.normal), text: () => "" }], ax);
    renderCharts($("#tchart"), S, [{ title: "Gradiente N/km", sub: "", draw: "line", H: 160, range: (it) => [Math.min(-175, ...it.map((x) => x.gradient)) - 10, Math.max(20, ...it.map((x) => x.gradient)) + 10], mean: (i) => S[i].gradient,
      refs: [{ v: -79, label: "super-refracción" }, { v: -157, label: "conducto" }], tip: (i) => `${new Date(S[i].time).toLocaleString("es", { weekday: "short", hour: "2-digit" })} · <b>${S[i].gradient} N/km</b> · ${S[i].cat}` }], ax);
  } catch (e) { $("#prop").innerHTML = `<div class="err"><strong>No se pudo calcular</strong><p class="empty">${esc(e.message)}</p></div>`; }
}

/* ---------- Satélites ---------- */
let satTimer;
function stopSat() { clearInterval(satTimer); }
const pad2 = (n) => String(n).padStart(2, "0");
function ics(p) {
  const f = (iso) => new Date(iso).toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  return `BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Radio Guilda//ES\r\nBEGIN:VEVENT\r\nUID:${p.norad}-${f(p.aos)}@radio.guildawork.com\r\nDTSTAMP:${f(new Date().toISOString())}\r\nDTSTART:${f(p.aos)}\r\nDTEND:${f(p.los)}\r\nSUMMARY:Pase ${p.satelite} (${p.max_el}° máx.)\r\nDESCRIPTION:AOS azimut ${p.aos_az}° · máx. ${p.max_el}° · LOS azimut ${p.los_az}°. Radio Guilda\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n`;
}
function skyplot(p) {
  const S = 220, c = S / 2, R = 92, pt = ([az, el]) => { const r = ((90 - el) / 90) * R, a = ((az - 90) * Math.PI) / 180; return [c + r * Math.cos(a), c + r * Math.sin(a)]; };
  const path = p.traza.map((t, i) => (i ? "L" : "M") + pt(t).map((v) => v.toFixed(1)).join(",")).join("");
  const a = pt(p.traza[0]), z = pt(p.traza.at(-1)), mx = pt([p.max_el_az, p.max_el]);
  return `<svg viewBox="0 0 ${S} ${S}" role="img" aria-label="Trayectoria en el cielo">${[1, 2 / 3, 1 / 3].map((f) => `<circle cx="${c}" cy="${c}" r="${R * f}" class="rose-ring"/>`).join("")}<path d="M${c - R},${c}H${c + R}M${c},${c - R}V${c + R}" class="rose-ring"/>
    ${[["N", 0], ["E", 90], ["S", 180], ["O", 270]].map(([t, d]) => { const q = ((d - 90) * Math.PI) / 180; return `<text x="${c + (R + 12) * Math.cos(q)}" y="${c + (R + 12) * Math.sin(q) + 4}" text-anchor="middle" class="rose-l">${t}</text>`; }).join("")}
    <path d="${path}" class="sat-path"/><circle cx="${a[0]}" cy="${a[1]}" r="4" class="sat-aos"/><circle cx="${z[0]}" cy="${z[1]}" r="4" class="sat-los"/><circle cx="${mx[0]}" cy="${mx[1]}" r="3" class="sat-max"/></svg>`;
}
async function satelites() {
  const my = req, at = here();
  ctx.colb.innerHTML = `${head("Satélites", "Pasos de la ISS y de satélites de aficionados sobre tu ubicación")}${where()}
    <div class="row" style="margin-bottom:16px"><span class="lbl">Elevación mínima</span><span class="seg" id="mel">${[10, 20, 30].map((v) => `<button type="button" data-v="${v}" aria-pressed="${v === (satelites.el || 10)}">${v}°</button>`).join("")}</span></div>
    <div id="sats"><div class="skeleton" style="height:140px"></div></div>`;
  await fillWhere(() => satelites());
  ctx.marker(at.lon, at.lat, { label: at.name, cls: "on" }); ctx.fly(at.lat, at.lon, 5);
  $("#mel").addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (b) { satelites.el = +b.dataset.v; satelites(); } });
  try {
    const d = await api(`/api/satellites/passes?lat=${at.lat}&lon=${at.lon}&hours=36&min_el=${satelites.el || 10}`);
    if (my !== req) return;
    const t = (iso) => new Date(iso).toLocaleString("es", { weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    $("#sats").innerHTML = `<div class="card" id="sdet" style="margin-bottom:16px" hidden></div><div class="grid2" id="sp">${d.pasos.slice(0, 14).map((p, i) => `<button class="card pass" data-i="${i}" type="button"><div class="row between"><b>${esc(p.satelite)}</b><span class="pill ${p.max_el >= 45 ? "lv0" : "lv1"}"><i></i>${p.max_el}°</span></div>
      <div class="num" style="font-size:18px;margin:6px 0">${t(p.aos)}</div><div class="meta">${Math.round(p.duracion_s / 60)} min · sale por ${compass(p.aos_az)} · entra por ${compass(p.los_az)}</div></button>`).join("") || '<p class="empty">No hay pasos con esa elevación en 36 h.</p>'}</div>
    <section class="sec"><div class="label">Satélites seguidos</div><p class="empty">${d.satelites.map((s) => esc(s.nombre)).join(" · ")}</p>
      <p class="empty" style="margin-top:10px"><b>ISS:</b> APRS en ${esc(d.iss.aprs)}; voz: ${esc(d.iss.voz)}. ${esc(d.nota)}</p></section>`;
    const show1 = (i) => { const p = d.pasos[i], box = $("#sdet"); box.hidden = false;
      box.innerHTML = `<div class="row between" style="align-items:flex-start"><div><h3 style="margin:0">${esc(p.satelite)}</h3><p class="empty">${t(p.aos)} → ${new Date(p.los).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })} (hora de tu dispositivo)</p></div>
        <span class="row" style="gap:6px;flex-wrap:nowrap"><button class="btn ghost sm" id="alarm" type="button" aria-pressed="${alarms.has(p.aos)}">${icon("bell", 14)} ${alarms.has(p.aos) ? "Aviso activado" : "Avisarme 5 min antes"}</button><button class="btn ghost sm" id="ics" type="button">${icon("download", 14)} Añadir al calendario</button></span></div><div class="grid2"><div class="skyplot">${skyplot(p)}</div><div>
        <div class="kv"><span>Aparece</span><span>${compass(p.aos_az)} (${p.aos_az}°)</span></div><div class="kv"><span>Máxima elevación</span><span>${p.max_el}° al ${compass(p.max_el_az)}</span></div><div class="kv"><span>Desaparece</span><span>${compass(p.los_az)} (${p.los_az}°)</span></div>
        <div class="kv"><span>Distancia mínima</span><span>${nf.format(p.distancia_min_km)} km</span></div><div class="kv"><span>Duración</span><span>${Math.floor(p.duracion_s / 60)}:${pad2(p.duracion_s % 60)}</span></div></div></div>`;
      $("#alarm").addEventListener("click", () => toggleAlarm(p, () => show1(i)));
      $("#ics").addEventListener("click", () => { const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([ics(p)], { type: "text/calendar" })), download: `pase-${p.satelite.replace(/\W+/g, "-")}.ics` }); a.click(); URL.revokeObjectURL(a.href); });
      $$("#sp .pass").forEach((b, k) => b.setAttribute("aria-pressed", String(k === i))); };
    $("#sp")?.addEventListener("click", (e) => { const b = e.target.closest("[data-i]"); if (b) show1(+b.dataset.i); });
    if (d.pasos.length) show1(0);
  } catch (e) { $("#sats").innerHTML = `<div class="err"><strong>No se pudieron calcular los pasos</strong><p class="empty">${esc(e.message)}</p></div>`; }
}

/* ---------- aviso de paso: solo mientras esta pestaña esté abierta (no hay servidor de notificaciones push) ---------- */
const alarms = new Map();
async function toggleAlarm(p, redraw) {
  if (alarms.has(p.aos)) { clearTimeout(alarms.get(p.aos)); alarms.delete(p.aos); ctx.toast("Aviso cancelado"); return redraw(); }
  const ms = new Date(p.aos).getTime() - Date.now() - 5 * 60000;
  if (ms < -4 * 60000) { ctx.toast("Ese paso ya ha empezado o está a punto"); return; }
  if ("Notification" in window && Notification.permission === "default") { try { await Notification.requestPermission(); } catch { /* sin permiso: se usa el aviso en pantalla */ } }
  const fire = () => {
    alarms.delete(p.aos);
    const txt = `${p.satelite}: pasa en 5 min (máx. ${p.max_el}°, sale por ${compass(p.aos_az)})`;
    try { if (Notification.permission === "granted") { new Notification("Paso de satélite", { body: txt, tag: p.aos }); return; } } catch { /* cae al aviso en pantalla */ }
    ctx.toast(txt);
  };
  alarms.set(p.aos, setTimeout(fire, Math.max(0, ms)));
  ctx.toast(`Te aviso 5 min antes · deja esta pestaña abierta${"Notification" in window && Notification.permission !== "granted" ? " (sin permiso de notificaciones: aviso en pantalla)" : ""}`);
  redraw();
}

/* ---------- Operación portátil ---------- */
async function portatil() {
  const my = req;
  ctx.colb.innerHTML = `${head("Operación portátil", "Mejor ventana para operar al aire libre (SOTA, POTA, campo) y alertas para tu antena")}${where()}<div id="pt"><div class="skeleton" style="height:180px"></div></div>`;
  await fillWhere(() => portatil());
  const at = here(); ctx.marker(at.lon, at.lat, { label: at.name, cls: "on" }); ctx.fly(at.lat, at.lon, 8);
  try {
    const d = await api(`/api/portable?lat=${at.lat}&lon=${at.lon}`);
    if (my !== req) return;
    const H = d.horas, tm = (iso) => new Date(iso).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
    const col = ["#2c8a5a", "#e2a548", "#c0392b"];
    $("#pt").innerHTML = `${d.horas_con_tormenta.length ? `<div class="warn-note" style="margin-top:0">${icon("bolt", 18)}<span><b>Riesgo de tormenta</b> a las ${[...new Set(d.horas_con_tormenta.map((t) => tm(t)))].slice(0, 6).join(", ")}. Desconecta antenas y no operes al aire libre con tormenta cerca.</span></div>` : ""}
      <div class="kv" style="margin:10px 0"><span>Ubicación</span><span>${esc(d.ubicacion.locator)} · ${nf.format(d.ubicacion.altitud_m ?? 0)} m</span></div>
      <section class="sec" style="margin-top:12px"><div class="label">Mejores ventanas</div>${d.mejores_ventanas.length ? `<div class="windows">${d.mejores_ventanas.map((w) => `<div class="win"><span class="pill lv0"><i></i>${w.horas} h limpias</span><b>${new Date(w.desde).toLocaleString("es", { weekday: "short", hour: "2-digit", minute: "2-digit" })} – ${tm(w.hasta)}</b><small>Sin rachas peligrosas, lluvia ni tormentas.</small></div>`).join("")}</div>` : '<p class="empty">No hay tramos de 2 h o más sin incidencias en las próximas 48 h.</p>'}</section>
      <section class="sec"><div class="label">Próximas 48 h <em>verde = bien, ámbar = precaución, rojo = no</em></div><div class="strip" id="pstrip"></div>
        <div class="legend" style="margin-top:8px"><span>${icon("wind", 13)} racha km/h</span><span>${icon("drop", 13)} lluvia mm/h</span></div></section>
      <section class="sec"><div class="label">Antes de montar</div><ul class="chk"><li>Tirantes y piquetas según el viento previsto; baja el mástil si hay rachas fuertes.</li><li>Pon una buena toma de tierra y usa cable con menos de 5 m expuesto a la tormenta.</li><li>Avisa a alguien de tu ruta y de la hora de vuelta; el móvil puede no tener cobertura.</li><li>Comprueba la hora del ocaso: volver de noche suma riesgo.</li></ul></section>
      <p class="empty">${esc(d.aviso)}</p>`;
    const ax = (it) => it.map((x, i) => ({ i, label: hh(x.t.slice(0, 16)), major: false })).filter((a) => a.i % 4 === 0);
    const lv = (i) => H[i].nivel;
    renderStrip($("#pstrip"), H, [
      { label: "Riesgo", val: (i) => lv(i), color: (v) => col[v], text: () => "", tip: (i) => `${tm(H[i].t)}: ${H[i].motivos.join(" · ") || "sin incidencias"}` },
      { label: "Racha", val: (i) => H[i].racha_kmh, color: (v) => (v >= 60 ? "#e6a99f" : v >= 40 ? "#f0d9a6" : "#e8ece6"), text: (v) => nf.format(v) },
      { label: "Lluvia", val: (i) => H[i].lluvia_mm, color: (v) => (v >= 3 ? "#7e9fd0" : v >= 0.5 ? "#c4d4ec" : "#eef1f4"), text: (v) => (v < 0.05 ? "" : nf1.format(v)) },
    ], ax);
  } catch (e) { $("#pt").innerHTML = `<div class="err"><strong>No se pudo calcular</strong><p class="empty">${esc(e.message)}</p></div>`; }
}

/* ---------- Ventanas: cuándo operar ---------- */
const NOTA_COL = (v) => (v >= 70 ? "#2c8a5a" : v >= 40 ? "#9bc27a" : v >= 20 ? "#e8e0b0" : "#eceeea");
const NOTA_INK = (v) => (v >= 70 ? "#fff" : "#14191f");
async function ventanas() {
  const my = req, modo = LS.get("mg-radio-modo", "fijo");
  ctx.colb.innerHTML = `${head("Ventanas de operación", "Las próximas 48 h en tramos de 3 h: HF, tropo VHF/UHF, satélites y, si sales, el tiempo para tu antena")}${where()}
    <div class="row" style="margin:-4px 0 16px"><span class="lbl">Modo</span><span class="seg" id="vmodo">${[["fijo", "QTH fijo"], ["portatil", "POTA · SOTA · móvil"]].map(([v, t]) => `<button type="button" data-v="${v}" aria-pressed="${modo === v}">${t}</button>`).join("")}</span></div>
    <div id="vv"><div class="skeleton" style="height:220px"></div></div>`;
  await fillWhere(() => ventanas());
  $("#vmodo").addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (b) { LS.set("mg-radio-modo", b.dataset.v); pushSettings(); ventanas(); } });
  const at = here(); ctx.marker(at.lon, at.lat, { label: at.name, cls: "on" }); ctx.fly(at.lat, at.lon, 8);
  try {
    const [d, mias] = await Promise.all([api(`/api/radio/ventanas?lat=${at.lat}&lon=${at.lon}&modo=${modo}`), qsl.bandasHabituales().catch(() => [])]);
    if (my !== req) return;
    const mio = new Set(mias.map((x) => x.k)), esMia = (b) => mio.has(String(b).toLowerCase().replace(/\s/g, ""));          // tus bandas van primero y con estrella
    const T = d.tramos, hm = (iso) => new Date(iso).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }), dia = (iso) => new Date(iso).toLocaleDateString("es", { weekday: "short", day: "numeric" }).replace(".", "");
    const cuando = (iso) => `${dia(iso)} ${hm(iso)}–${hm(new Date(+new Date(iso) + 3 * 3600e3))}`;
    const nombre = { hf: "HF", tropo: "Tropo VHF/UHF", sat: "Satélites", general: "Mejor momento" };
    const ok = (t) => modo !== "portatil" || t.campo.nivel < 2;                       // en portátil, un tramo con tiempo rojo no se recomienda
    const mejorDe = (capa) => T.filter(ok).reduce((m, t) => (t[capa].nota > (m?.[capa].nota ?? 39) ? t : m), null);
    const mejores = [["hf", mejorDe("hf")], ["tropo", mejorDe("tropo")], ["sat", mejorDe("sat")]].filter(([, t]) => t);
    const capaTxt = { hf: (t) => `${[...t.hf.bandas].sort((a, b) => esMia(b) - esMia(a)).slice(0, 3).map((b) => (esMia(b) ? `★ ${esc(b)}` : esc(b))).join(", ")} <small>${esc(t.hf.motivo)}</small>`, tropo: (t) => `${esc(t.tropo.cat)} <small>${esc(t.tropo.motivo)}</small>`,
      sat: (t) => `${esc(t.sat.pasos[0]?.satelite || "")} a ${t.sat.pasos[0]?.max_el}° <small>paso a las ${hm(t.sat.pasos[0]?.aos)}</small>` };
    const aviso = modo === "portatil" ? T.filter((t) => t.campo.nivel === 2) : [];
    const fuentesOff = Object.entries({ tropo: "tropo", solar: "estado solar (Kp/SFI)", satelites: "satélites" }).filter(([k]) => !d.fuentes[k]).map(([, v]) => v);
    $("#vv").innerHTML = `${aviso.length ? `<div class="warn-note" style="margin-top:0">${icon("bolt", 18)}<span><b>Tiempo adverso para operar fuera</b>: ${[...new Set(aviso.flatMap((t) => t.campo.motivos))].slice(0, 3).map(esc).join(" · ")}. Esos tramos salen en cero.</span></div>` : ""}
      <section class="sec"><div class="label">Mejores momentos <em>${modo === "portatil" ? "contando el tiempo para la antena" : "para tu QTH"}</em></div>
        ${mias.length ? `<p style="margin:0 0 10px;font-size:13px;color:var(--ink-3)">★ = banda en la que ya has operado (${mias.slice(0, 4).map((x) => `${esc(x.k)} · ${x.n}`).join(", ")}). Salen primero entre las recomendadas.</p>` : ""}
        ${mejores.length ? `<div class="windows">${mejores.map(([c, t]) => `<div class="win"><span class="pill lv0"><i></i>${nombre[c]} · ${t[c].nota}/100</span><b>${esc(cuando(t.desde))}</b><span>${capaTxt[c](t)}</span></div>`).join("")}</div>` : '<p class="empty">Ningún tramo destaca en las próximas 48 h.</p>'}</section>
      <section class="sec"><div class="label">Próximas 48 h <em>más oscuro = mejor</em></div><div class="strip" id="vstrip"></div>
        <div class="legend" style="margin-top:8px"><span>Toca un tramo para ver las notas</span></div></section>
      <section class="sec"><div class="label">Qué mirar en cada capa</div><ul class="chk">
        <li><b>HF</b>: ${d.solar.kp != null ? `Kp ${d.solar.kp}` : "Kp ?"} · SFI ${d.solar.sfi ?? "?"}. Con luz suben las bandas altas, de noche las bajas, y la línea gris (±1 h del orto/ocaso) es la mejor hora para DX.</li>
        <li><b>Tropo</b>: nota alta = gradiente de refractividad favorable (alcances de cientos de km en 144/432 MHz). Los modelos no ven los conductos de capa fina.</li>
        <li><b>Satélites</b>: pasos de ISS y satélites de aficionados de 15° o más; pesa la elevación máxima.</li>
        ${modo === "portatil" ? "<li><b>Campo</b>: rachas, lluvia y tormenta bajan o anulan la nota; en SOTA/POTA no montes mástil con tormenta cerca.</li>" : ""}</ul></section>
      ${fuentesOff.length ? `<p class="empty">Sin datos de: ${fuentesOff.join(", ")}. Esas capas salen sin nota.</p>` : ""}<p class="empty">${esc(d.aviso)}</p>`;
    const items = T.map((t) => ({ time: t.desde })), ax = (it) => it.map((x, i) => ({ i, label: hh(x.time.slice(0, 16)), major: false })).filter((a) => a.i % 2 === 0);
    const rows = [{ label: "Total", val: (i) => T[i].total, color: NOTA_COL, ink: NOTA_INK, text: (v) => v },
      { label: "HF", val: (i) => T[i].hf.nota, color: NOTA_COL, ink: NOTA_INK, text: (v) => v },
      { label: "Tropo", val: (i) => T[i].tropo.nota, color: NOTA_COL, ink: NOTA_INK, text: (v) => v },
      { label: "Satélite", val: (i) => T[i].sat.nota, color: NOTA_COL, ink: NOTA_INK, text: (v) => (v ? v : "") }];
    if (modo === "portatil") rows.push({ label: "Campo", val: (i) => T[i].campo.nivel, color: (v) => ["#2c8a5a", "#e2a548", "#c0392b"][v], ink: () => "#fff", text: (v) => ["ok", "!", "×"][v] });
    renderStrip($("#vstrip"), items, rows, ax);
  } catch (e) { $("#vv").innerHTML = `<div class="err"><strong>No se pudo calcular</strong><p class="empty">${esc(e.message)}</p></div>`; }
}

/* ---------- Herramientas ---------- */
const Q = [["QRA", "Nombre de mi estación es…"], ["QRG", "Frecuencia exacta"], ["QRK", "Inteligibilidad de sus señales"], ["QRL", "Frecuencia ocupada"], ["QRM", "Interferencias de otras estaciones"], ["QRN", "Ruido atmosférico (estáticos, tormentas)"], ["QRO", "Aumentar potencia"], ["QRP", "Reducir potencia (baja potencia)"], ["QRS", "Transmitir más despacio"], ["QRT", "Cesar la transmisión"], ["QRV", "Estoy listo"], ["QRX", "Espere / volveré a llamar"], ["QRZ", "¿Quién me llama?"], ["QSB", "Desvanecimiento (fading)"], ["QSL", "Acuso recibo / confirmo el contacto"], ["QSO", "Contacto entre estaciones"], ["QSY", "Cambiar de frecuencia"], ["QTH", "Ubicación"], ["QTR", "Hora exacta"]];
const ICAO = { A: "Alfa", B: "Bravo", C: "Charlie", D: "Delta", E: "Echo", F: "Foxtrot", G: "Golf", H: "Hotel", I: "India", J: "Juliett", K: "Kilo", L: "Lima", M: "Mike", N: "November", O: "Oscar", P: "Papa", Q: "Quebec", R: "Romeo", S: "Sierra", T: "Tango", U: "Uniform", V: "Victor", W: "Whiskey", X: "X-ray", Y: "Yankee", Z: "Zulu", 0: "Zero", 1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine" };
const CTCSS = [67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 162.2, 167.9, 173.8, 179.9, 186.2, 192.8, 203.5, 210.7, 218.1, 225.7, 233.6, 241.8, 250.3];
const MORSE = { A: ".-", B: "-...", C: "-.-.", D: "-..", E: ".", F: "..-.", G: "--.", H: "....", I: "..", J: ".---", K: "-.-", L: ".-..", M: "--", N: "-.", O: "---", P: ".--.", Q: "--.-", R: ".-.", S: "...", T: "-", U: "..-", V: "...-", W: ".--", X: "-..-", Y: "-.--", Z: "--..", 0: "-----", 1: ".----", 2: "..---", 3: "...--", 4: "....-", 5: ".....", 6: "-....", 7: "--...", 8: "---..", 9: "----.", "/": "-..-.", "?": "..--..", ".": ".-.-.-", ",": "--..--" };
function playMorse(code, wpm = 18) {
  const ac = new (window.AudioContext || window.webkitAudioContext)(), u = 1.2 / wpm; let t = ac.currentTime + 0.05;
  for (const ch of code) { if (ch === " ") { t += u * 2; continue; } if (ch === "/") { t += u * 4; continue; }
    const o = ac.createOscillator(), g = ac.createGain(), d = ch === "." ? u : u * 3; o.frequency.value = 650; g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.25, t + 0.006); g.gain.setValueAtTime(0.25, t + d - 0.006); g.gain.linearRampToValueAtTime(0, t + d);
    o.connect(g).connect(ac.destination); o.start(t); o.stop(t + d); t += d + u; }
  setTimeout(() => ac.close(), (t - ac.currentTime + 0.5) * 1000);
}
function herramientas() {
  const q = getQth();
  ctx.colb.innerHTML = `${head("Herramientas", "Pequeñas utilidades de radioaficionado, sin salir de la web")}
  <div class="grid2" style="margin-top:20px">
    <div class="card"><h3>${icon("globe", 16)} Locator Maidenhead y coordenadas</h3><div class="field"><label for="t-loc">Locator</label><input id="t-loc" placeholder="JN58td"></div><div class="field"><label for="t-ll">Latitud, longitud</label><input id="t-ll" placeholder="48.1466, 11.6083"></div><p class="empty" id="t-out"></p></div>
    <div class="card"><h3>${icon("compass", 16)} Distancia y rumbo</h3><div class="field"><label for="d-a">Desde (locator)</label><input id="d-a" value="${esc(q?.locator || "")}" placeholder="IN80do"></div><div class="field"><label for="d-b">Hasta (locator)</label><input id="d-b" placeholder="JN58td"></div><p class="empty" id="d-out"></p></div>
    <div class="card"><h3>${icon("antenna", 16)} Longitud de antena</h3><div class="field"><label for="a-f">Frecuencia (MHz)</label><input id="a-f" type="number" step="0.001" placeholder="145.500"></div><div class="field"><label for="a-vf">Factor de velocidad del cable/elemento</label><input id="a-vf" type="number" step="0.01" value="0.95"></div><div id="a-out" class="empty"></div></div>
    <div class="card"><h3>${icon("code", 16)} Morse</h3><div class="field"><label for="m-in">Texto</label><input id="m-in" placeholder="CQ CQ DE EA3XYZ"></div><p class="num" id="m-out" style="word-break:break-all;font-size:15px"></p><button class="btn ghost sm" id="m-play" type="button">${icon("wave", 14)} Reproducir</button></div>
    <div class="card"><h3>${icon("book", 16)} Alfabeto fonético</h3><div class="field"><label for="p-in">Indicativo o texto</label><input id="p-in" placeholder="EA3XYZ"></div><p class="empty" id="p-out" style="line-height:1.8"></p></div>
    <div class="card"><h3>${icon("list", 16)} Código Q</h3><div class="field"><input id="q-in" type="search" placeholder="Buscar: QSL, QTH, ruido…" aria-label="Buscar código Q"></div><div class="qgrid" id="q-out"></div></div>
    <div class="card"><h3>${icon("gauge", 16)} Potencia W y dBm</h3><div class="field"><label for="w-in">Vatios</label><input id="w-in" type="number" step="any" placeholder="5"></div><p class="empty" id="w-out"></p></div></div>
  <section class="sec"><div class="label">Subtonos CTCSS estándar <em>Hz</em></div><div class="qgrid">${CTCSS.map((t) => `<div><b>${t.toFixed(1)}</b></div>`).join("")}</div>
    <p class="empty" style="margin-top:10px">El subtono (a veces llamado «SQL» o squelch codificado) se transmite por debajo de la voz para abrir el repetidor. Se configura en el campo TX tone de tu equipo.</p></section>`;
  const loc = $("#t-loc"), ll = $("#t-ll"), out = $("#t-out");
  loc.addEventListener("input", () => { const p = fromLocator(loc.value); out.textContent = p ? `Centro del cuadro: ${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}` : ""; if (p) ll.value = `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`; });
  ll.addEventListener("input", () => { const [a, b] = ll.value.split(/[,;\s]+/).map(Number); if (isFinite(a) && isFinite(b) && Math.abs(a) <= 90 && Math.abs(b) <= 180) { loc.value = toLocator(a, b); out.textContent = `Locator: ${toLocator(a, b)}`; } });
  const dist = () => { const a = fromLocator($("#d-a").value), b = fromLocator($("#d-b").value); $("#d-out").innerHTML = a && b ? `<b class="num">${nf.format(distKm(a, b))} km</b> · rumbo <b class="num">${Math.round(bearing(a, b))}°</b> (${compass(bearing(a, b))}) · inverso ${Math.round(bearing(b, a))}°` : ""; };
  $("#d-a").addEventListener("input", dist); $("#d-b").addEventListener("input", dist); dist();
  const ant = () => { const f = +$("#a-f").value, vf = +$("#a-vf").value || 0.95; $("#a-out").innerHTML = f > 0 ? `<div class="kv"><span>Dipolo λ/2 (total)</span><span>${(150 * vf / f).toFixed(2)} m</span></div><div class="kv"><span>Cada brazo</span><span>${(75 * vf / f).toFixed(2)} m</span></div><div class="kv"><span>Vertical λ/4</span><span>${(71.25 * vf / f).toFixed(2)} m</span></div><p class="empty" style="margin-top:6px">Punto de partida: ajusta con un analizador o ROE-metro y recorta poco a poco.</p>` : ""; };
  $("#a-f").addEventListener("input", ant); $("#a-vf").addEventListener("input", ant);
  const mor = () => { const code = [...$("#m-in").value.toUpperCase()].map((c) => (c === " " ? "/" : MORSE[c] || "")).filter((x) => x !== "").join(" "); $("#m-out").textContent = code; $("#m-play").dataset.code = code; };
  $("#m-in").addEventListener("input", mor); $("#m-play").addEventListener("click", (e) => e.currentTarget.dataset.code && playMorse(e.currentTarget.dataset.code));
  $("#p-in").addEventListener("input", (e) => { $("#p-out").innerHTML = [...e.target.value.toUpperCase()].filter((c) => ICAO[c]).map((c) => `<b>${c}</b> ${ICAO[c]}`).join(" · "); });
  const qq = () => { const t = $("#q-in").value.toLowerCase(); $("#q-out").innerHTML = Q.filter(([c, d]) => !t || c.toLowerCase().includes(t) || d.toLowerCase().includes(t)).map(([c, d]) => `<div><b>${c}</b><br>${esc(d)}</div>`).join(""); };
  $("#q-in").addEventListener("input", qq); qq();
  $("#w-in").addEventListener("input", (e) => { const w = +e.target.value; $("#w-out").innerHTML = w > 0 ? `<b class="num">${(10 * Math.log10(w * 1000)).toFixed(1)} dBm</b> · ${(w * 1000).toLocaleString("es")} mW` : ""; });
  if (q?.lat != null) ctx.marker(q.lon, q.lat, { label: qthName(q), cls: "fav" });
}
