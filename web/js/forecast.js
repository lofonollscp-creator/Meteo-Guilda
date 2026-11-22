import { $, $$, api, esc, nf, nf1, t0, LS, debounce, compass, wd, hh, slug } from "./util.js";
import { renderCharts, renderStrip, sparkline, ramp, SCALES } from "./charts.js";

let ctx, places = [], favs = LS.get("mg-favs", []), recent = LS.get("mg-recent", []), snaps = {}, sel = null, req = 0, horizon = 16, last = null;
const ICON = {
  star: '<svg viewBox="0 0 24 24"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/></svg>',
  pin: '<svg viewBox="0 0 24 24"><path d="M9 3h6l-1 6 3 3v2H7v-2l3-3-1-6zM12 14v7"/></svg>',
  link: '<svg viewBox="0 0 24 24"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1"/></svg>',
  json: '<svg viewBox="0 0 24 24"><path d="M8 4C5 4 6 9 4 12c2 3 1 8 4 8M16 4c3 0 2 5 4 8-2 3-1 8-4 8"/></svg>',
};
const saveFavs = () => LS.set("mg-favs", favs);
const favKey = (lat, lon) => slug(lat, lon);
const findFav = (lat, lon) => favs.find((f) => f.key === favKey(lat, lon));
const sortedFavs = () => [...favs].sort((a, b) => (b.pinned | 0) - (a.pinned | 0));

export function mount(c) {
  ctx = c;
  ctx.cola.innerHTML = `
    <div class="cola-head"><h2>Previsión</h2><p>Modelos abiertos + balizas reales</p></div>
    <div class="search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4-4"/></svg>
      <input id="q" type="search" placeholder="Buscar cualquier lugar…" autocomplete="off" aria-label="Buscar lugar"><kbd>/</kbd>
      <div class="results" id="results" hidden></div></div>
    <div id="favs"></div>
    <div class="group-label">Lugares principales</div>
    <ul class="places" id="places" role="list"></ul>
    <p class="cola-note">Resumen de la media de 4 modelos. Los lugares guardados quedan en este dispositivo.</p>`;
  ctx.colb.innerHTML = '<p class="empty">Elige un lugar de la lista, busca uno nuevo o toca el mapa.</p>';
  ctx.stations(true);
  ctx.onMapClick = ({ lat, lng }) => { location.hash = `#/forecast/p/${lat.toFixed(4)},${lng.toFixed(4)},Punto del mapa`; };
  ctx.mapCtl('<button class="chip" id="fit" type="button">Ver los 6 lugares</button><span class="chip chip-ghost">Toca el mapa para consultar un punto</span>');
  $("#fit").addEventListener("click", () => ctx.fit(places.map((p) => [p.lon, p.lat])));
  $("#places").addEventListener("click", (e) => { const b = e.target.closest("[data-go]"); if (b) location.hash = b.dataset.go; });
  $("#favs").addEventListener("click", (e) => { const b = e.target.closest("[data-go]"); if (b) location.hash = b.dataset.go; });
  setupSearch();
  renderPlaces(); renderFavs();
  api("/api/places").then((p) => { places = p; renderPlaces(); drawPins(); if (!sel) ctx.fit(places.map((x) => [x.lon, x.lat])); }).catch(() => {});
  loadFavSnaps();
}
export function unmount() { ctx.stations(false); }

/* ---------- listas ---------- */
function placeRow(go, name, area, snap, { current, pinned } = {}) {
  const n = snap;
  return `<li><button class="place" data-go="${esc(go)}" aria-current="${!!current}">
    <span class="place-name">${pinned ? ICON.pin.replace("<svg", '<svg class="pinned"') : ""}${esc(name)}</span>
    <span class="place-temp"><span class="num">${n ? t0(n.temp_c) + "°" : '<span class="skeleton">00°</span>'}</span></span>
    <span class="place-area">${esc(area)}</span>
    <span class="place-meta">${n ? sparkline(n.spark) + `<span class="num">racha ${n.gust_kmh} · lluvia ${nf1.format(n.rain_24h_mm)}</span>` : ""}</span></button></li>`;
}
function renderPlaces() {
  $("#places").innerHTML = places.length ? places.map((p) => placeRow(`#/forecast/${p.slug}`, p.name, p.area, p.now, { current: sel?.slug === p.slug })).join("")
    : Array.from({ length: 6 }, (_, i) => `<li><button class="place"><span class="place-name skeleton">Cargando lugar</span><span class="place-area skeleton">Comarca</span></button></li>`).join("");
}
function renderFavs() {
  const f = sortedFavs();
  $("#favs").innerHTML = f.length ? `<div class="group-label">Mis lugares</div><ul class="places">${f.map((x) =>
    placeRow(favHash(x), x.name, x.sub || "", snaps[x.key], { current: sel?.key === x.key, pinned: x.pinned })).join("")}</ul>` : "";
}
const favHash = (x) => `#/forecast/p/${x.lat.toFixed(4)},${x.lon.toFixed(4)},${encodeURIComponent(x.name)}`;
async function loadFavSnaps() {
  if (!favs.length) return;
  try {
    const r = await api(`/api/snapshot?pts=${favs.slice(0, 15).map((f) => `${f.lat},${f.lon}`).join(";")}`);
    r.forEach((s, i) => (snaps[favs[i].key] = s.now));
    renderFavs();
  } catch {}
}
function drawPins() {
  ctx.clearMarkers();
  places.forEach((p) => ctx.marker(p.lon, p.lat, { label: p.name, cls: sel?.slug === p.slug ? "on" : "", onClick: () => (location.hash = `#/forecast/${p.slug}`) }));
  favs.forEach((f) => ctx.marker(f.lon, f.lat, { label: f.name, cls: "fav" + (sel?.key === f.key ? " on" : ""), onClick: () => (location.hash = favHash(f)) }));
  if (sel?.kind === "point" && !findFav(sel.lat, sel.lon)) ctx.marker(sel.lon, sel.lat, { label: sel.name, cls: "pt on" });
}
function toggleFav(item, pin) {
  const key = favKey(item.lat, item.lon), i = favs.findIndex((f) => f.key === key);
  if (pin === undefined) { if (i >= 0) favs.splice(i, 1); else favs.push({ key, ...item }); }
  else if (i >= 0) { favs[i].pinned = !favs[i].pinned; } else favs.push({ key, ...item, pinned: true });
  saveFavs(); renderFavs(); drawPins(); loadFavSnaps();
  if (last) paintActions();
}

/* ---------- búsqueda ---------- */
function setupSearch() {
  const q = $("#q"), box = $("#results");
  let items = [], idx = -1;
  const close = () => { box.hidden = true; idx = -1; };
  const row = (it, tag) => `<button class="r" data-i="${items.indexOf(it)}" type="button"><span class="grow"><b>${esc(it.name)}</b><small>${esc(it.sub || "")}</small></span>
    <span class="icon-btn" data-fav="${items.indexOf(it)}" role="button" aria-label="Guardar en favoritos" aria-pressed="${!!findFav(it.lat, it.lon)}">${ICON.star}</span></button>`;
  const paint = (title, list, more = "") => { items.push(...list); return list.length ? `<div class="r-head">${title}</div>` + list.map((it) => row(it)).join("") + more : more; };
  async function run() {
    const t = q.value.trim(); items = [];
    let html = "";
    const mine = sortedFavs().filter((f) => !t || f.name.toLowerCase().includes(t.toLowerCase())).map((f) => ({ ...f, sub: (f.pinned ? "Anclado · " : "Favorito · ") + (f.sub || "") }));
    if (!t) {
      html += paint("Mis lugares", mine);
      html += paint("Recientes", recent.filter((r) => !findFav(r.lat, r.lon)).slice(0, 5).map((r) => ({ ...r })));
      if (!html) html = '<div class="r-head">Escribe un lugar (municipio, pico, valle…)</div>';
    } else {
      html += paint("Mis lugares", mine);
      if (t.length >= 2) {
        box.innerHTML = html + '<div class="r-head">Buscando…</div>'; box.hidden = false;
        try {
          const res = await api(`/api/search?q=${encodeURIComponent(t)}`);
          if (q.value.trim() !== t) return;
          html += paint("Resultados", res.map((r) => ({ name: r.name, sub: [r.admin2, r.admin1, r.country].filter(Boolean).join(" · ") + (r.ele != null ? ` · ${nf.format(r.ele)} m` : ""), lat: r.lat, lon: r.lon })));
          if (!res.length) html += '<div class="r-head">Sin resultados</div>';
        } catch (e) { html += `<div class="r-head">${esc(e.message)}</div>`; }
      }
    }
    box.innerHTML = html; box.hidden = false;
  }
  const go = (it) => {
    close(); q.value = "";
    recent = [{ name: it.name, sub: it.sub?.replace(/^(Anclado|Favorito) · /, ""), lat: it.lat, lon: it.lon }, ...recent.filter((r) => favKey(r.lat, r.lon) !== favKey(it.lat, it.lon))].slice(0, 8);
    LS.set("mg-recent", recent);
    location.hash = `#/forecast/p/${it.lat.toFixed(4)},${it.lon.toFixed(4)},${encodeURIComponent(it.name)}`;
  };
  q.addEventListener("input", debounce(run, 280));
  q.addEventListener("focus", run);
  q.addEventListener("keydown", (e) => {
    const bs = $$(".r", box);
    if (e.key === "Escape") { close(); q.blur(); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); idx = (idx + (e.key === "ArrowDown" ? 1 : -1) + bs.length) % bs.length; bs.forEach((b, i) => b.classList.toggle("on", i === idx)); bs[idx]?.scrollIntoView({ block: "nearest" }); }
    else if (e.key === "Enter") { const it = items[idx >= 0 ? idx : 0]; if (it) go(it); }
  });
  box.addEventListener("mousedown", (e) => e.preventDefault());
  box.addEventListener("click", (e) => {
    const f = e.target.closest("[data-fav]");
    if (f) { const it = items[+f.dataset.fav]; toggleFav({ name: it.name, sub: it.sub?.replace(/^(Anclado|Favorito) · /, ""), lat: it.lat, lon: it.lon }); f.setAttribute("aria-pressed", String(!!findFav(it.lat, it.lon))); return; }
    const b = e.target.closest(".r"); if (b) go(items[+b.dataset.i]);
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) close(); });
}

/* ---------- navegación ---------- */
export function show(parts) {
  if (!parts.length) {
    if (matchMedia("(max-width:760px)").matches) { sel = null; ctx.pane("list"); renderPlaces(); renderFavs(); return; }
    parts = ["garos"];
  }
  const my = ++req;
  let url, item;
  if (parts[0] === "p") {
    const [lat, lon, ...n] = parts[1].split(",");
    item = { kind: "point", lat: +lat, lon: +lon, name: n.join(",") || "Punto del mapa" };
    sel = { ...item, key: favKey(item.lat, item.lon) };
    url = `/api/forecast?lat=${item.lat}&lon=${item.lon}&name=${encodeURIComponent(item.name)}`;
  } else {
    const p = places.find((x) => x.slug === parts[0]);
    const s = parts[0];
    sel = { kind: "place", slug: s, ...(p ? { lat: p.lat, lon: p.lon, name: p.name } : {}) };
    url = `/api/forecast?place=${s}`;
  }
  renderPlaces(); renderFavs(); drawPins();
  ctx.colb.innerHTML = skeleton(); ctx.colb.scrollTop = 0;
  ctx.pane("detail");
  api(url).then((d) => { if (my === req) render(d); }).catch((e) => {
    if (my !== req) return;
    ctx.colb.innerHTML = `<div class="err"><strong>No se pudo cargar el pronóstico</strong><p class="empty">${esc(e.message)}</p><button class="btn" id="retry" style="margin-top:10px">Reintentar</button></div>`;
    $("#retry").addEventListener("click", () => show(parts));
  });
}
const skeleton = () => `<div class="skeleton" style="width:40%;height:36px;margin-bottom:12px"></div><div class="skeleton" style="height:150px;border-radius:12px;margin:22px 0"></div>
  ${[92, 84, 70].map((w) => `<div class="skeleton" style="width:${w}%;height:14px;margin:12px 0"></div>`).join("")}`;
const confBars = (c) => `<span class="conf" aria-hidden="true">${[1, 2, 3].map((i) => `<i class="${i <= ({ baja: 1, media: 2, alta: 3 }[c] || 1) ? "on" : ""}"></i>`).join("")}</span>`;
const list = (title, a, cls = "", none = "") => `<div class="${cls}"><h3>${title}</h3>${a?.length ? `<ul>${a.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="none">${none}</p>`}</div>`;
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
const avg = (a) => a.reduce((x, y) => x + y, 0) / (a.length || 1);
const mx = (a) => Math.max(...a.filter((x) => x != null), 0);

function paintActions() {
  const box = $("#actions"); if (!box || !last) return;
  const f = findFav(last.lat, last.lon);
  box.innerHTML = `<button class="icon-btn" data-a="fav" aria-pressed="${!!f}" aria-label="Guardar en favoritos" title="Favorito">${ICON.star}</button>
    <button class="icon-btn" data-a="pin" aria-pressed="${!!f?.pinned}" aria-label="Anclar" title="Anclar arriba">${ICON.pin}</button>
    <button class="icon-btn" data-a="link" aria-label="Copiar enlace" title="Copiar enlace">${ICON.link}</button>
    <a class="icon-btn" href="/api/raw?lat=${last.lat}&lon=${last.lon}" target="_blank" rel="noopener" aria-label="Datos crudos JSON" title="Datos crudos (JSON)">${ICON.json}</a>`;
}

/* ---------- detalle ---------- */
function axisFor(steps, W) {
  const n = steps.length, px = (W - 46) / n, stride = px >= 30 ? 2 : px >= 15 ? 4 : 8, out = [];
  const majors = new Set();
  steps.forEach((s, i) => { if (i > 0 && s.time.slice(0, 10) !== steps[i - 1].time.slice(0, 10)) { out.push({ i, label: wd(s.time), major: true }); majors.add(i); } });
  steps.forEach((s, i) => { if (i % stride === 0 && !majors.has(i) && !majors.has(i + 1) && !majors.has(i - 1)) out.push({ i, label: hh(s.time) }); });
  const fc = [...majors][0];
  if (fc === undefined || fc > 3) out.push({ i: 0, label: wd(steps[0].time), major: true });
  return out;
}

function render(d) {
  const lug = d.lugar, p = d.pronostico, c = d.datos, T = c.tramos, la = lug.lat ?? c.ubicacion.lat, lo = lug.lon ?? c.ubicacion.lon;
  last = { lat: la, lon: lo, name: lug.name, sub: lug.area };
  sel = { ...sel, lat: la, lon: lo, name: lug.name, key: favKey(la, lo) };
  const day = T.slice(0, 8), now = T[0];
  const rain24 = day.reduce((s, x) => s + (x.lluvia_mm?.mean ?? 0), 0), gust24 = mx(day.map((x) => x.racha_kmh?.max)), wind24 = mx(day.map((x) => x.viento_kmh?.mean));
  const cloud = avg(day.map((x) => x.nubes_pct?.mean ?? 0)), iso = day.map((x) => x.isocero_m?.mean).filter((v) => v != null);
  const near = c.estaciones_cercanas?.find((s) => s.dist_km <= 15 && s.temp_c != null);
  const mountain = lug.kind === "montaña" || (c.ubicacion.altitud_m ?? 0) > 900;
  const alt = c.ubicacion.altitud_m != null ? ` · ${nf.format(c.ubicacion.altitud_m)} m` : "";
  const area = lug.area ? `${esc(lug.area)} · ${esc(lug.region)}${alt}` : `${la.toFixed(3)}°, ${lo.toFixed(3)}°${alt}`;
  const aiBadge = d.origen === "ia" ? `Lectura de IA · ${esc((d.modelo_ia || "").split("/").pop().replace(":free", ""))}` : "Resumen automático (sin IA)";
  const tint = ramp(SCALES.temp, now.temp_c.mean).replace("rgb(", "rgba(").replace(")", ",.16)");
  const press = T.map((x) => x.presion_hpa?.mean), trend = press[4] != null && press[0] != null ? (press[4] - press[0] > 1 ? "↑ subiendo" : press[4] - press[0] < -1 ? "↓ bajando" : "→ estable") : "";
  const gmin = Math.min(...c.dias.map((x) => x.temp_min_c ?? 99)), gmax = Math.max(...c.dias.map((x) => x.temp_max_c ?? -99));
  const dayRows = c.dias.map((x) => {
    const l = ((x.temp_min_c - gmin) / (gmax - gmin || 1)) * 100, w = Math.max(6, ((x.temp_max_c - x.temp_min_c) / (gmax - gmin || 1)) * 100);
    return `<div class="day"><span class="dn">${esc(wd(x.fecha))}</span>
      <div><div class="tt"><span>${t0(x.temp_min_c)}°</span><span>${t0(x.temp_max_c)}°</span></div><div class="rng"><i style="left:${l}%;width:${w}%;--c1:${ramp(SCALES.temp, x.temp_min_c)};--c2:${ramp(SCALES.temp, x.temp_max_c)}"></i></div></div>
      <span class="m">${nf1.format(x.lluvia_mm)}<small>mm</small></span><span class="m">${x.racha_max_kmh}<small>km/h</small></span></div>`;
  }).join("");

  ctx.colb.innerHTML = `
  <div class="d-head"><div><h1>${esc(lug.name)}</h1><p>${area}</p></div><div class="d-actions" id="actions"></div></div>
  <div class="badges"><span class="badge">${confBars(p.confianza)} Confianza ${esc(p.confianza)}</span><span class="badge">Actualizado ${fmtTime(d.generado)}</span><span class="badge">${aiBadge}</span></div>

  <div class="hero" style="--tint:${tint}">
    <div class="hero-temp num" aria-label="Temperatura prevista ahora">${t0(now.temp_c.mean)}<sup>°C</sup></div>
    <div class="hero-note">${near ? `Estación <b>${esc(near.id)}</b> a ${near.dist_km} km mide <b class="num">${t0(near.temp_c)} °C</b> ahora.` : "Media de 4 modelos para el tramo actual. Sin balizas cercanas con datos fiables."}</div>
    <div class="hero-now">
      ${now.viento_dir_txt ? `<span>Viento <b>${now.viento_dir_txt} ${t0(now.viento_kmh?.mean)} km/h</b></span>` : ""}
      ${now.humedad_pct ? `<span>Humedad <b>${t0(now.humedad_pct.mean)}%</b></span>` : ""}
      ${now.presion_hpa ? `<span>Presión <b>${t0(now.presion_hpa.mean)} hPa</b> ${trend}</span>` : ""}
    </div>
  </div>

  <div class="stats">
    <div class="stat"><span class="lbl">Viento máx.</span><span class="num">${t0(wind24)}<small>km/h</small></span></div>
    <div class="stat"><span class="lbl">Racha máx.</span><span class="num">${t0(gust24)}<small>km/h</small></span></div>
    <div class="stat"><span class="lbl">Lluvia 24 h</span><span class="num">${nf1.format(rain24)}<small>mm</small></span></div>
    <div class="stat"><span class="lbl">Nubosidad</span><span class="num">${t0(cloud)}<small>%</small></span></div>
    ${mountain && iso.length ? `<div class="stat"><span class="lbl">Isocero</span><span class="num">${t0(avg(iso))}<small>m</small></span></div>` : ""}
  </div>

  <section class="sec"><div class="label">Lectura <em>${aiBadge}</em></div>
    <p class="reading">${esc(p.resumen)}</p>
    <div class="cols">${list("Riesgos", p.riesgos, "risk", "Sin riesgos destacados en las próximas 48 h.")}${list("Mejores ventanas", p.ventanas, "", "No se han identificado ventanas concretas.")}${list("Recomendaciones", p.recomendaciones, "", "—")}</div>
    <div class="warn-note"><svg viewBox="0 0 24 24"><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 10v4.5M12 17.5v.01"/></svg>
      <span>Apoyo informativo, no sustituye a los avisos oficiales. Antes de salir al monte o al mar, consulta <a href="${esc(lug.warnings_url || "https://www.aemet.es/es/eltiempo/prediccion/avisos")}" target="_blank" rel="noopener">${esc(lug.warnings_label || "AEMET")}</a>.</span></div>
  </section>

  <section class="sec"><div class="label">Próximos días <em>mínima–máxima · lluvia · racha</em></div><div class="days">${dayRows}</div></section>

  <section class="sec"><div class="label">Horas y gráficos
      <span class="seg" role="group" aria-label="Horizonte"><button type="button" data-h="16" aria-pressed="${horizon === 16}">48 h</button><button type="button" data-h="40" aria-pressed="${horizon === 40}">5 días</button></span></div>
    <div class="strip" id="strip"></div><div id="charts"></div>
    <div class="legend"><span><i style="border-color:var(--accent)"></i>media de modelos</span><span><i style="border-color:var(--wind);border-top-style:dashed"></i>racha</span><span>Flechas: hacia dónde sopla</span></div></section>

  <section class="sec"><div class="label">Observaciones reales <em>balizas APRS con control de calidad</em></div>
    ${c.estaciones_cercanas?.length ? `<div class="tablewrap"><table><thead><tr><th>Estación</th><th>Dist.</th><th>°C</th><th>Viento</th><th>Racha</th><th>HR</th><th>hPa</th></tr></thead><tbody>
      ${c.estaciones_cercanas.map((s) => `<tr><td>${esc(s.id)}</td><td class="num">${s.dist_km} km</td><td class="num">${s.temp_c != null ? nf1.format(s.temp_c) : "–"}</td><td class="num">${s.viento_kmh ?? "–"}</td><td class="num">${s.racha_kmh ?? "–"}</td><td class="num">${s.humedad_pct != null ? s.humedad_pct + "%" : "–"}</td><td class="num">${s.presion_hpa != null ? nf1.format(s.presion_hpa) : "–"}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">Ninguna baliza APRS con datos fiables en 40 km. El pronóstico se apoya solo en modelos numéricos.</p>`}</section>

  <section class="sec"><div class="label">Datos de origen</div>
    <p class="empty">Cada cifra del texto se comprueba contra estos datos antes de mostrarse. Modelos: ${esc(c.modelos.join(", "))}. Los mismos datos están disponibles para tu propia IA mediante el <a href="#/learn/mcp">servidor MCP</a>.</p>
    <details><summary>Ver tabla de datos crudos</summary><div class="tablewrap"><table><thead><tr><th>Tramo</th><th>°C</th><th>Rango</th><th>Viento</th><th>Racha</th><th>mm</th><th>Conf.</th></tr></thead><tbody>
      ${T.map((s) => `<tr><td>${esc(s.time.slice(5, 10).split("-").reverse().join("/"))} ${s.time.slice(11, 13)}h</td><td class="num">${t0(s.temp_c?.mean)}</td><td class="num">${t0(s.temp_c?.min)}…${t0(s.temp_c?.max)}</td><td class="num">${s.viento_kmh?.mean ?? "–"}</td><td class="num">${s.racha_kmh?.max ?? "–"}</td><td class="num">${nf1.format(s.lluvia_mm?.mean ?? 0)}</td><td>${esc(s.confianza)}</td></tr>`).join("")}</tbody></table></div></details></section>
  <footer class="foot">Meteo Guilda combina modelos numéricos abiertos (ECMWF, DWD ICON, NOAA GFS, Météo-France) con observaciones de estaciones aficionadas. Las estaciones pueden tener errores; el sistema las filtra pero no las garantiza.</footer>`;

  paintActions();
  $("#actions").addEventListener("click", (e) => {
    const b = e.target.closest("[data-a]"); if (!b) return;
    const item = { name: last.name, sub: last.sub, lat: last.lat, lon: last.lon };
    if (b.dataset.a === "fav") toggleFav(item); else if (b.dataset.a === "pin") toggleFav(item, true);
    else navigator.clipboard?.writeText(location.href).then(() => ctx.toast("Enlace copiado"));
  });
  $$("[data-h]", ctx.colb).forEach((b) => b.addEventListener("click", () => { horizon = +b.dataset.h; $$("[data-h]", ctx.colb).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); drawCharts(T, mountain); }));
  drawCharts(T, mountain);
}

function drawCharts(all, mountain) {
  const T = all.slice(0, horizon), g = (k, f = "mean") => (i) => T[i][k]?.[f] ?? 0, hasSnow = T.some((x) => (x.nieve_cm?.mean ?? 0) > 0.05);
  renderStrip($("#strip"), T, [
    { label: "Temp °C", val: (i) => T[i].temp_c?.mean, color: (v) => ramp(SCALES.temp, v), text: (v) => t0(v) },
    { label: "Lluvia mm", val: (i) => T[i].lluvia_mm?.mean, color: (v) => ramp(SCALES.rain, v), text: (v) => (v < 0.05 ? "" : nf1.format(v)), ink: (v) => (v > 3 ? "#fff" : "#14191f") },
    { label: "Viento", val: (i) => T[i].viento_kmh?.mean, color: (v) => ramp(SCALES.wind, v), text: (v) => nf.format(v), ink: (v) => (v > 65 ? "#fff" : "#14191f") },
    { label: "Nubes %", val: (i) => T[i].nubes_pct?.mean, color: (v) => ramp(SCALES.cloud, v), text: (v) => nf.format(v) },
  ], axisFor);
  const defs = [
    { title: "Temperatura", sub: "°C · banda = rango entre modelos", draw: "band", H: 170, range: () => [Math.min(...T.map((x) => x.temp_c.min)), Math.max(...T.map((x) => x.temp_c.max))],
      mean: g("temp_c"), min: g("temp_c", "min"), max: g("temp_c", "max"), tip: (i) => `${wd(T[i].time)} ${hh(T[i].time)} · <b>${t0(T[i].temp_c.mean)} °C</b> (${t0(T[i].temp_c.min)} a ${t0(T[i].temp_c.max)})` },
    { title: "Precipitación", sub: "mm por tramo de 3 h", draw: "bars", H: 115, ticks: 2, range: () => [0, Math.max(2, ...T.map((x) => x.lluvia_mm?.mean ?? 0))], mean: g("lluvia_mm"), tip: (i) => `${wd(T[i].time)} ${hh(T[i].time)} · <b>${nf1.format(T[i].lluvia_mm?.mean ?? 0)} mm</b>` },
    ...(hasSnow ? [{ title: "Nieve", sub: "cm por tramo de 3 h", draw: "bars", cls: "bar snow", H: 100, ticks: 2, range: () => [0, Math.max(1, ...T.map((x) => x.nieve_cm?.mean ?? 0))], mean: g("nieve_cm"), tip: (i) => `${wd(T[i].time)} ${hh(T[i].time)} · <b>${nf1.format(T[i].nieve_cm?.mean ?? 0)} cm</b>` }] : []),
    { title: "Viento y rachas", sub: "km/h", draw: "wind", H: 160, range: () => [0, Math.max(20, ...T.map((x) => x.racha_kmh?.max ?? 0))], mean: g("viento_kmh"), gust: g("racha_kmh", "max"), dir: (i) => T[i].viento_dir,
      tip: (i) => `${wd(T[i].time)} ${hh(T[i].time)} · <b>${t0(T[i].viento_kmh?.mean)}</b> km/h ${T[i].viento_dir_txt || ""} · racha <b>${t0(T[i].racha_kmh?.max)}</b>` },
    { title: "Nubosidad", sub: "%", draw: "area", H: 110, ticks: 2, range: () => [0, 100], mean: g("nubes_pct"), tip: (i) => `${wd(T[i].time)} ${hh(T[i].time)} · <b>${t0(T[i].nubes_pct?.mean)}%</b>` },
    ...(T.some((x) => x.presion_hpa) ? [{ title: "Presión", sub: "hPa", draw: "line", H: 120, ticks: 3, range: () => { const v = T.map((x) => x.presion_hpa?.mean).filter(Boolean); return [Math.min(...v) - 2, Math.max(...v) + 2]; }, mean: g("presion_hpa"), tip: (i) => `${wd(T[i].time)} ${hh(T[i].time)} · <b>${t0(T[i].presion_hpa?.mean)} hPa</b>` }] : []),
    ...(mountain && T.some((x) => x.isocero_m) ? [{ title: "Isocero", sub: "m · altitud de la isoterma 0 °C", draw: "line", H: 120, ticks: 3, range: () => { const v = T.map((x) => x.isocero_m?.mean).filter((x) => x != null); return [Math.min(...v) - 100, Math.max(...v) + 100]; }, mean: g("isocero_m"), tip: (i) => `${wd(T[i].time)} ${hh(T[i].time)} · <b>${nf.format(T[i].isocero_m?.mean)} m</b>` }] : []),
  ];
  renderCharts($("#charts"), T, defs, axisFor);
}
