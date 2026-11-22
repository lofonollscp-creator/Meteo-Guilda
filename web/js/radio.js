import { $, $$, api, esc, nf, nf1, LS, debounce, toLocator, fromLocator, distKm, bearing, compass } from "./util.js";
import { renderCharts, renderStrip, ramp } from "./charts.js";

let placesP = Promise.resolve([]), ctx, sub = "repetidores", qth = LS.get("mg-qth", null), qsos = LS.get("mg-qsl", []), reps = null, showCov = true, band = "", loc = null, places = [];
const MENU = [
  ["repetidores", "Repetidores", "Frecuencias, subtonos y cobertura", '<path d="M12 11v10M8 21h8M7.5 6.5a6.4 6.4 0 0 0 0 9M16.5 6.5a6.4 6.4 0 0 1 0 9"/><circle cx="12" cy="11" r="1.5"/>'],
  ["propagacion", "Propagación", "Tropo VHF/UHF y condiciones HF", '<path d="M3 17c3-8 6-8 9 0s6 8 9 0"/>'],
  ["qsl", "Cuaderno QSL", "Registro de contactos y confirmaciones", '<path d="M5 3h14v18l-7-4-7 4z"/>'],
  ["herramientas", "Herramientas", "Locator, código Q, fonético, CTCSS", '<path d="M14.5 6.5a4 4 0 0 0-5 5L3 18l3 3 6.5-6.5a4 4 0 0 0 5-5l-2.5 2.5-2.5-.5-.5-2.5z"/>'],
];
const Q = [["QRA", "Nombre de mi estación es…"], ["QRG", "Frecuencia exacta"], ["QRK", "Inteligibilidad de sus señales"], ["QRL", "Frecuencia ocupada"], ["QRM", "Interferencias de otras estaciones"], ["QRN", "Ruido atmosférico (estáticos, tormentas)"], ["QRO", "Aumentar potencia"], ["QRP", "Reducir potencia (operación de baja potencia)"], ["QRS", "Transmitir más despacio"], ["QRT", "Cesar la transmisión"], ["QRV", "Estoy listo"], ["QRX", "Espere / volveré a llamar"], ["QRZ", "¿Quién me llama?"], ["QSB", "Desvanecimiento (fading) de las señales"], ["QSL", "Acuso recibo / confirmo el contacto"], ["QSO", "Contacto entre estaciones"], ["QSY", "Cambiar de frecuencia"], ["QTH", "Ubicación"], ["QTR", "Hora exacta"]];
const ICAO = { A: "Alfa", B: "Bravo", C: "Charlie", D: "Delta", E: "Echo", F: "Foxtrot", G: "Golf", H: "Hotel", I: "India", J: "Juliett", K: "Kilo", L: "Lima", M: "Mike", N: "November", O: "Oscar", P: "Papa", Q: "Quebec", R: "Romeo", S: "Sierra", T: "Tango", U: "Uniform", V: "Victor", W: "Whiskey", X: "X-ray", Y: "Yankee", Z: "Zulu", 0: "Zero", 1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine" };
const CTCSS = [67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 162.2, 167.9, 173.8, 179.9, 186.2, 192.8, 203.5, 210.7, 218.1, 225.7, 233.6, 241.8, 250.3];
const BANDS = ["160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m", "4m", "2m", "70cm", "23cm"];
const MODES = ["SSB", "CW", "FM", "FT8", "FT4", "DMR", "D-STAR", "C4FM", "RTTY", "AM", "Otro"];
const CAT = { normal: "#c9ced2", "sub-refracción": "#a9b7c6", "super-refracción": "#e2a548", conducto: "#c0392b" };
const fmtF = (f) => f.toFixed(4).replace(".", ",");
const saveQ = () => LS.set("mg-qsl", qsos);

export function mount(c) {
  ctx = c;
  ctx.cola.innerHTML = `<div class="cola-head"><h2>Radioafición</h2><p>Meteo y radio, juntas</p></div>
    <div class="form" style="padding-bottom:6px"><div class="field"><label for="qth">Mi locator (QTH)</label><input id="qth" type="text" maxlength="8" placeholder="p. ej. JN02ac" value="${esc(qth?.locator || "")}"></div><p class="empty" id="qthinfo" style="margin-top:-6px;font-size:12.5px"></p></div>
    <div class="menu" id="menu">${MENU.map(([id, t, s, ic]) => `<button type="button" data-sub="${id}"><svg viewBox="0 0 24 24">${ic}</svg><span>${t}<small>${s}</small></span></button>`).join("")}</div>
    <p class="cola-note">Cuaderno y ajustes se guardan solo en este dispositivo. Para tener repetidores en el mapa hay que importarlos (ver «Repetidores»).</p>`;
  $("#menu").addEventListener("click", (e) => { const b = e.target.closest("[data-sub]"); if (b) location.hash = `#/radio/${b.dataset.sub}`; });
  $("#qth").addEventListener("input", (e) => {
    const p = fromLocator(e.target.value);
    qth = p ? { locator: e.target.value.toUpperCase(), ...p } : null; LS.set("mg-qth", qth); qthInfo();
  });
  qthInfo();
  ctx.stations(false);
  placesP = api("/api/places").then((p) => (places = p)).catch(() => []);
}
function qthInfo() { const i = $("#qthinfo"); if (i) i.textContent = qth ? `${qth.lat.toFixed(3)}°, ${qth.lon.toFixed(3)}°` : ""; }
export function unmount() { clearLayers(); }
function clearLayers() {
  const m = ctx.map; if (!m || !ctx.mapLoaded) return;
  ["rep-cov", "rep-cov-l", "qso-lines"].forEach((l) => m.getLayer(l) && m.removeLayer(l));
  ["rep-c", "qso-l"].forEach((s) => m.getSource(s) && m.removeSource(s));
}
export function show(parts) {
  sub = MENU.some((m) => m[0] === parts[0]) ? parts[0] : "repetidores";
  $$("#menu [data-sub]").forEach((b) => b.setAttribute("aria-current", String(b.dataset.sub === sub)));
  ctx.clearMarkers(); clearLayers(); ctx.mapCtl(""); ctx.onMapClick = null; ctx.pane("detail");
  ({ repetidores, propagacion, qsl, herramientas })[sub]();
}

/* ---------- Repetidores ---------- */
async function repetidores() {
  ctx.colb.innerHTML = `<div class="d-head"><div><h1>Repetidores</h1><p>Frecuencias, subtonos y cobertura estimada por línea de vista</p></div></div>
    <div class="row" style="margin:18px 0"><select id="band" style="width:auto"><option value="">Todas las bandas</option><option>10m</option><option>6m</option><option>2m</option><option>70cm</option></select>
      <label class="check" style="margin:0"><input id="cov" type="checkbox" ${showCov ? "checked" : ""}> Mostrar cobertura en el mapa</label></div>
    <div id="replist"><div class="skeleton" style="height:90px"></div></div>
    <p class="empty" style="margin-top:18px">La cobertura es una <b>estimación orientativa</b>: línea de vista con modelo digital del terreno y radio efectivo de 4/3. No considera potencia, ganancia, ruido ni edificios. Úsala para decidir dónde probar, no como garantía.</p>`;
  $("#band").value = band;
  $("#band").addEventListener("change", (e) => { band = e.target.value; paintReps(); });
  $("#cov").addEventListener("change", (e) => { showCov = e.target.checked; drawRepMap(); });
  ctx.onMapClick = async ({ lat, lng }) => { loc = { lat, lon: lng }; paintReps(); };
  try { reps = await api("/api/repeaters?coverage=true"); } catch (e) { $("#replist").innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  paintReps(); drawRepMap();
}
function paintReps() {
  const ref = loc || (qth && { lat: qth.lat, lon: qth.lon });
  let list = (reps || []).filter((r) => !band || r.band === band).map((r) => ({ ...r, d: ref ? distKm(ref, r) : null }));
  if (ref) list.sort((a, b) => a.d - b.d);
  const box = $("#replist"); if (!box) return;
  if (!reps?.length) { box.innerHTML = `<div class="card"><h3>Aún no hay repetidores cargados</h3><p class="empty">Los datos de repetidores no se inventan: se importan desde una fuente fiable (listas de la URE, asociaciones locales o tus propios datos).</p>
    <p class="empty" style="margin-top:10px">Importación desde CSV (columnas <code>name, callsign, band, freq_mhz, shift_khz, tone_hz, mode, height_m, range_km, lat, lon</code>):</p><pre class="num" style="font-size:12px;overflow:auto">python -m meteoguilda.radio import repetidores.csv\npython -m meteoguilda.radio coverage</pre></div>`; return; }
  box.innerHTML = (ref ? `<p class="empty" style="margin-bottom:6px">Ordenados por distancia a ${loc ? "el punto que has tocado" : `tu QTH (${esc(qth.locator)})`}.</p>` : '<p class="empty" style="margin-bottom:6px">Indica tu locator o toca el mapa para ordenar por distancia.</p>') +
    list.map((r) => {
      const cov = ref && r.coverage_geojson && pointIn(r.coverage_geojson, ref);
      return `<div class="rep"><span class="f">${fmtF(r.freq_mhz)} MHz</span><span>${cov ? '<span class="tag ok">cubre tu punto</span>' : ""}${r.d != null ? `<span class="meta">${nf.format(r.d)} km · ${compass(bearing(ref, r))}</span>` : ""}</span>
      <span class="meta"><b>${esc(r.callsign || r.name)}</b> ${esc(r.name)} · ${esc(r.mode)}${r.tone_hz ? ` · CTCSS ${r.tone_hz} Hz` : ""}${r.shift_khz ? ` · ${r.shift_khz > 0 ? "+" : "−"}${Math.abs(r.shift_khz)} kHz (TX ${fmtF(r.freq_mhz + r.shift_khz / 1000)})` : ""}</span><span class="meta">${toLocator(r.lat, r.lon)}</span></div>`;
    }).join("") || '<p class="empty">Ningún repetidor en esa banda.</p>';
  drawRepMap();
}
function pointIn(geo, p) { // ray casting sobre el anillo exterior
  const ring = geo.coordinates[0]; let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if (yi > p.lat !== yj > p.lat && p.lon < ((xj - xi) * (p.lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function drawRepMap() {
  const m = ctx.map; if (!m || sub !== "repetidores") return;
  if (!ctx.mapLoaded) { ctx.mapReady.then(drawRepMap); return; }
  ctx.clearMarkers(); clearLayers();
  const list = (reps || []).filter((r) => !band || r.band === band);
  list.forEach((r) => ctx.marker(r.lon, r.lat, { label: `${r.callsign || r.name} ${fmtF(r.freq_mhz)}`, cls: "rep" }));
  if (qth) ctx.marker(qth.lon, qth.lat, { label: "Mi QTH", cls: "fav" });
  if (showCov) {
    const feats = list.filter((r) => r.coverage_geojson).map((r) => ({ type: "Feature", properties: {}, geometry: r.coverage_geojson }));
    m.addSource("rep-c", { type: "geojson", data: { type: "FeatureCollection", features: feats } });
    m.addLayer({ id: "rep-cov", type: "fill", source: "rep-c", paint: { "fill-color": "#7a5bd6", "fill-opacity": 0.14 } });
    m.addLayer({ id: "rep-cov-l", type: "line", source: "rep-c", paint: { "line-color": "#7a5bd6", "line-width": 1.2, "line-opacity": 0.7 } });
  }
  const pts = list.map((r) => [r.lon, r.lat]); if (qth) pts.push([qth.lon, qth.lat]);
  if (pts.length && !drawRepMap.done) { ctx.fit(pts, { maxZoom: 9 }); drawRepMap.done = true; }
}

/* ---------- Propagación ---------- */
async function propagacion() {
  await placesP;
  if (!loc || loc.name === undefined) loc = qth ? { lat: qth.lat, lon: qth.lon, name: "Mi QTH" } : { lat: 42.71, lon: 0.86, name: "Garòs" };
  const opts = [...(qth ? [{ name: "Mi QTH", lat: qth.lat, lon: qth.lon }] : []), ...places.map((p) => ({ name: p.name, lat: p.lat, lon: p.lon }))];
  ctx.colb.innerHTML = `<div class="d-head"><div><h1>Propagación</h1><p>Cómo se comportará la señal en VHF/UHF y HF</p></div></div>
    <div class="row" style="margin:16px 0"><select id="where" style="width:auto;max-width:100%">${opts.map((o, i) => `<option value="${i}" ${o.name === loc.name ? "selected" : ""}>${esc(o.name)}</option>`).join("")}${loc.name === "Punto del mapa" ? '<option value="map" selected>Punto del mapa</option>' : ""}</select><span class="chip chip-ghost">o toca el mapa</span></div>
    <div id="prop"><div class="skeleton" style="height:200px"></div></div>`;
  $("#where").addEventListener("change", (e) => { if (e.target.value !== "map") { loc = opts[+e.target.value]; propagacion(); } });
  ctx.onMapClick = ({ lat, lng }) => { loc = { lat, lon: lng, name: "Punto del mapa" }; propagacion(); };
  ctx.marker(loc.lon, loc.lat, { label: loc.name, cls: "on" }); ctx.fly(loc.lat, loc.lon, 7);
  try {
    const [p, s] = await Promise.all([api(`/api/propagation?lat=${loc.lat}&lon=${loc.lon}`), api("/api/solar").catch(() => null)]);
    if (sub !== "propagacion") return;
    const S = p.serie, w = p.peor;
    $("#prop").innerHTML = `<div class="grid2">
      <div class="card"><h3>Troposférica · VHF/UHF/microondas</h3><div class="big" style="color:${w ? CAT[w.cat] : "inherit"}">${w ? esc(w.cat) : "–"}</div>
        <p class="empty" style="margin-top:8px">${w ? `Peor momento previsto: <b>${new Date(w.time).toLocaleString("es", { weekday: "short", hour: "2-digit", minute: "2-digit" })}</b> (${w.gradient} N/km). ` : ""}Gradiente vertical de refractividad entre superficie y ${S[0]?.level_hpa ?? 850} hPa.</p></div>
      <div class="card"><h3>HF · actividad solar</h3>${s && (s.kp != null || s.sfi != null) ? `<div class="kv"><span>Índice Kp</span><span>${s.kp ?? "–"}</span></div><div class="kv"><span>Flujo solar (SFI)</span><span>${s.sfi ?? "–"}</span></div>${(s.resumen || []).map((t) => `<p class="empty" style="margin-top:8px">${esc(t)}</p>`).join("")}` : '<p class="empty">Datos de NOAA SWPC no disponibles ahora.</p>'}</div>
      <div class="card"><h3>Ubicación</h3><div class="kv"><span>Locator</span><span>${esc(p.locator)}</span></div><div class="kv"><span>Coordenadas</span><span>${loc.lat.toFixed(3)}, ${loc.lon.toFixed(3)}</span></div></div></div>
    <section class="sec"><div class="label">Gradiente de refractividad, 72 h <em>más negativo = más alcance</em></div><div class="strip" id="tstrip"></div><div id="tchart"></div>
      <div class="legend">${Object.entries(CAT).map(([k, c]) => `<span><i style="border-color:${c}"></i>${k}</span>`).join("")}</div>
      <p class="empty" style="margin-top:10px">${esc(p.nota)}</p><p class="empty" style="margin-top:8px">¿Qué significa? <a href="#/learn/tropo">Propagación troposférica y conductos</a> · <a href="#/learn/hf">HF y actividad solar</a>.</p></section>`;
    const axis = (it) => it.map((x, i) => ({ i, label: x.time.slice(11, 13) + "h", major: false })).filter((a) => a.i % 6 === 0);
    renderStrip($("#tstrip"), S, [{ label: "Tropo", val: (i) => S[i].gradient, color: (v, i) => CAT[S[i]?.cat] || "#ccc", text: () => "" }].map((r) => ({ ...r, color: (v) => (v <= -157 ? CAT.conducto : v <= -79 ? CAT["super-refracción"] : v > 0 ? CAT["sub-refracción"] : CAT.normal) })), axis);
    renderCharts($("#tchart"), S, [{ title: "Gradiente N/km", sub: "", draw: "line", H: 160, range: (it) => [Math.min(-175, ...it.map((x) => x.gradient)) - 10, Math.max(20, ...it.map((x) => x.gradient)) + 10], mean: (i) => S[i].gradient,
      refs: [{ v: -79, label: "super-refracción" }, { v: -157, label: "conducto" }], tip: (i) => `${new Date(S[i].time).toLocaleString("es", { weekday: "short", hour: "2-digit" })} · <b>${S[i].gradient} N/km</b> · ${S[i].cat}` }],
      (it) => it.map((x, i) => ({ i, label: x.time.slice(11, 13) + "h", major: false })).filter((a) => a.i % 6 === 0));
  } catch (e) { $("#prop").innerHTML = `<div class="err"><strong>No se pudo calcular</strong><p class="empty">${esc(e.message)}</p></div>`; }
}

/* ---------- Cuaderno QSL ---------- */
const utcInput = (d = new Date()) => d.toISOString().slice(0, 16);
function adif(list) {
  const f = (k, v) => (v ? `<${k}:${[...String(v)].length}>${v}` : "");
  return `Exportado por Meteo Guilda\n<ADIF_VER:5>3.1.4<PROGRAMID:11>MeteoGuilda<EOH>\n` + list.map((q) => {
    const d = new Date(q.when + "Z"), D = d.toISOString();
    return [f("CALL", q.call), f("QSO_DATE", D.slice(0, 10).replace(/-/g, "")), f("TIME_ON", D.slice(11, 16).replace(":", "")), f("BAND", q.band), f("MODE", q.mode === "Otro" ? "" : q.mode.replace("-", "")),
      f("FREQ", q.freq), f("RST_SENT", q.rsts), f("RST_RCVD", q.rstr), f("GRIDSQUARE", q.grid), f("NAME", q.name), f("COMMENT", q.notes), f("QSL_SENT", q.sent ? "Y" : "N"), f("QSL_RCVD", q.rcvd ? "Y" : "N"), f("MY_GRIDSQUARE", qth?.locator)].join("") + "<EOR>";
  }).join("\n") + "\n";
}
function dl(name, text, type) { const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([text], { type })), download: name }); a.click(); URL.revokeObjectURL(a.href); }
let qf = { text: "", status: "" };
function qsl() {
  const opt = (a) => a.map((x) => `<option>${x}</option>`).join("");
  ctx.colb.innerHTML = `<div class="d-head"><div><h1>Cuaderno QSL</h1><p>Registra tus contactos y marca las confirmaciones</p></div><div class="d-actions"><button class="btn ghost sm" id="exp" type="button">Exportar ADIF</button><button class="btn ghost sm" id="bak" type="button">Copia JSON</button><label class="btn ghost sm" style="cursor:pointer">Importar JSON<input id="imp" type="file" accept=".json,application/json" hidden></label></div></div>
    <div id="qstats" class="stats" style="margin:20px 0"></div>
    <section class="sec" style="margin-top:8px"><div class="label">Nuevo contacto</div><form id="qf" class="qsl-form">
      <div class="field"><label for="f-call">Indicativo</label><input id="f-call" required maxlength="14" style="text-transform:uppercase" autocomplete="off"></div>
      <div class="field"><label for="f-when">Fecha y hora UTC</label><input id="f-when" type="datetime-local" value="${utcInput()}" required></div>
      <div class="field"><label for="f-band">Banda</label><select id="f-band">${opt(BANDS)}</select></div>
      <div class="field"><label for="f-mode">Modo</label><select id="f-mode">${opt(MODES)}</select></div>
      <div class="field"><label for="f-freq">Frecuencia MHz</label><input id="f-freq" type="number" step="0.0001" placeholder="145.5000"></div>
      <div class="field"><label for="f-rs">RST enviado</label><input id="f-rs" value="59"></div>
      <div class="field"><label for="f-rr">RST recibido</label><input id="f-rr" value="59"></div>
      <div class="field"><label for="f-grid">Locator</label><input id="f-grid" maxlength="8" placeholder="IN80do"></div>
      <div class="field"><label for="f-name">Nombre / QTH</label><input id="f-name"></div>
      <div class="field" style="grid-column:1/-1"><label for="f-notes">Notas</label><input id="f-notes"></div>
      <div style="grid-column:1/-1" class="row"><button class="btn" type="submit">Añadir contacto</button><span class="empty" id="f-msg"></span></div></form></section>
    <section class="sec"><div class="label">Contactos <em id="qcount"></em></div>
      <div class="row" style="margin-bottom:12px"><input id="qft" type="search" placeholder="Filtrar por indicativo, nombre…" style="max-width:280px" value="${esc(qf.text)}"><select id="qfs" style="width:auto"><option value="">Todos</option><option value="pend">Pendientes de QSL</option><option value="conf">Confirmados</option></select></div>
      <div class="tablewrap"><table><thead><tr><th>Indicativo</th><th>UTC</th><th>Banda · modo</th><th>Locator</th><th>QSL</th><th></th></tr></thead><tbody id="qrows"></tbody></table></div></section>`;
  $("#qfs").value = qf.status;
  $("#qf").addEventListener("submit", (e) => {
    e.preventDefault();
    const g = $("#f-grid").value.trim(), pos = g ? fromLocator(g) : null;
    if (g && !pos) { $("#f-msg").textContent = "Locator no válido (p. ej. IN80 o IN80do)"; return; }
    qsos.unshift({ id: Date.now(), call: $("#f-call").value.trim().toUpperCase(), when: $("#f-when").value, band: $("#f-band").value, mode: $("#f-mode").value, freq: $("#f-freq").value, rsts: $("#f-rs").value, rstr: $("#f-rr").value, grid: g.toUpperCase(), name: $("#f-name").value, notes: $("#f-notes").value, sent: false, rcvd: false });
    saveQ(); $("#f-call").value = $("#f-name").value = $("#f-notes").value = $("#f-grid").value = ""; $("#f-when").value = utcInput(); $("#f-msg").textContent = "Guardado"; qslRows(); qslMap(); $("#f-call").focus();
  });
  $("#qft").addEventListener("input", (e) => { qf.text = e.target.value; qslRows(); });
  $("#qfs").addEventListener("change", (e) => { qf.status = e.target.value; qslRows(); });
  $("#exp").addEventListener("click", () => qsos.length ? dl("meteoguilda.adi", adif(qsos), "text/plain") : ctx.toast("No hay contactos"));
  $("#bak").addEventListener("click", () => dl("meteoguilda-qsl.json", JSON.stringify({ qth, qsos }, null, 1), "application/json"));
  $("#imp").addEventListener("change", async (e) => { try { const d = JSON.parse(await e.target.files[0].text()); if (Array.isArray(d.qsos)) { qsos = d.qsos; saveQ(); qslRows(); qslMap(); ctx.toast("Importado"); } } catch { ctx.toast("Archivo no válido"); } });
  $("#qrows").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]"); if (!b) return; const q = qsos.find((x) => x.id === +b.dataset.id);
    if (b.dataset.act === "del") { if (confirm(`¿Borrar el contacto con ${q.call}?`)) qsos = qsos.filter((x) => x !== q); } else q[b.dataset.act] = !q[b.dataset.act];
    saveQ(); qslRows(); qslMap();
  });
  qslRows(); qslMap();
}
function qslRows() {
  const t = qf.text.toLowerCase(), list = qsos.filter((q) => (!t || `${q.call} ${q.name} ${q.notes}`.toLowerCase().includes(t)) && (!qf.status || (qf.status === "conf" ? q.rcvd : !q.rcvd)));
  $("#qrows").innerHTML = list.map((q) => `<tr><td><b>${esc(q.call)}</b>${q.name ? `<br><small style="color:var(--ink-3)">${esc(q.name)}</small>` : ""}</td><td class="num">${esc(q.when.replace("T", " "))}</td><td>${esc(q.band)} · ${esc(q.mode)}</td><td class="num">${esc(q.grid || "–")}</td>
    <td><button class="st ${q.sent ? "on" : ""}" data-id="${q.id}" data-act="sent" type="button">Enviada</button> <button class="st ${q.rcvd ? "on" : ""}" data-id="${q.id}" data-act="rcvd" type="button">Recibida</button></td><td><button class="icon-btn" data-id="${q.id}" data-act="del" aria-label="Borrar" style="width:28px;height:28px">×</button></td></tr>`).join("") ||
    `<tr><td colspan="6" class="empty" style="text-align:center;padding:20px">${qsos.length ? "Sin resultados" : "Todavía no has registrado ningún contacto"}</td></tr>`;
  $("#qcount").textContent = `${list.length} de ${qsos.length}`;
  const conf = qsos.filter((q) => q.rcvd).length, calls = new Set(qsos.map((q) => q.call)).size, grids = new Set(qsos.filter((q) => q.grid).map((q) => q.grid.slice(0, 4))).size;
  const far = qth ? Math.max(0, ...qsos.filter((q) => q.grid && fromLocator(q.grid)).map((q) => distKm(qth, fromLocator(q.grid)))) : 0;
  $("#qstats").innerHTML = [["QSO", qsos.length], ["Confirmados", `${conf}`], ["Indicativos", calls], ["Cuadros", grids], ["Más lejano", far ? `${nf.format(far)} km` : "–"]].map(([l, v]) => `<div class="stat"><span class="lbl">${l}</span><span class="num">${v}</span></div>`).join("");
}
function qslMap() {
  const m = ctx.map; if (!m || sub !== "qsl") return;
  if (!ctx.mapLoaded) { ctx.mapReady.then(qslMap); return; }
  ctx.clearMarkers(); clearLayers();
  const pts = [];
  if (qth) { ctx.marker(qth.lon, qth.lat, { label: `Mi QTH ${qth.locator}`, cls: "fav" }); pts.push([qth.lon, qth.lat]); }
  const lines = [];
  qsos.forEach((q) => { const p = q.grid && fromLocator(q.grid); if (!p) return; ctx.marker(p.lon, p.lat, { label: q.call, cls: "qso" }); pts.push([p.lon, p.lat]); if (qth) lines.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [[qth.lon, qth.lat], [p.lon, p.lat]] } }); });
  m.addSource("qso-l", { type: "geojson", data: { type: "FeatureCollection", features: lines } });
  m.addLayer({ id: "qso-lines", type: "line", source: "qso-l", paint: { "line-color": "#2c8a5a", "line-width": 1.2, "line-opacity": 0.6, "line-dasharray": [2, 2] } });
  if (pts.length > 1) ctx.fit(pts, { maxZoom: 6 });
}

/* ---------- Herramientas ---------- */
function herramientas() {
  ctx.colb.innerHTML = `<div class="d-head"><div><h1>Herramientas</h1><p>Pequeñas utilidades de radioaficionado, sin salir de la web</p></div></div>
  <div class="grid2" style="margin-top:20px">
    <div class="card"><h3>Locator Maidenhead ↔ coordenadas</h3><div class="field"><label for="t-loc">Locator</label><input id="t-loc" placeholder="JN58td"></div><div class="field"><label for="t-ll">Latitud, longitud</label><input id="t-ll" placeholder="48.1466, 11.6083"></div><p class="empty" id="t-out"></p></div>
    <div class="card"><h3>Distancia y rumbo</h3><div class="field"><label for="d-a">Desde (locator)</label><input id="d-a" value="${esc(qth?.locator || "")}" placeholder="IN80do"></div><div class="field"><label for="d-b">Hasta (locator)</label><input id="d-b" placeholder="JN58td"></div><p class="empty" id="d-out"></p></div>
    <div class="card"><h3>Alfabeto fonético</h3><div class="field"><label for="p-in">Indicativo o texto</label><input id="p-in" placeholder="EA3XYZ"></div><p class="empty" id="p-out" style="line-height:1.8"></p></div>
    <div class="card"><h3>Código Q</h3><div class="field"><input id="q-in" type="search" placeholder="Buscar: QSL, QTH, ruido…" aria-label="Buscar código Q"></div><div class="qgrid" id="q-out"></div></div>
  </div>
  <section class="sec"><div class="label">Subtonos CTCSS estándar <em>Hz</em></div><div class="qgrid">${CTCSS.map((t) => `<div><b>${t.toFixed(1)}</b></div>`).join("")}</div>
    <p class="empty" style="margin-top:10px">El subtono (a veces llamado «SQL» o tono de squelch codificado) se transmite por debajo de la voz para abrir el repetidor. Se configura en el campo TX tone de tu equipo.</p></section>`;
  const loc = $("#t-loc"), ll = $("#t-ll"), out = $("#t-out");
  loc.addEventListener("input", () => { const p = fromLocator(loc.value); out.textContent = p ? `Centro del cuadro: ${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}` : ""; if (p) ll.value = `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`; });
  ll.addEventListener("input", () => { const [a, b] = ll.value.split(/[,;\s]+/).map(Number); if (isFinite(a) && isFinite(b) && Math.abs(a) <= 90 && Math.abs(b) <= 180) { loc.value = toLocator(a, b); out.textContent = `Locator: ${toLocator(a, b)}`; } });
  const dist = () => { const a = fromLocator($("#d-a").value), b = fromLocator($("#d-b").value); $("#d-out").innerHTML = a && b ? `<b class="num">${nf.format(distKm(a, b))} km</b> · rumbo <b class="num">${Math.round(bearing(a, b))}°</b> (${compass(bearing(a, b))}) · inverso ${Math.round((bearing(b, a)))}°` : ""; };
  $("#d-a").addEventListener("input", dist); $("#d-b").addEventListener("input", dist); dist();
  $("#p-in").addEventListener("input", (e) => { $("#p-out").innerHTML = [...e.target.value.toUpperCase()].filter((c) => ICAO[c]).map((c) => `<b>${c}</b> ${ICAO[c]}`).join(" · "); });
  const qq = () => { const t = $("#q-in").value.toLowerCase(); $("#q-out").innerHTML = Q.filter(([c, d]) => !t || c.toLowerCase().includes(t) || d.toLowerCase().includes(t)).map(([c, d]) => `<div><b>${c}</b><br>${esc(d)}</div>`).join(""); };
  $("#q-in").addEventListener("input", qq); qq();
  if (qth) ctx.marker(qth.lon, qth.lat, { label: "Mi QTH", cls: "fav" });
}
