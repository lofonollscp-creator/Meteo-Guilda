// Aviso de un paso de satélite programado desde el asistente. Sin servidor de notificaciones: solo suena con esta pestaña abierta.
import { compass } from "./util.js";

const timers = new Map();

/** Programa el aviso 5 min antes del paso `p` ({satelite, aos, aos_az, max_el}). Devuelve el texto de confirmación o lanza Error. */
export async function programarPaso(p) {
  const ms = new Date(p.aos).getTime() - Date.now() - 5 * 60000;
  if (!(ms > -4 * 60000)) throw new Error("Ese paso ya ha empezado o está a punto");
  if ("Notification" in window && Notification.permission === "default") { try { await Notification.requestPermission(); } catch { /* sin permiso: aviso en pantalla */ } }
  if (timers.has(p.aos)) clearTimeout(timers.get(p.aos));
  timers.set(p.aos, setTimeout(() => {
    timers.delete(p.aos);
    const txt = `${p.satelite}: pasa en 5 min (máx. ${p.max_el}°, sale por ${compass(p.aos_az)})`;
    try { if (Notification.permission === "granted") { new Notification("Paso de satélite", { body: txt, tag: p.aos }); return; } } catch { /* cae al aviso en pantalla */ }
    const t = Object.assign(document.createElement("div"), { className: "toast", textContent: txt }); document.body.appendChild(t); setTimeout(() => t.remove(), 8000);
  }, Math.max(0, ms)));
  return `Te aviso 5 min antes del paso de ${p.satelite} · deja esta pestaña abierta`;
}
