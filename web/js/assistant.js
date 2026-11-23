// Asistente de IA: chat lateral (pantalla completa en móvil) con datos reales del lugar que estás mirando.
import { $, esc, LS } from "./util.js";
import { icon } from "./icons.js";
import { md } from "./md.js";
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
  el.innerHTML = `<header><span class="chat-av" aria-hidden="true">${icon("chat", 18)}</span><div class="chat-t"><h2>Asistente</h2><p>Datos reales de modelos y estaciones</p></div>
      <button class="icon-btn" id="chat-new" type="button" aria-label="Nueva conversación" title="Nueva conversación">${icon("refresh")}</button>
      <button class="icon-btn" id="chat-cfg" type="button" aria-label="Ajustes de IA" title="Ajustes de IA">${icon("gear")}</button>
      <button class="icon-btn" id="chat-x" type="button" aria-label="Cerrar">${icon("x")}</button></header>
    <div class="ctxline">${pl ? `<span class="chip">${icon("map", 13)} Viendo <b>${esc(pl.name || "tu lugar")}</b></span>` : `<span class="chip dim">Ningún lugar abierto</span>`}${home ? `<span class="chip" title="«aquí» y «donde estoy» se refieren a tu ubicación">${icon("pin", 13)} <b>${esc(home.name)}</b></span>` : ""}${prov && prov !== "server" ? `<span class="chip dim">${esc((me.data.model || prov).split("/").pop())}</span>` : ""}${!pl && !home ? `<span class="ctx-help">Abre un lugar o activa «Mi ubicación» en Previsión para que responda con datos reales.</span>` : ""}</div>
    <div class="msgs" id="chat-msgs" aria-live="polite"></div>
    <div class="hints" id="chat-hints"></div>
    <form id="chat-form"><textarea id="chat-in" rows="1" maxlength="1500" placeholder="Pregunta lo que quieras…" aria-label="Mensaje"></textarea>
      <button class="chat-send" type="submit" aria-label="Enviar" id="chat-send">${icon("send", 18)}</button></form>`;
  $("#chat-x", el).onclick = () => toggle(false);
  $("#chat-cfg", el).onclick = () => { toggle(false); openAccount("ia"); };
  $("#chat-new", el).onclick = () => { msgs = []; LS.set("mg-chat", msgs); paint(); };
  const ta = $("#chat-in", el);
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(120, ta.scrollHeight) + "px"; });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form", el).requestSubmit(); } });
  $("#chat-form", el).addEventListener("submit", (e) => { e.preventDefault(); const v = ta.value.trim(); if (v && !busy) { ta.value = ""; ta.style.height = "auto"; ask(v); } });
  drawMsgs();
}
const shortModel = (m = "") => { const t = m.replace(":free", ""); return t.length > 26 ? t.split("/").pop() : t; };
const AV = () => `<span class="av" aria-hidden="true">${icon("chat", 15)}</span>`;
function bubble(m, i) {
  if (m.role === "user") return `<div class="m u">${esc(m.content)}</div>`;
  const foot = !m.err && m.model !== undefined ? `<div class="mf"><small>${esc(shortModel(m.model))}</small><button type="button" class="copy" data-copy="${i}" aria-label="Copiar respuesta" title="Copiar">${icon("link", 13)} Copiar</button></div>` : "";
  return `<div class="m a${m.err ? " err" : ""}">${AV()}<div class="mb">${m.err ? `<p>${esc(m.content)}</p>` : md(m.content)}${foot}</div></div>`;
}
function drawMsgs() {
  const box = $("#chat-msgs", el); if (!box) return;
  box.innerHTML = msgs.length ? msgs.map(bubble).join("")
    : `<div class="m a">${AV()}<div class="mb"><p>Hola, soy el asistente de Guilda. Puedo:</p><ul><li>explicarte la <strong>previsión</strong> del lugar que miras,</li><li>decirte <strong>cuándo salir</strong> o qué riesgos hay,</li><li>resolver dudas de <strong>radio</strong> y propagación.</li></ul></div></div>`;
  $("#chat-hints", el).innerHTML = msgs.length ? "" : (ctx.home?.() ? HINTS_HOME : HINTS).map((h) => `<button type="button">${esc(h)}</button>`).join("");
  $$h(el);
  box.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = async () => { try { await navigator.clipboard.writeText(msgs[+b.dataset.copy].content); b.textContent = "Copiado"; setTimeout(() => (b.innerHTML = `${icon("link", 13)} Copiar`), 1500); } catch {} }));
  box.scrollTop = box.scrollHeight;
}
const $$h = (root) => root.querySelectorAll("#chat-hints button").forEach((b) => (b.onclick = () => ask(b.textContent)));
async function ask(text) {
  busy = true; $("#chat-send", el)?.setAttribute("disabled", "");
  msgs.push({ role: "user", content: text }); drawMsgs();
  const box = $("#chat-msgs", el), wait = document.createElement("div");
  wait.className = "m a"; wait.innerHTML = `${AV()}<div class="mb"><span class="typing" aria-label="Pensando"><i></i><i></i><i></i></span></div>`; box.appendChild(wait); box.scrollTop = box.scrollHeight;
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
