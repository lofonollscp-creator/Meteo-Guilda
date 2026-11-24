// Modo Live: charla por voz con Guilda. Usa el reconocimiento y la síntesis de voz del propio navegador (Web Speech API): no se sube ningún
// audio a nuestros servidores. Chrome y Safari lo ofrecen; en navegadores sin soporte el botón no aparece.
import { icon } from "./icons.js";

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
export const liveSupported = () => !!SR && "speechSynthesis" in window;

// «para» como palabra suelta o con cortesía; una frase que EMPIEZA por «para…» («para mañana, ¿llueve?») no debe cerrar el modo
const STOP = /^(para|parar|basta|adiós|adios|cierra|cerrar|terminar|termina|fin|salir)( ya| ahora| por favor)?[\s.!¡¿?]*$/i;
const LABEL = { listening: "Te escucho…", thinking: "Pensando…", speaking: "Hablando… toca para interrumpir", paused: "En pausa · toca para hablar", error: "" };
const ESPERA_MAX = 90000;     // si el asistente tarda más, se avisa en vez de quedarse «pensando» para siempre

/** Texto de la respuesta pasado a voz: sin Markdown, sin enlaces, con un tope para no leer páginas enteras. */
export function paraVoz(md = "") {
  let t = md.replace(/```[\s\S]*?```/g, " ").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/^\s*\|.*\|\s*$/gm, " ").replace(/^#{1,6}\s*/gm, "")
    .replace(/^\s*[-*•]\s+/gm, "").replace(/[*_`>~]/g, "").replace(/\s*\n+\s*/g, ". ").replace(/\.\s*\./g, ".").replace(/\s+/g, " ").trim();
  if (t.length > 900) { const cut = t.slice(0, 900), i = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("? ")); t = (i > 300 ? cut.slice(0, i + 1) : cut) + " El resto lo tienes en pantalla."; }
  return t;
}
const trozos = (t) => t.match(/[^.!?]+[.!?]*\s*/g)?.flatMap((s) => (s.length > 200 ? s.match(/.{1,200}(\s|$)/g) || [s] : [s])) || [t];

/** Abre el modo Live dentro de `root`. `ask(texto)` envía la pregunta al asistente y devuelve su respuesta (texto) o null si falló. */
export function startLive({ root, ask, onClose }) {
  const box = document.createElement("div");
  box.className = "live"; box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Modo Live: conversación por voz");
  box.innerHTML = `<div class="live-h"><b><i></i>Live</b><button type="button" class="live-x icon-btn" aria-label="Salir del modo Live">${icon("x")}</button></div>
    <div class="live-c"><button type="button" class="orb" aria-label="Hablar o interrumpir"><i></i><i></i><i></i></button>
    <p class="live-st" aria-live="polite"></p><p class="live-cap" aria-live="polite"></p></div>
    <div class="live-ctl"><button type="button" class="rb" id="live-mute" aria-pressed="false" aria-label="Silenciar micrófono">${icon("mic")}</button>
      <button type="button" class="rb" id="live-kb" aria-label="Volver al teclado">${icon("keyboard")}</button>
      <button type="button" class="end" id="live-end">Terminar</button></div>
    <p class="live-note">El reconocimiento de voz lo hace tu navegador y puede enviar el audio a su proveedor. Di «para» o toca Terminar.</p>`;
  root.appendChild(box);
  const orb = box.querySelector(".orb"), st = box.querySelector(".live-st"), cap = box.querySelector(".live-cap");
  let rec = null, state = "paused", dead = false, quiet = 0, voice = null, gen = 0, wd = 0;
  const keep = [];             // Chrome recoge las locuciones sin referencia antes de que terminen y no avisa: se guardan hasta acabar
  const set = (s, msg) => { state = s; orb.className = "orb st-" + s; st.textContent = msg ?? LABEL[s] ?? ""; };
  const pickVoice = () => { const v = speechSynthesis.getVoices(); voice = v.find((x) => /^es[-_]ES/i.test(x.lang) && /natural|premium|neural|online/i.test(x.name)) || v.find((x) => /^es[-_]ES/i.test(x.lang)) || v.find((x) => /^es/i.test(x.lang)) || null; };
  pickVoice(); speechSynthesis.addEventListener?.("voiceschanged", pickVoice);
  try { speechSynthesis.speak(new SpeechSynthesisUtterance("")); } catch {}      // «desbloquea» la voz dentro del gesto de la persona (Safari/iOS)

  /** Empieza a escuchar. Chrome necesita un respiro tras abort() o start() falla con InvalidStateError: de ahí el retardo y los reintentos. */
  function listen(delay = 250) {
    if (dead) return;
    const my = ++gen; clearTimeout(wd); speechSynthesis.cancel(); try { rec?.abort(); } catch {}
    set("listening"); setTimeout(() => start(my, 0), delay);
  }
  function start(my, tries, lang = "es-ES") {
    if (dead || my !== gen) return;
    const r = new SR(); rec = r; r.lang = lang; r.interimResults = true; r.continuous = false; r.maxAlternatives = 1;
    let final = "", interim = "", done = false, err = "";
    r.onresult = (e) => {                                       // e.results es la lista COMPLETA de la sesión: se recompone cada vez (si no, el texto se duplica)
      final = ""; interim = "";
      for (let i = 0; i < e.results.length; i++) { const x = e.results[i]; if (x.isFinal) final += x[0].transcript; else interim += x[0].transcript; }
      cap.textContent = `${final} ${interim}`.trim(); quiet = 0;
    };
    r.onerror = (e) => {
      err = e.error;
      if (err === "not-allowed" || err === "service-not-allowed") { set("error", "No tengo permiso para usar el micrófono. Actívalo en los ajustes del navegador para este sitio."); dead = true; setTimeout(close, 4000); }
      else if (err === "audio-capture") set("error", "No encuentro ningún micrófono. Conéctalo o escribe tu pregunta.");
      else if (err === "language-not-supported" && lang !== "es") { done = true; start(my, tries, "es"); }
      else if (err === "network") set("error", "El reconocimiento de voz de tu navegador no responde ahora. Toca el círculo para reintentarlo o escribe.");
    };
    r.onend = async () => {
      clearTimeout(wd);
      if (done || dead || my !== gen) return;                   // un reconocimiento viejo no puede disparar el actual
      done = true;
      if (state === "error") return;
      const text = (final || interim).trim();                   // sin resultado «final» (pasa a menudo con frases cortas) vale lo último oído
      if (!text) { if (++quiet < 4) return listen(); return set("paused"); }          // silencio: reintenta y, tras 4 veces, pausa
      quiet = 0;
      if (STOP.test(text)) return close();
      set("thinking", "Pensando…"); cap.textContent = text;
      const answer = await Promise.race([ask(text), new Promise((ok) => setTimeout(() => ok(undefined), ESPERA_MAX))]);
      if (dead || my !== gen) return;
      if (answer === undefined) return say("El asistente tarda demasiado en responder. Inténtalo de nuevo en un momento.");
      if (answer == null) return say("No he podido responder ahora. Inténtalo de nuevo.");
      cap.textContent = answer.replace(/[*_`#>]/g, "").slice(0, 240);                 // la respuesta también se ve, por si el audio no suena
      say(paraVoz(answer));
    };
    try { r.start(); wd = setTimeout(() => { try { r.stop(); } catch {} }, 15000); }          // si no llega nada en 15 s, se cierra y se reintenta
    catch { if (tries < 3) setTimeout(() => start(my, tries + 1, lang), 350); else set("paused", "No he podido activar el micrófono. Toca el círculo para reintentar."); }
  }
  function say(text) {
    if (dead) return;
    set("speaking"); speechSynthesis.cancel(); keep.length = 0;
    const parts = trozos(text || "Hecho."), my = ++gen; let i = 0;
    const next = () => {
      if (dead || state !== "speaking" || my !== gen) return;
      if (i >= parts.length) { keep.length = 0; return listen(); }
      const t = parts[i++].trim(); if (!t) return next();
      const u = new SpeechSynthesisUtterance(t); u.lang = "es-ES"; if (voice) u.voice = voice; u.rate = 1.05;
      u.onend = next; u.onerror = next; keep.push(u); speechSynthesis.speak(u);
    };
    setTimeout(next, 150);                                      // Chrome pierde la primera locución si se habla justo tras cancel()
  }
  function close() { if (!box.isConnected) return; dead = true; clearTimeout(wd); try { rec?.abort(); } catch {} speechSynthesis.cancel(); speechSynthesis.removeEventListener?.("voiceschanged", pickVoice); box.remove(); onClose?.(); }
  box.querySelector(".live-x").onclick = close; box.querySelector("#live-end").onclick = close; box.querySelector("#live-kb").onclick = close;
  const mute = box.querySelector("#live-mute");
  mute.onclick = () => { const on = mute.getAttribute("aria-pressed") !== "true"; mute.setAttribute("aria-pressed", String(on)); if (on) { gen++; clearTimeout(wd); speechSynthesis.cancel(); try { rec?.abort(); } catch {} set("paused", "Micrófono silenciado"); } else { quiet = 0; listen(); } };
  box.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  orb.onclick = () => {
    if (state === "speaking") { gen++; listen(); }
    else if (state === "listening") { gen++; clearTimeout(wd); set("paused"); try { rec.abort(); } catch {} }
    else if (state === "paused" || state === "error") { quiet = 0; listen(); }
  };
  box.tabIndex = -1; box.focus({ preventScroll: true }); listen();
  return { close };
}
