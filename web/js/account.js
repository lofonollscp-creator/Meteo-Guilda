// Ajustes de la cuenta: IA interna (activar, proveedor, modelo, clave propia), cuenta y sincronización/API.
import { $, $$, api, esc, LS } from "./util.js";
import { me, load, save, setKey, delKey, aiOn } from "./me.js";
import { getQth, hasQth, label as qthLabel, locate, clearQth } from "./qth.js";
import { infoPantalla } from "./pantalla.js";
import * as push from "./push.js";

const json = { "content-type": "application/json" };
let ctx, dlg, tab = "ia", syncView;

export function init(c, sync) { ctx = c; syncView = sync; dlg = $("#dlg"); }
export function open(which = "ia") {
  tab = which;
  dlg.innerHTML = '<div class="dlg-in acct"><div id="acct-body"></div></div>';
  dlg.showModal?.();
  paint();
  load().then(paint);
}
const TABS = [["ia", "IA"], ["cuenta", "Cuenta"], ["avisos", "Avisos"], ["memoria", "Memoria"], ["sync", "Sincronización y API"]];
function frame(inner) {
  const d = me.data;
  return `<h2>Ajustes</h2><p class="who">${d ? esc(d.email || "") : "Cargando…"}</p>
    <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button type="button" role="tab" data-t="${k}" aria-selected="${tab === k}">${l}</button>`).join("")}</div>
    <div id="tabbody">${inner}</div>
    <div class="foot"><span></span><button class="btn ghost sm" type="button" id="aclose">Cerrar</button></div>`;
}
function paint() {
  const body = $("#acct-body"); if (!body) return;
  body.innerHTML = frame(tab === "ia" ? iaHTML() : tab === "cuenta" ? cuentaHTML() : tab === "avisos" ? '<div id="avbox"><div class="skeleton" style="height:120px;border-radius:16px"></div></div>' : tab === "memoria" ? '<div id="membox"><div class="skeleton" style="height:120px;border-radius:16px"></div></div>' : '<div id="syncbox"></div>');
  $$("[data-t]", body).forEach((b) => b.addEventListener("click", () => { tab = b.dataset.t; paint(); }));
  $("#aclose", body).addEventListener("click", () => dlg.close());
  if (tab === "ia") bindIA(body); else if (tab === "cuenta") bindCuenta(body); else if (tab === "avisos") avisosView(body); else if (tab === "memoria") memoriaView(body); else syncView($("#syncbox", body));
}

/* ---------- IA ---------- */
function iaHTML() {
  const d = me.data; if (!d) return '<p class="note">Cargando tus ajustes…</p>';
  const P = d.providers, p = d.provider, needKey = P[p]?.key, k = d.keys?.[p];
  return `
    <label class="sw"><input type="checkbox" id="ai-on" ${d.ai_enabled ? "checked" : ""}><span><b>Usar la IA interna</b>
      <small>Lectura redactada del pronóstico y asistente de chat. Si la desactivas, ves solo el resumen automático por reglas y el asistente se oculta.</small></span></label>
    <div id="ai-opts" ${d.ai_enabled ? "" : 'style="opacity:.5;pointer-events:none" aria-disabled="true"'}>
      <div class="field"><label for="ai-prov">Proveedor</label>
        <select id="ai-prov">${Object.entries(P).map(([id, v]) => `<option value="${id}" ${id === p ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</select>
        <p class="note">${p === "server" && d.server_mistral ? "Usa Mistral (empresa europea) con la clave del servidor de Guilda. Puedes elegir el modelo; vacío = el del servidor." : p === "server" ? (d.server_ai ? "Usa los modelos gratuitos que el servidor tiene configurados. Los modelos gratuitos pueden registrar los textos; no incluyas datos sensibles." : "El servidor no tiene IA configurada: elige otro proveedor y añade tu clave.") : "Con tu propia clave, las consultas se envían a ese proveedor y se facturan en tu cuenta."}</p></div>
      ${needKey ? `<div class="field"><label for="ai-key">Tu clave de ${esc(P[p].label.split(" (")[0])}</label>
        ${k?.set ? `<div class="row"><span class="ok">Clave guardada ••••${esc(k.last4 || "")}</span><button class="btn ghost sm danger" type="button" id="ai-delkey">Quitar clave</button></div>`
          : d.can_store_keys ? `<div class="row"><input id="ai-key" type="password" autocomplete="off" placeholder="Pega aquí tu clave de API"><button class="btn sm" type="button" id="ai-savekey">Guardar</button></div>`
          : '<p class="note bad">Este servidor no puede guardar claves todavía (falta configurar MG_SECRET_KEY).</p>'}
        <p class="note">Se guarda cifrada en el servidor y solo se usa para tus consultas; nunca se vuelve a mostrar.</p></div>` : ""}
      <div class="field"><label for="ai-model">Modelo ${p === "server" ? "(vacío = automático)" : ""}</label>
        <div class="row"><input id="ai-model" type="text" list="ai-models" autocomplete="off" value="${esc(d.model || "")}" placeholder="${p === "server" ? (d.server_mistral ? `Automático (${esc(d.server_model || "")})` : "Automático") : (P[p]?.suggested?.[0] || "nombre del modelo")}">
          <datalist id="ai-models">${(P[p]?.suggested || []).map((m) => `<option value="${esc(m)}">`).join("")}</datalist>
          <button class="btn ghost sm" type="button" id="ai-list">Ver modelos</button></div></div>
      ${p === "server" && !d.server_mistral ? '<div class="ai-status" id="ai-status" aria-live="polite"><div class="skeleton" style="height:70px;border-radius:16px"></div></div>' : ""}
      <div class="row"><button class="btn sm" type="button" id="ai-test">Probar conexión</button><span id="ai-res" class="note" role="status"></span></div>
    </div>`;
}
function bindIA(body) {
  if ($("#ai-status", body)) api("/api/me/ai-status").then((r) => {
    const box = $("#ai-status", body); if (!box) return;
    const pill = (m) => m.estado === "disponible" ? '<span class="pill lv0"><i></i>disponible</span>' : m.estado === "pausado" ? `<span class="pill lv1"><i></i>${m.motivo === "429" ? "saturado" : "en pausa"} · ${Math.max(1, Math.round(m.segundos / 60))} min</span>` : '<span class="pill"><i></i>sin probar</span>';
    box.innerHTML = r.modelos.length ? `<h3>Estado de los modelos gratuitos</h3>${r.modelos.map((m) => `<div class="mh"><span>${esc(m.modelo.split("/").pop().replace(":free", ""))}</span>${pill(m)}</div>`).join("")}<p class="note">${esc(r.nota)}</p>`
      : `<h3>Estado de los modelos gratuitos</h3><p class="note">Aún no se ha usado ninguno desde el último reinicio del servidor.</p>`;
  }).catch(() => { const box = $("#ai-status", body); if (box) box.remove(); });
  const msg = (t, cls = "") => { const r = $("#ai-res", body); if (r) { r.className = "note " + cls; r.textContent = t; } };
  $("#ai-on", body)?.addEventListener("change", async (e) => { try { await save({ ai_enabled: e.target.checked }); } catch (er) { msg(er.message, "bad"); } paint(); });
  $("#ai-prov", body)?.addEventListener("change", async (e) => { try { await save({ provider: e.target.value, model: "" }); } catch (er) { msg(er.message, "bad"); } paint(); });
  $("#ai-model", body)?.addEventListener("change", async (e) => { try { await save({ model: e.target.value }); msg("Modelo guardado", "ok"); } catch (er) { msg(er.message, "bad"); } });
  $("#ai-savekey", body)?.addEventListener("click", async () => { const v = $("#ai-key", body).value.trim(); if (!v) return; try { await setKey(me.data.provider, v); paint(); } catch (er) { msg(er.message, "bad"); } });
  $("#ai-delkey", body)?.addEventListener("click", async () => { try { await delKey(me.data.provider); paint(); } catch (er) { msg(er.message, "bad"); } });
  $("#ai-list", body)?.addEventListener("click", async () => {
    msg("Consultando modelos…");
    try { const r = await api(`/api/me/models?provider=${me.data.provider}`); $("#ai-models", body).innerHTML = r.models.slice(0, 300).map((m) => `<option value="${esc(m)}">`).join(""); msg(`${r.models.length} modelos: pulsa el campo para elegir.`, "ok"); }
    catch (er) { msg(er.message, "bad"); }
  });
  $("#ai-test", body)?.addEventListener("click", async () => {
    msg("Probando…");
    try { const r = await api("/api/me/test", { method: "POST" }); r.ok ? msg(`Funciona · ${r.model} · ${r.ms} ms`, "ok") : msg(r.error, "bad"); } catch (er) { msg(er.message, "bad"); }
  });
}

/* ---------- Cuenta ---------- */
function cuentaHTML() {
  const rec = LS.get("mg-recent", []);
  const q = getQth();
  return `<p class="note">Has iniciado sesión con tu cuenta de Guilda Work${me.data ? ` como <b>${esc(me.data.email || "")}</b>` : ""}.</p>
    <div class="field"><span class="lbl">Mi ubicación</span>
      <p class="note">${hasQth() ? `${esc(qthLabel(q))} · ${q.lat.toFixed(3)}°, ${q.lon.toFixed(3)}°${q.locator ? ` · ${esc(q.locator)}` : ""}` : "Sin ubicación."} La usan Previsión, Radio, el asistente y el MCP. Se guarda en este dispositivo y en tu cuenta (con precisión de unos 100 m).</p>
      <div class="row"><button class="btn ghost sm" type="button" id="loc-go">Usar la posición de este dispositivo</button><button class="btn ghost sm danger" type="button" id="loc-del" ${hasQth() ? "" : "disabled"}>Quitar mi ubicación</button></div></div>
    <div class="field"><span class="lbl">Búsquedas recientes (este dispositivo)</span>
      <div class="row"><span class="note">${rec.length ? `${rec.length} lugar${rec.length > 1 ? "es" : ""} guardado${rec.length > 1 ? "s" : ""}` : "Ninguna"}</span>
      <button class="btn ghost sm" type="button" id="rec-clear" ${rec.length ? "" : "disabled"}>Borrar historial de búsquedas</button></div></div>
    <div class="field"><span class="lbl">Tus datos en el servidor</span>
      <p class="note">Tus ajustes de IA y tus claves. El cuaderno QSL y los favoritos se gestionan en «Sincronización y API».</p>
      <div class="row"><button class="btn ghost sm danger" type="button" id="me-del">Borrar mis ajustes y claves</button></div></div>
    <div class="row"><a class="btn ghost sm" href="/api/auth/logout">Cerrar sesión de Guilda</a></div>
    <p class="note" id="acc-res" role="status"></p>
    <details class="field"><summary class="note">Diagnóstico de pantalla</summary><p class="note" id="diag">${esc(infoPantalla())} · versión ${esc(window.__mgv || "?")}</p></details>`;
}
function bindCuenta(body) {
  $("#rec-clear", body)?.addEventListener("click", () => { LS.set("mg-recent", []); document.dispatchEvent(new CustomEvent("mg:recent")); paint(); $("#acc-res").textContent = "Historial borrado."; });
  $("#loc-go", body)?.addEventListener("click", async () => { $("#acc-res").textContent = "Buscando tu posición…"; try { await locate(); paint(); $("#acc-res").textContent = "Ubicación actualizada."; } catch (e) { $("#acc-res").textContent = e.message; } });
  $("#loc-del", body)?.addEventListener("click", () => { clearQth(); paint(); $("#acc-res").textContent = "Ubicación quitada."; });
  $("#me-del", body)?.addEventListener("click", async () => {
    if (!confirm("¿Borrar tus ajustes de IA y tus claves del servidor? No se puede deshacer.")) return;
    try { await api("/api/me", { method: "DELETE" }); await load(); $("#acc-res").textContent = "Borrado."; } catch (e) { $("#acc-res").textContent = e.message; }
  });
}
/* ---------- Avisos (notificaciones push) ---------- */
async function avisosView(body) {
  const box = $("#avbox", body); if (!box) return;
  const [cfg, st] = await Promise.all([push.config(), push.estado()]);
  if (!$("#avbox")) return;
  const q = getQth(), msg = (t, cls = "") => { const r = $("#av-res"); if (r) { r.className = "note " + cls; r.textContent = t; } };
  const prefs = () => ({ lat: q?.lat, lon: q?.lon, nombre: q?.name || q?.locator || null, avisos: $("#av-avisos")?.checked ?? true, nivel_min: +($("#av-nivel")?.value || 2), pasos: $("#av-pasos")?.checked ?? true });
  const pintar = (est) => {
    box.innerHTML = `
      <p class="note">Te avisamos en este dispositivo, <b>aunque tengas la web cerrada</b>, de los avisos oficiales de tu zona y de los pasos de satélite que programes. Solo si lo activas aquí.</p>
      ${!push.soportado() ? '<p class="note bad">Este navegador no admite notificaciones. En iPhone/iPad, añade primero la web a la pantalla de inicio (Compartir → Añadir a pantalla de inicio).</p>'
        : !cfg.disponible ? '<p class="note bad">Este servidor todavía no tiene las notificaciones configuradas.</p>'
        : est.permiso === "denied" ? '<p class="note bad">Has bloqueado las notificaciones de este sitio en el navegador. Permítelas en los ajustes del sitio y vuelve aquí.</p>'
        : `<label class="sw"><input type="checkbox" id="av-on" ${est.suscrito ? "checked" : ""}><span><b>Recibir notificaciones en este dispositivo</b><small>Puedes desactivarlas cuando quieras: se borra este dispositivo del servidor.</small></span></label>
        <div id="av-opts" ${est.suscrito ? "" : 'style="opacity:.5;pointer-events:none" aria-disabled="true"'}>
          <label class="sw"><input type="checkbox" id="av-avisos" ${est.avisos !== false ? "checked" : ""}><span><b>Avisos oficiales de mi zona</b><small>${q?.lat != null ? `Para «${esc(qthLabel(q))}» (Mi ubicación, redondeada a ~1 km). Si la cambias, vuelve a guardar aquí.` : "Necesitas fijar «Mi ubicación» en la pestaña Cuenta."} Fuente: AEMET.</small></span></label>
          <div class="field"><label for="av-nivel">Avisar a partir de</label><select id="av-nivel"><option value="2" ${(est.nivel_min || 2) === 2 ? "selected" : ""}>Naranja (peligro importante)</option><option value="1" ${est.nivel_min === 1 ? "selected" : ""}>Amarillo (algo de riesgo)</option></select></div>
          <label class="sw"><input type="checkbox" id="av-pasos" ${est.pasos !== false ? "checked" : ""}><span><b>Recordatorios de pasos de satélite</b><small>5 minutos antes, cuando se lo pidas al asistente.</small></span></label>
          <div class="row"><button class="btn sm" type="button" id="av-save">Guardar</button><button class="btn ghost sm" type="button" id="av-test">Enviar una de prueba</button></div>
        </div>`}
      <p class="note" id="av-res" role="status"></p>
      <details class="field"><summary class="note">Qué guardamos</summary><p class="note">El identificador de este navegador que le da su servicio de notificaciones (Google, Mozilla o Apple), sus claves de cifrado, tu ubicación aproximada (1 km) y lo que has elegido recibir. El contenido de cada notificación viaja cifrado hasta tu navegador. Se borra al desactivarlas, al borrar tu cuenta o tras 90 días sin poder entregarte nada. No se usa para publicidad ni se cede a terceros.</p></details>`;
    $("#av-on")?.addEventListener("change", async (e) => {
      try { if (e.target.checked) { await push.activar(prefs()); msg("Notificaciones activadas en este dispositivo.", "ok"); } else { await push.desactivar(); msg("Notificaciones desactivadas y dispositivo borrado del servidor.", "ok"); } } catch (er) { msg(er.message, "bad"); }
      pintar(await push.estado());
    });
    $("#av-save")?.addEventListener("click", async () => { try { await push.guardar(prefs()); msg("Guardado.", "ok"); } catch (er) { msg(er.message, "bad"); } });
    $("#av-test")?.addEventListener("click", async () => { msg("Enviando…"); try { (await push.probar()).ok ? msg("Enviada: debería llegarte en unos segundos.", "ok") : msg("El servicio de notificaciones no la ha aceptado.", "bad"); } catch (er) { msg(er.message, "bad"); } });
  };
  pintar(st);
}

/* ---------- Memoria del asistente y privacidad (opt-in, RGPD) ---------- */
async function memoriaView(body) {
  const box = $("#membox", body); if (!box) return;
  let m; try { m = await api("/api/me/memoria"); } catch (e) { box.innerHTML = `<p class="note bad">${esc(e.message)}</p>`; return; }
  if (!$("#membox")) return;
  const res = (t, cls = "") => { const r = $("#mem-res"); if (r) { r.className = "note " + cls; r.textContent = t; } };
  const por = Object.entries(m.categorias).map(([k, l]) => [l, m.items.filter((i) => i.cat === k)]).filter(([, xs]) => xs.length);
  box.innerHTML = `
    <p class="note">El asistente puede recordar <b>tus preferencias</b> (por ejemplo «prefiero senderismo suave» o «uso 2 m en FM») para adaptar sus respuestas. Está <b>desactivado</b> hasta que lo actives.</p>
    <label class="sw"><input type="checkbox" id="mem-on" ${m.activa ? "checked" : ""}><span><b>Dejar que el asistente recuerde mis preferencias</b>
      <small>Solo guarda lo que tú aceptes con el botón «Recordar» que aparece en el chat o lo que escribas aquí. Desactivarlo borra todo lo recordado.</small></span></label>
    ${m.activa ? `<div class="field"><span class="lbl">Lo que recuerda (${m.items.length}/${m.max_items})</span>
      ${por.length ? por.map(([l, xs]) => `<p class="note"><b>${esc(l)}</b></p><ul class="memlist">${xs.map((i) => `<li><span>${esc(i.texto)}</span><button type="button" class="btn ghost sm" data-del="${esc(i.id)}" aria-label="Borrar: ${esc(i.texto)}">Borrar</button></li>`).join("")}</ul>`).join("") : '<p class="note">Todavía no recuerda nada.</p>'}
      <div class="row"><input id="mem-txt" type="text" maxlength="${m.max_len}" placeholder="Añade una preferencia…" aria-label="Nueva preferencia"><select id="mem-cat" aria-label="Categoría">${Object.entries(m.categorias).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select><button class="btn sm" type="button" id="mem-add">Añadir</button></div>
      <div class="row"><button class="btn ghost sm danger" type="button" id="mem-clear" ${m.items.length ? "" : "disabled"}>Borrar todo lo recordado</button></div>
      <p class="note">Consentimiento dado el ${esc(new Date(m.consentimiento).toLocaleDateString("es"))}. Las notas caducan a los ${m.retencion_dias / 365 === 1 ? "12 meses" : m.retencion_dias + " días"}.</p></div>` : ""}
    <p class="note" id="mem-res" role="status"></p>
    <div class="field"><span class="lbl">Tus datos</span><div class="row"><a class="btn ghost sm" href="/api/me/datos" download="mis-datos-guilda.json">Descargar todos mis datos (JSON)</a></div>
      <p class="note">Incluye ajustes, memoria y dispositivos de notificaciones; no incluye tus claves de API. Para borrarlo todo, usa «Borrar mis ajustes y claves» en la pestaña Cuenta.</p></div>
    <details class="field"><summary class="note">Cómo tratamos tus datos</summary>
      <p class="note"><b>Para qué:</b> mejorar las respuestas del asistente y avisarte de lo que pidas. <b>Base legal:</b> tu consentimiento, que puedes retirar cuando quieras. <b>Qué nunca guardamos:</b> salud, creencias, ideología, origen, orientación sexual, correos, teléfonos, documentos ni claves (el servidor lo rechaza).
      <b>Con quién se comparte:</b> las notas activas se envían, junto con tu pregunta, al proveedor de IA que tengas elegido en la pestaña IA (por defecto Mistral, empresa europea; o el tuyo). No se usan para publicidad ni se venden.
      <b>Tus derechos:</b> acceso y portabilidad (descarga), rectificación y supresión (borrar notas o todo) y retirar el consentimiento (desactivar). Responsable: Guilda Work.</p></details>`;
  $("#mem-on")?.addEventListener("change", async (e) => {
    if (!e.target.checked && m.items.length && !confirm("Se borrará todo lo que el asistente recuerda de ti. ¿Continuar?")) { e.target.checked = true; return; }
    try { await api("/api/me/memoria", { method: "PUT", headers: json, body: JSON.stringify({ activa: e.target.checked }) }); } catch (er) { res(er.message, "bad"); }
    memoriaView(body);
  });
  $$("[data-del]", box).forEach((b) => b.addEventListener("click", async () => { try { await api(`/api/me/memoria/notas/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); } catch (er) { res(er.message, "bad"); } memoriaView(body); }));
  $("#mem-add", box)?.addEventListener("click", async () => {
    const t = $("#mem-txt").value.trim(); if (!t) return;
    try { await api("/api/me/memoria/notas", { method: "POST", headers: json, body: JSON.stringify({ texto: t, cat: $("#mem-cat").value }) }); memoriaView(body); } catch (er) { res(er.message, "bad"); }
  });
  $("#mem-txt", box)?.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#mem-add").click(); } });
  $("#mem-clear", box)?.addEventListener("click", async () => { if (!confirm("¿Borrar todo lo que el asistente recuerda de ti?")) return; try { await api("/api/me/memoria/notas", { method: "DELETE" }); } catch (er) { res(er.message, "bad"); } memoriaView(body); });
}
export { aiOn };
