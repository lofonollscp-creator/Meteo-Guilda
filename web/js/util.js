export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const nf = new Intl.NumberFormat("es", { maximumFractionDigits: 0 });
export const nf1 = new Intl.NumberFormat("es", { maximumFractionDigits: 1 });
export const minus = (s) => String(s).replace("-", "−");
export const t0 = (v) => (v == null ? "–" : minus(nf.format(v)));
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const LS = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
export const api = async (url, opts) => {
  const r = await fetch(url, opts);
  const off = document.getElementById("offline");
  if (off) off.hidden = !r.headers.get("x-from-cache");
  if (r.status === 401 && r.headers.get("x-auth") === "login") { location.href = "/api/auth/login?return_to=" + encodeURIComponent(location.pathname + location.search); return new Promise(() => {}); }
  if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(typeof e.detail === "string" ? e.detail : `Error ${r.status}`); }
  return r.json();
};
export const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
export const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"];
export const compass = (d) => COMPASS[Math.round(d / 22.5) % 16];
export const wd = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d).toLocaleDateString("es", { weekday: "short", day: "numeric" }).replace(".", ""); };
export const hh = (iso) => iso.slice(11, 13) + "h";
export const fmtMin = (m) => (m >= 60 ? `${Math.floor(m / 60)} h ${String(Math.round(m % 60)).padStart(2, "0")} min` : `${Math.round(m)} min`);
export const fmtDT = (iso) => new Date(iso).toLocaleString("es", { weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
export const slug = (lat, lon) => `${lat.toFixed(3)},${lon.toFixed(3)}`;

/* ----- Maidenhead ----- */
export function toLocator(lat, lon) {
  lon += 180; lat += 90;
  let s = String.fromCharCode(65 + Math.floor(lon / 20), 65 + Math.floor(lat / 10));
  lon %= 20; lat %= 10;
  s += Math.floor(lon / 2) + "" + Math.floor(lat);
  lon = (lon % 2) * 12; lat = (lat % 1) * 24;
  return s + String.fromCharCode(97 + Math.floor(lon), 97 + Math.floor(lat));
}
export function fromLocator(loc) {
  const u = loc.trim().toUpperCase();
  if (!/^[A-R]{2}\d{2}([A-X]{2})?$/.test(u)) return null;
  let lon = (u.charCodeAt(0) - 65) * 20 - 180 + +u[2] * 2, lat = (u.charCodeAt(1) - 65) * 10 - 90 + +u[3], w = 2, h = 1;
  if (u.length >= 6) { lon += (u.charCodeAt(4) - 65) / 12; lat += (u.charCodeAt(5) - 65) / 24; w = 1 / 12; h = 1 / 24; }
  return { lat: lat + h / 2, lon: lon + w / 2 };
}
export const distKm = (a, b) => {
  const p = Math.PI / 180, dl = (b.lon - a.lon) * p, dp = (b.lat - a.lat) * p;
  const x = Math.sin(dp / 2) ** 2 + Math.cos(a.lat * p) * Math.cos(b.lat * p) * Math.sin(dl / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
};
export const bearing = (a, b) => {
  const p = Math.PI / 180, dl = (b.lon - a.lon) * p;
  const y = Math.sin(dl) * Math.cos(b.lat * p), x = Math.cos(a.lat * p) * Math.sin(b.lat * p) - Math.sin(a.lat * p) * Math.cos(b.lat * p) * Math.cos(dl);
  return (Math.atan2(y, x) / p + 360) % 360;
};
