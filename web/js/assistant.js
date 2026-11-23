// Asistente de IA: chat lateral (pantalla completa en móvil) con datos reales del lugar que estás mirando.
import { $, esc, LS } from "./util.js";
import { icon } from "./icons.js";
import { aiOn, me, onChange } from "./me.js";

const json = { "content-type": "application/json" };
let ctx, el, msgs = LS.get("mg-chat", []), busy = false, openAccount;
const HINTS = ["¿Qué tiempo hará mañana?", "¿Es buena hora para salir?", "¿Qué riesgos hay estos días?", "Explícame la propagación troposférica"];
const HINTS_HOME = ["¿Qué tiempo hace aquí ahora?", "¿Va a llover hoy donde estoy?", "¿Cuándo es mejor salir a caminar?", "¿Qué debería llevar hoy?"];

export function init(c, openSettings) {
  ctx = c; openAccount = openSettings;
  el = document.createElement("aside");
  el.className = "chat"; el.setAttribute("role", "dialog"); el.setAttribute("aria-label", "Asistente de IA"); el.setAttribute("aria-hidden", "true");
  document.body.appendChild(el);
  onChange(() => { if (el.classList.contains("on")) paint(); sync(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && el.classList.contains("on")) toggle(false); });
  sync();
}
/** Muestra u oculta el botón de chat según los ajustes (IA desactivada = sin asistente). */
export function sync() { const b = $("#chatbtn"); if (b) b.hidden = !aiOn(); }
export function toggle(force) {
  const on = force ?? !el.classList.contains("on");
  el.classList.toggle("on", on); el.setAttribute("aria-hidden", String(!on));
  if (on) { paint(); setTimeout(() => $("#chat-in", el)?.focus({ preventScroll: true }), 250); }
}
const place = () => ctx.place?.() || null;
function paint() {
  if (!aiOn()) {
    el.innerHTML = `<header><h2>Asistente</h2><button class="icon-btn" id="chat-x" type="button" aria-label="Cerrar">${icon("x")}</button></header>
      <div class="off"><p>La IA interna está desactivada en tus ajustes.</p><button class="btn" id="chat-set" type="button">Abrir ajustes de IA</button></div>`;
    $("#chat-x", el).onclick = () => toggle(false); $("#chat-set", el).onclick = () => { toggle(false); openAccount("ia"); };
    return;
  }
  const pl = place(), home = ctx.home?.(), prov = me.data?.provider;
  el.innerHTML = `<header><h2>Asistente</h2>
      <button class="icon-btn" id="chat-new" type="button" aria-label="Nueva conversación" title="Nueva conversación">${icon("refresh")}</button>
      <button class="icon-btn" id="chat-cfg" type="button" aria-label="Ajustes de IA" title="Ajustes de IA">${icon("gear")}</button>
      <button class="icon-btn" id="chat-x" type="button" aria-label="Cerrar">${icon("x")}</button></header>
    <div class="ctxline">${pl ? `Viendo <b>${esc(pl.name || "tu lugar")}</b>` : "Ningún lugar abierto."}${home ? ` · 📍 <b>${esc(home.name)}</b> (tu ubicación: «aquí» y «donde estoy» se refieren a ella)` : (pl ? "" : " Abre un lugar o activa «Mi ubicación» en Previsión para que responda con datos reales.")}${prov && prov !== "server" ? ` · ${esc(me.data.model || prov)}` : ""}</div>
    <div class="msgs" id="chat-msgs" aria-live="polite"></div>
    <div class="hints" id="chat-hints"></div>
    <form id="chat-form"><textarea id="chat-in" rows="1" maxlength="1500" placeholder="Escribe tu pregunta…" aria-label="Mensaje"></textarea>
      <button class="btn" type="submit" aria-label="Enviar" id="chat-send">${icon("send", 18)}</button></form>`;
  $("#chat-x", el).onclick = () => toggle(false);
  $("#chat-cfg", el).onclick = () => { toggle(false); openAccount("ia"); };
  $("#chat-new", el).onclick = () => { msgs = []; LS.set("mg-chat", msgs); paint(); };
  const ta = $("#chat-in", el);
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(120, ta.scrollHeight) + "px"; });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form", el).requestSubmit(); } });
  $("#chat-form", el).addEventListener("submit", (e) => { e.preventDefault(); const v = ta.value.trim(); if (v && !busy) { ta.value = ""; ta.style.height = "auto"; ask(v); } });
  drawMsgs();
}
function drawMsgs() {
  const box = $("#chat-msgs", el); if (!box) return;
  box.innerHTML = msgs.length ? msgs.map((m) => `<div class="m ${m.role === "user" ? "u" : "a"}${m.err ? " err" : ""}">${esc(m.content)}${m.model ? `<small>${esc(m.model.split("/").pop().replace(":free", ""))}</small>` : ""}</div>`).join("")
    : '<div class="m a">Hola, soy el asistente de Guilda. Puedo explicarte la previsión del lugar que estás mirando, ayudarte a decidir cuándo salir o resolver dudas de radio.</div>';
  $("#chat-hints", el).innerHTML = msgs.length ? "" : (ctx.home?.() ? HINTS_HOME : HINTS).map((h) => `<button type="button">${esc(h)}</button>`).join("");
  $$h(el);
  box.scrollTop = box.scrollHeight;
}
const $$h = (root) => root.querySelectorAll("#chat-hints button").forEach((b) => (b.onclick = () => ask(b.textContent)));
async function ask(text) {
  busy = true; $("#chat-send", el)?.setAttribute("disabled", "");
  msgs.push({ role: "user", content: text }); drawMsgs();
  const box = $("#chat-msgs", el), wait = document.createElement("div");
  wait.className = "m a"; wait.innerHTML = '<span class="ai-wait"><i></i>Pensando…</span>'; box.appendChild(wait); box.scrollTop = box.scrollHeight;
  const pl = place();
  try {
    const r = await fetch("/api/assistant", { method: "POST", headers: json, body: JSON.stringify({
      messages: msgs.filter((m) => !m.err).slice(-14).map(({ role, content }) => ({ role, content })), lat: pl?.lat, lon: pl?.lon, name: pl?.name, section: ctx.section?.(), home: ctx.home?.() ? { lat: ctx.home().lat, lon: ctx.home().lon, name: ctx.home().name } : undefined }) });
    if (r.status === 401 && r.headers.get("x-auth") === "login") { location.href = "/api/auth/login?return_to=" + encodeURIComponent(location.pathname + location.hash); return; }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
    msgs.push({ role: "assistant", content: d.answer, model: d.model });
  } catch (e) { msgs.push({ role: "assistant", content: e.message, err: true }); }
  msgs = msgs.slice(-30); LS.set("mg-chat", msgs.filter((m) => !m.err));
  busy = false; drawMsgs(); $("#chat-send", el)?.removeAttribute("disabled");
}
