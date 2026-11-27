// APRS en vivo (última posición de cada estación oída por el ingestor) y clúster DX (spots públicos con caché del servidor).
// Ver backend/meteoguilda/aprs_vivo.py. Se refresca solo mientras la vista está abierta: APRS cada minuto y DX cada dos.
import { $, $$, api, esc, nf1 } from "./util.js";

let timer = null, filtros = { banda: "", modo: "" }, enlazado = false, ref = null;
const LAYER = "aprs-pt", SRC = "aprs";
export function stop() { clearInterval(timer); timer = null; }
export function clear(m) { if (!m) return; if (m.getLayer(LAYER)) m.removeLayer(LAYER); if (m.getSource(SRC)) m.removeSource(SRC); }

const COL = { meteo: "#0f8a82", coche: "#c4501b", camión: "#c4501b", furgoneta: "#c4501b", moto: "#c4501b", persona: "#7a5bd6", bici: "#7a5bd6", barco: "#3a7bd5", velero: "#3a7bd5", digipeater: "#2c8a5a", igate: "#2c8a5a" };

async function aprs(ctx, onMap, here) {
  const m = ctx.map; let bb = null;
  if (m && ctx.mapLoaded) { const b = m.getBounds(); bb = `${b.getSouth()},${b.getWest()},${b.getNorth()},${b.getEast()}`; }
  let r; try { r = await api(bb ? `/api/aprs?bbox=${bb}&max_age_min=60` : `/api/aprs?lat=${here.lat}&lon=${here.lon}&km=80&max_age_min=60`); } catch (e) { const l = $("#aprs-list"); if (l) l.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  onMap(() => {
    const mm = ctx.map; if (!mm || !$("#aprs-list")) return;
    const data = { type: "FeatureCollection", features: r.estaciones.map((e) => ({ type: "Feature", geometry: { type: "Point", coordinates: [e.lon, e.lat] }, properties: { c: e.indicativo, t: e.tipo, d: [e.hace_min != null ? `hace ${e.hace_min} min` : "", e.comentario || ""].filter(Boolean).join(" · "), col: COL[e.tipo] || "#555" } })) };
    if (mm.getSource(SRC)) mm.getSource(SRC).setData(data);
    else {
      mm.addSource(SRC, { type: "geojson", data });
      mm.addLayer({ id: LAYER, type: "circle", source: SRC, paint: { "circle-radius": 5, "circle-color": ["get", "col"], "circle-stroke-width": 1.5, "circle-stroke-color": "#fff" } });
      // el estilo del mapa no trae tipografías (glyphs): el indicativo se enseña al tocar el punto
      if (!mm._aprsClick) { mm._aprsClick = true; mm.on("click", LAYER, (ev) => { const p = ev.features?.[0]?.properties; if (p) ctx.toast(`${p.c} · ${p.t}${p.d ? ` · ${p.d}` : ""}`); }); }
    }
  });
  const l = $("#aprs-list"); if (!l) return;
  const cerca = r.estaciones.slice(0, 40);
  l.innerHTML = cerca.length ? `<div class="tablewrap"><table><thead><tr><th>Indicativo</th><th>Tipo</th><th>Hace</th><th>Detalle</th></tr></thead><tbody>${cerca.map((e) => `<tr><td><b>${esc(e.indicativo)}</b></td><td>${esc(e.tipo)}</td><td class="num">${e.hace_min} min</td><td><small>${[e.velocidad_kmh ? `${e.velocidad_kmh} km/h` : "", e.altitud_m != null ? `${e.altitud_m} m` : "", e.comentario || ""].filter(Boolean).map(esc).join(" · ")}</small></td></tr>`).join("")}</tbody></table></div>
    <p class="empty">${r.total} estaciones en la zona del mapa en la última hora. ${esc(r.nota)}</p>` : `<p class="empty">Ninguna estación en esta zona en la última hora. ${esc(r.nota)}</p>`;
}

async function dx() {
  const q = new URLSearchParams({ limite: "60", ...(filtros.banda ? { banda: filtros.banda } : {}), ...(filtros.modo ? { modo: filtros.modo } : {}) });
  let r; try { r = await api(`/api/dxcluster?${q}`); } catch (e) { const b = $("#dx-list"); if (b) b.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  const b = $("#dx-list"); if (!b) return;
  b.innerHTML = r.spots.length ? `<div class="tablewrap"><table><thead><tr><th>UTC</th><th>DX</th><th>kHz</th><th>Banda · modo</th><th>País</th><th>Comentario</th></tr></thead><tbody>${r.spots.map((s) => `<tr><td class="num">${esc(s.hora.slice(11, 16))}</td><td><b>${esc(s.dx)}</b>${s.lotw ? ' <small class="tag ok">LoTW</small>' : ""}</td><td class="num">${nf1.format(s.frecuencia_khz)}</td><td>${esc(s.banda || "–")} · ${esc(s.modo || "–")}</td><td>${esc(s.pais || "")}</td><td><small>${esc(s.comentario)} · de ${esc(s.spotter)}</small></td></tr>`).join("")}</tbody></table></div>
    <p class="empty">Fuente: ${esc(r.fuente || "sin conexión")}${r.edad_s != null ? ` · hace ${Math.round(r.edad_s / 60)} min` : ""}. ${esc(r.nota)}</p>` : `<p class="empty">${r.fuente ? "Sin spots con esos filtros." : "La fuente del clúster no responde ahora; se reintenta en un minuto."}</p>`;
}

/** Pinta la vista en el panel de detalle. `here()` = punto de referencia (QTH o lugar elegido). */
export function view(ctx, { onMap, here, head }) {
  stop();
  const bandas = ["", "160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m", "2m"], modos = ["", "FT8", "FT4", "CW", "SSB", "RTTY"];
  ctx.colb.innerHTML = `${head("APRS y clúster DX", "Estaciones APRS en vivo en el mapa y los últimos spots DX")}
    <section class="sec"><div class="label">APRS en vivo <em>última hora · se actualiza cada minuto</em></div><div id="aprs-list"><div class="skeleton" style="height:120px"></div></div></section>
    <section class="sec"><div class="label">Clúster DX <em>spots públicos · cada 2 min</em></div>
      <div class="row" style="gap:8px;margin-bottom:10px"><select id="dx-b" aria-label="Banda" style="width:auto">${bandas.map((b) => `<option value="${b}" ${filtros.banda === b ? "selected" : ""}>${b || "Todas las bandas"}</option>`).join("")}</select>
        <select id="dx-m" aria-label="Modo" style="width:auto">${modos.map((m) => `<option value="${m}" ${filtros.modo === m ? "selected" : ""}>${m || "Todos los modos"}</option>`).join("")}</select></div>
      <div id="dx-list"><div class="skeleton" style="height:160px"></div></div></section>`;
  $$("#dx-b, #dx-m", ctx.colb).forEach((s) => s.addEventListener("change", () => { filtros = { banda: $("#dx-b").value, modo: $("#dx-m").value }; dx(); }));
  const h = here();
  onMap(() => ctx.fly(h.lat, h.lon, 8));
  let n = 0;
  const tick = () => { if (!$("#aprs-list")) return stop(); aprs(ctx, onMap, h); if (n++ % 2 === 0) dx(); };
  setTimeout(tick, 600);                         // tras el vuelo del mapa, para pedir la zona que se ve
  timer = setInterval(tick, 60000);
  ref = { ctx, onMap, h };
  onMap(() => { if (!enlazado && ctx.map) { enlazado = true; ctx.map.on("moveend", () => { if (ref && $("#aprs-list")) aprs(ref.ctx, ref.onMap, ref.h); }); } });
}
