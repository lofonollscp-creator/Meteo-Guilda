// Comprueba que los botones superiores no se solapan entre sí en anchos pequeños (con el botón de instalar y el chat visibles).
import { chromium } from "playwright";
const b = await chromium.launch();
for (const w of [320, 360, 390, 768, 1024]) {
  const c = await b.newContext({ viewport: { width: w, height: 700 }, hasTouch: true, isMobile: w < 600, serviceWorkers: "block" }), p = await c.newPage();
  await p.goto("http://127.0.0.1:8099/#/forecast"); await p.waitForTimeout(1500);
  await p.evaluate(() => { document.querySelector("#install").hidden = false; document.querySelector("#chatbtn").hidden = false; });
  const r = await p.evaluate(() => {
    const els = ["#acctbtn", "#chatbtn", "#theme", "#install", ".panes"].map((s) => [s, document.querySelector(s)?.getBoundingClientRect()]).filter(([, r]) => r && r.width);
    const out = [];
    for (let i = 0; i < els.length; i++) for (let j = i + 1; j < els.length; j++) { const a = els[i][1], c = els[j][1]; if (a.left < c.right && c.left < a.right && a.top < c.bottom && c.top < a.bottom) out.push(els[i][0] + "×" + els[j][0]); }
    return { solapes: out, panes: Math.round(els.find(([s]) => s === ".panes")?.[1].left ?? -1), install: Math.round(els.find(([s]) => s === "#install")?.[1].right ?? -1) };
  });
  console.log(w, JSON.stringify(r));
  if (w === 320) await p.screenshot({ path: "/out/ancho-320.png" });
  await c.close();
}
await b.close();
