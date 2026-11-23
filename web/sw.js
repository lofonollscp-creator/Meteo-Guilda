// Service worker de Guilda: shell offline + datos de API con red primero y copia de respaldo.
const V = "guilda-v29";
const SHELL = ["/", "/index.html", "/css/app.css", "/js/app.js", "/js/util.js", "/js/icons.js", "/js/charts.js", "/js/forecast.js", "/js/routes.js", "/js/radio.js", "/js/learn.js",
  "/js/learn-data.js", "/js/qsl.js", "/js/sync.js", "/js/me.js", "/js/account.js", "/js/assistant.js", "/js/cards.js", "/js/voice.js", "/js/qth.js", "/js/md.js", "/js/pantalla.js", "/icons/icon-192.png", "/icons/icon-512.png", "/vendor/maplibre-gl.js", "/vendor/maplibre-gl.css", "/favicon.svg", "/manifest.webmanifest",
  "/fonts/ibm-plex-sans-latin-400-normal.woff2", "/fonts/ibm-plex-sans-latin-500-normal.woff2", "/fonts/ibm-plex-sans-latin-600-normal.woff2",
  "/fonts/ibm-plex-mono-latin-400-normal.woff2", "/fonts/ibm-plex-mono-latin-500-normal.woff2", "/fonts/newsreader-latin-500-normal.woff2", "/fonts/newsreader-latin-600-normal.woff2"];
const API_CACHEABLE = /^\/api\/(places|forecast$|raw|astro|portable|propagation|solar|repeaters|satellites|snapshot|observations)/;

self.addEventListener("install", (e) => { e.waitUntil(caches.open(V).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => { e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k)))).then(() => self.clients.claim())); });

self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) {
    if (!API_CACHEABLE.test(url.pathname)) return;
    e.respondWith(networkFirst(req));
  } else {
    e.respondWith(staleWhileRevalidate(req));
  }
});

async function networkFirst(req) {
  const cache = await caches.open(V + "-api");
  try {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 9000);
    const r = await fetch(req, { signal: ctl.signal }); clearTimeout(t);
    if (r.ok) cache.put(req, r.clone());
    return r;
  } catch {
    const hit = await cache.match(req);
    if (hit) { const h = new Headers(hit.headers); h.set("x-from-cache", "1"); return new Response(await hit.blob(), { status: 200, headers: h }); }
    return new Response(JSON.stringify({ detail: "Sin conexión y sin copia guardada" }), { status: 503, headers: { "content-type": "application/json" } });
  }
}
async function staleWhileRevalidate(req) {
  const cache = await caches.open(V);
  const hit = await cache.match(req, { ignoreSearch: true });
  const net = fetch(req).then((r) => { if (r.ok) cache.put(req, r.clone()); return r; }).catch(() => null);
  return hit || (await net) || cache.match("/index.html");
}
