// Cuaderno conectado con LoTW, eQSL y QRZ (Radio → Cuaderno → Servicios). Las credenciales se escriben SOLO aquí: viajan una vez al servidor,
// que las guarda cifradas y nunca las devuelve. Subir y bajar confirmaciones trabajan sobre el cuaderno sincronizado (clave). Ver backend/meteoguilda/qsl_servicios.py.
import { $, $$, api, esc } from "./util.js";
import { syncing, call } from "./sync.js";

const json = { "content-type": "application/json" };
const fecha = (iso) => (iso ? new Date(iso).toLocaleString("es", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "nunca");

function form(id, s) {
  if (id === "qrz") return `<div class="field" style="margin:0"><label for="qs-qrz-key">Clave del logbook de QRZ</label><input id="qs-qrz-key" type="password" autocomplete="off" placeholder="XXXX-XXXX-XXXX-XXXX"></div>`;
  const base = `<div class="field" style="margin:0"><label for="qs-${id}-u">Usuario${id === "lotw" ? " de la web de LoTW" : ""}</label><input id="qs-${id}-u" autocomplete="off" value="${esc(s.usuario || "")}"></div>
    <div class="field" style="margin:0"><label for="qs-${id}-p">Contraseña</label><input id="qs-${id}-p" type="password" autocomplete="off"></div>`;
  if (id === "eqsl") return base + `<div class="field" style="margin:0"><label for="qs-eqsl-n">QTH Nickname (opcional)</label><input id="qs-eqsl-n" value="${esc(s.qth_nick || "")}"></div>`;
  return base + `<details style="grid-column:1/-1"><summary class="note">Para subir: certificado de la ARRL (.p12) y estación, como en TQSL</summary>
    <div class="row" style="gap:8px;flex-wrap:wrap;align-items:flex-end">
      <div class="field" style="margin:0"><label for="qs-lotw-f">Certificado .p12</label><input id="qs-lotw-f" type="file" accept=".p12,.pfx,application/x-pkcs12"></div>
      <div class="field" style="margin:0"><label for="qs-lotw-fp">Contraseña del certificado</label><input id="qs-lotw-fp" type="password" autocomplete="off"></div>
      <div class="field" style="margin:0;width:110px"><label for="qs-lotw-c">Indicativo</label><input id="qs-lotw-c" style="text-transform:uppercase" value="${esc(s.indicativo || "")}"></div>
      <div class="field" style="margin:0;width:90px"><label for="qs-lotw-d">DXCC</label><input id="qs-lotw-d" type="number" value="281"></div>
      <div class="field" style="margin:0;width:90px"><label for="qs-lotw-g">Locator</label><input id="qs-lotw-g" maxlength="6" value="${esc(s.grid || "")}"></div>
      <div class="field" style="margin:0;width:80px"><label for="qs-lotw-cq">Zona CQ</label><input id="qs-lotw-cq" type="number" value="14"></div>
      <div class="field" style="margin:0;width:80px"><label for="qs-lotw-it">Zona ITU</label><input id="qs-lotw-it" type="number" value="37"></div></div>
    <p class="note">España peninsular y Baleares: DXCC 281, CQ 14, ITU 37. La firma de LoTW es experimental: si la rechaza, exporta el ADIF y fírmalo con TQSL.</p></details>`;
}

async function cuerpo(id) {
  if (id === "qrz") return { api_key: $("#qs-qrz-key").value.trim() };
  const b = { usuario: $(`#qs-${id}-u`).value.trim(), password: $(`#qs-${id}-p`).value };
  if (id === "eqsl") return { ...b, qth_nick: $("#qs-eqsl-n").value.trim() || null };
  const f = $("#qs-lotw-f").files[0];
  if (!f) return b;
  const bytes = new Uint8Array(await f.arrayBuffer());
  let bin = ""; bytes.forEach((x) => (bin += String.fromCharCode(x)));
  return { ...b, p12_b64: btoa(bin), p12_password: $("#qs-lotw-fp").value, indicativo: $("#qs-lotw-c").value.trim().toUpperCase(), dxcc: +$("#qs-lotw-d").value, grid: $("#qs-lotw-g").value.trim(), cqz: +$("#qs-lotw-cq").value, ituz: +$("#qs-lotw-it").value };
}

/** Pinta el panel en `box`; `recargar()` vuelve a pintar el cuaderno tras importar confirmaciones. */
export async function view(box, recargar) {
  if (!box) return;
  let d; try { d = await api("/api/qsl/servicios"); } catch (e) { box.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  const abierto = box.dataset.abierto || "";
  box.innerHTML = `<p class="empty">Sube tus contactos y baja las confirmaciones. Tus credenciales se guardan <b>cifradas</b> en el servidor, solo se escriben aquí (nunca por el asistente ni el MCP) y no se vuelven a mostrar.${syncing() ? "" : " Para subir y confirmar hace falta el cuaderno sincronizado (icono de llave)."}</p>
    ${Object.entries(d.servicios).map(([id, s]) => `<div class="field" style="border-top:1px solid var(--line);padding-top:10px">
      <div class="row" style="align-items:center;gap:8px;flex-wrap:wrap"><b style="flex:1">${esc(s.nombre)}</b>
        ${s.conectado ? `<span class="tag ok">Conectado${s.usuario ? ` · ${esc(s.usuario)}` : s.clave ? ` · ${esc(s.clave)}` : ""}</span>
          ${syncing() ? `<button class="btn ghost sm" type="button" data-up="${id}" ${id === "lotw" && !s.puede_subir ? "disabled title=\"Falta el certificado .p12\"" : ""}>Subir pendientes</button><button class="btn ghost sm" type="button" data-down="${id}">Bajar confirmaciones</button>` : ""}
          <button class="btn ghost sm danger" type="button" data-x="${id}">Quitar</button>` : `<button class="btn ghost sm" type="button" data-open="${id}">Conectar</button>`}</div>
      ${s.conectado ? `<p class="note">Última subida: ${fecha(s.ultima_subida)} · última descarga: ${fecha(s.ultima_descarga)}${s.ultimo_error ? ` · <span class="bad">${esc(s.ultimo_error)}</span>` : ""}</p>` : ""}
      ${abierto === id ? `<div class="row" style="gap:8px;flex-wrap:wrap;align-items:flex-end">${form(id, s)}</div><div class="row"><button class="btn sm" type="button" data-save="${id}">Guardar cifrado</button></div>` : ""}
    </div>`).join("")}
    ${d.puede_guardar ? "" : '<p class="note bad">Este servidor no puede guardar credenciales todavía (falta MG_SECRET_KEY).</p>'}
    <p class="empty" id="qs-res" role="status"></p>`;
  const msg = (t) => { const r = $("#qs-res"); if (r) r.textContent = t; };
  const again = () => view(box, recargar);
  $$("[data-open]", box).forEach((b) => b.addEventListener("click", () => { box.dataset.abierto = b.dataset.open; again(); }));
  $$("[data-save]", box).forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/qsl/servicios/${b.dataset.save}`, { method: "PUT", headers: json, body: JSON.stringify(await cuerpo(b.dataset.save)) }); box.dataset.abierto = ""; await again(); msg("Guardado cifrado."); } catch (e) { msg(e.message); }
  }));
  $$("[data-x]", box).forEach((b) => b.addEventListener("click", async () => { if (!confirm("¿Quitar las credenciales de este servicio? Lo ya subido y confirmado se queda en tu cuaderno.")) return; try { await api(`/api/qsl/servicios/${b.dataset.x}`, { method: "DELETE" }); again(); } catch (e) { msg(e.message); } }));
  $$("[data-up]", box).forEach((b) => b.addEventListener("click", async () => {
    msg("Subiendo…");
    try { const r = await call(`/api/qsl/${b.dataset.up}/subir`, { method: "POST" }); msg(r.mensaje || `${r.enviados} subidos${r.con_error ? `, ${r.con_error} con error (${r.errores[0] || ""})` : ""}; quedan ${r.pendientes}.`); again(); } catch (e) { msg(e.message); }
  }));
  $$("[data-down]", box).forEach((b) => b.addEventListener("click", async () => {
    msg("Bajando confirmaciones…");
    try { const r = await call(`/api/qsl/${b.dataset.down}/confirmaciones`, { method: "POST" }); msg(`${r.descargadas} descargadas, ${r.confirmados_nuevos} confirmaciones nuevas${r.sin_pareja ? ` (${r.sin_pareja} sin contacto en tu cuaderno)` : ""}.`); recargar?.(); again(); } catch (e) { msg(e.message); }
  }));
}
