// Modo Live: charla por voz con Guilda. Usa el reconocimiento y la síntesis de voz del propio navegador (Web Speech API): no se sube ningún
// audio a nuestros servidores. Chrome y Safari lo ofrecen; en navegadores sin soporte el botón no aparece.
import { icon } from "./icons.js";

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
export const liveSupported = () => !!SR && "speechSynthesis" in window;

const STOP = /^(para|parar|adiós|adios|cierra|cerrar|terminar|termina|fin|salir)\b/i;
const LABEL = { listening: "Te escucho…", thinking: "Pensando…", speaking: "Hablando… toca para interrumpir", paused: "En pausa · toca para hablar", error: "" };

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
      <button type="button" class="rb" id="live-kb" aria-label="Volver al teclado">${icon("edit")}</button>
      <button type="button" class="end" id="live-end">Terminar</button></div>
    <p class="live-note">El reconocimiento de voz lo hace tu navegador y puede enviar el audio a su proveedor. Di «para» o toca Terminar.</p>`;
  root.appendChild(box);
  const orb = box.querySelector(".orb"), st = box.querySelector(".live-st"), cap = box.querySelector(".live-cap");
  let rec = null, state = "paused", dead = false, quiet = 0, voice = null;
  const set = (s, msg) => { state = s; orb.className = "orb st-" + s; st.textContent = msg ?? LABEL[s] ?? ""; };
  const pickVoice = () => { const v = speechSynthesis.getVoices(); voice = v.find((x) => /^es[-_]ES/i.test(x.lang) && /natural|premium|neural|online/i.test(x.name)) || v.find((x) => /^es[-_]ES/i.test(x.lang)) || v.find((x) => /^es/i.test(x.lang)) || null; };
  pickVoice(); speechSynthesis.addEventListener?.("voiceschanged", pickVoice);

  function listen() {
    if (dead) return;
    speechSynthesis.cancel(); try { rec?.abort(); } catch {}
    rec = new SR(); rec.lang = "es-ES"; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
    let heard = false, final = "";
    rec.onresult = (e) => {
      let interim = ""; heard = true; quiet = 0;
      for (const r of e.results) { if (r.isFinal) final += r[0].transcript; else interim += r[0].transcript; }
      cap.textContent = (final + interim).trim();
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") { set("error", "No tengo permiso para usar el micrófono. Actívalo en los ajustes del navegador para este sitio."); dead = true; setTimeout(close, 3500); }
      else if (e.error === "no-speech" || e.error === "aborted") { /* se gestiona en onend */ }
      else if (e.error === "network") { set("error", "El reconocimiento de voz de tu navegador no responde. Prueba de nuevo o escribe."); state = "paused"; }
    };
    rec.onend = async () => {
      if (dead || state === "paused" || state === "error" || state === "thinking" || state === "speaking") return;
      const text = final.trim();
      if (!text) { if (heard || ++quiet < 3) return listen(); return set("paused"); }         // silencio: reintenta 3 veces y se pone en pausa
      if (STOP.test(text)) return close();
      set("thinking"); cap.textContent = text;
      const answer = await ask(text);
      if (dead) return;
      if (answer == null) return say("No he podido responder ahora. Inténtalo de nuevo.");
      say(paraVoz(answer));
    };
    try { rec.start(); set("listening"); } catch { set("paused"); }
  }
  function say(text) {
    if (dead) return;
    set("speaking"); speechSynthesis.cancel();
    const parts = trozos(text || "Hecho."); let i = 0;
    const next = () => {
      if (dead || state !== "speaking") return;
      if (i >= parts.length) { cap.textContent = ""; return listen(); }
      const u = new SpeechSynthesisUtterance(parts[i++].trim()); u.lang = "es-ES"; if (voice) u.voice = voice; u.rate = 1.05;
      u.onend = next; u.onerror = next; speechSynthesis.speak(u);
    };
    next();
  }
  function close() { if (dead && !box.isConnected) return; dead = true; try { rec?.abort(); } catch {} speechSynthesis.cancel(); speechSynthesis.removeEventListener?.("voiceschanged", pickVoice); box.remove(); onClose?.(); }
  box.querySelector(".live-x").onclick = close; box.querySelector("#live-end").onclick = close; box.querySelector("#live-kb").onclick = close;
  const mute = box.querySelector("#live-mute");
  mute.onclick = () => { const on = mute.getAttribute("aria-pressed") !== "true"; mute.setAttribute("aria-pressed", String(on)); if (on) { speechSynthesis.cancel(); try { rec?.abort(); } catch {} set("paused", "Micrófono silenciado"); } else { quiet = 0; listen(); } };
  box.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  orb.onclick = () => { if (state === "speaking") { speechSynthesis.cancel(); state = "paused"; listen(); } else if (state === "listening") { set("paused"); try { rec.abort(); } catch {} } else if (state === "paused" || state === "error") { quiet = 0; listen(); } };
  box.tabIndex = -1; box.focus({ preventScroll: true }); listen();
  return { close };
}
