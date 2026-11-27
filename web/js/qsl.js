// Cuaderno QSL: local en el dispositivo o sincronizado con la clave (API y MCP). Importa/exporta ADIF y genera tarjetas QSL.
import { $, $$, esc, nf, LS, fromLocator, toLocator, distKm, debounce } from "./util.js";
import { icon } from "./icons.js";
import { syncing, call } from "./sync.js";
import * as qslserv from "./qslserv.js";

const BANDS = ["160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m", "4m", "2m", "70cm", "23cm"];
const MODES = ["SSB", "CW", "FM", "FT8", "FT4", "DMR", "D-STAR", "C4FM", "RTTY", "AM", "Otro"];
let ctx, H, rows = [], f = { text: "", status: "" };
const JSONH = { "content-type": "application/json" };

/* ---------- almacenamiento ---------- */
function localAll() {
  let l = LS.get("mg-qso", null);
  if (l === null) {                                           // migración del formato antiguo
    l = LS.get("mg-qsl", []).map((q) => ({ id: q.id, call: q.call, qso_at: q.when + ":00Z", band: q.band, mode: q.mode, freq_mhz: q.freq ? +q.freq : null, rst_sent: q.rsts, rst_rcvd: q.rstr, grid: q.grid || null, name: q.name || null, notes: q.notes || null, qsl_sent: !!q.sent, qsl_rcvd: !!q.rcvd }));
    LS.set("mg-qso", l);
  }
  return l;
}
async function load() { rows = syncing() ? await call("/api/qso?limit=1000").catch(() => localAll()) : localAll(); return rows; }
async function add(q) {
  if (syncing()) { const r = await call("/api/qso", { method: "POST", headers: JSONH, body: JSON.stringify(q) }); return r; }
  const all = localAll(); all.unshift({ id: Date.now(), ...q }); LS.set("mg-qso", all); return { ya_trabajado: [] };
}
async function patch(id, p) { if (syncing()) return call(`/api/qso/${id}`, { method: "PATCH", headers: JSONH, body: JSON.stringify(p) }); const all = localAll(), q = all.find((x) => x.id === id); Object.assign(q, p); LS.set("mg-qso", all); }
async function del(id) { if (syncing()) return call(`/api/qso/${id}`, { method: "DELETE" }); LS.set("mg-qso", localAll().filter((x) => x.id !== id)); }
/** Anota un contacto desde fuera del cuaderno (asistente): en la cuenta sincronizada o en este dispositivo. */
export const addQso = (q) => add(q);
/** Bandas en las que has operado, de más a menos usadas, con la clave normalizada («20m») para compararlas con otras pantallas. */
export async function bandasHabituales() {
  const c = {};
  for (const q of await load().catch(() => [])) if (q.band) { const k = String(q.band).toLowerCase().replace(/\s/g, ""); c[k] = (c[k] || 0) + 1; }
  return Object.entries(c).sort((a, b) => b[1] - a[1]).map(([k, n]) => ({ k, n }));
}
export async function quickStats() { const r = await load(); return r.length ? { total: r.length, confirmados: r.filter((q) => q.qsl_rcvd).length, indicativos: new Set(r.map((q) => q.call)).size } : null; }

/* ---------- ADIF ---------- */
const fld = (k, v) => (v === null || v === undefined || v === "" ? "" : `<${k}:${[...String(v)].length}>${v}`);
export function toAdif(list, myGrid) {
  return "Exportado por Radio Guilda\n<ADIF_VER:5>3.1.4<PROGRAMID:11>RadioGuilda<EOH>\n" + list.map((q) => {
    const d = new Date(q.qso_at).toISOString();
    return fld("CALL", q.call) + fld("QSO_DATE", d.slice(0, 10).replace(/-/g, "")) + fld("TIME_ON", d.slice(11, 16).replace(":", "")) + fld("BAND", q.band) + fld("MODE", q.mode && q.mode !== "Otro" ? q.mode.replace("-", "") : "") + fld("FREQ", q.freq_mhz) +
      fld("RST_SENT", q.rst_sent) + fld("RST_RCVD", q.rst_rcvd) + fld("GRIDSQUARE", q.grid) + fld("NAME", q.name) + fld("COMMENT", q.notes) + fld("QSL_SENT", q.qsl_sent ? "Y" : "N") + fld("QSL_RCVD", q.qsl_rcvd ? "Y" : "N") + fld("MY_GRIDSQUARE", myGrid) + "<EOR>";
  }).join("\n") + "\n";
}
export function parseAdif(text) {
  const body = text.replace(/^[\s\S]*?<eoh>/i, ""), out = [];
  for (const rec of body.split(/<eor>/i)) {
    const d = {}; const re = /<([A-Za-z_0-9]+):(\d+)(?::[A-Za-z])?>/g; let m;
    while ((m = re.exec(rec))) { d[m[1].toUpperCase()] = rec.substr(re.lastIndex, +m[2]); re.lastIndex += +m[2]; }
    if (!d.CALL || !/^\d{8}$/.test(d.QSO_DATE || "")) continue;
    const t = (d.TIME_ON || "0000").padEnd(6, "0"), D = d.QSO_DATE;
    out.push({ call: d.CALL.toUpperCase(), qso_at: `${D.slice(0, 4)}-${D.slice(4, 6)}-${D.slice(6, 8)}T${t.slice(0, 2)}:${t.slice(2, 4)}:${t.slice(4, 6)}Z`, band: (d.BAND || "").toLowerCase() || null, mode: d.MODE || null, freq_mhz: d.FREQ ? +d.FREQ : null,
      rst_sent: d.RST_SENT || null, rst_rcvd: d.RST_RCVD || null, grid: d.GRIDSQUARE || null, name: d.NAME || null, notes: d.COMMENT || null, qsl_sent: (d.QSL_SENT || "").toUpperCase() === "Y", qsl_rcvd: (d.QSL_RCVD || "").toUpperCase() === "Y" });
  }
  return out;
}
const dl = (name, text, type) => { const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([text], { type })), download: name }); a.click(); URL.revokeObjectURL(a.href); };

/* ---------- vista ---------- */
const utcInput = (d = new Date()) => d.toISOString().slice(0, 16);
const gridPos = (g) => (g && fromLocator(g)) || null;
export async function view(c, h) {
  ctx = c; H = h;
  const q = H.getQth();
  const opt = (a) => a.map((x) => `<option>${x}</option>`).join("");
  ctx.colb.innerHTML = `<div class="d-head"><div><h1>Cuaderno QSL</h1><p>Registra tus contactos, marca las confirmaciones y genera tus tarjetas</p></div>
    <div class="d-actions"><button class="btn ghost sm" id="exp" type="button">${icon("download", 14)} ADIF</button><label class="btn ghost sm" style="cursor:pointer">${icon("upload", 14)} Importar ADIF<input id="imp" type="file" accept=".adi,.adif,text/plain" hidden></label></div></div>
    <p class="empty" style="margin-top:8px">${syncing() ? "Sincronizado con tu clave: también accesible por API y MCP." : `Guardado solo en este dispositivo. Activa la sincronización (icono de llave) para tenerlo en todos tus dispositivos y por API/MCP.`}</p>
    <div id="qstats" class="stats" style="margin:18px 0"></div><div class="grid2" id="qcharts" style="margin-bottom:6px"></div>
    <section class="sec" style="margin-top:12px"><div class="label">Nuevo contacto</div><form id="qf" class="qsl-form">
      <div class="field"><label for="f-call">Indicativo</label><input id="f-call" required maxlength="14" style="text-transform:uppercase" autocomplete="off"></div>
      <div class="field"><label for="f-when">Fecha y hora UTC</label><input id="f-when" type="datetime-local" value="${utcInput()}" required></div>
      <div class="field"><label for="f-band">Banda</label><select id="f-band">${opt(BANDS)}</select></div>
      <div class="field"><label for="f-mode">Modo</label><select id="f-mode">${opt(MODES)}</select></div>
      <div class="field"><label for="f-freq">Frecuencia MHz</label><input id="f-freq" type="number" step="0.0001" placeholder="145.5000"></div>
      <div class="field"><label for="f-rs">RST enviado</label><input id="f-rs" value="59"></div><div class="field"><label for="f-rr">RST recibido</label><input id="f-rr" value="59"></div>
      <div class="field"><label for="f-grid">Locator</label><input id="f-grid" maxlength="8" placeholder="IN80do"></div><div class="field"><label for="f-name">Nombre / QTH</label><input id="f-name"></div>
      <div class="field" style="grid-column:1/-1"><label for="f-notes">Notas</label><input id="f-notes" maxlength="500"></div>
      <div style="grid-column:1/-1" id="dupe"></div>
      <div style="grid-column:1/-1" class="row"><button class="btn" type="submit">${icon("plus", 14)} Añadir contacto</button><span class="empty" id="f-msg"></span></div></form></section>
    <section class="sec"><div class="label">Contactos <em id="qcount"></em></div>
      <div class="row" style="margin-bottom:12px"><input id="qft" type="search" placeholder="Filtrar por indicativo, nombre…" style="max-width:280px" value="${esc(f.text)}"><select id="qfs" style="width:auto"><option value="">Todos</option><option value="pend">Pendientes de QSL</option><option value="conf">Confirmados</option></select></div>
      <div class="tablewrap"><table><thead><tr><th>Indicativo</th><th>UTC</th><th>Banda · modo</th><th>Locator</th><th>QSL</th><th></th></tr></thead><tbody id="qrows"></tbody></table></div></section>
    <section class="sec"><div class="label">Servicios <em>LoTW, eQSL y QRZ</em></div><div id="qsrv"></div></section>`;
  $("#qfs").value = f.status;
  await load(); paint(); map();
  qslserv.view($("#qsrv"), async () => { await load(); paint(); map(); });
  const call$ = $("#f-call");
  call$.addEventListener("input", debounce(() => {                          // aviso de duplicado y autocompletado
    const c = call$.value.trim().toUpperCase(), prev = rows.filter((r) => r.call === c);
    $("#dupe").innerHTML = c.length >= 3 && prev.length ? `<div class="dupe">${icon("info", 15)} Ya trabajado ${prev.length} ${prev.length > 1 ? "veces" : "vez"}: ${prev.slice(0, 3).map((r) => `${new Date(r.qso_at).toISOString().slice(0, 10)} ${esc(r.band || "")} ${esc(r.mode || "")}`).join(" · ")}</div>` : "";
    if (prev.length) { if (!$("#f-name").value) $("#f-name").value = prev[0].name || ""; if (!$("#f-grid").value) $("#f-grid").value = prev[0].grid || ""; }
  }, 200));
  $("#qf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const g = $("#f-grid").value.trim();
    if (g && !fromLocator(g)) return ($("#f-msg").textContent = "Locator no válido (p. ej. IN80 o IN80do)");
    const body = { call: call$.value.trim().toUpperCase(), qso_at: $("#f-when").value + ":00Z", band: $("#f-band").value, mode: $("#f-mode").value, freq_mhz: $("#f-freq").value ? +$("#f-freq").value : null, rst_sent: $("#f-rs").value, rst_rcvd: $("#f-rr").value, grid: g || null, name: $("#f-name").value || null, notes: $("#f-notes").value || null, qsl_sent: false, qsl_rcvd: false };
    try { await add(body); await load(); call$.value = $("#f-name").value = $("#f-notes").value = $("#f-grid").value = ""; $("#dupe").innerHTML = ""; $("#f-when").value = utcInput(); $("#f-msg").textContent = "Guardado"; paint(); map(); call$.focus(); }
    catch (err) { $("#f-msg").textContent = err.message; }
  });
  $("#qft").addEventListener("input", (e) => { f.text = e.target.value; rowsPaint(); });
  $("#qfs").addEventListener("change", (e) => { f.status = e.target.value; rowsPaint(); });
  $("#exp").addEventListener("click", () => (rows.length ? dl("radioguilda.adi", toAdif(rows, q?.locator), "text/plain") : ctx.toast("No hay contactos")));
  $("#imp").addEventListener("change", async (e) => {
    try {
      const text = await e.target.files[0].text();
      if (syncing()) { const r = await call("/api/qso/import", { method: "POST", headers: { "content-type": "text/plain" }, body: text }); ctx.toast(`${r.importados} contactos importados`); }
      else { const recs = parseAdif(text), all = localAll(); recs.forEach((r, i) => all.push({ id: Date.now() + i, ...r })); LS.set("mg-qso", all); ctx.toast(`${recs.length} contactos importados`); }
      await load(); paint(); map();
    } catch (err) { ctx.toast(err.message || "ADIF no válido"); }
  });
  $("#qrows").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-id]"); if (!b) return; const id = +b.dataset.id, r = rows.find((x) => x.id === id);
    try {
      if (b.dataset.act === "del") { if (!confirm(`¿Borrar el contacto con ${r.call}?`)) return; await del(id); }
      else if (b.dataset.act === "card") return card(r);
      else await patch(id, { [b.dataset.act]: !r[b.dataset.act] });
      await load(); paint(); map();
    } catch (err) { ctx.toast(err.message); }
  });
}
function paint() { rowsPaint(); statsPaint(); }
function rowsPaint() {
  const t = f.text.toLowerCase(), list = rows.filter((q) => (!t || `${q.call} ${q.name || ""} ${q.notes || ""}`.toLowerCase().includes(t)) && (!f.status || (f.status === "conf" ? q.qsl_rcvd : !q.qsl_rcvd)));
  $("#qrows").innerHTML = list.slice(0, 300).map((q) => `<tr><td><b>${esc(q.call)}</b>${q.name ? `<br><small style="color:var(--ink-3)">${esc(q.name)}</small>` : ""}</td><td class="num">${esc(new Date(q.qso_at).toISOString().slice(0, 16).replace("T", " "))}</td><td>${esc(q.band || "–")} · ${esc(q.mode || "–")}</td><td class="num">${esc(q.grid || "–")}</td>
    <td><button class="st ${q.qsl_sent ? "on" : ""}" data-id="${q.id}" data-act="qsl_sent" type="button">Enviada</button> <button class="st ${q.qsl_rcvd ? "on" : ""}" data-id="${q.id}" data-act="qsl_rcvd" type="button">Recibida</button></td>
    <td style="white-space:nowrap"><button class="icon-btn" data-id="${q.id}" data-act="card" aria-label="Tarjeta QSL" title="Tarjeta QSL" style="width:30px;height:30px">${icon("card", 16)}</button><button class="icon-btn" data-id="${q.id}" data-act="del" aria-label="Borrar" title="Borrar" style="width:30px;height:30px">${icon("trash", 16)}</button></td></tr>`).join("") ||
    `<tr><td colspan="6" class="empty" style="text-align:center;padding:20px">${rows.length ? "Sin resultados" : "Todavía no has registrado ningún contacto"}</td></tr>`;
  $("#qcount").textContent = `${list.length} de ${rows.length}`;
}
function statsPaint() {
  const q = H.getQth(), conf = rows.filter((r) => r.qsl_rcvd).length, calls = new Set(rows.map((r) => r.call)).size, grids = new Set(rows.filter((r) => r.grid).map((r) => r.grid.slice(0, 4).toUpperCase())).size;
  const far = q?.lat != null ? rows.map((r) => ({ r, p: gridPos(r.grid) })).filter((x) => x.p).map((x) => ({ d: distKm(q, x.p), c: x.r.call })).sort((a, b) => b.d - a.d)[0] : null;
  $("#qstats").innerHTML = [["QSO", rows.length], ["Confirmados", `${conf}${rows.length ? ` · ${Math.round((conf / rows.length) * 100)}%` : ""}`], ["Indicativos", calls], ["Cuadros", grids], ["Más lejano", far ? `${nf.format(far.d)} km` : "–"]].map(([l, v]) => `<div class="stat"><span class="lbl">${l}</span><span class="num">${v}</span></div>`).join("");
  const by = (k) => { const m = {}; rows.forEach((r) => r[k] && (m[r[k]] = (m[r[k]] || 0) + 1)); return Object.entries(m).sort((a, b) => b[1] - a[1]).slice(0, 8); };
  const bars = (title, ic, e) => { const mx = Math.max(1, ...e.map((x) => x[1])); return `<div class="card"><h3>${icon(ic, 16)} ${title}</h3>${e.length ? e.map(([k, n]) => `<div class="hbar"><span>${esc(k)}</span><i style="width:${(n / mx) * 100}%"></i><b class="num">${n}</b></div>`).join("") : '<p class="empty">Sin datos</p>'}</div>`; };
  $("#qcharts").innerHTML = rows.length ? bars("Por banda", "wave", by("band")) + bars("Por modo", "antenna", by("mode")) : "";
}

/* ---------- mapa: contactos, líneas y cuadros trabajados ---------- */
function map() {
  H.onMap(() => {
    const m = ctx.map; ctx.clearMarkers(); H.clearLayers();
    const q = H.getQth(), pts = [], lines = [], squares = new Set(), feats = [];
    if (q?.lat != null) { ctx.marker(q.lon, q.lat, { label: `Mi QTH ${q.locator || ""}`, cls: "fav" }); pts.push([q.lon, q.lat]); }
    const seen = new Set();
    rows.forEach((r) => {
      const p = gridPos(r.grid); if (!p) return;
      pts.push([p.lon, p.lat]); if (!seen.has(r.call)) { seen.add(r.call); ctx.marker(p.lon, p.lat, { label: r.call, cls: "qso" }); }
      if (q?.lat != null) lines.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [[q.lon, q.lat], [p.lon, p.lat]] } });
      squares.add(r.grid.slice(0, 4).toUpperCase());
    });
    squares.forEach((g) => { const c = fromLocator(g); if (!c) return; const w = 1, h = 0.5, [x, y] = [c.lon, c.lat]; feats.push({ type: "Feature", properties: { g }, geometry: { type: "Polygon", coordinates: [[[x - w, y - h], [x + w, y - h], [x + w, y + h], [x - w, y + h], [x - w, y - h]]] } }); });
    m.addSource("qso-g", { type: "geojson", data: { type: "FeatureCollection", features: feats } });
    m.addLayer({ id: "qso-grids", type: "fill", source: "qso-g", paint: { "fill-color": "#2c8a5a", "fill-opacity": 0.14 } });
    m.addSource("qso-l", { type: "geojson", data: { type: "FeatureCollection", features: lines } });
    m.addLayer({ id: "qso-lines", type: "line", source: "qso-l", paint: { "line-color": "#2c8a5a", "line-width": 1.2, "line-opacity": 0.6, "line-dasharray": [2, 2] } });
    if (pts.length > 1) ctx.fit(pts, { maxZoom: 6 }); else if (q?.lat != null) ctx.fly(q.lat, q.lon, 5);
  });
}

/* ---------- tarjeta QSL (PNG) ---------- */
async function card(r) {
  const q = H.getQth(), my = (q?.call || "").toUpperCase() || "TU INDICATIVO";
  await document.fonts.ready;
  const cv = Object.assign(document.createElement("canvas"), { width: 1200, height: 760 }), x = cv.getContext("2d");
  const bg = x.createLinearGradient(0, 0, 1200, 760); bg.addColorStop(0, "#0f2a2e"); bg.addColorStop(1, "#0b1217"); x.fillStyle = bg; x.fillRect(0, 0, 1200, 760);
  x.strokeStyle = "rgba(255,255,255,.07)"; x.lineWidth = 2;                                       // cordillera de fondo
  [[0.62, 36, 0.0], [0.72, 28, 1.7], [0.82, 22, 3.1]].forEach(([y0, a, ph]) => { x.beginPath(); for (let i = 0; i <= 1200; i += 10) { const yy = 760 * y0 + Math.sin(i / 90 + ph) * a + Math.sin(i / 37 + ph) * 9; i ? x.lineTo(i, yy) : x.moveTo(i, yy); } x.stroke(); });
  x.save(); x.translate(56, 50); x.scale(2.3, 2.3); x.strokeStyle = "#f08a56"; x.lineWidth = 2.3; x.lineCap = x.lineJoin = "round";
  x.stroke(new Path2D("M3.5 26.5 12 11.5l5 8.5 3.5-5 8 11.5ZM8.46 7.46A5 5 0 0 1 15.54 7.46M5.64 4.64A9 9 0 0 1 18.36 4.64")); x.beginPath(); x.arc(25, 7.2, 2.6, 0, 7); x.stroke(); x.restore();
  x.fillStyle = "#e7ecef"; x.textBaseline = "alphabetic";
  x.font = '600 26px "Plex Sans", sans-serif'; x.fillStyle = "#f08a56"; x.fillText("RADIO GUILDA", 150, 92); x.fillStyle = "#a6b1ba"; x.font = '400 22px "Plex Sans", sans-serif'; x.fillText("Confirmación de contacto · QSL", 150, 124);
  x.fillStyle = "#e7ecef"; x.font = '500 150px Newsreader, serif'; x.fillText(my, 56, 300);
  x.font = '400 26px "Plex Sans", sans-serif'; x.fillStyle = "#a6b1ba"; x.fillText(q?.locator ? `QTH ${q.locator} · ${q.lat.toFixed(2)}°, ${q.lon.toFixed(2)}°` : "", 60, 346);
  const d = new Date(r.qso_at), p = gridPos(r.grid), km = p && q?.lat != null ? `${nf.format(distKm(q, p))} km` : "";
  const cells = [["PARA / TO", r.call], ["FECHA UTC", d.toISOString().slice(0, 10)], ["HORA UTC", d.toISOString().slice(11, 16)], ["BANDA", r.band || "–"], ["MODO", r.mode || "–"], ["RST", r.rst_sent || "–"], ["LOCATOR", r.grid || "–"], ["DISTANCIA", km || "–"]];
  cells.forEach(([k, v], i) => { const cx = 60 + (i % 4) * 285, cy = 430 + Math.floor(i / 4) * 130;
    x.fillStyle = "rgba(255,255,255,.06)"; x.beginPath(); x.roundRect(cx, cy, 265, 108, 12); x.fill();
    x.fillStyle = "#74808a"; x.font = '600 17px "Plex Sans", sans-serif'; x.fillText(k, cx + 18, cy + 34); x.fillStyle = "#e7ecef"; x.font = '500 38px "Plex Mono", monospace'; x.fillText(String(v).slice(0, 12), cx + 18, cy + 84); });
  x.fillStyle = "#a6b1ba"; x.font = 'italic 500 30px Newsreader, serif'; x.fillText(`Gracias por el QSO${r.name ? ", " + r.name : ""}. 73`, 60, 720);
  x.textAlign = "right"; x.font = '400 20px "Plex Sans", sans-serif'; x.fillText("radio.guildawork.com", 1140, 720);
  const fname = `qsl-${r.call}-${d.toISOString().slice(0, 10)}.png`, canShare = !!navigator.canShare && navigator.canShare({ files: [new File([""], fname, { type: "image/png" })] });
  ctx.openDialog(`<div class="qsl-h"><div><h2 style="margin:0">Tarjeta QSL</h2><p class="qsl-s">${esc(r.call)} · ${d.toISOString().slice(0, 10)} · ${esc(r.band || "")} ${esc(r.mode || "")}</p></div><form method="dialog"><button class="icon-btn" aria-label="Cerrar">${icon("x")}</button></form></div>
    <canvas id="cvp" style="width:100%;border-radius:14px"></canvas>
    <div class="row qsl-acts"><button class="btn" id="cvdl" type="button">${icon("download", 14)} Descargar PNG</button>${canShare ? `<button class="btn ghost" id="cvsh" type="button">${icon("share", 14)} Compartir</button>` : ""}<button class="btn ghost" id="cvcp" type="button">${icon("link", 14)} Copiar imagen</button></div>
    <p class="qsl-s" id="cvmsg" style="margin:8px 0 0">Imagen de 1200 × 760 px, lista para enviar o imprimir.</p>`);
  document.activeElement?.blur?.();
  const t = $("#cvp"); t.width = 1200; t.height = 760; t.getContext("2d").drawImage(cv, 0, 0);
  const blob = () => new Promise((ok) => cv.toBlob(ok));
  $("#cvdl").addEventListener("click", async () => { const b = await blob(), a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(b), download: fname }); a.click(); URL.revokeObjectURL(a.href); });
  $("#cvsh")?.addEventListener("click", async () => { try { await navigator.share({ files: [new File([await blob()], fname, { type: "image/png" })], title: `QSL ${r.call}` }); } catch {} });
  $("#cvcp").addEventListener("click", async () => { const m = $("#cvmsg"); try { await navigator.clipboard.write([new ClipboardItem({ "image/png": await blob() })]); m.textContent = "Imagen copiada al portapapeles."; } catch { m.textContent = "Tu navegador no permite copiar imágenes; usa Descargar."; } });
}
