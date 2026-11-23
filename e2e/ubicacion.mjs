// «Mi ubicación» (geolocalización simulada), lectura de IA a petición, actividades, aire/UV/polen, asistente con home y diagnóstico de pantalla.
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8099", W = +(process.env.W || 390);
const b = await chromium.launch(), c = await b.newContext({ viewport: { width: W, height: W < 600 ? 844 : 800 }, hasTouch: W < 600, isMobile: W < 600, serviceWorkers: "block",
  permissions: ["geolocation"], geolocation: { latitude: 41.6176, longitude: 0.6200, accuracy: 25 } }), p = await c.newPage();
const errs = [], reqs = []; p.on("pageerror", (e) => errs.push("JS: " + e.message)); p.on("console", (m) => m.type() === "error" && errs.push(m.text().slice(0, 120)));
p.on("request", (r) => r.url().includes("/api/") && reqs.push(r.method() + " " + r.url().replace(BASE, "")));
const shot = (n) => p.screenshot({ path: `/out/u-${W}-${n}.png` }), say = (...a) => console.log(...a);
await p.goto(`${BASE}/#/forecast`); await p.waitForTimeout(2500);
say("CTA visible:", await p.isVisible("#myloc [data-loc=go]")); await shot("1-lista");
await p.click("#myloc [data-loc=go]"); await p.waitForTimeout(4500); await shot("2-con-ubicacion");
say("LS qth:", await p.evaluate(() => { const q = JSON.parse(localStorage.getItem("mg-qth")); return q && { lat: q.lat, lon: q.lon, loc: q.locator, name: q.name }; }));
say("fila Mi ubicación:", (await p.textContent("#myloc .places")).replace(/\s+/g, " ").slice(0, 90));
// ajustes en servidor
say("home en servidor:", JSON.stringify((await (await p.request.get(`${BASE}/api/me/settings`)).json()).home));
// abrir la previsión de mi ubicación: la IA NO se genera sola
reqs.length = 0;
await p.click("#myloc [data-go]"); await p.waitForTimeout(3500);
const ia = reqs.filter((r) => r.includes("/api/forecast/ai"));
say("peticiones de IA al abrir:", ia, "| botón generar visible:", await p.isVisible("#ai-gen"));
await shot("3-detalle-boton-ia");
// actividades y aire (cargan al hacer scroll)
await p.evaluate(() => document.querySelector("#act-sec").scrollIntoView({ block: "start", behavior: "instant" })); await p.waitForTimeout(2500);
say("actividad:", (await p.textContent("#actout")).replace(/\s+/g, " ").slice(0, 150)); await shot("4-actividades");
await p.click('[data-act="ciclismo"]'); await p.waitForTimeout(1200);
await p.evaluate(() => document.querySelector("#air-sec").scrollIntoView({ block: "start", behavior: "instant" })); await p.waitForTimeout(2500);
say("aire:", (await p.textContent("#airbox")).replace(/\s+/g, " ").slice(0, 200)); await shot("5-aire");
// asistente: lleva home y ya no hay lugar duplicado
await p.route("**/api/assistant", async (r) => { say("assistant body:", r.request().postData().slice(0, 220)); await r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ answer: "Prueba.", model: "m" }) }); });
await p.click("#chatbtn"); await p.waitForTimeout(500); say("hint 1:", await p.textContent("#chat-hints button")); await shot("6-chat");
await p.fill("#chat-in", "¿Qué tiempo hace aquí?"); await p.click("#chat-send"); await p.waitForTimeout(1200); await p.click("#chat-x");
// ajustes → cuenta
await p.click("#acctbtn"); await p.waitForTimeout(500); await p.click('[data-t="cuenta"]'); await p.waitForTimeout(400); await p.click("#dlg details summary"); await shot("7-cuenta");
say("diagnóstico:", (await p.textContent("#diag")).slice(0, 220)); await p.click("#aclose");
// Hoy
await p.goto(`${BASE}/#/forecast`); await p.waitForTimeout(500); await p.click(".home-link"); await p.waitForTimeout(3000); await shot("8-hoy");
say("tarjetas Hoy:", (await p.locator("#pg .pcard .pn").allTextContents()).slice(0, 3));
say("errores:", errs.slice(0, 5));
await b.close();
