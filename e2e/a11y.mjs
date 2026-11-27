// Revisión de accesibilidad y desbordes sin dependencias: nombres accesibles, tamaño táctil, ids duplicados, referencias ARIA rotas, desborde horizontal y errores de JS.
// Uso: docker run --rm --network host -v $PWD/e2e/a11y.mjs:/e2e/a11y.mjs:ro mg-e2e node a11y.mjs   (BASE=url, ANCHOS=390,1280, MIN=40)
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8099", ANCHOS = (process.env.ANCHOS || "390,1280").split(",").map(Number), MIN_TACTIL = +(process.env.MIN || 40), MIN_RATON = 24;
const RUTAS = ["forecast", "forecast/garos", "routes", "radio", "radio/repetidores", "radio/satelites", "radio/aprs", "radio/propagacion", "radio/ventanas", "radio/portatil", "radio/qsl", "radio/herramientas", "learn"];
const FAKE = { answer: "Prueba.", model: "x", herramientas: [], lugares: [], tarjetas: [
  { tipo: "comparar", lugares: [{ nombre: "Sitges", lat: 41.23, lon: 1.81, min_c: 9, max_c: 18, lluvia_48h_mm: 0, racha_max_kmh: 24 }, { nombre: "Vilanova", lat: 41.22, lon: 1.72, min_c: 9, max_c: 17, lluvia_48h_mm: 2, racha_max_kmh: 31 }], veredicto: ["Sitges es más cálido"] },
  { tipo: "mar", lugar: "Sitges", lat: 41.23, lon: 1.81, ahora: { ola_m: 0.6, estado: "Marejadilla" }, maximo: { ola_m: 1.4 }, serie: [0.6, 0.7, 0.9, 1.2, 1.4, 1.1, 0.8, 0.7], horas: Array(8).fill("2026-11-24T00:00") },
  { tipo: "accion", accion: "qso", lugar: "EA3XYZ", qso: { call: "EA3XYZ" } }] };
const b = await chromium.launch(); let total = 0;
const revisar = (MIN) => {
  const vis = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none" && !e.closest("[hidden],[inert]"); };
  const nombre = (e) => (e.getAttribute("aria-label") || e.getAttribute("title") || (e.getAttribute("aria-labelledby") ? [...e.getAttribute("aria-labelledby").split(" ")].map((i) => document.getElementById(i)?.textContent).join(" ") : "") || e.innerText || e.value || e.querySelector("img[alt]")?.alt || (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)?.textContent) || e.closest("label")?.textContent || e.placeholder || "").trim();
  const out = { sinNombre: [], pequenos: [], idsDup: [], ariaRota: [], desborde: document.documentElement.scrollWidth > innerWidth };
  const desc = (e) => `${e.tagName.toLowerCase()}${e.id ? "#" + e.id : ""}${e.className && typeof e.className === "string" ? "." + e.className.trim().split(/\s+/).slice(0, 2).join(".") : ""}${e.id || e.className ? "" : `(en ${(e.parentElement?.id && "#" + e.parentElement.id) || (typeof e.parentElement?.className === "string" && "." + e.parentElement.className.trim().split(/\s+/)[0]) || e.parentElement?.tagName.toLowerCase()})`}`;
  for (const e of document.querySelectorAll("button,a[href],input:not([type=hidden]),select,textarea,[role=button],[role=tab]")) {
    if (!vis(e)) continue;
    if (e.matches(".skip") || e.closest(".maplibregl-ctrl")) continue;
    if (!nombre(e)) out.sinNombre.push(desc(e));
    const r = e.getBoundingClientRect();
    if (Math.min(r.width, r.height) < MIN && !(e.tagName === "A" && getComputedStyle(e).display === "inline" ) && e.type !== "checkbox" && e.type !== "radio") out.pequenos.push(`${desc(e)} ${Math.round(r.width)}x${Math.round(r.height)}`);
  }
  const vistos = new Map(); for (const e of document.querySelectorAll("[id]")) vistos.set(e.id, (vistos.get(e.id) || 0) + 1);
  out.idsDup = [...vistos].filter(([, n]) => n > 1).map(([i]) => i);
  for (const e of document.querySelectorAll("[aria-labelledby],[aria-controls],[aria-describedby]")) for (const a of ["aria-labelledby", "aria-controls", "aria-describedby"]) for (const i of (e.getAttribute(a) || "").split(" ").filter(Boolean)) if (!document.getElementById(i)) out.ariaRota.push(`${desc(e)} ${a}=${i}`);
  return out;
};
for (const w of ANCHOS) {
  const c = await b.newContext({ viewport: { width: w, height: w < 600 ? 844 : 900 }, hasTouch: w < 600, isMobile: w < 600, serviceWorkers: "block" });
  await c.addInitScript(() => { try { localStorage.setItem("mg-qth", JSON.stringify({ lat: 41.49, lon: 2.36, locator: "JN11bm", name: "Premià de Mar" })); } catch {} });
  const p = await c.newPage(); const errs = []; p.on("pageerror", (e) => errs.push("JS: " + String(e).slice(0, 120))); p.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && errs.push(m.text().slice(0, 120)));
  await p.route("**/api/assistant*", (r) => r.fulfill({ json: FAKE }));
  const informe = async (nombre) => {
    const r = await p.evaluate(revisar, w < 600 ? MIN_TACTIL : MIN_RATON); const n = r.sinNombre.length + r.pequenos.length + r.idsDup.length + r.ariaRota.length + (r.desborde ? 1 : 0); total += n;
    console.log(`${n ? "✗" : "✓"} ${w}px ${nombre}`); for (const k of ["sinNombre", "pequenos", "idsDup", "ariaRota"]) if (r[k].length) console.log(`    ${k}: ${[...new Set(r[k])].slice(0, 8).join(" | ")}`); if (r.desborde) console.log("    desborde horizontal");
  };
  for (const ruta of RUTAS) { await p.goto(`${BASE}/#/${ruta}`); await p.waitForTimeout(2500); await informe(ruta); }
  await p.goto(`${BASE}/#/forecast`); await p.waitForTimeout(2500);
  await p.evaluate(() => document.querySelector("#chatbtn")?.click()); await p.waitForTimeout(500);
  await p.fill("#chat-in, .chat textarea", "prueba"); await p.keyboard.press("Enter"); await p.waitForTimeout(1500); await informe("chat con tarjetas");
  await p.evaluate(() => document.querySelector("#chat-mic")?.click()); await p.waitForTimeout(1200); await informe("modo Live");
  if (errs.length) { total += errs.length; console.log("    errores:", [...new Set(errs)].slice(0, 5)); }
  await c.close();
}
await b.close(); console.log(total ? `\n${total} hallazgos` : "\nSin hallazgos"); process.exit(total ? 1 : 0);
