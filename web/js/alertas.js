// Alertas de radio y salidas (Ajustes → Avisos): tropo VHF/UHF, HF, pasos de satélite y tiempo en el QTH o en una ruta guardada.
// Son de la persona (llegan a todos sus dispositivos con notificaciones) y tienen tope diario y horario de silencio. Ver backend/meteoguilda/alertas.py.
import { $, $$, api, esc } from "./util.js";
import { getQth, label as qthLabel } from "./qth.js";

const json = { "content-type": "application/json" };
const H = Array.from({ length: 24 }, (_, h) => h);
let sats = null;

function describe(a, rutas) {
  const p = a.params, lugar = p.nombre ? ` · ${p.nombre}` : "";
  if (a.tipo === "tropo") return `Tropo VHF/UHF: ${p.nivel}${lugar}`;
  if (a.tipo === "hf") return p.modo === "tormenta" ? `HF: tormenta con Kp ≥ ${p.kp_min}` : `HF: flujo solar ≥ ${p.sfi_min} y Kp ≤ ${p.kp_max}`;
  if (a.tipo === "satelite") return `Satélites ${p.norad.map((n) => sats?.find((s) => s.norad === n)?.nombre || n).join(", ")} · ≥ ${p.min_el}° · ${p.antelacion_min} min antes${lugar}`;
  const v = { viento: "Viento", racha: "Rachas", lluvia: "Lluvia 24 h", oleaje: "Oleaje", aviso: "Aviso oficial" }[p.variable] || p.variable;
  const u = p.variable === "aviso" ? (p.umbral >= 3 ? "rojo" : p.umbral >= 2 ? "naranja o más" : "amarillo o más") : `≥ ${p.umbral} ${{ viento: "km/h", racha: "km/h", lluvia: "mm", oleaje: "m" }[p.variable]}`;
  if (a.tipo === "ruta") return `Ruta «${rutas.find((r) => r.id === p.ruta_id)?.nombre || p.ruta_id}»: ${v} ${u}`;
  return `${v} ${u}${lugar}`;
}

function campos(tipo, rutas, q) {
  const sinQth = '<p class="note">Fija «Mi ubicación» en la pestaña Cuenta: estas alertas usan tu QTH.</p>';
  const variable = `<div class="field" style="margin:0"><label for="al-var">Qué</label><select id="al-var"><option value="racha">Rachas (km/h)</option><option value="viento">Viento (km/h)</option><option value="lluvia">Lluvia en 24 h (mm)</option><option value="oleaje">Oleaje (m)</option><option value="aviso">Aviso oficial de AEMET</option></select></div>
    <div class="field" style="margin:0;width:110px"><label for="al-umb">Umbral</label><input id="al-umb" type="number" inputmode="decimal" step="any" value="60"></div>`;
  if (tipo === "tropo") return q ? `<div class="field" style="margin:0"><label for="al-niv">Avisar con</label><select id="al-niv"><option value="super-refracción">Super-refracción o más</option><option value="conducto">Solo conducto</option></select></div>` : sinQth;
  if (tipo === "hf") return `<div class="field" style="margin:0"><label for="al-modo">Cuándo</label><select id="al-modo"><option value="mejora">Mejora de HF</option><option value="tormenta">Tormenta geomagnética</option></select></div>
    <div class="field" style="margin:0;width:110px"><label for="al-sfi">Flujo solar ≥</label><input id="al-sfi" type="number" min="60" max="300" value="120"></div>
    <div class="field" style="margin:0;width:90px"><label for="al-kp">Kp ≤ (o ≥ en tormenta)</label><input id="al-kp" type="number" min="0" max="9" step="1" value="3"></div>`;
  if (tipo === "satelite") return q ? `<div class="field" style="margin:0;min-width:200px"><label for="al-sat">Satélites (hasta 5)</label><select id="al-sat" multiple size="5">${(sats || []).map((s) => `<option value="${s.norad}">${esc(s.nombre)}</option>`).join("")}</select></div>
    <div class="field" style="margin:0;width:100px"><label for="al-el">Elevación ≥</label><input id="al-el" type="number" min="0" max="90" value="20"></div>
    <div class="field" style="margin:0;width:100px"><label for="al-ant">Minutos antes</label><input id="al-ant" type="number" min="1" max="60" value="10"></div>` : sinQth;
  if (tipo === "meteo") return q ? variable : sinQth;
  return rutas.length ? `<div class="field" style="margin:0"><label for="al-ruta">Ruta guardada</label><select id="al-ruta">${rutas.map((r) => `<option value="${r.id}">${esc(r.nombre)}</option>`).join("")}</select></div>${variable}`
    : '<p class="note">Aún no tienes rutas guardadas: calcula una en Rutas y pulsa «Guardar ruta».</p>';
}

function params(tipo, q) {
  const n = (id) => parseFloat($(id)?.value);
  const lugar = q ? { lat: q.lat, lon: q.lon, nombre: q.name || q.locator || null } : {};
  if (tipo === "tropo") return { ...lugar, nivel: $("#al-niv").value };
  if (tipo === "hf") return $("#al-modo").value === "tormenta" ? { modo: "tormenta", kp_min: n("#al-kp") } : { modo: "mejora", sfi_min: n("#al-sfi"), kp_max: n("#al-kp") };
  if (tipo === "satelite") return { ...lugar, norad: $$("#al-sat option").filter((o) => o.selected).map((o) => +o.value).slice(0, 5), min_el: n("#al-el"), antelacion_min: n("#al-ant") };
  const v = { variable: $("#al-var").value, umbral: n("#al-umb") };
  return tipo === "ruta" ? { ruta_id: +$("#al-ruta").value, ...v } : { ...lugar, ...v };
}

/** Pinta la sección dentro de `box` (en la pestaña Avisos de Ajustes). */
export async function view(box) {
  if (!box) return;
  let d, rutas;
  try { [d, { rutas }] = await Promise.all([api("/api/alertas"), api("/api/rutas")]); } catch (e) { box.innerHTML = `<p class="note bad">${esc(e.message)}</p>`; return; }
  if (!sats) sats = await api("/api/satellites").catch(() => []);
  const q = getQth()?.lat != null ? getQth() : null, P = d.prefs;
  let tipo = box.dataset.tipo || "tropo";
  const msg = (t, cls = "") => { const r = $("#al-res"); if (r) { r.className = "note " + cls; r.textContent = t; } };
  const hsel = (id, v, lbl) => `<div class="field" style="margin:0;width:110px"><label for="${id}">${lbl}</label><select id="${id}"><option value="">—</option>${H.map((h) => `<option value="${h}" ${v === h ? "selected" : ""}>${String(h).padStart(2, "0")}:00</option>`).join("")}</select></div>`;
  box.innerHTML = `
    <p class="note">Te llegan a <b>todos tus dispositivos</b> con las notificaciones activadas (${d.dispositivos} ahora)${q ? `, para «${esc(qthLabel(q))}» si son de tu QTH` : ""}. Cada alerta avisa como mucho una vez al día; los satélites, una vez por paso.</p>
    ${d.alertas.length ? d.alertas.map((a) => `<div class="al-row"><span class="al-dot${a.activa ? " on" : ""}" aria-hidden="true"></span><span class="al-n" style="flex:1;min-width:160px${a.activa ? "" : ";opacity:.55"}">${esc(a.nombre || describe(a, rutas))}${a.activa ? "" : " (en pausa)"}</span>
      <button class="btn ghost sm" type="button" data-chk="${a.id}">Comprobar</button><button class="btn ghost sm" type="button" data-tog="${a.id}" data-on="${a.activa}">${a.activa ? "Pausar" : "Reanudar"}</button><button class="btn ghost sm" type="button" data-del="${a.id}" aria-label="Borrar esta alerta">Borrar</button></div>`).join("") : '<p class="note">Aún no tienes ninguna.</p>'}
    ${d.alertas.length < d.maximo ? `<div class="field"><label for="al-tipo">Nueva alerta</label><select id="al-tipo">${Object.entries(d.tipos).map(([k, l]) => `<option value="${k}" ${k === tipo ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>
      <div class="row" style="gap:8px;flex-wrap:wrap;align-items:flex-end" id="al-campos">${campos(tipo, rutas, q)}</div>
      <div class="row"><button class="btn sm" type="button" id="al-add">Añadir alerta</button></div>` : `<p class="note">Has llegado al máximo de ${d.maximo} alertas.</p>`}
    <div class="field"><span class="lbl">Límites</span>
      <div class="row" style="gap:8px;flex-wrap:wrap;align-items:flex-end"><div class="field" style="margin:0;width:110px"><label for="al-max">Máximo al día</label><input id="al-max" type="number" min="1" max="30" value="${P.max_dia}"></div>
        ${hsel("al-sd", P.silencio_desde, "Silencio desde")}${hsel("al-sh", P.silencio_hasta, "hasta")}<button class="btn ghost sm" type="button" id="al-prefs">Guardar límites</button></div>
      <p class="note">Hoy llevas ${d.enviadas_hoy} de ${P.max_dia}. Durante el silencio no se manda nada (hora de ${esc(P.zona)}); si sigue vigente, llega al terminar.</p></div>
    ${d.ultimos_envios.length ? `<details class="field"><summary class="note">Últimas enviadas</summary><ul class="note">${d.ultimos_envios.slice(0, 8).map((e) => `<li>${esc(new Date(e.cuando).toLocaleString("es", { weekday: "short", hour: "2-digit", minute: "2-digit" }))} · ${esc(e.titulo)}</li>`).join("")}</ul></details>` : ""}
    <p class="note" id="al-res" role="status"></p>`;
  const recarga = () => view(box);
  $("#al-tipo", box)?.addEventListener("change", (e) => { box.dataset.tipo = e.target.value; tipo = e.target.value; $("#al-campos", box).innerHTML = campos(tipo, rutas, q); });
  $("#al-add", box)?.addEventListener("click", async () => {
    try { await api("/api/alertas", { method: "POST", headers: json, body: JSON.stringify({ tipo, params: params(tipo, q) }) }); await recarga(); msg("Alerta guardada.", "ok"); } catch (e) { msg(e.message, "bad"); }
  });
  $$("[data-del]", box).forEach((b) => b.addEventListener("click", async () => { try { await api(`/api/alertas/${b.dataset.del}`, { method: "DELETE" }); await recarga(); msg("Alerta borrada.", "ok"); } catch (e) { msg(e.message, "bad"); } }));
  $$("[data-tog]", box).forEach((b) => b.addEventListener("click", async () => { try { await api(`/api/alertas/${b.dataset.tog}`, { method: "PATCH", headers: json, body: JSON.stringify({ activa: b.dataset.on !== "true" }) }); await recarga(); } catch (e) { msg(e.message, "bad"); } }));
  $$("[data-chk]", box).forEach((b) => b.addEventListener("click", async () => {
    msg("Comprobando…");
    try { const r = await api(`/api/alertas/${b.dataset.chk}/comprobar`); msg(r.error ? r.error : r.saltaria ? `Saltaría: ${r.avisos[0].titulo}${r.avisos[0].en_silencio ? " (pero ahora es tu silencio)" : ""}${r.tope_alcanzado ? " (pero ya has llegado al tope de hoy)" : ""}` : "Ahora no saltaría.", r.saltaria ? "ok" : ""); }
    catch (e) { msg(e.message, "bad"); }
  }));
  $("#al-prefs", box)?.addEventListener("click", async () => {
    const sd = $("#al-sd").value, sh = $("#al-sh").value;
    const body = { max_dia: +$("#al-max").value, ...(sd === "" || sh === "" ? { sin_silencio: true } : { silencio_desde: +sd, silencio_hasta: +sh }) };
    try { await api("/api/alertas/prefs", { method: "PUT", headers: json, body: JSON.stringify(body) }); await recarga(); msg("Límites guardados.", "ok"); } catch (e) { msg(e.message, "bad"); }
  });
}
