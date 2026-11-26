// Tarjetas de datos del asistente: previsión (con gráfico por pestañas y cursor), ruta, contraste con estaciones y acciones (guardar lugar, Mi ubicación).
import { api, esc, LS, toLocator } from "./util.js";
import { icon } from "./icons.js";
import { setQth } from "./qth.js";
import { pushSettings } from "./sync.js";
import { addQso } from "./qsl.js";
import { programarPaso } from "./recordatorio.js";

const rd = (v) => (v == null || isNaN(v) ? "–" : Math.round(v));
const NIVEL = ["Favorable", "Precaución", "Desaconsejable"];
const PLOT = { temp: ["Temperatura", "°", "serie"], lluvia: ["Lluvia", " mm", "lluvia"], viento: ["Viento", " km/h", "viento_serie"], mar: ["Oleaje", " m", "serie"] };
const BARS = (v) => v === "lluvia" || v === "mar";
const hh = (t) => (t ? `${t.slice(11, 13)} h` : "");
const short = (n = "") => n.split(" (")[0];

/** Una serie como SVG: línea suave con área (temperatura, viento) o barras (lluvia). Devuelve el SVG; el cursor lo mueve `bindCards`. */
function plot(c, v) {
  const [, unit, key] = PLOT[v], raw = c[key] || [];
  if (raw.length < 3) return "";
  const W = 320, H = 96, L = 6, R = 6, T = 10, B = 20, n = raw.length, vals = raw.map((x) => (x == null ? 0 : x));
  const hi = Math.max(...vals), lo = BARS(v) ? 0 : Math.min(...vals), span = Math.max(hi - lo, v === "lluvia" ? 1 : v === "mar" ? 0.5 : 2);
  const x = (i) => L + (i * (W - L - R)) / (n - 1), y = (val) => T + (1 - (val - lo) / span) * (H - T - B);
  let body = "";
  if (BARS(v)) {
    const bw = ((W - L - R) / n) * 0.62;
    body = vals.map((val, i) => `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${y(val).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(val > 0 ? 2 : 0, H - B - y(val)).toFixed(1)}" rx="3" fill="var(--ai)" opacity=".85"/>`).join("");
  } else {
    const pts = vals.map((val, i) => [x(i), y(val)]);
    let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) { const mx = (pts[i - 1][0] + pts[i][0]) / 2; d += ` C${mx.toFixed(1)},${pts[i - 1][1].toFixed(1)} ${mx.toFixed(1)},${pts[i][1].toFixed(1)} ${pts[i][0].toFixed(1)},${pts[i][1].toFixed(1)}`; }
    body = `<path d="${d} L${pts.at(-1)[0].toFixed(1)},${H - B} L${pts[0][0].toFixed(1)},${H - B}Z" fill="var(--ai)" opacity=".12"/><path d="${d}" fill="none" stroke="var(--ai)" stroke-width="2.2" stroke-linecap="round"/>`;
    const im = vals.indexOf(hi);
    body += `<text x="${Math.min(W - 24, Math.max(24, pts[im][0])).toFixed(1)}" y="${Math.max(9, pts[im][1] - 5).toFixed(1)}" text-anchor="middle" class="pk">${rd(hi)}${unit.trim()}</text>`;
  }
  const ticks = [0, Math.floor((n - 1) / 3), Math.floor(((n - 1) * 2) / 3), n - 1].map((i) => `<text x="${x(i).toFixed(1)}" y="${H - 5}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}" class="tk">${hh((c.horas || [])[i])}</text>`).join("");
  const pos = vals.map((val, i) => `${x(i).toFixed(1)},${y(val).toFixed(1)}`).join(" ");
  return `<svg class="dc-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(PLOT[v][0])} de las próximas ${n * 3} horas: de ${rd(lo)} a ${rd(hi)}${unit}" data-pos="${pos}" data-v="${v}">
    <line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" stroke="var(--line)"/>${body}${ticks}<line class="cur" y1="${T - 2}" y2="${H - B}" x1="-9" x2="-9"/><circle class="cur-d" r="4" cx="-9" cy="0"/></svg>`;
}

const contraste = (k) => {
  if (!k) return "";
  const t = k.temp, w = k.viento, ok = k.acuerdo === "alto";
  const bits = [t && `${t.obs}° medidos · ${t.modelo}° modelos`, w && `viento ${w.obs} · ${w.modelo} km/h`].filter(Boolean).join(" · ");
  return `<div class="dc-obs ${esc(k.acuerdo)}"><span class="dot"></span><div><b>${ok ? "Las estaciones reales coinciden" : k.acuerdo === "medio" ? "Estaciones reales con ligera desviación" : "Las estaciones reales discrepan"}</b><small>${esc(bits)} · ${k.estaciones} ${k.estaciones === 1 ? "estación" : "estaciones"} (${esc((k.fuentes || []).join(", "))})</small></div></div>`;
};

const qsoTxt = (q = {}) => `¿Anoto el contacto con <b>${esc(q.call)}</b>${[q.band, q.mode, q.freq_mhz && q.freq_mhz + " MHz", q.rst_sent && "RST " + q.rst_sent + "/" + (q.rst_rcvd || "–")].filter(Boolean).length ? " (" + esc([q.band, q.mode, q.freq_mhz && q.freq_mhz + " MHz", q.rst_sent && "RST " + q.rst_sent + "/" + (q.rst_rcvd || "–")].filter(Boolean).join(" · ")) + ")" : ""} en tu cuaderno, con la fecha y hora de ahora?`;
const pasoTxt = (p = {}, lugar = "") => `¿Te aviso 5 min antes del paso de <b>${esc(p.satelite)}</b> sobre ${esc(short(lugar))}? Sale a las ${p.aos ? new Date(p.aos).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }) : "–"} y llega a ${p.max_el}° de elevación. <small>Solo suena con esta pestaña abierta.</small>`;

/** HTML de una tarjeta. `m`/`k`: posición en la conversación (para la interacción). */
export function dataCard(c, m, k) {
  const at = `data-m="${m}" data-k="${k}"`;
  if (c.tipo === "prevision") {
    const n = c.ahora || {}, link = `#/forecast/p/${(+c.lat).toFixed(4)},${(+c.lon).toFixed(4)},${encodeURIComponent(c.lugar)}`;
    const days = (c.dias || []).map((d) => `<div><small>${esc(new Date(d.fecha + "T12:00").toLocaleDateString("es", { weekday: "short" }))}</small><b>${rd(d.temp_max_c)}°</b><span>${rd(d.temp_min_c)}°</span>${d.lluvia_mm >= 0.5 ? `<em>${d.lluvia_mm} mm</em>` : ""}</div>`).join("");
    const tabs = Object.entries(PLOT).filter(([, [, , key]]) => (c[key] || []).filter((x) => x != null).length > 2).map(([id, [t]], i) => `<button type="button" role="tab" aria-selected="${i === 0}" data-tab="${id}">${t}</button>`).join("");
    return `<div class="dcard" ${at}><div class="dc-h"><span>${esc(short(c.lugar))}</span><a href="${link}">Abrir ${icon("right", 12)}</a></div>
      <div class="dc-main"><b>${rd(n.temp)}°</b><div><span>${rd(n.viento)} km/h ${esc(n.dir || "")}</span><span>${n.lluvia >= 0.1 ? n.lluvia + " mm/3 h" : "Sin lluvia"}</span><span>Confianza ${esc(n.confianza || "–")}${c.modelos ? ` · ${c.modelos} modelos` : ""}</span></div></div>
      ${tabs ? `<div class="dc-tabs" role="tablist" aria-label="Gráfico">${tabs}</div><div class="dc-plot">${plot(c, "temp")}<div class="dc-tip" aria-live="polite"> </div></div>` : ""}
      ${contraste(c.contraste)}
      ${days ? `<div class="dc-days">${days}</div>` : ""}
      <div class="dc-acts"><button type="button" data-act="favorito">${icon("star", 14)} Guardar</button><button type="button" data-act="ubicacion">${icon("pin", 14)} Mi ubicación</button><button type="button" data-act="ask" data-q="¿Qué me recomiendas para ${esc(short(c.lugar))} estos días?">${icon("spark", 14)} Pregúntame</button></div></div>`;
  }
  if (c.tipo === "ruta") {
    const min = c.min || 0, t = min >= 60 ? `${Math.floor(min / 60)} h ${String(Math.round(min % 60)).padStart(2, "0")}` : `${Math.round(min)} min`;
    return `<div class="dcard" ${at}><div class="dc-h"><span>${esc(c.lugar)}</span></div><div class="dc-route">
      <div><b>${c.km != null ? (+c.km).toFixed(1) : "–"}</b><small>km</small></div><div><b>${t}</b><small>${c.modo === "car" ? "en coche" : "a pie"}</small></div><div><b>${rd(c.asc)}</b><small>m de subida</small></div>
      <div class="lv${c.nivel ?? 0}"><b>${NIVEL[c.nivel ?? 0] || "–"}</b><small>${esc((c.texto || "meteo").slice(0, 60))}</small></div></div>
      <div class="dc-acts"><button type="button" data-act="ask" data-q="¿Y si salgo antes o más tarde?">${icon("clock", 14)} ¿Y si salgo antes?</button></div></div>`;
  }
  if (c.tipo === "accion") {
    const T = {
      favorito: ["Guardar en favoritos", `¿Guardo <b>${esc(short(c.lugar))}</b> en tus lugares?`, "Guardar", "Guardado"],
      ubicacion: ["Usar como Mi ubicación", `¿Pongo <b>${esc(short(c.lugar))}</b> como tu ubicación? El asistente y la previsión la usarán para «aquí».`, "Usar como Mi ubicación", "Ubicación actualizada"],
      qso: ["Anotar en el cuaderno QSL", qsoTxt(c.qso), "Anotar", "Anotado en tu cuaderno"],
      paso: ["Aviso de paso", pasoTxt(c.paso, c.lugar), "Avisarme", "Aviso programado"],
      memoria: ["Recordar preferencia", `¿Quieres que recuerde esto para futuras respuestas? <b>«${esc(c.memoria?.texto || c.lugar)}»</b> <small>Puedes verlo y borrarlo en Ajustes → Memoria.</small>`, "Recordar", "Anotado en tu memoria"],
    }[c.accion] || ["Acción", "", "Hacer", "Hecho"];
    return `<div class="dcard act" ${at}><div class="dc-h"><span>${T[0]}</span></div>
      <p class="dc-q">${T[1]}</p>
      <div class="dc-acts">${c.hecho === "no" ? `<span class="done" style="color:var(--ink-3)">Lo dejamos así</span>` : c.hecho ? `<span class="done">${icon("check", 14)} ${T[3]}</span>` : `<button type="button" class="pri" data-act="${esc(c.accion)}">${T[2]}</button><button type="button" class="ghost" data-act="no">Ahora no</button>`}</div></div>`;
  }
  if (c.tipo === "comparar") {
    const ls = c.lugares || [], mx = Math.max(...ls.map((l) => l.max_c ?? -99));
    const rows = ls.map((l) => `<a class="cmp-r${l.max_c === mx ? " best" : ""}" href="#/forecast/p/${(+l.lat).toFixed(4)},${(+l.lon).toFixed(4)},${encodeURIComponent(l.nombre)}"><b>${esc(short(l.nombre))}</b><span>${rd(l.min_c)}° / ${rd(l.max_c)}°</span><span>${l.lluvia_48h_mm >= 0.5 ? l.lluvia_48h_mm + " mm" : "Seco"}</span><span>${rd(l.racha_max_kmh)} km/h</span></a>`).join("");
    return `<div class="dcard" ${at}><div class="dc-h"><span>Comparación · 48 h</span></div><div class="cmp-h"><span></span><span>Mín / máx</span><span>Lluvia</span><span>Racha</span></div>${rows}
      ${(c.veredicto || []).length ? `<ul class="cmp-v">${c.veredicto.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>` : ""}
      <div class="dc-acts"><button type="button" data-act="ask" data-q="¿Cuál me conviene más para salir mañana?">${icon("spark", 14)} ¿Cuál me conviene?</button></div></div>`;
  }
  if (c.tipo === "mar") {
    const n = c.ahora || {}, m = c.maximo || {};
    return `<div class="dcard" ${at}><div class="dc-h"><span>Mar · ${esc(short(c.lugar))}</span><a href="#/forecast/p/${(+c.lat).toFixed(4)},${(+c.lon).toFixed(4)},${encodeURIComponent(c.lugar)}">Abrir ${icon("right", 12)}</a></div>
      <div class="dc-main"><b>${n.ola_m != null ? (+n.ola_m).toFixed(1) : "–"} m</b><div><span>${esc(n.estado || "")}${n.periodo_s ? ` · ${rd(n.periodo_s)} s` : ""}</span><span>${n.agua_c != null ? "Agua " + rd(n.agua_c) + "°" : ""}</span><span>Máx. 48 h: ${m.ola_m != null ? (+m.ola_m).toFixed(1) + " m" : "–"}</span></div></div>
      <div class="dc-plot">${plot(c, "mar")}<div class="dc-tip" aria-live="polite"> </div></div>
      <div class="dc-acts"><button type="button" data-act="ask" data-q="¿Es buen momento para bañarse o salir en barca en ${esc(short(c.lugar))}?">${icon("spark", 14)} ¿Salgo al mar?</button></div></div>`;
  }
  if (c.tipo === "satelites") {
    const f = (t) => new Date(t).toLocaleString("es", { weekday: "short", hour: "2-digit", minute: "2-digit" });
    const rows = (c.pasos || []).map((x) => `<div class="sat-r"><b>${esc(x.satelite)}</b><span>${f(x.aos)}</span><span>${x.max_el}° máx.</span><span>${Math.round((x.duracion_s || 0) / 60)} min</span></div>`).join("");
    return `<div class="dcard" ${at}><div class="dc-h"><span>Próximos pasos · ${esc(short(c.lugar))}</span></div>${rows}
      <div class="dc-acts"><button type="button" data-act="ask" data-q="¿Qué frecuencia y modo uso en el mejor paso?">${icon("spark", 14)} ¿Qué frecuencia uso?</button></div></div>`;
  }
  return "";
}

/** Ejecuta una acción sobre un lugar; devuelve el texto de confirmación. */
export async function accion(tipo, c) {
  if (tipo === "qso") {
    const q = { ...c.qso, qso_at: new Date().toISOString().slice(0, 16) + ":00Z" };
    await addQso(q);
    return `Contacto con ${q.call} anotado`;
  }
  if (tipo === "paso") return programarPaso(c.paso);
  if (tipo === "memoria") { await api("/api/me/memoria/notas", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(c.memoria) }); return "Anotado en tu memoria"; }
  const lat = +(+c.lat).toFixed(4), lon = +(+c.lon).toFixed(4), name = short(c.lugar);
  if (tipo === "favorito") {
    const ev = new CustomEvent("mg:fav", { detail: { name, sub: (c.lugar.match(/\((.*)\)/) || [])[1] || "", lat, lon } });
    window.dispatchEvent(ev);
    return `${name} guardado en tus lugares`;
  }
  if (tipo === "ubicacion") {
    const l = toLocator(lat, lon);
    setQth({ ...(LS.get("mg-qth", null) || {}), lat, lon, locator: l.slice(0, 4).toUpperCase() + l.slice(4), name, at: Date.now() });
    return `Mi ubicación: ${name}`;
  }
  return "";
}

/** Interacción: pestañas del gráfico y cursor con valor. `get(m, k)` devuelve la tarjeta de ese mensaje. */
export function bindCards(box, get) {
  box.addEventListener("click", (e) => {
    const t = e.target.closest("[data-tab]");
    if (!t) return;
    const card = t.closest(".dcard"), c = get(+card.dataset.m, +card.dataset.k);
    if (!c) return;
    card.querySelectorAll("[data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b === t)));
    card.querySelector(".dc-plot").innerHTML = plot(c, t.dataset.tab) + '<div class="dc-tip" aria-live="polite"> </div>';
  });
  box.addEventListener("pointermove", (e) => {
    const svg = e.target.closest?.(".dc-svg");
    if (!svg) return;
    const card = svg.closest(".dcard"), c = get(+card.dataset.m, +card.dataset.k), v = svg.dataset.v, pts = svg.dataset.pos.split(" ").map((p) => p.split(",").map(Number));
    const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * 320;
    let i = 0, best = 1e9;
    pts.forEach((p, j) => { const d = Math.abs(p[0] - px); if (d < best) { best = d; i = j; } });
    const [name, unit, key] = PLOT[v], val = (c[key] || [])[i];
    svg.querySelector(".cur").setAttribute("x1", pts[i][0]); svg.querySelector(".cur").setAttribute("x2", pts[i][0]);
    const d = svg.querySelector(".cur-d"); d.setAttribute("cx", pts[i][0]); d.setAttribute("cy", pts[i][1]);
    svg.parentElement.querySelector(".dc-tip").textContent = `${hh((c.horas || [])[i])} · ${name} ${val == null ? "–" : (BARS(v) ? val.toFixed(1) : rd(val))}${unit}`;
  });
}
