// «Quién acierta más»: informe mensual de verificación de modelos y lugares extra de la persona (hasta 5). Ver backend/meteoguilda/verif.py.
import { $, $$, api, esc, nf1, LS } from "./util.js";
import { getQth } from "./qth.js";

const json = { "content-type": "application/json" };
const mesActual = () => new Date().toISOString().slice(0, 7);
const signo = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + nf1.format(Math.abs(v));

function tabla(l) {
  if (!l.a_24h.length) return `<p class="note">${esc(l.nota || "Sin datos este mes.")}</p>`;
  return `<div class="tablewrap"><table><thead><tr><th>Modelo</th><th>Error a 24 h</th><th>Sesgo</th><th>±2 °C</th><th>Mes anterior</th><th>N</th></tr></thead><tbody>
    ${l.a_24h.slice(0, 6).map((m, i) => `<tr><td>${i === 0 ? "<b>" : ""}${esc(m.etiqueta)}${i === 0 ? "</b>" : ""}</td><td class="num">±${nf1.format(m.mae_c)}°</td><td class="num">${signo(m.sesgo_c)}°</td><td class="num">${m.acierto_2c_pct} %</td><td class="num">${m.mae_mes_anterior_c != null ? `±${nf1.format(m.mae_mes_anterior_c)}°` : "–"}</td><td class="num">${m.n}</td></tr>`).join("")}
    </tbody></table></div>`;
}

async function pintar(box, actual) {
  const mes = box.dataset.mes || mesActual();
  let inf, mios;
  try { [inf, mios] = await Promise.all([api(`/api/verificacion/informe?mes=${mes}`), api("/api/verificacion/mis-lugares")]); } catch (e) { box.innerHTML = `<p class="note bad">${esc(e.message)}</p>`; return; }
  const ya = (la, lo) => mios.lugares.some((x) => Math.abs(x.lat - la) < 0.006 && Math.abs(x.lon - lo) < 0.006);
  const cand = [actual, getQth()?.lat != null ? { lat: getQth().lat, lon: getQth().lon, nombre: getQth().name || "Mi ubicación" } : null, ...LS.get("mg-favs", []).map((f) => ({ lat: f.lat, lon: f.lon, nombre: f.name }))]
    .filter((x) => x && x.lat != null && !ya(x.lat, x.lon)).filter((x, i, a) => a.findIndex((y) => Math.abs(y.lat - x.lat) < 0.006 && Math.abs(y.lon - x.lon) < 0.006) === i).slice(0, 8);
  box.innerHTML = `
    <div class="row" style="gap:8px;align-items:flex-end;flex-wrap:wrap"><div class="field" style="margin:0"><label for="vf-mes">Mes</label><input id="vf-mes" type="month" value="${esc(mes)}" max="${mesActual()}"></div>
      <p class="note" style="flex:1;min-width:200px">${inf.ganador_general ? `Este mes acierta más, en conjunto: <b>${esc(inf.ganador_general)}</b>.` : "Aún no hay datos suficientes este mes."}</p></div>
    ${inf.lugares.map((l) => `<details class="field" ${l.ganador ? "open" : ""}><summary><b>${esc(l.nombre)}</b>${l.principal ? "" : " <small>(tuyo)</small>"} · ${l.ganador ? `gana ${esc(l.ganador)}` : "sin datos suficientes"} <small>· ${l.dias_con_datos} días</small></summary>${tabla(l)}</details>`).join("")}
    <div class="field"><span class="lbl">Mis lugares verificados (${mios.lugares.length}/${mios.maximo})</span>
      ${mios.lugares.map((x) => `<div class="row" style="align-items:center;gap:8px"><span style="flex:1">${esc(x.nombre)} <small class="note">· ${x.comparaciones} comparaciones</small></span><button class="btn ghost sm" type="button" data-vq="${esc(x.clave)}">Quitar</button></div>`).join("") || '<p class="note">Ninguno: solo se verifican los lugares principales.</p>'}
      ${mios.lugares.length < mios.maximo && cand.length ? `<div class="row" style="gap:6px;flex-wrap:wrap">${cand.map((c, i) => `<button class="btn ghost sm" type="button" data-va="${i}">+ ${esc(c.nombre || "Este lugar")}</button>`).join("")}</div>` : ""}
      <p class="note">${esc(mios.nota)}</p></div>
    <details class="field"><summary class="note">Cómo se calcula</summary><p class="note">${esc(inf.metodo)}</p></details>
    <p class="note" id="vf-res" role="status"></p>`;
  const guardar = async (lista) => { try { await api("/api/verificacion/mis-lugares", { method: "PUT", headers: json, body: JSON.stringify({ lugares: lista }) }); pintar(box, actual); } catch (e) { $("#vf-res").textContent = e.message; } };
  $("#vf-mes", box).addEventListener("change", (e) => { if (/^\d{4}-\d{2}$/.test(e.target.value)) { box.dataset.mes = e.target.value; pintar(box, actual); } });
  $$("[data-vq]", box).forEach((b) => b.addEventListener("click", () => guardar(mios.lugares.filter((x) => x.clave !== b.dataset.vq).map(({ lat, lon, nombre }) => ({ lat, lon, nombre })))));
  $$("[data-va]", box).forEach((b) => b.addEventListener("click", () => guardar([...mios.lugares.map(({ lat, lon, nombre }) => ({ lat, lon, nombre })), cand[+b.dataset.va]])));
}

/** Abre el informe en el diálogo común. `actual` = {lat, lon, nombre} del lugar que se está mirando (para ofrecer añadirlo). */
export function open(actual) {
  const dlg = $("#dlg");
  dlg.innerHTML = `<div class="dlg-in acct"><h2>Quién acierta más</h2><p class="who">Informe mensual de los modelos frente a estaciones oficiales</p><div id="vf-body"><div class="skeleton" style="height:160px;border-radius:16px"></div></div>
    <div class="foot"><span></span><button class="btn ghost sm" type="button" id="vf-close">Cerrar</button></div></div>`;
  dlg.showModal?.();
  $("#vf-close", dlg).addEventListener("click", () => dlg.close());
  pintar($("#vf-body", dlg), actual);
}
