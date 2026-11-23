import { $, $$, api, esc, nf, nf1, t0, LS, debounce, fmtMin, fmtDT, distKm, compass } from "./util.js";
import { renderCharts, renderProfile } from "./charts.js";
import { icon } from "./icons.js";
import { aiOn } from "./me.js";

let ctx, mode = "foot", wps, gpx = null, gpxName = "", depart, ia = false, picking = -1, result = null, recents = LS.get("mg-routes", []), hoverM = null, busy = false;
const blank = () => [{ label: "", lat: null, lon: null }, { label: "", lat: null, lon: null }];
const pad = (n) => String(n).padStart(2, "0");
const localInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const nextHalf = () => { const d = new Date(Date.now() + 30 * 60000); d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0); return localInput(d); };
const cssv = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const LV = ["Favorable", "Precaución", "Desaconsejable"];

export function mount(c) {
  ctx = c; wps = wps || blank(); depart = depart || nextHalf();
  ctx.cola.innerHTML = `${ctx.brand("routes", "Planificador de rutas")}${aiOn() ? `<form class="route-ai" id="route-ai"><span class="ai-tag"><span class="av" aria-hidden="true">${icon("spark", 12)}</span>Cuéntaselo a Guilda</span><div class="route-ai-in"><input id="route-q" type="text" maxlength="400" placeholder="«Espot al refugi, mañana a las 8»" aria-label="Describe tu ruta"><button class="go" type="submit" aria-label="Enviar">${icon("up", 16)}</button></div><small>Te lo explica con el tiempo real; los puntos los fijas abajo o en el mapa.</small></form>` : ""}<div class="form" id="form"></div>
    <div id="recents"></div><p class="cola-note">Rutas a pie y en coche con Valhalla/OpenStreetMap. Tiempos a pie con la regla de Naismith. Importa un GPX para usar tu propio track.</p>`;
  ctx.colb.innerHTML = `<div class="article"><h1>Planifica la salida con el tiempo a favor</h1><p class="lead">Marca un origen y un destino (o importa un GPX), elige cuándo sales y Meteo Guilda calcula la previsión en cada punto <em>a la hora en que llegarás</em>, el riesgo por tramo y la mejor ventana para salir.</p>
    <div class="callout">Consejo: pulsa «Mapa» junto a un punto y toca el mapa para fijarlo.</div></div>`;
  ctx.onMapClick = ({ lat, lng }) => {
    const i = picking >= 0 ? picking : wps.findIndex((w) => w.lat == null);
    if (i < 0) return ctx.toast("Todos los puntos están fijados; pulsa «Mapa» en el que quieras mover");
    setWp(i, lat, lng, `${lat.toFixed(4)}, ${lng.toFixed(4)}`); picking = -1; paintForm();
  };
  ctx.mapCtl('<span class="chip chip-ghost">Toca el mapa para fijar el siguiente punto</span>');
  ctx.stations(false);
  paintForm(); paintRecents(); drawWps();
  $("#route-ai")?.addEventListener("submit", (e) => { e.preventDefault(); const v = $("#route-q").value.trim(); if (v) { $("#route-q").value = ""; ctx.ask?.(`Quiero planificar una ruta: ${v}`); } });
  if (result) { paintResult(); drawRoute(); }
}
export function unmount() { picking = -1; ctx.map?.getCanvas && (ctx.map.getCanvas().style.cursor = ""); clearRoute(); }
export function show() {}

function setWp(i, lat, lon, label) { wps[i] = { label, lat, lon }; drawWps(); }
function drawWps() {
  ctx.markers.forEach((m) => { if (m.kind === "wp") ctx.removeMarker(m); });
  wps.forEach((w, i) => { if (w.lat != null) { const m = ctx.marker(w.lon, w.lat, { label: i === 0 ? "Origen" : i === wps.length - 1 ? "Destino" : `Parada ${i}`, cls: "wp", draggable: true }); if (m) { m.kind = "wp"; m.on("dragend", () => { const p = m.getLngLat(); setWp(i, p.lat, p.lng, `${p.lat.toFixed(4)}, ${p.lng.toFixed(4)}`); paintForm(); }); } } });
}

/* ---------- formulario ---------- */
function paintForm() {
  const f = $("#form");
  f.innerHTML = `
    <div class="field"><span class="lbl">Modo</span><span class="seg" role="group"><button type="button" data-m="foot" aria-pressed="${mode === "foot"}">${icon("walk", 16)} A pie</button><button type="button" data-m="car" aria-pressed="${mode === "car"}">${icon("car", 16)} En coche</button></span></div>
    ${gpx ? `<div class="field"><span class="lbl">Track importado</span><div class="row"><span class="badge">${esc(gpxName)}</span><button class="btn ghost sm" type="button" id="gpx-x">Quitar</button></div></div>` :
      `<div class="field"><span class="lbl">Puntos</span>${wps.map((w, i) => `<div class="wp ${i === wps.length - 1 ? "end" : ""}"><span class="dot"></span>
        <div class="wsearch"><input type="text" data-i="${i}" value="${esc(w.label)}" placeholder="${i === 0 ? "Origen" : i === wps.length - 1 ? "Destino" : "Parada"}" autocomplete="off"><div class="results" hidden></div></div>
        <span class="row" style="gap:6px"><button class="mapbtn" type="button" data-pick="${i}">${picking === i ? "Toca el mapa…" : "Mapa"}</button>${wps.length > 2 && i > 0 && i < wps.length - 1 ? `<button class="icon-btn" type="button" data-rm="${i}" aria-label="Quitar parada" style="width:26px;height:26px">${icon("x", 14)}</button>` : ""}</span></div>`).join("")}
      <div class="row"><button class="btn ghost sm" type="button" id="addwp">${icon("plus", 14)} Parada</button><button class="btn ghost sm" type="button" id="swap">${icon("swap", 14)} Invertir</button>
        <label class="btn ghost sm" style="cursor:pointer">${icon("upload", 14)} Importar GPX<input type="file" id="gpxf" accept=".gpx,application/gpx+xml,text/xml" hidden></label></div></div>`}
    <div class="field"><label for="dep">Salida</label><input id="dep" type="datetime-local" value="${depart}"></div>
    <label class="check"><input type="checkbox" id="ia" ${ia ? "checked" : ""}> Añadir lectura de IA (usa cuota gratuita)</label>
    <button class="btn" id="go" type="button" ${busy ? "disabled" : ""} style="width:100%">${busy ? "Calculando…" : "Calcular ruta y meteo"}</button>`;
  $$("[data-m]", f).forEach((b) => b.addEventListener("click", () => { mode = b.dataset.m; paintForm(); }));
  $$("[data-pick]", f).forEach((b) => b.addEventListener("click", () => { picking = picking === +b.dataset.pick ? -1 : +b.dataset.pick; ctx.map.getCanvas().style.cursor = picking >= 0 ? "crosshair" : ""; ctx.pane("map"); paintForm(); }));
  $$("[data-rm]", f).forEach((b) => b.addEventListener("click", () => { wps.splice(+b.dataset.rm, 1); paintForm(); drawWps(); }));
  $("#addwp", f)?.addEventListener("click", () => { wps.splice(wps.length - 1, 0, { label: "", lat: null, lon: null }); paintForm(); });
  $("#swap", f)?.addEventListener("click", () => { wps.reverse(); paintForm(); drawWps(); });
  $("#gpx-x", f)?.addEventListener("click", () => { gpx = null; paintForm(); });
  $("#gpxf", f)?.addEventListener("change", async (e) => {
    const file = e.target.files[0]; if (!file) return;
    if (file.size > 2.5e6) return ctx.toast("GPX demasiado grande (máx. 2,5 MB)");
    gpx = await file.text(); gpxName = file.name; paintForm();
  });
  $("#dep", f).addEventListener("change", (e) => (depart = e.target.value));
  $("#ia", f).addEventListener("change", (e) => (ia = e.target.checked));
  $("#go", f).addEventListener("click", () => calc());
  $$(".wsearch input", f).forEach(wireSearch);
}
function wireSearch(inp) {
  const box = inp.nextElementSibling, i = +inp.dataset.i;
  const run = debounce(async () => {
    const t = inp.value.trim(); if (t.length < 2) { box.hidden = true; return; }
    try {
      const r = await api(`/api/search?q=${encodeURIComponent(t)}`);
      box.innerHTML = r.length ? r.map((x, k) => `<button class="r" type="button" data-k="${k}"><span class="grow"><b>${esc(x.name)}</b><small>${esc([x.admin1, x.country].filter(Boolean).join(" · "))}${x.ele != null ? ` · ${nf.format(x.ele)} m` : ""}</small></span></button>`).join("") : '<div class="r-head">Sin resultados</div>';
      box.hidden = false;
      box._r = r;
    } catch (e) { box.innerHTML = `<div class="r-head">${esc(e.message)}</div>`; box.hidden = false; }
  }, 300);
  inp.addEventListener("input", () => { wps[i] = { label: inp.value, lat: null, lon: null }; run(); });
  box.addEventListener("mousedown", (e) => e.preventDefault());
  box.addEventListener("click", (e) => { const b = e.target.closest("[data-k]"); if (!b) return; const x = box._r[+b.dataset.k]; setWp(i, x.lat, x.lon, x.name); box.hidden = true; inp.value = x.name; ctx.fit(wps.filter((w) => w.lat != null).map((w) => [w.lon, w.lat]), { maxZoom: 11 }); });
  inp.addEventListener("blur", () => setTimeout(() => (box.hidden = true), 150));
}
function paintRecents() {
  $("#recents").innerHTML = recents.length ? `<div class="group-label">Recientes</div><div class="recent">${recents.map((r, i) => `<button type="button" data-r="${i}">${esc(r.a)} ${icon("right", 12)} ${esc(r.b)} <small>· ${r.mode === "foot" ? "a pie" : "coche"}</small></button>`).join("")}</div>` : "";
  $$("[data-r]", $("#recents")).forEach((b) => b.addEventListener("click", () => { const r = recents[+b.dataset.r]; mode = r.mode; wps = JSON.parse(JSON.stringify(r.wps)); gpx = null; paintForm(); drawWps(); ctx.fit(wps.map((w) => [w.lon, w.lat])); calc(); }));
}

/* ---------- cálculo ---------- */
async function calc(opts = {}) {
  if (busy) return;
  const body = { mode, ia: opts.ia ?? ia, depart: depart ? new Date(depart).toISOString() : null };
  if (gpx) body.gpx = gpx;
  else {
    body.waypoints = wps.filter((w) => w.lat != null).map((w) => ({ lat: w.lat, lon: w.lon }));
    if (body.waypoints.length < 2) return ctx.toast("Fija al menos origen y destino (busca un lugar o usa «Mapa»)");
  }
  busy = true; paintForm();
  ctx.pane("detail");
  ctx.colb.innerHTML = `<div class="skeleton" style="width:50%;height:34px"></div><div class="skeleton" style="height:90px;margin:20px 0"></div><div class="skeleton" style="height:190px"></div>`;
  try {
    result = await api("/api/route", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!gpx) { recents = [{ a: wps[0].label || "Origen", b: wps.at(-1).label || "Destino", mode, wps }, ...recents.filter((r) => r.a !== wps[0].label || r.b !== wps.at(-1).label)].slice(0, 5); LS.set("mg-routes", recents); paintRecents(); }
    paintResult(); drawRoute();
  } catch (e) {
    result = null; clearRoute();
    ctx.colb.innerHTML = `<div class="err"><strong>No se pudo calcular la ruta</strong><p class="empty">${esc(e.message)}</p></div>`;
  } finally { busy = false; paintForm(); }
}

/* ---------- mapa ---------- */
function clearRoute() {
  const m = ctx.map; if (!m || !ctx.mapLoaded) return;
  ["route-line", "route-case", "route-pts"].forEach((l) => m.getLayer(l) && m.removeLayer(l));
  ["route", "route-p"].forEach((s) => m.getSource(s) && m.removeSource(s));
  if (hoverM) { ctx.removeMarker(hoverM); hoverM = null; }
}
function drawRoute() {
  const m = ctx.map, r = result; if (!m || !r) return;
  if (!ctx.mapLoaded) { ctx.mapReady.then(drawRoute); return; }
  clearRoute();
  const g = r.geometria, S = r.muestras;
  const lvAt = (km) => { let k = 0; S.forEach((s, i) => { if (s.d_km <= km + 1e-6) k = i; }); return S[k].nivel ?? 0; };
  let cum = 0; const feats = [];
  for (let i = 1; i < g.length; i++) {
    const d = distKm({ lat: g[i - 1][1], lon: g[i - 1][0] }, { lat: g[i][1], lon: g[i][0] }); cum += d;
    feats.push({ type: "Feature", properties: { lv: lvAt(cum - d / 2) }, geometry: { type: "LineString", coordinates: [g[i - 1], g[i]] } });
  }
  const col = ["match", ["get", "lv"], 0, cssv("--ok"), 1, cssv("--warn"), cssv("--bad")];
  m.addSource("route", { type: "geojson", data: { type: "FeatureCollection", features: feats } });
  m.addLayer({ id: "route-case", type: "line", source: "route", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#fff", "line-width": 8, "line-opacity": 0.9 } });
  m.addLayer({ id: "route-line", type: "line", source: "route", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": col, "line-width": 5 } });
  m.addSource("route-p", { type: "geojson", data: { type: "FeatureCollection", features: S.map((s) => ({ type: "Feature", properties: { lv: s.nivel ?? 0, t: s.d_km }, geometry: { type: "Point", coordinates: [s.lon, s.lat] } })) } });
  m.addLayer({ id: "route-pts", type: "circle", source: "route-p", paint: { "circle-radius": 5, "circle-color": col, "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
  ctx.fit(g, { maxZoom: 13 });
}
function pointAt(km) {
  const g = result.geometria; let cum = 0;
  for (let i = 1; i < g.length; i++) {
    const d = distKm({ lat: g[i - 1][1], lon: g[i - 1][0] }, { lat: g[i][1], lon: g[i][0] });
    if (cum + d >= km) { const f = d ? (km - cum) / d : 0; return [g[i - 1][0] + f * (g[i][0] - g[i - 1][0]), g[i - 1][1] + f * (g[i][1] - g[i - 1][1])]; }
    cum += d;
  }
  return g.at(-1);
}
function hover(km) {
  if (km == null) { if (hoverM) { ctx.removeMarker(hoverM); hoverM = null; } return; }
  const [lo, la] = pointAt(km);
  if (!hoverM) hoverM = ctx.marker(lo, la, { cls: "pt on" }); else hoverM.setLngLat([lo, la]);
}

/* ---------- resultado ---------- */
const timeOf = (iso) => new Date(iso).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
function gpxOf(r) {
  return `<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="Meteo Guilda" xmlns="http://www.topografix.com/GPX/1/1"><trk><name>Meteo Guilda ${r.distancia_km} km</name><trkseg>${r.geometria.map(([lo, la]) => `<trkpt lat="${la}" lon="${lo}"/>`).join("")}</trkseg></trk></gpx>`;
}
function paintResult() {
  const r = result, S = r.muestras, ok = r.nivel_global != null;
  const eta = ok ? timeOf(new Date(new Date(r.salida).getTime() + r.duracion_min * 60000).toISOString()) : "–";
  const win = (w) => `<button class="win" type="button" data-dep="${esc(w.salida)}" aria-pressed="${w.salida.slice(0, 13) === (r.salida || "").slice(0, 13)}"><span class="pill lv${w.nivel}"><i></i>${LV[w.nivel]}</span><b>${esc(fmtDT(w.salida))}</b><small>${w.motivos.length ? esc(w.motivos.slice(0, 2).join(" · ")) : "Sin incidencias previstas"}</small></button>`;
  const L = r.lectura;
  ctx.colb.innerHTML = `
  <div class="d-head"><div><h1>${r.modo === "foot" ? "Ruta a pie" : "Ruta en coche"} · ${nf1.format(r.distancia_km)} km</h1><p>Salida ${esc(fmtDT(r.salida))} · llegada ${eta}</p></div>
    <div class="d-actions"><button class="btn ghost sm" id="gpx-dl" type="button">${icon("download", 14)} Descargar GPX</button></div></div>
  <div class="badges"><span class="pill lv${r.nivel_global ?? 1}"><i></i>${esc(r.nivel_texto)}</span></div>
  <div class="summary" style="margin-top:16px">
    <div class="stat"><span class="lbl">Distancia</span><span class="num">${nf1.format(r.distancia_km)}<small>km</small></span></div>
    <div class="stat"><span class="lbl">Duración</span><span class="num" style="font-size:17px">${fmtMin(r.duracion_min)}</span></div>
    ${r.ascenso_m || r.descenso_m ? `<div class="stat"><span class="lbl">Ascenso</span><span class="num">+${nf.format(r.ascenso_m)}<small>m</small></span></div><div class="stat"><span class="lbl">Descenso</span><span class="num">−${nf.format(r.descenso_m)}<small>m</small></span></div>` : ""}
  </div>
  ${r.modo === "foot" ? '<p class="empty" style="margin-top:-6px">Duración a pie: 5 km/h + 1 h por cada 600 m de ascenso (regla de Naismith); el tuyo puede variar.</p>' : ""}

  <section class="sec"><div class="label">Mejores ventanas de salida <em>próximas 36 h</em></div>
    ${r.ventanas.length ? `<div class="windows">${r.ventanas.map(win).join("")}</div>` : '<p class="empty">No hay datos meteorológicos suficientes para proponer ventanas.</p>'}</section>

  <section class="sec"><div class="label">Recorrido <em>color = nivel de riesgo a la hora de paso</em></div><div id="profile"></div><div id="meteo"></div></section>

  <section class="sec"><div class="label">Lectura ${L ? `<em>IA · ${esc((L.modelo_ia || "").split("/").pop().replace(":free", ""))}</em>` : ""}</div>
    ${L ? `<p class="reading">${esc(L.resumen)}</p><div class="cols">${[["Riesgos", L.riesgos, "risk"], ["Ventanas", L.ventanas, ""], ["Recomendaciones", L.recomendaciones, ""]].map(([t, a, c]) => `<div class="${c}"><h3>${t}</h3>${a.length ? `<ul>${a.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="none">—</p>'}</div>`).join("")}</div>`
      : `<p class="empty">Genera un briefing redactado por IA con estos datos (se comprueba que no invente cifras).</p><button class="btn ghost sm" id="mk-ia" type="button" style="margin-top:10px">Generar lectura de IA</button>`}
    <div class="warn-note"><svg viewBox="0 0 24 24"><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 10v4.5M12 17.5v.01"/></svg><span>El riesgo se calcula con reglas sobre la media de modelos; no sustituye a los avisos oficiales ni a tu criterio en montaña.</span></div></section>

  <section class="sec"><div class="label">Punto a punto <em>hora de paso estimada</em></div><div class="tl">
    ${ok ? S.map((s) => `<div class="tl-row"><span class="t">${timeOf(s.t)}</span><span class="lv lv${s.nivel}" title="${LV[s.nivel]}"></span><div><div class="w">km ${nf1.format(s.d_km)}${s.ele != null ? ` · ${nf.format(s.ele)} m` : ""} · ${t0(s.meteo.temp_c)}° · ${s.meteo.viento_kmh} km/h (racha ${s.meteo.racha_kmh}) ${s.meteo.viento_dir != null ? compass(s.meteo.viento_dir) : ""} · ${nf1.format(s.meteo.lluvia_mm)} mm</div>
      <div class="why">${s.motivos.length ? esc(s.motivos.join(" · ")) : "Sin incidencias"}</div></div></div>`).join("") : '<p class="empty">La hora de salida queda fuera de los datos disponibles (máx. 72 h).</p>'}</div></section>

  <section class="sec"><div class="label">Balizas meteo junto al recorrido <em>APRS, ≤ 5 km</em></div>
    ${r.balizas?.length ? `<div class="tablewrap"><table><thead><tr><th>Estación</th><th>Dist.</th><th>°C</th><th>Viento</th><th>Racha</th></tr></thead><tbody>${r.balizas.map((b) => `<tr><td>${esc(b.id)}</td><td class="num">${b.dist_km} km</td><td class="num">${b.temp_c != null ? nf1.format(b.temp_c) : "–"}</td><td class="num">${b.viento_kmh ?? "–"}</td><td class="num">${b.racha_kmh ?? "–"}</td></tr>`).join("")}</tbody></table></div>` : '<p class="empty">No hay balizas con datos recientes cerca del recorrido.</p>'}</section>

  <section class="sec"><div class="label">Cobertura de repetidores <em>estimada por línea de vista</em></div>
    ${r.repetidores?.length ? r.repetidores.map((x) => `<div class="rep"><span class="f">${x.freq_mhz.toFixed(3)} MHz</span><span><span class="tag ok">${Math.round(x.pct)}% del recorrido</span></span><span class="meta">${esc(x.callsign || x.name)} · ${esc(x.mode)}${x.tone_hz ? ` · ${x.tone_hz} Hz` : ""}${x.shift_khz ? ` · ${x.shift_khz > 0 ? "+" : ""}${x.shift_khz} kHz` : ""}</span></div>`).join("") : '<p class="empty">Sin repetidores con cobertura calculada en este recorrido. Útil en montaña: <a href="#/radio/repetidores">consulta la sección Radio</a>.</p>'}</section>`;
  $$("[data-dep]", ctx.colb).forEach((b) => b.addEventListener("click", () => { depart = localInput(new Date(b.dataset.dep)); paintForm(); calc(); }));
  $("#mk-ia")?.addEventListener("click", () => calc({ ia: true }));
  $("#gpx-dl").addEventListener("click", () => { const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([gpxOf(r)], { type: "application/gpx+xml" })), download: "meteoguilda-ruta.gpx" }); a.click(); URL.revokeObjectURL(a.href); });
  renderProfile($("#profile"), r, hover);
  if (ok) {
    const f = (k) => (i) => S[i].meteo[k] ?? 0, step = S.length > 8 ? 2 : 1;
    const axis = (items) => items.map((s, i) => ({ i, label: `${nf1.format(s.d_km)}`, major: false })).filter((a) => a.i % step === 0);
    renderCharts($("#meteo"), S, [
      { title: "Temperatura en ruta", sub: "°C · km", draw: "line", H: 120, ticks: 3, range: (it) => [Math.min(...it.map((s) => s.meteo.temp_c)) - 1, Math.max(...it.map((s) => s.meteo.temp_c)) + 1], mean: f("temp_c"), tip: (i) => `km ${nf1.format(S[i].d_km)} · <b>${t0(S[i].meteo.temp_c)} °C</b>` },
      { title: "Viento y rachas en ruta", sub: "km/h", draw: "wind", H: 140, range: (it) => [0, Math.max(20, ...it.map((s) => s.meteo.racha_kmh))], mean: f("viento_kmh"), gust: f("racha_kmh"), dir: (i) => S[i].meteo.viento_dir, tip: (i) => `km ${nf1.format(S[i].d_km)} · <b>${S[i].meteo.viento_kmh}</b> km/h · racha <b>${S[i].meteo.racha_kmh}</b>` },
      { title: "Lluvia en ruta", sub: "mm/h", draw: "bars", H: 100, ticks: 2, range: (it) => [0, Math.max(2, ...it.map((s) => s.meteo.lluvia_mm))], mean: f("lluvia_mm"), tip: (i) => `km ${nf1.format(S[i].d_km)} · <b>${nf1.format(S[i].meteo.lluvia_mm)} mm/h</b>` },
    ], axis, hover && ((i) => hover(i == null ? null : S[i].d_km)));
  }
}
