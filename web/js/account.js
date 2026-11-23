// Ajustes de la cuenta: IA interna (activar, proveedor, modelo, clave propia), cuenta y sincronización/API.
import { $, $$, api, esc, LS } from "./util.js";
import { me, load, save, setKey, delKey, aiOn } from "./me.js";

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
const TABS = [["ia", "IA"], ["cuenta", "Cuenta"], ["sync", "Sincronización y API"]];
function frame(inner) {
  const d = me.data;
  return `<h2>Ajustes</h2><p class="who">${d ? esc(d.email || "") : "Cargando…"}</p>
    <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button type="button" role="tab" data-t="${k}" aria-selected="${tab === k}">${l}</button>`).join("")}</div>
    <div id="tabbody">${inner}</div>
    <div class="foot"><span></span><button class="btn ghost sm" type="button" id="aclose">Cerrar</button></div>`;
}
function paint() {
  const body = $("#acct-body"); if (!body) return;
  body.innerHTML = frame(tab === "ia" ? iaHTML() : tab === "cuenta" ? cuentaHTML() : '<div id="syncbox"></div>');
  $$("[data-t]", body).forEach((b) => b.addEventListener("click", () => { tab = b.dataset.t; paint(); }));
  $("#aclose", body).addEventListener("click", () => dlg.close());
  if (tab === "ia") bindIA(body); else if (tab === "cuenta") bindCuenta(body); else syncView($("#syncbox", body));
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
        <p class="note">${p === "server" ? (d.server_ai ? "Usa los modelos gratuitos que el servidor tiene configurados. Los modelos gratuitos pueden registrar los textos; no incluyas datos sensibles." : "El servidor no tiene IA configurada: elige otro proveedor y añade tu clave.") : "Con tu propia clave, las consultas se envían a ese proveedor y se facturan en tu cuenta."}</p></div>
      ${needKey ? `<div class="field"><label for="ai-key">Tu clave de ${esc(P[p].label.split(" (")[0])}</label>
        ${k?.set ? `<div class="row"><span class="ok">Clave guardada ••••${esc(k.last4 || "")}</span><button class="btn ghost sm danger" type="button" id="ai-delkey">Quitar clave</button></div>`
          : d.can_store_keys ? `<div class="row"><input id="ai-key" type="password" autocomplete="off" placeholder="Pega aquí tu clave de API"><button class="btn sm" type="button" id="ai-savekey">Guardar</button></div>`
          : '<p class="note bad">Este servidor no puede guardar claves todavía (falta configurar MG_SECRET_KEY).</p>'}
        <p class="note">Se guarda cifrada en el servidor y solo se usa para tus consultas; nunca se vuelve a mostrar.</p></div>` : ""}
      <div class="field"><label for="ai-model">Modelo ${p === "server" ? "(vacío = automático)" : ""}</label>
        <div class="row"><input id="ai-model" type="text" list="ai-models" autocomplete="off" value="${esc(d.model || "")}" placeholder="${p === "server" ? "Automático" : (P[p]?.suggested?.[0] || "nombre del modelo")}">
          <datalist id="ai-models">${(P[p]?.suggested || []).map((m) => `<option value="${esc(m)}">`).join("")}</datalist>
          <button class="btn ghost sm" type="button" id="ai-list">Ver modelos</button></div></div>
      <div class="row"><button class="btn sm" type="button" id="ai-test">Probar conexión</button><span id="ai-res" class="note" role="status"></span></div>
    </div>`;
}
function bindIA(body) {
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
  return `<p class="note">Has iniciado sesión con tu cuenta de Guilda Work${me.data ? ` como <b>${esc(me.data.email || "")}</b>` : ""}.</p>
    <div class="field"><span class="lbl">Búsquedas recientes (este dispositivo)</span>
      <div class="row"><span class="note">${rec.length ? `${rec.length} lugar${rec.length > 1 ? "es" : ""} guardado${rec.length > 1 ? "s" : ""}` : "Ninguna"}</span>
      <button class="btn ghost sm" type="button" id="rec-clear" ${rec.length ? "" : "disabled"}>Borrar historial de búsquedas</button></div></div>
    <div class="field"><span class="lbl">Tus datos en el servidor</span>
      <p class="note">Tus ajustes de IA y tus claves. El cuaderno QSL y los favoritos se gestionan en «Sincronización y API».</p>
      <div class="row"><button class="btn ghost sm danger" type="button" id="me-del">Borrar mis ajustes y claves</button></div></div>
    <div class="row"><a class="btn ghost sm" href="/api/auth/logout">Cerrar sesión de Guilda</a></div>
    <p class="note" id="acc-res" role="status"></p>`;
}
function bindCuenta(body) {
  $("#rec-clear", body)?.addEventListener("click", () => { LS.set("mg-recent", []); document.dispatchEvent(new CustomEvent("mg:recent")); paint(); $("#acc-res").textContent = "Historial borrado."; });
  $("#me-del", body)?.addEventListener("click", async () => {
    if (!confirm("¿Borrar tus ajustes de IA y tus claves del servidor? No se puede deshacer.")) return;
    try { await api("/api/me", { method: "DELETE" }); await load(); $("#acc-res").textContent = "Borrado."; } catch (e) { $("#acc-res").textContent = e.message; }
  });
}
export { aiOn };
