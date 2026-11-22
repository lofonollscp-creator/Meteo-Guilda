// Sincronización opcional por clave secreta: favoritos, QTH y cuaderno quedan accesibles desde cualquier dispositivo, la API y el MCP.
import { LS, api } from "./util.js";

export const getKey = () => LS.get("mg-key", null);
export const setKey = (k) => (k ? LS.set("mg-key", k) : localStorage.removeItem("mg-key"));
export const authHeaders = () => ({ Authorization: `Bearer ${getKey()}` });
export const syncing = () => !!getKey();
export const call = (path, opts = {}) => api(path, { ...opts, headers: { ...(opts.headers || {}), ...authHeaders() } });

let timer;
export function pushSettings() {
  if (!syncing()) return;
  clearTimeout(timer);
  timer = setTimeout(() => call("/api/profile", { method: "PUT", headers: { "content-type": "application/json" },
    body: JSON.stringify({ qth: LS.get("mg-qth", null), favs: LS.get("mg-favs", []) }) }).catch(() => {}), 600);
}
/** Al arrancar con clave: el servidor manda sobre lo local si tiene datos. */
export async function pullSettings() {
  if (!syncing()) return false;
  try {
    const { settings } = await call("/api/profile");
    if (settings.qth !== undefined) LS.set("mg-qth", settings.qth);
    if (Array.isArray(settings.favs)) LS.set("mg-favs", settings.favs);
    return true;
  } catch (e) { if (/401/.test(e.message) || /clave/i.test(e.message)) setKey(null); return false; }
}
export async function createProfile() {
  const { key } = await api("/api/profile", { method: "POST" });
  setKey(key);
  pushSettings();
  const local = LS.get("mg-qsl", []);
  if (local.length) {                      // migra el cuaderno local en bloque
    const body = local.map((q) => ({ call: q.call, qso_at: q.when + ":00Z", band: q.band, mode: q.mode, freq_mhz: q.freq ? +q.freq : null, rst_sent: q.rsts, rst_rcvd: q.rstr, grid: q.grid || null, name: q.name, notes: q.notes, qsl_sent: !!q.sent, qsl_rcvd: !!q.rcvd }));
    for (const b of body) await call("/api/qso", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b) }).catch(() => {});
  }
  return key;
}
