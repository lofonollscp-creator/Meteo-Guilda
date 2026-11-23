// Capturas, errores de consola, desbordes y tiempos de carga de la web.
//   docker run --rm --network host -v $PWD/e2e:/e2e/s:ro -v $PWD/e2e/salida:/out -e BASE=http://127.0.0.1:8099 mg-e2e node /e2e/s/captura.mjs
// Variables: RUTAS="forecast,forecast/garos" ANCHOS="390,1280" ESPERA=2500 CLIC="sel|sel" (se pulsa tras cargar) PREFIJO=base
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8099";
const RUTAS = (process.env.RUTAS || "forecast,forecast/garos").split(",");
const ANCHOS = (process.env.ANCHOS || "390,1280").split(",").map(Number);
const ESPERA = +(process.env.ESPERA || 2500), PREF = process.env.PREFIJO || "p";
const CLIC = (process.env.CLIC || "").split("|").filter(Boolean);
const b = await chromium.launch();
for (const w of ANCHOS) for (const ruta of RUTAS) {
  const ctx = await b.newContext({ viewport: { width: w, height: w < 600 ? 844 : 800 }, deviceScaleFactor: 1, hasTouch: w < 600, isMobile: w < 600, serviceWorkers: "block" });
  const p = await ctx.newPage(), errs = [], reqs = [];
  p.on("console", (m) => { if (m.type() === "error") errs.push(m.text().slice(0, 160)); });
  p.on("pageerror", (e) => errs.push("JS: " + e.message.slice(0, 160)));
  p.on("response", (r) => reqs.push([r.url().replace(BASE, ""), r.status()]));
  const t0 = Date.now();
  await p.goto(`${BASE}/#/${ruta}`, { waitUntil: "load" });
  const tLoad = Date.now() - t0;
  await p.waitForTimeout(ESPERA);
  for (const c of CLIC) { try { await p.click(c, { timeout: 3000 }); await p.waitForTimeout(600); } catch (e) { errs.push("clic " + c + ": " + e.message.split("\n")[0]); } }
  // TAP="selector@fx,fy": toca (táctil) o pasa el ratón por esa fracción del elemento, antes de la captura. TAPSCROLL: px de #detail antes de tocar.
  if (process.env.TAPSCROLL) { await p.evaluate((y) => document.querySelector("#detail").scrollTo({ top: y, behavior: "instant" }), +process.env.TAPSCROLL); await p.waitForTimeout(300); }
  if (process.env.TAP) { const [sel, f] = process.env.TAP.split("@"), [fx, fy] = (f || "0.5,0.5").split(",").map(Number);
    const bb = await p.locator(sel).first().boundingBox(); if (bb) { const x = bb.x + bb.width * fx, y = bb.y + bb.height * fy; if (w < 600) await p.touchscreen.tap(x, y); else await p.mouse.move(x, y); await p.waitForTimeout(400); } else errs.push("TAP sin elemento " + sel); }
  const m = await p.evaluate(() => {
    const vw = innerWidth, over = [...document.querySelectorAll("body *")].filter((e) => { const r = e.getBoundingClientRect(); return r.width && r.right > vw + 2 && getComputedStyle(e).position !== "fixed"; })
      .slice(0, 6).map((e) => e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") + (e.className && typeof e.className === "string" ? "." + e.className.split(" ")[0] : ""));
    const nav = performance.getEntriesByType("navigation")[0], res = performance.getEntriesByType("resource");
    return { vw, scrollW: document.documentElement.scrollWidth, over, dcl: Math.round(nav.domContentLoadedEventEnd), loadEv: Math.round(nav.loadEventEnd),
      kb: Math.round(res.reduce((s, r) => s + (r.encodedBodySize || 0), 0) / 1024), n: res.length, fcp: Math.round(performance.getEntriesByName("first-contentful-paint")[0]?.startTime || 0) };
  });
  const file = `${PREF}-${ruta.replace(/\W+/g, "_")}-${w}.png`;
  const SCROLLS = (process.env.SCROLLS || "").split(",").filter(Boolean).map(Number);   // posiciones de scroll de #detail (px) → una captura por cada una
  if (!SCROLLS.length) await p.screenshot({ path: `/out/${file}`, fullPage: false });
  for (const y of SCROLLS) { await p.evaluate((y) => { document.querySelector("#detail").scrollTo({ top: y, behavior: "instant" }); scrollTo(0, 0); }, y); await p.waitForTimeout(400); await p.screenshot({ path: `/out/${file.replace(".png", `-y${y}.png`)}` }); }
  console.log(JSON.stringify({ file, w, ruta, tLoad, ...m, errs: errs.slice(0, 5), malas: reqs.filter((r) => r[1] >= 400).slice(0, 5) }));
  await ctx.close();
}
await b.close();
