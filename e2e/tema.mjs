import { chromium } from "playwright";
const b = await chromium.launch(), out = [];
for (const scheme of ["light", "dark"]) {
  const c = await b.newContext({ viewport: { width: 1000, height: 500 }, colorScheme: scheme, serviceWorkers: "block" }), p = await c.newPage();
  const errs = []; p.on("pageerror", (e) => errs.push(e.message));
  await p.goto("http://127.0.0.1:8099/#/forecast"); await p.waitForTimeout(1500);
  const estado = async () => ({ attr: await p.evaluate(() => document.documentElement.dataset.theme || "(sin)"), bg: await p.evaluate(() => getComputedStyle(document.body).backgroundColor), t: await p.getAttribute("#theme", "title") });
  out.push(`SO ${scheme}: inicio`, JSON.stringify(await estado()));
  for (let i = 0; i < 3; i++) { await p.click("#theme"); await p.waitForTimeout(300); out.push(` clic ${i + 1}: ${JSON.stringify(await estado())}`); if (scheme === "dark" && i === 1) await p.screenshot({ path: "/out/tema-nav.png", clip: { x: 0, y: 380, width: 70, height: 120 } }); }
  await p.reload(); await p.waitForTimeout(800); out.push(` tras recargar (modo sistema): ${JSON.stringify(await estado())}`, errs.length ? "ERRORES " + errs : "");
  await c.close();
}
console.log(out.join("\n")); await b.close();
