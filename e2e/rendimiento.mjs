// Tiempos de carga con red móvil lenta simulada (Slow 4G ≈ 1,6 Mbps, 150 ms) y CPU x4 más lenta. BASES="nombre=url,nombre=url"
import { chromium } from "playwright";
const bases = (process.env.BASES || "nueva=http://127.0.0.1:8099").split(",").map((s) => s.split("="));
const b = await chromium.launch();
for (const [name, base] of bases) for (const ruta of ["forecast", "forecast/garos"]) {
  const res = [];
  for (let k = 0; k < 3; k++) {
    const c = await b.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, serviceWorkers: "block" }), p = await c.newPage();
    const cdp = await c.newCDPSession(p);
    await cdp.send("Network.enable"); await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 150, downloadThroughput: 200_000, uploadThroughput: 94_000 });
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    const t0 = Date.now(); let bytes = 0; cdp.on("Network.loadingFinished", (e) => (bytes += e.encodedDataLength));
    await p.goto(`${base}/#/${ruta}`, { waitUntil: "commit" });
    const sel = ruta === "forecast" ? "#places .place-temp .num" : ".hero-temp";
    await p.waitForSelector(sel, { timeout: 90000 }); const contenido = Date.now() - t0;
    const fcp = await p.evaluate(() => Math.round(performance.getEntriesByName("first-contentful-paint")[0]?.startTime || 0));
    res.push({ contenido, fcp, kb: Math.round(bytes / 1024) }); await c.close();
  }
  const med = (k) => res.map((r) => r[k]).sort((a, b) => a - b)[1];
  console.log(`${name.padEnd(6)} ${ruta.padEnd(15)} primer pintado ${med("fcp")} ms · datos en pantalla ${med("contenido")} ms · ${med("kb")} KB`);
}
await b.close();
