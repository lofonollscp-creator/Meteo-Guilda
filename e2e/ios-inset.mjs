// iPhone en modo app con env(safe-area-inset-top)=0 (como en algunos iOS): la app debe usar una zona segura estimada.
import { chromium, devices } from "playwright";
const b = await chromium.launch();
for (const [name, dev] of [["iPhone 15 Pro Max", devices["iPhone 15 Pro Max"]], ["iPhone 13", devices["iPhone 13"]]]) {
  const c = await b.newContext({ ...dev, viewport: dev.screen, serviceWorkers: "block" }), p = await c.newPage();
  await p.addInitScript(() => Object.defineProperty(navigator, "standalone", { value: true }));
  await p.goto("http://127.0.0.1:8099/#/forecast"); await p.waitForTimeout(2000);
  const r = await p.evaluate(() => ({ pad: getComputedStyle(document.querySelector(".app")).paddingTop, extra: getComputedStyle(document.documentElement).getPropertyValue("--sat-extra"), top: Math.round(document.querySelector("#detail").getBoundingClientRect().top), pantalla: `${screen.width}x${screen.height}`, ventana: `${innerWidth}x${innerHeight}` }));
  console.log(name.padEnd(18), JSON.stringify(r));
  await c.close();
}
await b.close();
