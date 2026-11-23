// iPhone 17 Pro (402×874) en modo app: simula la zona segura (barra de estado/isla 59 px, barra inferior 34 px) sustituyendo env() y comprueba
// que ninguna parte del contenido se dibuja ni se desliza bajo la barra de estado. ANTES=fichero.css para comparar con una versión anterior.
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
const TOP = 59, BOT = 34, b = await chromium.launch();
for (const [nombre, css] of [["antes", process.env.ANTES], ["ahora", null]]) {
  if (nombre === "antes" && !css) continue;
  const c = await b.newContext({ viewport: { width: 402, height: 874 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true, serviceWorkers: "block" }), p = await c.newPage();
  await p.route("**/css/app.css", async (r) => { let t = css ? readFileSync(css, "utf8") : await (await r.fetch()).text();
    t = t.replace(/env\(safe-area-inset-top[^)]*\)/g, TOP + "px").replace(/env\(safe-area-inset-bottom[^)]*\)/g, BOT + "px"); await r.fulfill({ body: t, contentType: "text/css" }); });
  await p.goto("http://127.0.0.1:8099/#/forecast/garos"); await p.waitForTimeout(3500);
  await p.evaluate(() => document.querySelector("#detail").scrollTo({ top: 520, behavior: "instant" })); await p.waitForTimeout(300);
  const r = await p.evaluate((TOP) => {
    const d = document.querySelector("#detail").getBoundingClientRect(), a = document.querySelector(".app").getBoundingClientRect();
    // ¿hay contenido de la app (lista, detalle, gráficos…) en algún punto de la franja de la barra de estado? (se mira qué elemento hay realmente en cada punto)
    let dentro = 0;
    for (let y = 2; y < TOP; y += 4) for (let x = 4; x < innerWidth; x += 24) { const e = document.elementFromPoint(x, y); if (e && e.closest("#detail,#cola,.mapcol,.chat")) dentro++; }
    return { zonaScrollEmpiezaEn: Math.round(d.top), appEmpiezaEn: Math.round(a.top), puntosConContenidoEnLaBarraDeEstado: dentro };
  }, TOP);
  console.log(nombre.padEnd(6), JSON.stringify(r));
  await p.screenshot({ path: `/out/zona-${nombre}.png`, clip: { x: 0, y: 0, width: 402, height: 300 } });
  await c.close();
}
await b.close();
