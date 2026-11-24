// Modo sin conexión: tras una visita con red, al recargar sin red la app carga y enseña la última previsión con el aviso «sin conexión».
// Uso: docker run --rm --network host -v $PWD/e2e/offline.mjs:/e2e/offline.mjs:ro mg-e2e node offline.mjs   (BASE=url)
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8099";
const b = await chromium.launch(), c = await b.newContext({ viewport: { width: 390, height: 844 } });
await c.addInitScript(() => { try { localStorage.setItem("mg-qth", JSON.stringify({ lat: 41.49, lon: 2.36, locator: "JN11bm", name: "Premià de Mar" })); } catch {} });
const p = await c.newPage(); const errs = []; p.on("pageerror", (e) => errs.push(String(e).slice(0, 120)));
await p.goto(`${BASE}/#/forecast/garos`); await p.waitForTimeout(4000);
await p.evaluate(() => navigator.serviceWorker?.ready); await p.waitForTimeout(1500);
const sw = await p.evaluate(async () => !!(await navigator.serviceWorker?.getRegistration())?.active);
await p.reload(); await p.waitForTimeout(3000);                       // segunda carga: ya la controla el service worker
const hay = async () => p.evaluate(() => ({ nombre: document.querySelector("#detail h1, #detail h2, .d-head h1")?.textContent?.trim()?.slice(0, 40), temp: !!document.querySelector("#detail .big, #detail [class*=temp], #detail svg"), offline: !document.getElementById("offline")?.hidden }));
console.log("service worker activo:", sw, "| con red:", await hay());
await c.setOffline(true);
await p.reload().catch(() => {}); await p.waitForTimeout(4000);
const off = await hay(); console.log("sin red:", off);
await p.screenshot({ path: "/out/offline.png" }).catch(() => {});
await c.setOffline(false); console.log("errores JS:", errs);
await b.close(); process.exit(sw && off.nombre && off.offline ? 0 : 1);
