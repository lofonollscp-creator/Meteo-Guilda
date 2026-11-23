// Ajustes de la cuenta cargados del servidor (IA interna sí/no, proveedor, modelo). Las pantallas se suscriben para reaccionar al cambio.
import { api } from "./util.js";

export const me = { data: null };
const listeners = new Set();
const json = { "content-type": "application/json" };
const emit = () => listeners.forEach((f) => { try { f(me.data); } catch {} });
export const onChange = (f) => { listeners.add(f); return () => listeners.delete(f); };
/** ¿La IA interna está activada para esta persona? (por defecto sí, hasta saber lo contrario) */
export const aiOn = () => me.data?.ai_enabled !== false;
export async function load() { try { me.data = await api("/api/me/settings"); } catch {} emit(); return me.data; }
export async function save(patch) { me.data = await api("/api/me/settings", { method: "PUT", headers: json, body: JSON.stringify(patch) }); emit(); return me.data; }
export async function setKey(provider, key) { me.data = await api("/api/me/key", { method: "PUT", headers: json, body: JSON.stringify({ provider, key }) }); emit(); return me.data; }
export async function delKey(provider) { me.data = await api(`/api/me/key?provider=${encodeURIComponent(provider)}`, { method: "DELETE" }); emit(); return me.data; }
