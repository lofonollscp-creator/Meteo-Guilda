// Notificaciones push (Web Push): avisos oficiales del lugar elegido y recordatorios de pasos de satélite, aunque la pestaña esté cerrada.
// Solo con el permiso del navegador y activándolas en Ajustes → Avisos; se pueden desactivar en cualquier momento (se borra el dispositivo del servidor).
import { api } from "./util.js";

const json = { "content-type": "application/json" };
const b64 = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")), (c) => c.charCodeAt(0));
const ab = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

// `serviceWorker.ready` no se resuelve nunca si el service worker no llega a registrarse (navegación privada, http…): se espera como mucho 3 s
const listo = () => Promise.race([navigator.serviceWorker.ready, new Promise((_, no) => setTimeout(() => no(new Error("El service worker no está activo")), 3000))]);
export const soportado = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
export async function config() { try { return await api("/api/push/config"); } catch { return { disponible: false }; } }
export async function suscripcion() {
  if (!soportado()) return null;
  try { return await (await listo()).pushManager.getSubscription(); } catch { return null; }
}
export async function estado() {
  if (!soportado()) return { suscrito: false, permiso: "no-soportado" };
  const s = await suscripcion();
  if (!s) return { suscrito: false, permiso: Notification.permission };
  try { return { ...(await api("/api/push/estado", { method: "POST", headers: json, body: JSON.stringify({ endpoint: s.endpoint }) })), permiso: Notification.permission }; }
  catch { return { suscrito: false, permiso: Notification.permission }; }
}
const cuerpo = (s, p) => JSON.stringify({ endpoint: s.endpoint, p256dh: ab(s.getKey("p256dh")), auth: ab(s.getKey("auth")),
  lat: p.lat ?? null, lon: p.lon ?? null, nombre: p.nombre ?? null, avisos: p.avisos !== false, nivel_min: p.nivel_min || 2, pasos: p.pasos !== false });

/** Pide permiso, suscribe este navegador y guarda las preferencias. Lanza Error con un texto claro si no se puede. */
export async function activar(prefs) {
  if (!soportado()) throw new Error("Este navegador no admite notificaciones (en iPhone, añade primero la web a la pantalla de inicio)");
  const cfg = await config();
  if (!cfg.disponible) throw new Error("El servidor no tiene las notificaciones configuradas");
  if ((await Notification.requestPermission()) !== "granted") throw new Error("El navegador ha bloqueado las notificaciones: permítelas en los ajustes del sitio");
  const reg = await listo();
  const s = (await reg.pushManager.getSubscription()) || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(cfg.clave_publica) }));
  await api("/api/push/suscripcion", { method: "PUT", headers: json, body: cuerpo(s, prefs) });
}
export async function guardar(prefs) { const s = await suscripcion(); if (!s) throw new Error("Las notificaciones no están activadas en este dispositivo"); await api("/api/push/suscripcion", { method: "PUT", headers: json, body: cuerpo(s, prefs) }); }
export async function desactivar() {
  const s = await suscripcion(); if (!s) return;
  try { await api("/api/push/suscripcion", { method: "DELETE", headers: json, body: JSON.stringify({ endpoint: s.endpoint }) }); } catch { /* si el servidor no responde, se da de baja igualmente en el navegador */ }
  await s.unsubscribe();
}
export async function probar() { const s = await suscripcion(); if (!s) throw new Error("Las notificaciones no están activadas en este dispositivo"); return api("/api/push/prueba", { method: "POST", headers: json, body: JSON.stringify({ endpoint: s.endpoint }) }); }
/** Recordatorio de un paso por el servidor. Devuelve true si quedó programado; false si este dispositivo no tiene push (el llamador usa el aviso local). */
export async function recordarPaso(p) {
  const s = await suscripcion(); if (!s) return false;
  try { await api("/api/push/paso", { method: "POST", headers: json, body: JSON.stringify({ endpoint: s.endpoint, satelite: p.satelite, aos: p.aos, aos_az: p.aos_az ?? null, max_el: p.max_el ?? null }) }); return true; }
  catch (e) { if (/ya ha empezado|demasiado/.test(e.message)) throw e; return false; }
}

/* Avisos personalizados por umbral (viento, rachas, lluvia, oleaje): ver backend/meteoguilda/reglas.py */
export async function reglas() { const s = await suscripcion(); if (!s) return { reglas: [], maximo: 5, variables: {} }; return api("/api/push/reglas/lista", { method: "POST", headers: json, body: JSON.stringify({ endpoint: s.endpoint }) }); }
export async function crearRegla(r) { const s = await suscripcion(); if (!s) throw new Error("Las notificaciones no están activadas en este dispositivo"); return api("/api/push/reglas", { method: "PUT", headers: json, body: JSON.stringify({ endpoint: s.endpoint, ...r }) }); }
export const borrarRegla = (id) => api(`/api/push/reglas/${id}`, { method: "DELETE" });

