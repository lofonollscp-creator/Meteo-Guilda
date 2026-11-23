// «Mi ubicación»: una sola para toda la app (Previsión, Radio y asistente). Se guarda en este dispositivo (mg-qth, como el QTH de Radio),
// se sincroniza con la clave de sincronización y, si hay sesión, en la cuenta para que el asistente y el MCP sepan dónde estás.
import { LS, api, toLocator } from "./util.js";
import { pushSettings } from "./sync.js";

const KEY = "mg-qth", subs = new Set(), json = { "content-type": "application/json" };
export const getQth = () => LS.get(KEY, null);
export const hasQth = () => getQth()?.lat != null;
export const onQth = (f) => { subs.add(f); return () => subs.delete(f); };
/** Nombre para mostrar: el del sitio, o el locator, o «Mi ubicación». */
export const label = (q = getQth()) => q?.name || (q?.locator ? `Mi QTH ${q.locator}` : "Mi ubicación");
export const hashOf = (q = getQth()) => `#/forecast/p/${q.lat.toFixed(4)},${q.lon.toFixed(4)},${encodeURIComponent(label(q))}`;

let timer;
function syncHome(q) {
  clearTimeout(timer);
  timer = setTimeout(() => send(q), 900);
}
async function send(q) {
  try { await api("/api/me/settings", { method: "PUT", headers: json, body: JSON.stringify({ home: q?.lat != null ? { lat: q.lat, lon: q.lon, name: q.name || null } : null }) }); } catch {}
}
export function setQth(q, { server = true } = {}) {
  LS.set(KEY, q); pushSettings(); subs.forEach((f) => f(q));
  if (server) syncHome(q);
}
export function clearQth() { localStorage.removeItem(KEY); pushSettings(); subs.forEach((f) => f(null)); syncHome(null); }

const MSG = {
  1: "Has denegado el permiso de ubicación. En iPhone/iPad: Ajustes → Privacidad y seguridad → Localización → Safari (o la app) → «Al usar la app». En Android: permisos del sitio en el navegador.",
  2: "No se ha podido determinar tu posición. Comprueba que la localización del dispositivo está activada.",
  3: "Tardó demasiado en obtener tu posición. Inténtalo de nuevo (mejor al aire libre o con la localización precisa activada).",
};
/** Pide la posición al dispositivo, la guarda como «Mi ubicación» y busca el nombre del sitio. Lanza Error con un mensaje claro. */
export async function locate() {
  if (!("geolocation" in navigator)) throw new Error("Este navegador no ofrece localización.");
  if (!window.isSecureContext) throw new Error("La localización solo funciona en una conexión segura (https).");
  const pos = await new Promise((ok, ko) => navigator.geolocation.getCurrentPosition(ok, (e) => ko(new Error(MSG[e.code] || "No se pudo obtener la ubicación.")),
    { enableHighAccuracy: false, timeout: 12000, maximumAge: 120000 }));
  const lat = +pos.coords.latitude.toFixed(4), lon = +pos.coords.longitude.toFixed(4), old = getQth();
  const l = toLocator(lat, lon), same = old?.lat != null && Math.abs(old.lat - lat) < 0.01 && Math.abs(old.lon - lon) < 0.01;
  let q = { ...(old || {}), lat, lon, locator: l.slice(0, 4).toUpperCase() + l.slice(4), accuracy: Math.round(pos.coords.accuracy || 0), at: Date.now(), name: same ? old.name : undefined };
  setQth(q);
  try {                                                  // el nombre es un extra: si falla, se queda el locator
    const r = await api(`/api/reverse?lat=${lat}&lon=${lon}`);
    if (r.name && getQth()?.lat === lat) { q = { ...getQth(), name: r.name, detalle: r.detalle }; setQth(q); }
  } catch {}
  return getQth();
}
