// Flujo de interacción: Detalle sin selección, ajustes de IA, chat, búsquedas recientes y recarga. Capturas en /out/f-*.png
import { chromium } from "playwright";
const BASE = process.env.BASE || "http://127.0.0.1:8099", W = +(process.env.W || 390);
const b = await chromium.launch(), c = await b.newContext({ viewport: { width: W, height: W < 600 ? 844 : 800 }, hasTouch: W < 600, isMobile: W < 600, serviceWorkers: "block" }), p = await c.newPage();
const errs = []; p.on("pageerror", (e) => errs.push("JS: " + e.message)); p.on("console", (m) => m.type() === "error" && errs.push(m.text().slice(0, 140)));
const shot = (n) => p.screenshot({ path: `/out/f-${W}-${n}.png` }), say = (...a) => console.log(...a);
await p.goto(`${BASE}/#/forecast`); await p.waitForTimeout(2500);
if (W < 600) { await p.click('[data-pane="detail"]'); await p.waitForTimeout(800); await shot("1-detalle-sin-seleccion"); say("detalle: ", (await p.textContent("#detail")).slice(0, 60).replace(/\s+/g, " ")); await p.click('[data-pane="list"]'); }
// búsqueda: recientes
await p.evaluate(() => localStorage.setItem("mg-recent", JSON.stringify([{ name: "Lleida", sub: "Catalunya", lat: 41.6176, lon: 0.62 }, { name: "Vielha", sub: "Aran", lat: 42.7, lon: 0.79 }, { name: "Burgos", sub: "Castilla", lat: 42.34, lon: -3.7 }])));
await p.reload(); await p.waitForTimeout(1500);
await p.click("#q"); await p.waitForTimeout(500); await shot("2-recientes");
say("recientes antes:", await p.locator("#results .r").count());
await p.click('#results [data-del]'); await p.waitForTimeout(300);
say("tras borrar 1:", await p.locator("#results .r").count(), "| LS:", await p.evaluate(() => JSON.parse(localStorage.getItem("mg-recent")).length));
await p.click("#results [data-clear]"); await p.waitForTimeout(300);
say("tras «Borrar todo»:", await p.evaluate(() => JSON.parse(localStorage.getItem("mg-recent")).length)); await p.keyboard.press("Escape");
// ajustes
await p.click("#acctbtn"); await p.waitForTimeout(900); await shot("3-ajustes-ia");
say("ajustes IA activada?", await p.isChecked("#ai-on"));
await p.check("#ai-on"); await p.waitForTimeout(700);
await p.selectOption("#ai-prov", "anthropic"); await p.waitForTimeout(700); await shot("4-ajustes-anthropic");
await p.fill("#ai-key", "sk-ant-prueba-0000000000"); await p.click("#ai-savekey"); await p.waitForTimeout(800);
say("clave guardada:", (await p.textContent("#tabbody")).includes("••••0000"));
await p.fill("#ai-model", "claude-sonnet-5-5"); await p.dispatchEvent("#ai-model", "change"); await p.waitForTimeout(500);
await p.click("#ai-test"); await p.waitForTimeout(2500); await shot("5-ajustes-probar"); say("prueba:", (await p.textContent("#ai-res")).slice(0, 110));
await p.click('[data-t="cuenta"]'); await p.waitForTimeout(400); await shot("6-cuenta");
await p.click('[data-t="sync"]'); await p.waitForTimeout(400); await shot("7-sync");
await p.click("#aclose"); await p.waitForTimeout(300);
// chat
await p.goto(`${BASE}/#/forecast/garos`); await p.waitForTimeout(3000);
await p.click("#chatbtn"); await p.waitForTimeout(600); await shot("8-chat");
await p.fill("#chat-in", "¿Qué tiempo hará mañana?"); await p.click("#chat-send"); await p.waitForTimeout(3500); await shot("9-chat-respuesta");
say("chat:", (await p.locator("#chat-msgs .m").allTextContents()).map((s) => s.slice(0, 90)));
await p.click("#chat-x"); await p.waitForTimeout(400);
// recarga
await p.click('[data-a="refresh"]'); await p.waitForTimeout(1500); await shot("10-recarga");
say("toast:", await p.locator(".toast").allTextContents());
// IA desactivada
await p.click("#acctbtn"); await p.waitForTimeout(600); await p.uncheck("#ai-on"); await p.waitForTimeout(600); await p.click("#aclose"); await p.waitForTimeout(500);
say("botón chat oculto con IA off:", await p.isHidden("#chatbtn"), "| etiqueta lectura:", (await p.textContent("#ailabel")).trim());
await shot("11-ia-off");
say("errores:", errs.slice(0, 6));
await b.close();
