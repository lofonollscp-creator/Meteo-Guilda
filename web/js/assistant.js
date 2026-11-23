// Guilda IA: barra «Pregunta…» en todas las pantallas y asistente de chat con datos reales (panel flotante o pantalla completa con historial y fuentes).
import { $, $$, api, esc, LS } from "./util.js";
import { icon } from "./icons.js";
import { md } from "./md.js";
import { aiOn, me, onChange } from "./me.js";

const json = { "content-type": "application/json" };
let ctx, el, chats = [], curId = null, busy = false, openAccount, full = LS.get("mg-chat-full", false), srcSeq = 0;

/* ---------- Texto contextual por sección ---------- */
const SPARK = '<path d="M12 3.5 13.6 9l5.4 1.6-5.4 1.6L12 17.5l-1.6-5.3L5 10.6 10.4 9z"/>';
const HINTS = [
  ["¿Qué tiempo hará mañana?", "Resumen del lugar, con lluvia y viento", "cloud"],
  ["¿Cuándo salgo a caminar?", "La mejor ventana de las próximas 48 h", "route"],
  ["¿Cómo está la propagación?", "HF, tropo y línea gris en tu QTH", "wave"],
  ["¿Cuándo pasa la ISS?", "Próximos pasos sobre tu ubicación", "satellite"],
];
const SUG = {
  home: ["¿Qué tiempo hará mañana?", "¿Cuándo salgo a caminar?", "¿Cómo está la propagación hoy?", "¿Cuándo pasa la ISS?"],
  forecast: ["¿Salgo mañana a caminar?", "¿Qué riesgos hay estos días?", "¿Qué me pongo?"],
  comparar: ["¿Cuál es mejor para salir mañana?", "¿Dónde llueve menos?"],
  routes: ["¿Y si salgo antes?", "¿Qué llevo para la cresta?", "Compara con otro día"],
  radio: ["¿Se abre hoy la banda de 20 m?", "¿Qué repetidor me llega?", "¿Cuándo pasa la ISS?"],
  ventanas: ["¿Cuándo activo una cima?", "¿Qué banda uso a las 18:00?"],
  repetidores: ["¿Cuál me llega mejor en DMR?", "Configúrame un repetidor"],
  propagacion: ["¿Qué banda abre a las 18:00?", "¿Hay conducto esta semana?", "Explícame el Kp"],
  satelites: ["¿Cuál es el mejor paso de la semana?", "¿Qué frecuencia uso en la ISS?"],
  portatil: ["Prepárame una activación para el martes", "¿Qué mástil me conviene con este viento?"],
  qsl: ["¿Qué países me faltan?", "QSL pendientes de este mes"],
  herramientas: ["Distancia a JN58td", "¿Qué significa QRZ?"],
  learn: ["Resume esta guía", "Ponme un ejemplo"],
};
const HOLD = { home: "Pregunta sobre el tiempo, una ruta o la radio…", forecast: "Pregunta sobre {p}…", comparar: "Pregunta qué lugar te conviene más…", routes: "Pregunta sobre tu ruta…",
  radio: "Pregunta sobre radio…", ventanas: "Pregunta cuándo conviene operar…", repetidores: "Pregunta qué repetidor te conviene…", propagacion: "Pregunta por las condiciones de hoy…",
  satelites: "Pregunta por un satélite o un paso…", portatil: "Pregunta por una activación SOTA o POTA…", qsl: "Pregunta por tu cuaderno…", herramientas: "Pide un cálculo: «dipolo para 7,1 MHz»…",
  learn: "¿No lo entiendes? Pregúntale a Guilda…" };
const MODELS = { ecmwf_ifs025: "ECMWF IFS", ecmwf_aifs025_single: "ECMWF AIFS", icon_seamless: "DWD ICON", gfs_seamless: "NOAA GFS", meteofrance_seamless: "Météo-France", gem_seamless: "ECCC GEM", ukmo_seamless: "UK Met Office", knmi_seamless: "KNMI" };

const place = () => ctx.place?.() || null;
function where() {   // «dónde estoy» en la app: sección + subsección para elegir sugerencias y textos
  const p = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean), s = ["forecast", "routes", "radio", "learn"].includes(p[0]) ? p[0] : "forecast";
  if (s === "forecast") return p[1] === "comparar" ? "comparar" : place() ? "forecast" : "home";
  if (s === "radio") return SUG[p[1]] ? p[1] : "radio";
  return s;
}

/* ---------- Conversaciones (en este dispositivo) ---------- */
function loadChats() {
  chats = LS.get("mg-chats", null);
  if (!Array.isArray(chats)) { const old = LS.get("mg-chat", []); chats = old.length ? [{ id: "c" + Date.now(), t: titleOf(old), m: old }] : []; }
  curId = LS.get("mg-chat-cur", null);
  if (!chats.some((c) => c.id === curId)) curId = chats[0]?.id ?? null;
}
const titleOf = (m) => { const u = m.find((x) => x.role === "user")?.content || "Conversación"; return u.length > 44 ? u.slice(0, 42) + "…" : u; };
const save = () => { LS.set("mg-chats", chats.slice(0, 30).map((c) => ({ ...c, m: c.m.filter((x) => !x.err).slice(-30) }))); LS.set("mg-chat-cur", curId); };
const cur = () => chats.find((c) => c.id === curId);
const msgs = () => cur()?.m || [];
function newChat() { const c = { id: "c" + Date.now(), t: "Conversación nueva", m: [], ctx: place()?.name }; chats.unshift(c); curId = c.id; save(); return c; }

/* ---------- Montaje ---------- */
export function init(c, openSettings) {
  ctx = c; openAccount = openSettings; loadChats();
  el = document.createElement("aside");
  el.className = "chat"; el.setAttribute("role", "dialog"); el.setAttribute("aria-label", "Asistente de IA"); el.setAttribute("aria-hidden", "true");
  document.body.appendChild(el);
  onChange(() => { if (el.classList.contains("on")) paint(); sync(); updateAsk?.(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && el.classList.contains("on")) toggle(false); });
  ctx.ask = open;
  sync();
}
/** Muestra u oculta el botón del asistente y la barra de pregunta según los ajustes (IA desactivada = sin asistente). */
export function sync() { const b = $("#chatbtn"); if (b) b.hidden = !aiOn(); document.body.classList.toggle("no-ai", !aiOn()); }
export function toggle(force) {
  const on = force ?? !el.classList.contains("on");
  el.classList.toggle("on", on); el.setAttribute("aria-hidden", String(!on)); document.body.classList.toggle("chat-open", on);
  if (on) { paint(); setTimeout(() => $("#chat-in", el)?.focus({ preventScroll: true }), 250); }
}
/** Abre el asistente y, si se da texto, lo envía como pregunta. */
export function open(text) { toggle(true); if (text && aiOn()) ask(text); }

/* ---------- Barra «Pregunta…» (arriba en escritorio, sobre la barra de pestañas en móvil) ---------- */
let updateAsk;
export function mountAsk(colb) {
  const wrap = document.createElement("div"), bar = document.createElement("form"), sug = document.createElement("div"), mq = matchMedia("(max-width:1180px)");
  wrap.className = "askstick"; bar.className = "askbar"; bar.setAttribute("role", "search"); bar.setAttribute("aria-label", "Preguntar a Guilda"); sug.className = "asks";
  bar.innerHTML = `<span class="avai" aria-hidden="true">${icon("spark", 18)}</span><input id="ask-in" type="text" autocomplete="off" maxlength="1500" aria-label="Pregunta para Guilda"><span class="cx"></span><kbd>Ctrl K</kbd>
    <button class="go" type="submit" aria-label="Enviar pregunta">${icon("up", 18)}</button>`;
  wrap.appendChild(bar);
  const input = $("#ask-in", bar), cx = $(".cx", bar);
  bar.addEventListener("submit", (e) => { e.preventDefault(); const v = input.value.trim(); input.value = ""; input.blur(); open(v || undefined); });
  sug.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) open(b.textContent); });
  let guard = false;
  updateAsk = () => {
    if (!colb.isConnected) return;
    const w = where(), p = place(), h = ctx.home?.(), sec = ctx.section?.();
    input.placeholder = (HOLD[w] || HOLD.radio).replace("{p}", p?.name || "este lugar");
    cx.textContent = w === "learn" ? "Esta guía" : p && sec === "forecast" ? p.name : sec === "radio" ? (LS.get("mg-qth", null)?.locator || "Mi QTH") : h?.name || "";
    cx.hidden = !cx.textContent;
    sug.innerHTML = (SUG[w] || SUG.radio).map((s) => `<button type="button">${esc(s)}</button>`).join("");
    wrap.hidden = sug.hidden = !aiOn();
    guard = true;
    if (mq.matches) { if (wrap.parentNode !== document.body) document.body.appendChild(wrap); if (sug.parentNode !== colb) colb.prepend(sug); }
    else if (colb.firstElementChild !== wrap) { colb.prepend(sug); colb.prepend(wrap); }
    guard = false;
  };
  new MutationObserver(() => { if (!guard) requestAnimationFrame(() => updateAsk()); }).observe(colb, { childList: true });
  mq.addEventListener?.("change", () => updateAsk());
  addEventListener("hashchange", () => setTimeout(() => updateAsk(), 60));
  document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && !$("#q") && aiOn()) { e.preventDefault(); input.focus(); } });
  updateAsk();
}

/* ---------- Panel / pantalla completa ---------- */
function paint() {
  el.classList.toggle("full", full);
  if (!aiOn()) {
    el.innerHTML = `<div class="chat-main"><header><h2>Asistente</h2><button class="icon-btn" id="chat-x" type="button" aria-label="Cerrar">${icon("x")}</button></header>
      <div class="off"><p>La IA interna está desactivada en tus ajustes.</p><button class="btn" id="chat-set" type="button">Abrir ajustes de IA</button></div></div>`;
    $("#chat-x", el).onclick = () => toggle(false); $("#chat-set", el).onclick = () => { toggle(false); openAccount("ia"); };
    return;
  }
  const pl = place(), home = ctx.home?.(), prov = me.data?.provider;
  el.innerHTML = `
    <div class="chat-hist" aria-label="Conversaciones"><div class="hist-head"><strong>Conversaciones</strong></div>
      <div style="padding:0 14px 8px"><button class="btn ai" id="chat-new2" type="button" style="width:100%">${icon("plus", 16)} Nueva conversación</button></div>
      <div class="hist-list" id="hist"></div><p class="hist-note">Se guardan en este dispositivo.</p></div>
    <div class="chat-main">
      <header><span class="chat-av" aria-hidden="true">${icon("spark", 18)}</span><div class="chat-t"><h2>Guilda</h2><p>Asistente · ${prov && prov !== "server" ? esc((me.data.model || prov).split("/").pop()) : "modelo gratuito"}</p></div>
        <button class="icon-btn" id="chat-new" type="button" aria-label="Nueva conversación" title="Nueva conversación">${icon("plus")}</button>
        <button class="icon-btn" id="chat-full" type="button" aria-label="${full ? "Reducir" : "Ampliar"}" title="${full ? "Reducir" : "Pantalla completa"}">${icon(full ? "minus" : "grid")}</button>
        <button class="icon-btn" id="chat-cfg" type="button" aria-label="Ajustes de IA" title="Ajustes de IA">${icon("gear")}</button>
        <button class="icon-btn" id="chat-x" type="button" aria-label="Cerrar">${icon("x")}</button></header>
      <div class="ctxline">${pl ? `<span class="chip on-ai">${icon("map", 13)} Hablando de <b>${esc(pl.name || "tu lugar")}</b></span>` : `<span class="chip dim">Ningún lugar abierto</span>`}${home ? `<span class="chip" title="«aquí» y «donde estoy» se refieren a tu ubicación">${icon("pin", 13)} <b>${esc(home.name)}</b></span>` : ""}${!pl && !home ? `<span class="ctx-help">Abre un lugar o activa «Mi ubicación» en Previsión para respuestas con datos reales.</span>` : ""}</div>
      <div class="msgs" id="chat-msgs" aria-live="polite"></div>
      <div class="hints" id="chat-hints"></div>
      <form id="chat-form"><textarea id="chat-in" rows="1" maxlength="1500" placeholder="Pregunta lo que quieras…" aria-label="Mensaje"></textarea>
        <button class="chat-send" type="submit" aria-label="Enviar" id="chat-send">${icon("up", 18)}</button></form>
      <p class="chat-foot">Modelo gratuito · cada cifra se comprueba contra los datos · <a href="#" id="chat-cfg2">Ajustes de IA</a></p></div>
    <div class="chat-src" id="chat-src" aria-label="Fuentes"></div>`;
  $("#chat-x", el).onclick = () => toggle(false);
  const cfg = (e) => { e.preventDefault(); toggle(false); openAccount("ia"); };
  $("#chat-cfg", el).onclick = cfg; $("#chat-cfg2", el).onclick = cfg;
  $("#chat-full", el).onclick = () => { full = !full; LS.set("mg-chat-full", full); paint(); };
  const fresh = () => { newChat(); paint(); };
  $("#chat-new", el).onclick = fresh; $("#chat-new2", el).onclick = fresh;
  const ta = $("#chat-in", el);
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(120, ta.scrollHeight) + "px"; });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form", el).requestSubmit(); } });
  $("#chat-form", el).addEventListener("submit", (e) => { e.preventDefault(); const v = ta.value.trim(); if (v && !busy) { ta.value = ""; ta.style.height = "auto"; ask(v); } });
  drawHist(); drawMsgs(); if (full) loadSources();
}
function drawHist() {
  const box = $("#hist", el); if (!box) return;
  box.innerHTML = chats.length ? chats.map((c) => `<div class="chat-i${c.id === curId ? " on" : ""}"><button type="button" data-c="${c.id}">${esc(c.t)}<small>${esc(c.ctx || "General")}</small></button><button type="button" class="del" data-del="${c.id}" aria-label="Borrar conversación">${icon("x", 14)}</button></div>`).join("") : '<p class="hist-note">Aún no hay conversaciones.</p>';
  box.onclick = (e) => {
    const d = e.target.closest("[data-del]"), c = e.target.closest("[data-c]");
    if (d) { chats = chats.filter((x) => x.id !== d.dataset.del); if (curId === d.dataset.del) curId = chats[0]?.id ?? null; save(); paint(); }
    else if (c) { curId = c.dataset.c; save(); paint(); }
  };
}
const shortModel = (m = "") => { const t = m.replace(":free", ""); return t.length > 26 ? t.split("/").pop() : t; };
const AV = () => `<span class="av" aria-hidden="true">${icon("spark", 14)}</span>`;
function bubble(m, i, last) {
  if (m.role === "user") return `<div class="m u">${esc(m.content)}</div>`;
  const foot = !m.err && m.model !== undefined ? `<div class="mf"><span class="ok-chk">${icon("check", 13)} Cifras comprobadas contra los datos</span><span class="mf-r"><small>${esc(shortModel(m.model))}</small>
    <button type="button" class="copy" data-copy="${i}" aria-label="Copiar respuesta" title="Copiar">${icon("link", 13)} Copiar</button>${last ? `<button type="button" class="copy" data-regen="1" aria-label="Volver a generar" title="Volver a generar">${icon("refresh", 13)} Regenerar</button>` : ""}</span></div>` : "";
  return `<div class="m a${m.err ? " err" : ""}">${AV()}<div class="mb">${m.err ? `<p>${esc(m.content)}</p>` : md(m.content)}${foot}</div></div>`;
}
function drawMsgs() {
  const box = $("#chat-msgs", el); if (!box) return;
  const list = msgs(), hello = new Date().getHours() < 6 ? "Buenas noches" : new Date().getHours() < 13 ? "Buenos días" : new Date().getHours() < 21 ? "Buenas tardes" : "Buenas noches";
  box.innerHTML = list.length ? list.map((m, i) => bubble(m, i, i === list.length - 1 && m.role === "assistant")).join("")
    : `<div class="hello"><p class="sub">${new Date().toLocaleDateString("es", { weekday: "long", day: "numeric", month: "long" })}</p><h3>${hello}.<br><span>¿Qué quieres saber?</span></h3>
      <div class="hintcards">${HINTS.map(([t, s, ic]) => `<button type="button" data-q="${esc(t)}"><span class="ico">${icon(ic, 19)}</span><span><b>${esc(t)}</b><small>${esc(s)}</small></span></button>`).join("")}</div></div>`;
  const hints = $("#chat-hints", el);
  hints.innerHTML = list.length && list.at(-1).role === "assistant" && !list.at(-1).err ? (SUG[where()] || SUG.radio).slice(0, 3).map((s) => `<button type="button">${esc(s)}</button>`).join("") : "";
  hints.onclick = (e) => { const b = e.target.closest("button"); if (b) ask(b.textContent); };
  box.onclick = async (e) => {
    const q = e.target.closest("[data-q]"), cp = e.target.closest("[data-copy]"), rg = e.target.closest("[data-regen]");
    if (q) return ask(q.dataset.q);
    if (rg && !busy) { const l = msgs(); while (l.length && l.at(-1).role === "assistant") l.pop(); const u = l.pop(); if (u) ask(u.content); return; }
    if (cp) { try { await navigator.clipboard.writeText(msgs()[+cp.dataset.copy].content); cp.textContent = "Copiado"; setTimeout(() => (cp.innerHTML = `${icon("link", 13)} Copiar`), 1500); } catch {} }
  };
  box.scrollTop = box.scrollHeight;
}
async function loadSources() {
  const box = $("#chat-src", el), pl = place(); if (!box) return;
  const my = ++srcSeq;
  box.innerHTML = `<h2>Lo que he usado</h2><p class="small">Datos reales, no memoria del modelo</p>${pl ? '<div class="skeleton" style="height:120px;border-radius:18px;margin-top:16px"></div>' : '<p class="empty" style="margin-top:16px">Abre un lugar en Previsión y aquí verás los modelos, estaciones y avisos que se usan para responder.</p>'}`;
  if (!pl) return;
  try {
    const [f, a] = await Promise.all([api(`/api/forecast?lat=${pl.lat}&lon=${pl.lon}`), api(`/api/avisos?lat=${pl.lat}&lon=${pl.lon}`).catch(() => null)]);
    if (my !== srcSeq || !$("#chat-src", el)) return;
    const mo = f.datos?.modelos || [], st = f.datos?.estaciones_cercanas || [], av = a?.avisos || [];
    const row = (n, t) => `<div class="src"><span>${esc(n)}</span><span class="num small">${esc(t || "")}</span></div>`;
    box.innerHTML = `<h2>Lo que he usado</h2><p class="small">Datos reales, no memoria del modelo · ${esc(pl.name || "")}</p>
      <div class="lbl" style="margin:18px 0 8px">Modelos</div><div class="rc">${mo.slice(0, 4).map((m) => row(MODELS[m] || m)).join("")}${mo.length > 4 ? row(`+ ${mo.length - 4} modelos más`) : ""}${mo.length ? "" : row("Sin datos")}</div>
      <div class="lbl" style="margin:18px 0 8px">Observaciones</div><div class="rc">${st.slice(0, 3).map((s) => row(`${s.nombre || s.id} · ${s.fuente || "APRS"}`, s.hora ? new Date(s.hora).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }) : "")).join("") || row("Sin estaciones cercanas")}</div>
      <div class="lbl" style="margin:18px 0 8px">Avisos oficiales</div><div class="rc">${a?.disponible === false ? row("No consultados (sin clave de AEMET)") : av.length ? av.slice(0, 3).map((x) => `<div class="src"><span><span class="pill lv${x.nivel >= 3 ? 2 : 1}"><i></i>${esc(x.color || "aviso")}</span> ${esc(x.zona || "")}</span></div>`).join("") : row("Sin avisos vigentes")}</div>`;
  } catch { if (my === srcSeq && $("#chat-src", el)) box.innerHTML = '<h2>Lo que he usado</h2><p class="empty" style="margin-top:16px">No se pudieron cargar las fuentes ahora.</p>'; }
}
async function ask(text) {
  if (busy) return;
  if (!cur()) newChat();
  const c = cur(); busy = true; $("#chat-send", el)?.setAttribute("disabled", "");
  c.m.push({ role: "user", content: text }); if (c.m.filter((x) => x.role === "user").length === 1) { c.t = titleOf(c.m); c.ctx = place()?.name; }
  drawMsgs(); drawHist();
  const box = $("#chat-msgs", el), wait = document.createElement("div");
  wait.className = "m a"; wait.innerHTML = `${AV()}<div class="mb"><span class="typing" aria-label="Pensando"><i></i><i></i><i></i></span></div>`; box.appendChild(wait); box.scrollTop = box.scrollHeight;
  const pl = place();
  try {
    const r = await fetch("/api/assistant", { method: "POST", headers: json, body: JSON.stringify({
      messages: c.m.filter((m) => !m.err).slice(-14).map(({ role, content }) => ({ role, content })), lat: pl?.lat, lon: pl?.lon, name: pl?.name, section: ctx.section?.(), home: ctx.home?.() ? { lat: ctx.home().lat, lon: ctx.home().lon, name: ctx.home().name } : undefined }) });
    if (r.status === 401 && r.headers.get("x-auth") === "login") { location.href = "/api/auth/login?return_to=" + encodeURIComponent(location.pathname + location.hash); return; }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
    c.m.push({ role: "assistant", content: d.answer, model: d.model });
  } catch (e) { c.m.push({ role: "assistant", content: e.message, err: true }); }
  c.m = c.m.slice(-30); save();
  busy = false; drawMsgs(); drawHist(); $("#chat-send", el)?.removeAttribute("disabled");
}
