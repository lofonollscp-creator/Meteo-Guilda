// Diálogo «Instalar en iPhone o iPad» (se muestra con un user-agent de iPhone).
import { chromium, devices } from "playwright";
const b = await chromium.launch(), c = await b.newContext({ ...devices["iPhone 13"], serviceWorkers: "block" }), p = await c.newPage();
const errs = []; p.on("pageerror", (e) => errs.push(e.message));
await p.goto("http://127.0.0.1:8099/#/forecast"); await p.waitForTimeout(2000);
await p.click("#install"); await p.waitForTimeout(500);
const r = await p.evaluate(() => { const d = document.querySelector("#dlg"), i = d.querySelector(".dlg-in"), bb = d.getBoundingClientRect(); return { abierto: d.open, conMargen: !!i && getComputedStyle(i).paddingLeft, ancho: Math.round(bb.width), vw: innerWidth, desborde: d.scrollWidth > d.clientWidth }; });
console.log(JSON.stringify(r), errs); await p.screenshot({ path: "/out/ios-dialogo.png" }); await b.close();
