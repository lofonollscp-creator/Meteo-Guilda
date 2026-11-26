import { $, $$, api, esc, nf, nf1, t0, LS, debounce, compass, wd, hh, slug, distKm } from "./util.js";
import { renderCharts, renderStrip, renderModels, MODEL_COLORS, sparkline, ramp, SCALES, renderRose, moonSvg, sunArc } from "./charts.js";
import { icon } from "./icons.js";
import { pushSettings } from "./sync.js";
import { aiOn, onChange as onMe, me } from "./me.js";
import { getQth, hasQth, label as qthLabel, hashOf as qthHash, locate, clearQth, onQth, ensureName } from "./qth.js";

let ctx, places = [], favs = LS.get("mg-favs", []), recent = LS.get("mg-recent", []), snaps = {}, sel = null, req = 0, horizon = 16, last = null, lastUrl = "", lastData = null, mvar = "temp_c", hiddenModels = new Set(), offMe = null, offQth = null, locating = false;
const ICON = { star: icon("star", 18), pin: icon("pin", 18), link: icon("link", 18), json: icon("code", 18) };
const saveFavs = () => { LS.set("mg-favs", favs); pushSettings(); };
const favKey = (lat, lon) => slug(lat, lon);
const findFav = (lat, lon) => favs.find((f) => f.key === favKey(lat, lon));
const MODEL_NAMES = { ecmwf_ifs025: "ECMWF IFS", ecmwf_aifs025_single: "ECMWF AIFS (IA)", icon_seamless: "DWD ICON", gfs_seamless: "NOAA GFS", meteofrance_seamless: "Météo-France",
  gem_seamless: "ECCC GEM", ukmo_seamless: "UK Met Office", knmi_seamless: "KNMI", dmi_seamless: "DMI", metno_seamless: "MET Noruega", meteoswiss_icon_seamless: "MeteoSwiss", italia_meteo_arpae_icon_2i: "ARPAE (Italia)", jma_seamless: "JMA" };
// Orden de «Mis lugares»: manual (el que dejas con ↑↓), A–Z, más cálido ahora o más cerca de tu QTH. Los fijados van siempre arriba.
const SORTS = [["manual", "Mi orden"], ["az", "A–Z"], ["temp", "Más cálido"], ["cerca", "Más cerca"]];
let favSort = LS.get("mg-favsort", "manual"), favEdit = false;
const favRank = (a, b) => {
  if (favSort === "az") return a.name.localeCompare(b.name, "es");
  if (favSort === "temp") return (snaps[b.key]?.temp_c ?? -999) - (snaps[a.key]?.temp_c ?? -999);
  const q = getQth();
  if (favSort === "cerca" && q?.lat != null) return distKm(q, a) - distKm(q, b);
  return 0;                                                        // manual: se respeta el orden del array (sort estable)
};
const sortedFavs = () => [...favs].sort((a, b) => (b.pinned | 0) - (a.pinned | 0) || favRank(a, b));
function moveFav(key, dir) {
  const list = sortedFavs(), i = list.findIndex((f) => f.key === key), j = i + dir;
  if (i < 0 || j < 0 || j >= list.length || !!list[i].pinned !== !!list[j].pinned) return;   // no cruza la frontera de los fijados
  [list[i], list[j]] = [list[j], list[i]];
  favs = list; saveFavs(); renderFavs();
}

export function mount(c) {
  ctx = c;
  ctx.cola.innerHTML = `
    ${ctx.brand("forecast")}
    <div class="search">${icon("search", 16)}
      <input id="q" type="search" placeholder="Buscar cualquier lugar…" autocomplete="off" aria-label="Buscar lugar"><kbd>/</kbd>
      <div class="results" id="results" hidden></div></div>
    <a class="group-label home-link" href="#/forecast">Inicio${icon("home", 14)}</a>
    <div id="myloc"></div>
    <div id="favs"></div>
    <a class="group-label home-link" href="#/forecast/comparar" id="cmp-link">Comparar lugares</a>
    <div class="group-label">Lugares principales</div>
    <ul class="places" id="places" role="list"></ul>
    <p class="cola-note">Resumen de la media de 8 modelos numéricos. Los lugares guardados quedan en este dispositivo.</p>`;
  ctx.stations(true);
  ctx.place = () => (last ? { lat: last.lat, lon: last.lon, name: last.name } : null);
  offMe = onMe(() => { if (lastData && $("#reading")) paintReading(lastData); });
  document.addEventListener("mg:recent", reloadRecent);
  ctx.onMapClick = ({ lat, lng }) => { location.hash = `#/forecast/p/${lat.toFixed(4)},${lng.toFixed(4)},Punto del mapa`; };
  ctx.mapCtl('<button class="chip" id="fit" type="button">Ver los 6 lugares</button><span class="chip chip-ghost">Toca el mapa para consultar un punto</span>');
  $("#fit").addEventListener("click", () => ctx.fit(places.map((p) => [p.lon, p.lat])));
  $("#places").addEventListener("click", (e) => { const b = e.target.closest("[data-go]"); if (b) location.hash = b.dataset.go; });
  $("#favs").addEventListener("click", (e) => {
    const m = e.target.closest("[data-mv]"); if (m) return moveFav(m.dataset.mv, +m.dataset.d);
    if (e.target.closest("#favedit")) { favEdit = !favEdit; return renderFavs(); }
    const b = e.target.closest("[data-go]"); if (b) location.hash = b.dataset.go;
  });
  $("#favs").addEventListener("change", (e) => { if (e.target.id === "favsort") { favSort = e.target.value; LS.set("mg-favsort", favSort); if (favSort !== "manual") favEdit = false; renderFavs(); } });
  setupSearch();
  $(".home-link").addEventListener("click", (e) => { e.preventDefault(); history.replaceState(null, "", "#/forecast"); home(); });
  renderPlaces(); renderFavs(); renderMyLoc(); loadHomeSnap(); ensureName();
  offQth = onQth(() => { renderMyLoc(); loadHomeSnap(); drawPins(); });
  $("#myloc").addEventListener("click", myLocClick);
  api("/api/places").then((p) => { places = p; renderPlaces(); drawPins(); if (!sel) ctx.fit(places.map((x) => [x.lon, x.lat])); }).catch(() => {});
  loadFavSnaps();
}
const reloadRecent = () => { recent = LS.get("mg-recent", []); };
export function unmount() { offQth?.(); ctx.stations(false); ctx.place = () => null; offMe?.(); document.removeEventListener("mg:recent", reloadRecent); }

/* ---------- listas ---------- */
function placeRow(go, name, area, snap, { current, pinned } = {}) {
  const n = snap;
  return `<li><button class="place" data-go="${esc(go)}" aria-current="${!!current}">
    <span class="place-name">${pinned ? icon("pin", 12, "pinned") : ""}${esc(name)}</span>
    <span class="place-temp"><span class="num">${n ? t0(n.temp_c) + "°" : '<span class="skeleton">00°</span>'}</span></span>
    <span class="place-area">${esc(area)}</span>
    <span class="place-meta">${n ? sparkline(n.spark) + `<span class="num">racha ${n.gust_kmh} · lluvia ${nf1.format(n.rain_24h_mm)}</span>` : ""}</span></button></li>`;
}
function renderPlaces() {
  $("#places").innerHTML = places.length ? places.map((p) => placeRow(`#/forecast/${p.slug}`, p.name, p.area, p.now, { current: sel?.slug === p.slug })).join("")
    : Array.from({ length: 6 }, (_, i) => `<li><button class="place"><span class="place-name skeleton">Cargando lugar</span><span class="place-area skeleton">Comarca</span></button></li>`).join("");
}
/* ---------- Mi ubicación ---------- */
const locBtn = (cls = "btn ghost sm") => `<button class="${cls}" type="button" data-loc="go">${icon("pin", 15)} ${hasQth() ? "Actualizar posición" : "Usar mi ubicación"}</button>`;
function renderMyLoc() {
  const box = $("#myloc"); if (!box) return;
  const q = getQth();
  if (q?.lat == null) {
    box.innerHTML = `<div class="myloc-cta"><button class="btn" type="button" data-loc="go">${icon("pin", 16)} Usar mi ubicación</button><p>Previsión, aire, actividades y asistente con los datos de donde estás.</p><p class="empty" id="loc-msg" role="status"></p></div>`;
    return;
  }
  const cur = sel && Math.abs(sel.lat - q.lat) < 0.002 && Math.abs(sel.lon - q.lon) < 0.002;
  box.innerHTML = `<div class="group-label">Mi ubicación</div><ul class="places">${placeRow(qthHash(q), qthLabel(q), q.detalle || (q.locator ? `Locator ${q.locator}` : ""), snaps.__home, { current: cur, pinned: true })}</ul>
    <div class="myloc-act">${locBtn()}<button class="btn ghost sm" type="button" data-loc="clear">Quitar</button></div><p class="empty" id="loc-msg" role="status"></p>`;
}
async function myLocClick(e) {
  const b = e.target.closest("[data-loc]"), go = e.target.closest("[data-go]");
  if (go) { location.hash = go.dataset.go; return; }
  if (!b) return;
  if (b.dataset.loc === "clear") { clearQth(); snaps.__home = null; ctx.toast("Ubicación quitada"); return; }
  await useMyLocation();
}
export async function useMyLocation(open = false) {
  if (locating) return;
  locating = true;
  const msg = (t) => { const m = $("#loc-msg"); if (m) m.textContent = t; };
  document.querySelectorAll("[data-loc=go]").forEach((x) => x.setAttribute("disabled", ""));
  msg("Buscando tu posición…");
  try { const q = await locate(); ctx.toast("Ubicación actualizada"); msg(""); if (open) location.hash = qthHash(q); }
  catch (e) { msg(e.message); ctx.toast(e.message.split(".")[0]); }
  finally { locating = false; document.querySelectorAll("[data-loc=go]").forEach((x) => x.removeAttribute("disabled")); }
}
async function loadHomeSnap() {
  const q = getQth(); if (q?.lat == null) return;
  try { const r = await api(`/api/snapshot?pts=${q.lat},${q.lon}`); snaps.__home = r[0]?.now; renderMyLoc(); } catch {}
}

function renderFavs() {
  const f = sortedFavs();
  const manual = favSort === "manual", move = (x, i) => (favEdit && manual ? `<span class="reord"><button type="button" data-mv="${esc(x.key)}" data-d="-1" aria-label="Subir ${esc(x.name)}"${i === 0 || !!f[i - 1].pinned !== !!x.pinned ? " disabled" : ""}>↑</button><button type="button" data-mv="${esc(x.key)}" data-d="1" aria-label="Bajar ${esc(x.name)}"${i === f.length - 1 || !!f[i + 1].pinned !== !!x.pinned ? " disabled" : ""}>↓</button></span>` : "");
  $("#favs").innerHTML = f.length ? `<div class="group-label">Mis lugares${f.length > 1 ? `<span class="favtools"><select id="favsort" aria-label="Ordenar mis lugares">${SORTS.map(([v, t]) => `<option value="${v}"${v === favSort ? " selected" : ""}>${t}</option>`).join("")}</select>${manual ? `<button type="button" class="chip" id="favedit" aria-pressed="${favEdit}">${favEdit ? "Listo" : "Mover"}</button>` : ""}</span>` : ""}</div>
    ${favSort === "cerca" && getQth()?.lat == null ? '<p class="empty" style="padding:0 18px">Define tu QTH para ordenar por cercanía.</p>' : ""}<ul class="places">${f.map((x, i) =>
    placeRow(favHash(x), x.name, x.sub || "", snaps[x.key], { current: sel?.key === x.key, pinned: x.pinned }).replace(/<\/li>$/, `${move(x, i)}</li>`)).join("")}</ul>` : "";
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
  if (hasQth()) ctx.marker(getQth().lon, getQth().lat, { label: qthLabel(), cls: "fav", onClick: () => (location.hash = qthHash()) });
  if (sel?.kind === "point" && !findFav(sel.lat, sel.lon)) ctx.marker(sel.lon, sel.lat, { label: sel.name, cls: "pt on" });
}
// el asistente guarda lugares con un evento (confirmado antes por la persona con un botón)
addEventListener("mg:fav", (e) => {
  const it = e.detail; if (!it || it.lat == null || findFav(it.lat, it.lon)) return;
  favs.push({ key: favKey(it.lat, it.lon), name: it.name, sub: it.sub || "", lat: it.lat, lon: it.lon });
  saveFavs(); try { renderFavs(); drawPins(); loadFavSnaps(); } catch {}
});
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
  const row = (it) => `<button class="r" data-i="${items.indexOf(it)}" type="button"><span class="grow"><b>${esc(it.name)}</b><small>${esc(it.sub || "")}</small></span>
    <span class="icon-btn" data-fav="${items.indexOf(it)}" role="button" aria-label="Guardar en favoritos" aria-pressed="${!!findFav(it.lat, it.lon)}">${ICON.star}</span>
    ${it.recent ? `<span class="icon-btn" data-del="${favKey(it.lat, it.lon)}" role="button" aria-label="Quitar de recientes" title="Quitar de recientes">${icon("x", 15)}</span>` : ""}</button>`;
  const paint = (title, list, more = "", head = "") => { items.push(...list); return list.length ? `<div class="r-head"><span>${title}</span>${head}</div>` + list.map((it) => row(it)).join("") + more : more; };
  async function run() {
    const t = q.value.trim(); items = [];
    let html = "";
    if (t.length >= 3 && aiOn()) { const it = { ai: true, q: t }; items.push(it); html += `<button class="r r-ai" data-i="0" type="button"><span class="ic2">${icon("spark", 16)}</span><span class="grow"><b>Preguntar a Guilda: «${esc(t.length > 48 ? t.slice(0, 46) + "…" : t)}»</b><small>Respuesta con datos reales, cifra a cifra</small></span></button>`; }
    const here = hasQth() ? [{ name: qthLabel(), sub: "Mi ubicación" + (getQth().locator ? ` · ${getQth().locator}` : ""), lat: getQth().lat, lon: getQth().lon, mine: true }] : [];
    const mine = [...(t && !"mi ubicación".includes(t.toLowerCase()) ? [] : here), ...sortedFavs().filter((f) => !t || f.name.toLowerCase().includes(t.toLowerCase())).map((f) => ({ ...f, sub: (f.pinned ? "Anclado · " : "Favorito · ") + (f.sub || "") }))];
    if (!t) {
      html += paint("Mis lugares", mine);
      html += paint("Recientes", recent.filter((r) => !findFav(r.lat, r.lon)).slice(0, 5).map((r) => ({ ...r, recent: true })), "", '<button type="button" data-clear>Borrar todo</button>');
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
    if (it.ai) { q.blur(); ctx.ask?.(it.q); return; }
    if (it.mine) { location.hash = qthHash(); return; }   // «Mi ubicación» no se guarda en recientes
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
    else if (e.key === "Enter") { const it = idx >= 0 ? items[idx] : (e.ctrlKey || e.metaKey ? items.find((i) => i.ai) : items.find((i) => !i.ai)) || items[0]; if (it) go(it); }
  });
  box.addEventListener("mousedown", (e) => e.preventDefault());
  box.addEventListener("click", (e) => {
    const dl = e.target.closest("[data-del]");
    if (dl) { e.stopPropagation(); recent = recent.filter((r) => favKey(r.lat, r.lon) !== dl.dataset.del); LS.set("mg-recent", recent); run(); return; }
    if (e.target.closest("[data-clear]")) { e.stopPropagation(); recent = []; LS.set("mg-recent", recent); run(); return; }
    const f = e.target.closest("[data-fav]");
    if (f) { const it = items[+f.dataset.fav]; toggleFav({ name: it.name, sub: it.sub?.replace(/^(Anclado|Favorito) · /, ""), lat: it.lat, lon: it.lon }); f.setAttribute("aria-pressed", String(!!findFav(it.lat, it.lon))); return; }
    const b = e.target.closest(".r"); if (b) go(items[+b.dataset.i]);
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) close(); });
}

/* ---------- navegación ---------- */
export function show(parts) {
  if (!parts.length) {
    if (matchMedia("(max-width:760px)").matches) { sel = null; ctx.pane("list"); renderPlaces(); renderFavs(); return home(true); }   // «Detalle» muestra Hoy, nunca vacío
    return home();
  }
  if (parts[0] === "comparar") { sel = null; renderPlaces(); renderFavs(); ctx.pane("detail"); return compararPage(); }
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
  lastUrl = url;
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
    <button class="icon-btn" data-a="refresh" aria-label="Actualizar datos" title="Actualizar datos (vuelve a consultar los modelos)">${icon("refresh", 18)}</button>
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
  const q0m = getQth(), isMine = q0m?.lat != null && Math.abs(q0m.lat - la) < 0.002 && Math.abs(q0m.lon - lo) < 0.002;
  const area = isMine ? `Mi ubicación${q0m.locator ? ` · ${esc(q0m.locator)}` : ""}${alt}` : lug.area ? `${esc(lug.area)} · ${esc(lug.region)}${alt}` : `${la.toFixed(3)}°, ${lo.toFixed(3)}°${alt}`;
  lastData = d; dayZoom = null;
  const nm = c.modelos.length, srcs = [...new Set((c.estaciones_cercanas || []).map((s) => s.fuente || "APRS"))];
  const tint = ramp(SCALES.temp, now.temp_c.mean).replace("rgb(", "rgba(").replace(")", ",.16)");
  const press = T.map((x) => x.presion_hpa?.mean), trend = press[4] != null && press[0] != null ? (press[4] - press[0] > 1 ? `${icon("up", 13)} subiendo` : press[4] - press[0] < -1 ? `${icon("down", 13)} bajando` : `${icon("right", 13)} estable`) : "";
  const gmin = Math.min(...c.dias.map((x) => x.temp_min_c ?? 99)), gmax = Math.max(...c.dias.map((x) => x.temp_max_c ?? -99));
  const dayRows = c.dias.map((x) => {
    const l = ((x.temp_min_c - gmin) / (gmax - gmin || 1)) * 100, w = Math.max(6, ((x.temp_max_c - x.temp_min_c) / (gmax - gmin || 1)) * 100);
    return `<div class="day"><span class="dn">${esc(wd(x.fecha))}</span>
      <div><div class="tt"><span>${t0(x.temp_min_c)}°</span><span>${t0(x.temp_max_c)}°</span></div><div class="rng"><i style="left:${l}%;width:${w}%;--c1:${ramp(SCALES.temp, x.temp_min_c)};--c2:${ramp(SCALES.temp, x.temp_max_c)}"></i></div></div>
      <span class="m">${nf1.format(x.lluvia_mm)}<small>mm</small></span><span class="m">${x.racha_max_kmh}<small>km/h</small></span></div>`;
  }).join("");

  ctx.colb.innerHTML = `
  <div class="d-head"><div><h1>${esc(lug.name)}</h1><p>${area}</p></div><div class="d-actions" id="actions"></div></div>
  <div class="badges"><span class="badge">${confBars(p.confianza)} Confianza ${esc(p.confianza)}</span><span class="badge">Actualizado ${fmtTime(d.generado)}</span><span class="badge">${nm} modelos</span><span class="badge" id="aibadge"></span></div>
  <div id="avisos" class="avisos" aria-live="polite"></div>

  <div class="hero" style="--tint:${tint}">
    <div class="hero-temp num" aria-label="Temperatura prevista ahora">${t0(now.temp_c.mean)}<sup>°C</sup></div>
    <div class="hero-note">${near ? `Estación <b>${esc(near.nombre || near.id)}</b> (${esc(near.fuente || "APRS")}) a ${near.dist_km} km mide <b class="num">${t0(near.temp_c)} °C</b> ahora.` : `Media de ${nm} modelos para el tramo actual. Sin estaciones cercanas con datos fiables.`}</div>
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

  <section class="sec" id="reading-sec"><div class="label">Lectura <em id="ailabel"></em></div>
    <div id="reading"></div>
    <div id="rcols"></div>${aiOn() ? `<div class="row" style="margin-top:18px;gap:10px"><button class="btn ai sm" type="button" data-askabout>${icon("spark", 14)} Preguntar sobre esto</button></div>` : ""}
    <div class="warn-note"><svg viewBox="0 0 24 24"><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 10v4.5M12 17.5v.01"/></svg>
      <span>Apoyo informativo, no sustituye a los avisos oficiales. Antes de salir al monte o al mar, consulta <a href="${esc(lug.warnings_url || "https://www.aemet.es/es/eltiempo/prediccion/avisos")}" target="_blank" rel="noopener">${esc(lug.warnings_label || "AEMET")}</a>.</span></div>
  </section>

  <section class="sec"><div class="label">Próximos días <em>mínima–máxima · lluvia · racha</em></div><div class="days">${dayRows}</div></section>
  <section class="sec" id="oficial" hidden></section>

  <section class="sec"><div class="label">Horas y gráficos
      <span class="seg" role="group" aria-label="Horizonte"><button type="button" data-h="16" aria-pressed="${horizon === 16}">48 h</button><button type="button" data-h="40" aria-pressed="${horizon === 40}">5 días</button></span></div>
    <div class="seg dz" id="dz" role="group" aria-label="Ampliar un día">${dayZoomBtns(c.dias)}</div>
    <div class="strip" id="strip"></div><div id="charts"></div>
    <div class="legend"><span><i style="border-color:var(--accent)"></i>media de modelos</span><span><i style="border-color:var(--wind);border-top-style:dashed"></i>racha</span><span>Flechas: hacia dónde sopla</span></div></section>

  <section class="sec" id="act-sec"><div class="label">Mejor momento para… <em>según la previsión</em></div>
    <div class="seg actseg" id="actsel" role="group" aria-label="Actividad"></div><div id="actout"><p class="empty">Cargando…</p></div></section>

  <section class="sec" id="air-sec"><div class="label">Aire, UV y polen <em>estimación del modelo CAMS</em></div><div id="airbox"><p class="empty">Cargando…</p></div></section>

  ${mountain ? `<section class="sec" id="mon-sec"><div class="label">Montaña <em>próximas 48 h</em></div>${mountainCard(T.slice(0, 16), c.ubicacion.altitud_m)}</section>` : ""}

  <section class="sec" id="mar-sec" hidden><div class="label">Mar <em>oleaje y agua · modelo marino</em></div><div id="marbox"></div></section>

  <section class="sec"><div class="label">Contraste de modelos <em>¿coinciden?</em>
      <span class="seg" role="group" aria-label="Variable">${[["temp_c", "Temp"], ["lluvia_mm", "Lluvia"], ["viento_kmh", "Viento"], ["racha_kmh", "Racha"], ["nubes_pct", "Nubes"]]
        .filter(([k]) => c.por_modelo?.[k]).map(([k, l]) => `<button type="button" data-mv="${k}" aria-pressed="${mvar === k}">${l}</button>`).join("")}</span></div>
    <p class="empty" id="mnote"></p><div class="mchips" id="mchips"></div><div id="mchart" class="chart"></div></section>

  <section class="sec"><div class="label">Viento y cielo</div><div class="grid2">
    <div class="card rosebox"><h3>${icon("compass", 16)} Rosa de los vientos <span class="sub">próximos 5 días</span></h3><div id="rose"></div><p class="empty" style="font-size:12px;margin-top:6px">Cada pétalo: de dónde sopla y cuánto tiempo; color = velocidad media.</p></div>
    <div class="card sunbox" id="skybox"><h3>${icon("sun", 16)} Sol y Luna</h3><div class="skeleton" style="height:100px"></div></div></div></section>

  <section class="sec"><div class="label">Observaciones reales <em>${srcs.length ? esc(srcs.join(" · ")) : "APRS · METAR · XEMA"}</em></div>
    ${c.contraste ? `<div class="contr ${esc(c.contraste.acuerdo)}"><span class="dot"></span><div><b>${c.contraste.acuerdo === "alto" ? "Las estaciones coinciden con los modelos" : c.contraste.acuerdo === "medio" ? "Ligera desviación entre estaciones y modelos" : "Las estaciones discrepan de los modelos"}</b>
      <small>${[c.contraste.temp && `Temperatura: ${c.contraste.temp.obs}° medidos · ${c.contraste.temp.modelo}° previstos`, c.contraste.viento && `viento: ${c.contraste.viento.obs} · ${c.contraste.viento.modelo} km/h`].filter(Boolean).join(" · ")} · ${c.contraste.estaciones} ${c.contraste.estaciones === 1 ? "estación" : "estaciones"} a ≤ 30 km. ${esc(c.contraste.nota)}</small></div></div>` : ""}
    ${c.estaciones_cercanas?.length ? `<div class="tablewrap"><table><thead><tr><th>Estación</th><th>Fuente</th><th>Dist.</th><th>°C</th><th>Viento</th><th>Racha</th><th>HR</th><th>hPa</th><th>Lluvia 1 h</th></tr></thead><tbody>
      ${c.estaciones_cercanas.map((s) => `<tr><td>${esc(s.nombre || s.id)}${s.hora ? `<br><small class="num">${esc(new Date(s.hora).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }))}</small>` : ""}</td><td>${esc(s.fuente || "APRS")}</td><td class="num">${s.dist_km} km</td><td class="num">${s.temp_c != null ? nf1.format(s.temp_c) : "–"}</td><td class="num">${s.viento_kmh ?? "–"}</td><td class="num">${s.racha_kmh ?? "–"}</td><td class="num">${s.humedad_pct != null ? s.humedad_pct + "%" : "–"}</td><td class="num">${s.presion_hpa != null ? nf1.format(s.presion_hpa) : "–"}</td><td class="num">${s.lluvia_1h_mm != null ? nf1.format(s.lluvia_1h_mm) + " mm" : "–"}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">Ninguna estación (APRS, METAR o XEMA) con datos fiables en 40 km. El pronóstico se apoya solo en modelos numéricos.</p>`}</section>

  <section class="sec"><div class="label">Datos de origen</div>
    <p class="empty">Cada cifra del texto se comprueba contra estos datos antes de mostrarse. Modelos (${nm}): ${esc(c.modelos.map((m) => MODEL_NAMES[m] || m).join(", "))}. Los mismos datos están disponibles para tu propia IA mediante el <a href="#/learn/mcp">servidor MCP</a>.</p>
    <details><summary>Ver tabla de datos crudos</summary><div class="tablewrap"><table><thead><tr><th>Tramo</th><th>°C</th><th>Rango</th><th>Viento</th><th>Racha</th><th>mm</th><th>Conf.</th></tr></thead><tbody>
      ${T.map((s) => `<tr><td>${esc(s.time.slice(5, 10).split("-").reverse().join("/"))} ${s.time.slice(11, 13)}h</td><td class="num">${t0(s.temp_c?.mean)}</td><td class="num">${t0(s.temp_c?.min)}…${t0(s.temp_c?.max)}</td><td class="num">${s.viento_kmh?.mean ?? "–"}</td><td class="num">${s.racha_kmh?.max ?? "–"}</td><td class="num">${nf1.format(s.lluvia_mm?.mean ?? 0)}</td><td>${esc(s.confianza)}</td></tr>`).join("")}</tbody></table></div></details></section>
  <footer class="foot">Meteo Guilda combina ${nm} modelos numéricos abiertos (ECMWF, DWD ICON, NOAA GFS, Météo-France, ECCC GEM, UK Met Office, KNMI) con observaciones de balizas APRS, METAR de aeropuertos y estaciones XEMA del Meteocat. Las estaciones pueden tener errores; el sistema filtra las APRS pero no las garantiza.</footer>`;

  paintActions();
  $("#actions").addEventListener("click", (e) => {
    const b = e.target.closest("[data-a]"); if (!b) return;
    const item = { name: last.name, sub: last.sub, lat: last.lat, lon: last.lon };
    if (b.dataset.a === "fav") toggleFav(item); else if (b.dataset.a === "pin") toggleFav(item, true);
    else if (b.dataset.a === "refresh") refreshNow(b);
    else navigator.clipboard?.writeText(location.href).then(() => ctx.toast("Enlace copiado"));
  });
  $$("[data-z]", ctx.colb).forEach((b) => b.addEventListener("click", () => { dayZoom = b.dataset.z || null; $$("[data-z]", ctx.colb).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); drawCharts(T, mountain); }));
  $$("[data-h]", ctx.colb).forEach((b) => b.addEventListener("click", () => { horizon = +b.dataset.h; dayZoom = null; $$("[data-z]", ctx.colb).forEach((x) => x.setAttribute("aria-pressed", String(!x.dataset.z))); $$("[data-h]", ctx.colb).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); drawCharts(T, mountain); drawModels(c); }));
  drawCharts(T, mountain);
  drawModels(c);
  loadAvisos(la, lo, req);
  loadOficial(la, lo, req, c.dias);
  lazyLoad("#act-sec", () => loadActivities(la, lo));
  lazyLoad("#air-sec", () => loadAir(la, lo));
  loadMar(la, lo);
  $$("[data-mv]", ctx.colb).forEach((b) => b.addEventListener("click", () => { mvar = b.dataset.mv; $$("[data-mv]", ctx.colb).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); drawModels(c); }));
  paintReading(d);
  $("[data-askabout]")?.addEventListener("click", () => ctx.ask?.(`¿Qué debo tener en cuenta en ${lug.name} en los próximos días?`));
  renderRose($("#rose"), T);
  api(`/api/astro?lat=${la}&lon=${lo}`).then((a) => {
    const box = $("#skybox"); if (!box) return;
    const sr = a.orto && new Date(a.orto), ss = a.ocaso && new Date(a.ocaso), t = (x) => (x ? new Date(x).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }) : "–");
    box.innerHTML = `<h3>${icon("sun", 16)} Sol y Luna</h3>${sr && ss ? sunArc(sr, ss) : ""}
      <div class="kv"><span>Orto / ocaso</span><span>${t(a.orto)} / ${t(a.ocaso)}</span></div><div class="kv"><span>Horas de luz</span><span>${a.horas_de_luz} h</span></div>
      <div class="kv"><span>Crepúsculo civil</span><span>${t(a.crepusculo_civil_inicio)} – ${t(a.crepusculo_civil_fin)}</span></div>
      <div class="row" style="margin-top:10px;gap:12px">${moonSvg(a.luna.edad_dias, 46)}<span style="font-size:13px">${esc(a.luna.fase)}<br><span style="color:var(--ink-3)">${a.luna.iluminacion_pct}% iluminada</span></span></div>
      <p class="empty" style="font-size:12px;margin-top:6px">Horas de tu dispositivo.</p>`;
  }).catch(() => {});
}

/* ---------- lectura: resumen por reglas al instante; la IA llega aparte y no bloquea nada ---------- */
const readingBody = (p) => `<p class="reading">${esc(p.resumen)}</p>`;
const readingCols = (p) => `<div class="cols">${list("Riesgos", p.riesgos, "risk", "Sin riesgos destacados en las próximas 48 h.")}${list("Mejores ventanas", p.ventanas, "", "No se han identificado ventanas concretas.")}${list("Recomendaciones", p.recomendaciones, "", "")}</div>`;
let aiSeq = 0;
function setReading(p, label, note = "") {
  $("#reading").innerHTML = readingBody(p) + note;
  $("#rcols").innerHTML = readingCols(p);
  $("#ailabel").textContent = label; $("#aibadge").textContent = label;
}
/** Lectura de IA A PETICIÓN: generarla gasta tokens, así que nunca se hace sola. Si ya hay una vigente (caché de 3 h) se muestra gratis. */
async function paintReading(d) {
  if (!$("#reading")) return;
  const rules = d.pronostico, my = ++aiSeq;
  const q = d.lugar.slug ? `place=${d.lugar.slug}` : `lat=${d.lugar.lat ?? d.datos.ubicacion.lat}&lon=${d.lugar.lon ?? d.datos.ubicacion.lon}`;   // coordenadas pedidas, no las de la rejilla del modelo (misma caché)
  const openSet = () => document.getElementById("acctbtn").click();
  if (!aiOn()) { setReading(rules, "Resumen automático (IA desactivada)", '<p class="ai-note">La IA interna está desactivada. <button class="btn ghost sm" type="button" id="ai-enable">Activar en ajustes</button></p>'); $("#ai-enable")?.addEventListener("click", openSet); return; }
  const own = me.data && me.data.provider !== "server";
  const offer = (msg = "") => {
    setReading(rules, "Resumen automático", `<p class="ai-note">${msg}<button class="btn sm" type="button" id="ai-gen">${icon("chat", 15)} Generar lectura con IA</button>
      <span>${own ? "Usa tu propia clave: consume tokens de tu cuenta." : "Usa la IA del servidor (modelos gratuitos)."}</span></p>`);
    $("#ai-gen")?.addEventListener("click", generate);
  };
  async function generate() {
    const my2 = ++aiSeq;
    setReading(rules, "Resumen automático", '<p class="ai-wait"><i></i>La IA está redactando la lectura…</p>');
    try {
      const r = await api(`/api/forecast/ai?${q}`);
      if (my2 !== aiSeq || !$("#reading")) return;
      if (r.origen === "ia") setReading(r.pronostico, `Lectura de IA · ${(r.modelo_ia || "").split("/").pop().replace(":free", "")}`);
      else if (r.origen === "sin_ia") { setReading(rules, "Resumen automático (sin IA)", '<p class="ai-note">El servidor no tiene IA configurada. Puedes usar tu propia clave en <button class="btn ghost sm" type="button" id="ai-enable">Ajustes</button>.</p>'); $("#ai-enable")?.addEventListener("click", openSet); }
      else offer(`${esc(r.aviso || "La IA no ha respondido ahora.")} `);
    } catch (e) { if (my2 === aiSeq && $("#reading")) offer(`No se pudo obtener la lectura (${esc(e.message)}). `); }
  }
  offer();                                                           // por defecto: resumen por reglas + botón
  try {                                                              // una lectura ya hecha y vigente se enseña sin gastar nada
    const r = await api(`/api/forecast/ai?${q}&solo_cache=1`);
    if (my === aiSeq && r.origen === "ia" && $("#reading")) setReading(r.pronostico, `Lectura de IA · ${(r.modelo_ia || "").split("/").pop().replace(":free", "")}`);
  } catch {}
}

/* ---------- actividades y aire: se piden cuando la sección entra en pantalla ---------- */
/* ---------- avisos oficiales (AEMET/Meteoalerta): la capa de seguridad, siempre antes que cualquier lectura ---------- */
/* «Tu día, según Guilda»: tres avisos útiles con datos reales (aviso oficial, mejor ventana para caminar y próximo paso de satélite); cada uno lleva a una pregunta */
async function loadInsights(at, my) {
  const box = $("#insights"); if (!box) return;
  const q = `lat=${at.lat}&lon=${at.lon}`, nm = at.name || places[0]?.name || "";
  const [av, act, sat, fc] = await Promise.all([api(`/api/avisos?${q}`).catch(() => null), api(`/api/activity?activity=senderismo&${q}`).catch(() => null), api(`/api/satellites/passes?${q}&hours=24&min_el=15`).catch(() => null), api(`/api/forecast?${q}`).catch(() => null)]);
  if (my !== req || !$("#insights")) return;
  const dia = (iso) => new Date(iso).toLocaleDateString("es", { weekday: "long" }), hm = (iso) => iso.slice(11, 16), rows = [];
  const a = av?.avisos?.[0];
  if (a) rows.push({ ic: "warn", cls: `lv-${a.color}`, t: `Aviso ${a.color}: ${a.evento}`, s: `${a.zona}${a.hasta ? ` · hasta ${new Date(a.hasta).toLocaleString("es", { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : ""}`, ask: `¿Qué implica el aviso ${a.color} de ${a.evento} en ${a.zona}?`, cta: "Preguntar" });
  const k = fc?.datos?.contraste;                 // estaciones reales frente a modelos: solo se avisa si no encajan
  if (k && k.acuerdo !== "alto") {
    const bits = [k.temp && `${k.temp.obs}° medidos frente a ${k.temp.modelo}° de los modelos`, k.viento && `viento ${k.viento.obs} frente a ${k.viento.modelo} km/h`].filter(Boolean).join(" · ");
    rows.push({ ic: "info", cls: k.acuerdo === "bajo" ? "lv-naranja" : "lv-amarillo", t: k.acuerdo === "bajo" ? "Las estaciones reales discrepan de los modelos" : "Las estaciones reales se desvían algo de los modelos", s: `${bits} · ${k.estaciones} ${k.estaciones === 1 ? "estación" : "estaciones"}`, ask: `Las estaciones cercanas no coinciden con los modelos en ${nm}: ¿qué debo esperar las próximas horas?`, cta: "Preguntar" });
  }
  const w = act?.ventanas?.[0];
  if (w) rows.push({ ic: "route", t: `Mejor ventana para caminar: ${dia(w.desde)} ${hm(w.desde)}–${hm(w.hasta)}`, s: `${w.puntuacion}/100${w.temp_c ? ` · ${w.temp_c[0]}–${w.temp_c[1]} °C` : ""} · viento hasta ${w.viento_kmh_max} km/h${w.lluvia_mm ? ` · ${nf1.format(w.lluvia_mm)} mm` : " · sin lluvia"}`, ask: `Prepárame una salida a caminar el ${dia(w.desde)} de ${hm(w.desde)} a ${hm(w.hasta)}`, cta: "Preguntar" });
  const p = sat?.pasos?.[0];
  if (p) rows.push({ ic: "satellite", t: `${p.satelite} pasa a las ${clock(p.aos)}, a ${p.max_el}° de altura`, s: `${Math.round(p.duracion_s / 60)} min · sale por ${compass(p.aos_az)}`, href: "#/radio/satelites", cta: "Ver paso" });
  if (!rows.length) return;
  box.hidden = false;
  box.innerHTML = `<div class="insights"><div class="ins-head"><span class="ai-tag"><span class="av" aria-hidden="true">${icon("spark", 12)}</span>Tu día, según Guilda</span><span class="small">${esc(nm)}</span></div>
    ${rows.map((r, i) => `<div class="ins"><span class="ic2 ${r.cls || ""}">${icon(r.ic, 19)}</span><div><b>${esc(r.t)}</b><span class="s">${esc(r.s)}</span></div>${r.href ? `<a class="askb" href="${r.href}">${r.cta}</a>` : `<button class="askb" type="button" data-i="${i}">${r.cta}</button>`}</div>`).join("")}</div>`;
  box.onclick = (e) => { const b = e.target.closest("[data-i]"); if (b) ctx.ask?.(rows[+b.dataset.i].ask); };
}
/* Predicción oficial del Meteocat (solo Cataluña) al lado de los modelos: máx. del Meteocat frente a la media de los modelos, para ver si coinciden */
async function loadOficial(la, lo, my, dias) {
  const box = $("#oficial"); if (!box) return;
  let r; try { r = await api(`/api/oficial?lat=${la}&lon=${lo}`); } catch { return; }
  if (my !== req || !$("#oficial") || !r?.disponible || !r.dias?.length) return;
  const mod = new Map((dias || []).map((x) => [x.fecha, x]));
  const dn = (f) => new Date(f + "T12:00").toLocaleDateString("es", { weekday: "short", day: "numeric" }).replace(".", "");
  const fila = (x) => { const m = mod.get(x.fecha), dif = m && x.tmax != null && m.temp_max_c != null ? Math.round(x.tmax - m.temp_max_c) : null;
    return `<tr><td>${esc(dn(x.fecha))}</td><td>${esc(x.cielo || "–")}</td><td class="num">${x.tmin != null ? Math.round(x.tmin) : "–"}°/${x.tmax != null ? Math.round(x.tmax) : "–"}°</td><td class="num">${m ? t0(m.temp_max_c) + "°" : "–"}${dif ? ` <small>(${dif > 0 ? "+" : ""}${dif})</small>` : ""}</td><td class="num">${x.lluvia_prob_pct != null ? Math.round(x.lluvia_prob_pct) + "%" : "–"}</td></tr>`; };
  box.hidden = false;
  box.innerHTML = `<div class="label">Predicción oficial · Meteocat <em>${esc(r.municipio)}${r.antiguo ? " · dato de hace más de un día" : ""}</em></div>
    <div class="tablewrap"><table><thead><tr><th>Día</th><th>Cielo</th><th>Mín/máx</th><th>Modelos</th><th>Lluvia</th></tr></thead><tbody>${r.dias.slice(0, 7).map(fila).join("")}</tbody></table></div>
    <p class="aviso-links">Predicción municipal del Servei Meteorològic de Catalunya · <a href="${esc(r.enlace)}" target="_blank" rel="noopener">ver en meteo.cat</a>. «Lluvia» es la probabilidad; entre paréntesis, la diferencia del Meteocat con la media de los modelos.</p>`;
}
async function loadAvisos(la, lo, my) {
  const box = $("#avisos"); if (!box) return;
  let r; try { r = await api(`/api/avisos?lat=${la}&lon=${lo}`); } catch { return; }
  if (my !== req || !$("#avisos")) return;
  const hm = (iso) => (iso ? new Date(iso).toLocaleString("es", { weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).replace(".", "") : "");
  const links = r.enlaces.map((e) => `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.nombre)}</a>`).join(" · ");
  box.innerHTML = r.avisos.map((a) => `<div class="aviso aviso-${esc(a.color)}" role="alert"><span class="aviso-dot" aria-hidden="true"></span><div><b>${esc(a.evento)}</b> <span class="aviso-zona">${esc(a.zona)}</span>
      <p>${a.desde ? `Desde ${esc(hm(a.desde))}` : ""}${a.hasta ? ` hasta ${esc(hm(a.hasta))}` : ""}${a.descripcion ? ` · ${esc(a.descripcion)}` : ""}</p>${a.instruccion ? `<p class="aviso-ins">${esc(a.instruccion)}</p>` : ""}</div></div>`).join("")
    + `<p class="aviso-links">${r.disponible ? (r.avisos.length ? "" : "Sin avisos oficiales vigentes en esta zona. ") : ""}Avisos oficiales: ${links}${r.disponible ? "" : " · <em>aquí solo mostramos el enlace; consúltalos antes de salir</em>"}</p>`;
}

function lazyLoad(sel, fn) {
  const el = $(sel); if (!el) return;
  if (!("IntersectionObserver" in window)) { fn(); return; }
  const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { io.disconnect(); fn(); } }, { rootMargin: "300px" });
  io.observe(el);
}
let actList = null, actSel = LS.get("mg-act", "senderismo");
async function loadActivities(la, lo) {
  const sec = $("#act-sec"); if (!sec) return;
  try {
    actList ||= await api("/api/activities");
    $("#actsel").innerHTML = actList.map((a) => `<button type="button" data-act="${a.id}" aria-pressed="${a.id === actSel}">${esc(a.nombre)}</button>`).join("");
    $$("[data-act]", sec).forEach((b) => b.addEventListener("click", () => { actSel = b.dataset.act; LS.set("mg-act", actSel); $$("[data-act]", sec).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); showActivity(la, lo); }));
    showActivity(la, lo);
  } catch (e) { $("#actout") && ($("#actout").innerHTML = `<p class="empty">No se pudo calcular (${esc(e.message)}).</p>`); }
}
const scoreCls = (s) => (s >= 80 ? "lv0" : s >= 60 ? "lv1" : "lv2");
const winTxt = (w) => `${wd(w.desde)} ${w.desde.slice(11, 16)}–${w.hasta.slice(11, 16)}`;
async function showActivity(la, lo) {
  const out = $("#actout"); if (!out) return;
  out.innerHTML = '<p class="empty">Calculando…</p>';
  try {
    const r = await api(`/api/activity?activity=${actSel}&lat=${la}&lon=${lo}`);
    if (!$("#actout")) return;
    const n = r.ahora;
    out.innerHTML = `${n ? `<div class="actnow"><span class="pill ${scoreCls(n.puntuacion)}"><i></i>Ahora ${n.puntuacion}/100</span><span class="empty">${n.en_franja ? (n.motivos.length ? "Penaliza: " + esc(n.motivos.join(", ")) : "Condiciones buenas.") : "Fuera de la franja habitual de esta actividad."}</span></div>` : ""}
      ${r.ventanas.length ? `<ol class="wins">${r.ventanas.map((w) => `<li><b>${esc(winTxt(w))}</b> <span class="pill ${scoreCls(w.puntuacion)}"><i></i>${w.puntuacion}/100</span>
        <span class="empty">${w.horas} h${w.temp_c ? ` · ${w.temp_c[0]}–${w.temp_c[1]} °C` : ""} · viento hasta ${w.viento_kmh_max} km/h · ${w.lluvia_mm ? w.lluvia_mm + " mm de lluvia" : "sin lluvia"}</span></li>`).join("")}</ol>`
        : '<p class="empty">No hay ventanas buenas en los próximos días con los criterios de esta actividad.</p>'}
      <p class="empty" style="font-size:12px">${esc(r.criterio)}</p>`;
  } catch (e) { out.innerHTML = `<p class="empty">No se pudo calcular (${esc(e.message)}).</p>`; }
}
async function loadAir(la, lo) {
  const box = $("#airbox"); if (!box) return;
  try {
    const a = await api(`/api/air?lat=${la}&lon=${lo}`), c = a.actual, aqiCls = c.aqi == null ? "" : c.aqi < 40 ? "lv0" : c.aqi < 60 ? "lv1" : "lv2", uvCls = c.uv == null ? "" : c.uv < 3 ? "lv0" : c.uv < 8 ? "lv1" : "lv2";
    if (!$("#airbox")) return;
    box.innerHTML = `<div class="grid2"><div class="card"><h3>${icon("wind", 16)} Calidad del aire</h3>
        <div class="kv"><span>Índice europeo (EAQI)</span><span>${c.aqi != null ? `<span class="pill ${aqiCls}"><i></i>${nf.format(c.aqi)} · ${esc(c.aqi_texto)}</span>` : "–"}</span></div>
        <div class="kv"><span>PM2,5 / PM10</span><span class="num">${c.pm2_5 != null ? nf1.format(c.pm2_5) : "–"} / ${c.pm10 != null ? nf1.format(c.pm10) : "–"} µg/m³</span></div>
        <div class="kv"><span>Ozono / NO₂</span><span class="num">${c.ozono != null ? nf.format(c.ozono) : "–"} / ${c.no2 != null ? nf1.format(c.no2) : "–"} µg/m³</span></div></div>
      <div class="card"><h3>${icon("sun", 16)} Índice UV</h3>
        <div class="kv"><span>Ahora</span><span>${c.uv != null ? `<span class="pill ${uvCls}"><i></i>${nf1.format(c.uv)} · ${esc(c.uv_texto)}</span>` : "–"}</span></div>
        ${a.uv_max_hoy ? `<div class="kv"><span>Máximo de hoy</span><span class="num">${nf1.format(a.uv_max_hoy.valor)} a las ${esc(a.uv_max_hoy.hora)}</span></div><p class="empty" style="margin-top:6px">${esc(a.uv_max_hoy.consejo)}</p>` : '<p class="empty">Sin datos de UV.</p>'}</div></div>
      ${a.polen?.length ? `<div class="card" style="margin-top:12px"><h3>Polen (máx. 24 h)</h3><div class="mchips">${a.polen.map((p) => `<span class="mchip"><i style="--c:${p.nivel === "sin polen" ? "#9aa5ad" : p.nivel === "bajo" ? "#2e9e5b" : p.nivel === "moderado" ? "#d9a21b" : "#d9482b"}"></i>${esc(p.tipo)} · ${esc(p.nivel)}</span>`).join("")}</div></div>`
        : '<p class="empty" style="margin-top:10px">Sin datos de polen (solo Europa y en temporada).</p>'}`;
  } catch (e) { $("#airbox") && ($("#airbox").innerHTML = `<p class="empty">No se pudo cargar la calidad del aire (${esc(e.message)}).</p>`); }
}

/* ---------- comparar lugares ---------- */
const CMP_VARS = [["temp_c", "Temperatura", (v) => `${nf1.format(v)} °C`], ["lluvia_mm", "Lluvia", (v) => `${nf1.format(v)} mm`], ["racha_kmh", "Racha", (v) => `${nf.format(v)} km/h`], ["nubes_pct", "Nubes", (v) => `${nf.format(v)} %`]];
let cmpVar = "temp_c";
function cmpCandidates() {
  const m = new Map(), add = (name, lat, lon) => { if (lat == null) return; const k = favKey(lat, lon); if (!m.has(k)) m.set(k, { key: k, name, lat, lon }); };
  if (hasQth() && getQth().lat != null) add(qthLabel(), getQth().lat, getQth().lon);
  favs.forEach((f) => add(f.name, f.lat, f.lon));
  places.forEach((p) => add(p.name, p.lat, p.lon));
  if (last) add(last.name, last.lat, last.lon);
  return [...m.values()];
}
async function compararPage() {
  const my = ++req;
  if (!places.length) { try { places = await api("/api/places"); } catch { /* se compara con lo que haya guardado */ } if (my !== req) return; }
  const cand = cmpCandidates();
  let chosen = LS.get("mg-cmp", []).filter((k) => cand.some((c) => c.key === k)).slice(0, 4);
  if (chosen.length < 2) chosen = cand.slice(0, 2).map((c) => c.key);
  ctx.colb.scrollTop = 0;
  ctx.colb.innerHTML = `<div class="d-head"><div><h1>Comparar lugares</h1><p>Elige de 2 a 4 lugares y mira cuál tiene mejor tiempo en las próximas 48 h.</p></div></div>
    <div class="mchips" id="cmp-pick" role="group" aria-label="Lugares a comparar"></div>
    <p class="empty" id="cmp-hint">${cand.length < 2 ? "Guarda algún lugar con la estrella o define tu QTH para poder compararlo." : ""}</p>
    <div id="cmp-out"></div>`;
  const draw = () => {
    $("#cmp-pick").innerHTML = cand.map((c) => `<button type="button" class="mchip" data-k="${esc(c.key)}" aria-pressed="${chosen.includes(c.key)}"><i></i>${esc(c.name)}</button>`).join("");
    $("#cmp-hint").textContent = chosen.length < 2 && cand.length >= 2 ? "Elige al menos 2." : chosen.length >= 4 ? "Máximo 4 a la vez." : "";
  };
  draw();
  const go = debounce(async () => {
    if (chosen.length < 2) { $("#cmp-out").innerHTML = ""; return; }
    LS.set("mg-cmp", chosen);
    $("#cmp-out").innerHTML = '<div class="skeleton" style="height:180px;border-radius:12px"></div>';
    try {
      const pts = chosen.map((k) => cand.find((c) => c.key === k)).map((c) => `${c.lat.toFixed(4)},${c.lon.toFixed(4)},${c.name.replace(/[;,]/g, " ")}`).join(";");
      const d = await api(`/api/compare?pts=${encodeURIComponent(pts)}`);
      if (my !== req || !$("#cmp-out")) return;
      paintCompare(d);
    } catch (e) { if (my === req && $("#cmp-out")) $("#cmp-out").innerHTML = `<div class="err"><strong>No se pudo comparar</strong><p class="empty">${esc(e.message)}</p></div>`; }
  }, 250);
  $("#cmp-pick").addEventListener("click", (e) => {
    const b = e.target.closest("[data-k]"); if (!b) return;
    const k = b.dataset.k, i = chosen.indexOf(k);
    if (i >= 0) chosen.splice(i, 1); else if (chosen.length < 4) chosen.push(k); else { ctx.toast("Máximo 4 lugares a la vez"); return; }
    draw(); go();
  });
  go();
}
function paintCompare(d) {
  const L = d.lugares, col = MODEL_COLORS, f1 = (v) => (v == null ? "–" : nf1.format(v)), f0v = (v) => (v == null ? "–" : nf.format(Math.round(v)));
  const best = (key, low, gap) => { const v = L.map((x) => x[key]).filter((x) => x != null); return v.length > 1 && Math.max(...v) - Math.min(...v) >= gap ? (low ? Math.min(...v) : Math.max(...v)) : null; };
  const bTmax = best("max_c", false, 2), bRain = best("lluvia_48h_mm", true, 1), bGust = best("racha_max_kmh", true, 10);
  const cell = (v, b, txt) => `<td class="num${b != null && v === b ? " best" : ""}">${txt}</td>`;
  $("#cmp-out").innerHTML = `${d.veredicto.length ? `<div class="card"><h3>En pocas palabras</h3><ul class="plain">${d.veredicto.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
    <div class="tablewrap" style="margin-top:14px"><table><thead><tr><th>48 h</th>${L.map((x, k) => `<th style="border-bottom:3px solid ${col[k]}">${esc(x.nombre)}</th>`).join("")}</tr></thead><tbody>
      <tr><td>Ahora</td>${L.map((x) => `<td class="num">${f0v(x.ahora_c)} °C</td>`).join("")}</tr>
      <tr><td>Mínima – máxima</td>${L.map((x) => cell(x.max_c, bTmax, `${f0v(x.min_c)} – ${f0v(x.max_c)} °C`)).join("")}</tr>
      <tr><td>Lluvia acumulada</td>${L.map((x) => cell(x.lluvia_48h_mm, bRain, `${f1(x.lluvia_48h_mm)} mm`)).join("")}</tr>
      <tr><td>Racha máxima</td>${L.map((x) => cell(x.racha_max_kmh, bGust, `${f0v(x.racha_max_kmh)} km/h`)).join("")}</tr>
      <tr><td>Nubosidad media</td>${L.map((x) => `<td class="num">${f0v(x.nubes_media_pct)} %</td>`).join("")}</tr>
      <tr><td>Altitud</td>${L.map((x) => `<td class="num">${f0v(x.elevacion_m)} m</td>`).join("")}</tr></tbody></table></div>
    <section class="sec"><div class="label">Superpuestos <span class="seg" role="group" aria-label="Variable">${CMP_VARS.map(([k, l]) => `<button type="button" data-cv="${k}" aria-pressed="${cmpVar === k}">${l}</button>`).join("")}</span></div>
      <div class="mchips">${L.map((x, k) => `<span class="mchip" style="--c:${col[k]}"><i></i>${esc(x.nombre)}</span>`).join("")}</div><div id="cmp-chart" class="chart"></div></section>
    <p class="empty" style="font-size:12px">En verde, el mejor valor de cada fila cuando la diferencia es apreciable. ${esc(d.nota)} <a href="#/forecast">Volver al inicio</a></p>`;
  const draw = () => {
    const [, , fmt] = CMP_VARS.find((v) => v[0] === cmpVar), n = Math.min(...L.map((x) => x.tiempo.length)), T = L[0].tiempo.slice(0, n).map((t) => ({ time: t }));
    const series = L.map((x, k) => ({ id: String(k), label: x.nombre, color: col[k], vals: x.series[cmpVar].slice(0, n) }));
    renderModels($("#cmp-chart"), T, series, T.map(() => null), axisFor, { fmt, zero: cmpVar !== "temp_c", noMean: true, when: (i) => `<b>${wd(T[i].time)} ${hh(T[i].time)}</b>`,
      lowLabel: cmpVar === "temp_c" ? "más frío" : "menor", highLabel: cmpVar === "temp_c" ? "más cálido" : "mayor" });
  };
  $$("[data-cv]", ctx.colb).forEach((b) => b.addEventListener("click", () => { cmpVar = b.dataset.cv; $$("[data-cv]", ctx.colb).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); draw(); }));
  draw();
}

/* ---------- montaña: lo que importa arriba, calculado con los mismos datos ---------- */
function mountainCard(T, alt) {
  const v = (k, f = "mean") => T.map((x) => x[k]?.[f]).filter((x) => x != null);
  const iso = v("isocero_m"), snow = v("nieve_cm").reduce((a, b) => a + b, 0), cape = Math.max(0, ...v("cape_jkg", "max")), gust = mx(v("racha_kmh", "max"));
  const chill = T.map((x) => { const t = x.temp_c?.mean, w = x.viento_kmh?.mean; if (t == null || w == null || t > 10 || w < 4.8) return t; const p = w ** 0.16; return 13.12 + 0.6215 * t - 11.37 * p + 0.3965 * t * p; }).filter((x) => x != null);
  const tmin = chill.length ? Math.min(...chill) : null, isoMin = iso.length ? Math.min(...iso) : null, isoMax = iso.length ? Math.max(...iso) : null;
  const pill = (lv, t) => `<span class="pill lv${lv}"><i></i>${t}</span>`;
  const notes = [];
  if (tmin != null && tmin <= -10) notes.push(pill(2, "Frío intenso: riesgo de congelación")); else if (tmin != null && tmin <= -3) notes.push(pill(1, "Sensación bajo cero"));
  if (gust >= 80) notes.push(pill(2, "Rachas muy fuertes en cresta")); else if (gust >= 55) notes.push(pill(1, "Viento notable"));
  if (snow >= 10) notes.push(pill(2, "Nevada importante")); else if (snow >= 1) notes.push(pill(1, "Nieve prevista"));
  if (cape >= 1000) notes.push(pill(2, "Tormentas: evita crestas")); else if (cape >= 400) notes.push(pill(1, "Posibles tormentas por la tarde"));
  const cota = alt != null && isoMin != null ? (alt > isoMax ? "Estás por encima del isocero: precipitación en forma de nieve." : alt < isoMin - 300 ? "Toda la precipitación caerá como lluvia en este punto." : "Cerca del isocero: la lluvia puede cambiar a nieve.") : "";
  return `<div class="card"><div class="grid2">
    <div><div class="kv"><span>Isocero</span><span class="num">${isoMin != null ? `${nf.format(isoMin)}–${nf.format(isoMax)} m` : "–"}</span></div>
    <div class="kv"><span>Nieve acumulada</span><span class="num">${nf1.format(snow)} cm</span></div>
    <div class="kv"><span>Sensación mínima (con viento)</span><span class="num">${tmin != null ? nf.format(Math.round(tmin)) + " °C" : "–"}</span></div></div>
    <div><div class="kv"><span>Racha máxima</span><span class="num">${nf.format(Math.round(gust))} km/h</span></div>
    <div class="kv"><span>Energía de tormenta (CAPE)</span><span class="num">${nf.format(Math.round(cape))} J/kg</span></div>
    <div class="kv"><span>Altitud del punto</span><span class="num">${alt != null ? nf.format(alt) + " m" : "–"}</span></div></div></div>
    <div class="row" style="margin-top:10px;gap:8px">${notes.join("") || pill(0, "Sin alertas destacadas en estas 48 h")}</div>
    ${cota ? `<p class="empty" style="margin-top:8px">${cota}</p>` : ""}
    <p class="empty" style="margin-top:6px;font-size:12px">Sensación térmica por viento y frío (fórmula de enfriamiento). En alta montaña consulta el boletín de aludes y el aviso oficial.</p></div>`;
}

/* ---------- mar: solo se muestra si el punto tiene oleaje (costa) ---------- */
async function loadMar(la, lo) {
  const sec = $("#mar-sec"); if (!sec) return;
  try {
    const m = await api(`/api/mar?lat=${la}&lon=${lo}`);
    if (!m.hay_mar || !$("#mar-sec")) return;
    const a = m.ahora, pk = m.maximo_48h, H = m.horas.map((x) => ({ ...x, time: x.hora })), fnum = (x, d = 1) => (x == null ? "–" : nf1.format(+x.toFixed(d)));
    const hh2 = (iso) => `${wd(iso)} ${hh(iso)}`;
    sec.hidden = false;
    $("#marbox").innerHTML = `<div class="card"><div class="grid2"><div>
        <div class="kv"><span>Estado ahora</span><span><span class="pill lv${a.nivel}"><i></i>${esc(a.estado)}</span></span></div>
        <div class="kv"><span>Ola</span><span class="num">${fnum(a.ola_m)} m · ${fnum(a.periodo_s, 0)} s</span></div>
        <div class="kv"><span>Viene del</span><span class="num">${a.dir != null ? `${compass(a.dir)} (${Math.round(a.dir)}°)` : "–"}</span></div></div><div>
        <div class="kv"><span>Mar de fondo</span><span class="num">${fnum(a.fondo_m)} m · ${fnum(a.fondo_periodo_s, 0)} s</span></div>
        <div class="kv"><span>Agua</span><span class="num">${a.agua_c != null ? nf1.format(a.agua_c) + " °C" : "–"}</span></div>
        <div class="kv"><span>Máximo 48 h</span><span class="num">${fnum(pk.ola_m)} m <small>${esc(hh2(pk.hora))}</small> <span class="pill lv${pk.nivel}"><i></i>${esc(pk.estado)}</span></span></div></div></div></div>
      <div id="marchart"></div><p class="empty" style="font-size:12px;margin-top:6px">${esc(m.nota)}</p>`;
    renderCharts($("#marchart"), H, [
      { title: "Altura de ola", sub: "m · línea = ola, punteada = mar de fondo", draw: "wind", H: 150, ticks: 3, tickFmt: (v) => nf1.format(v), range: () => [0, Math.max(1, ...H.map((x) => x.ola_m ?? 0))], mean: (i) => H[i].fondo_m ?? 0, gust: (i) => H[i].ola_m ?? 0, dir: (i) => H[i].dir,
        tip: (i) => `${hh2(H[i].hora)} · ola <b>${fnum(H[i].ola_m)} m</b> ${H[i].periodo_s != null ? `(${fnum(H[i].periodo_s, 0)} s)` : ""} · fondo ${fnum(H[i].fondo_m)} m · ${esc(H[i].estado || "")}` },
    ], axisFor);
  } catch { /* sin mar o fuente caída: la sección sigue oculta */ }
}

/* ---------- recargar los datos ---------- */
async function refreshNow(btn) {
  if (!lastUrl || btn.classList.contains("spin")) return;
  const y = ctx.colb.scrollTop, before = lastData?.generado;
  btn.classList.add("spin");
  try {
    const d = await api(lastUrl + (lastUrl.includes("?") ? "&" : "?") + "refresh=1");
    if (d.generado === before) ctx.toast("Los datos ya están al día (se vuelven a consultar como mucho cada 5 min)");
    else { render(d); ctx.colb.scrollTop = y; ctx.toast("Datos actualizados"); }
  } catch (e) { ctx.toast(e.message); }
  finally { btn.classList.remove("spin"); }
}

/* ---------- contraste de modelos ---------- */
const MFMT = { temp_c: (v) => `${nf1.format(v)} °C`, lluvia_mm: (v) => `${nf1.format(v)} mm`, viento_kmh: (v) => `${nf.format(v)} km/h`, racha_kmh: (v) => `${nf.format(v)} km/h`, nubes_pct: (v) => `${nf.format(v)} %` };
function drawModels(c) {
  const host = $("#mchart"); if (!host) return;
  const pm = c.por_modelo?.[mvar];
  if (!pm) { host.innerHTML = ""; return; }
  const T = c.tramos.slice(0, horizon), ids = Object.keys(pm);
  const series = ids.map((id, k) => ({ id, label: MODEL_NAMES[id] || id, color: MODEL_COLORS[k % MODEL_COLORS.length], vals: pm[id].slice(0, horizon) }));
  const mean = T.map((x) => x[mvar]?.mean ?? null);
  const spread = T.map((_, i) => { const v = series.map((s) => s.vals[i]).filter((x) => x != null); return v.length > 1 ? Math.max(...v) - Math.min(...v) : null; }).filter((x) => x != null);
  const sp = spread.length ? avg(spread) : null, unit = { temp_c: "°C", lluvia_mm: "mm", viento_kmh: "km/h", racha_kmh: "km/h", nubes_pct: "%" }[mvar];
  $("#mnote").textContent = sp == null ? "" : `${ids.length} modelos en esta variable. Discrepancia media entre el más bajo y el más alto: ${nf1.format(sp)} ${unit}. Cuanto más separadas las líneas, menos fiable el pronóstico.`;
  $("#mchips").innerHTML = series.map((s) => `<button type="button" class="mchip" data-m="${s.id}" style="--c:${s.color}" aria-pressed="${!hiddenModels.has(s.id)}"><i></i>${esc(s.label)}</button>`).join("");
  $$(".mchip", ctx.colb).forEach((b) => b.addEventListener("click", () => { hiddenModels.has(b.dataset.m) ? hiddenModels.delete(b.dataset.m) : hiddenModels.add(b.dataset.m); drawModels(c); }));
  renderModels(host, T, series, mean, axisFor, { fmt: MFMT[mvar], zero: mvar !== "temp_c", hidden: hiddenModels, when: (i) => `<b>${wd(T[i].time)} ${hh(T[i].time)}</b>`,
    lowLabel: mvar === "temp_c" ? "más frío" : "menor", highLabel: mvar === "temp_c" ? "más cálido" : "mayor" });
}

let dayZoom = null;
const dayZoomBtns = (dias) => `<button type="button" data-z="" aria-pressed="true">Todo</button>${dias.slice(0, 5).map((x) => `<button type="button" data-z="${esc(x.fecha)}" aria-pressed="false">${esc(wd(x.fecha))}</button>`).join("")}`;
function drawCharts(all, mountain) {
  const T = (dayZoom ? all.filter((x) => x.time.slice(0, 10) === dayZoom) : all.slice(0, horizon)), g = (k, f = "mean") => (i) => T[i][k]?.[f] ?? 0, hasSnow = T.some((x) => (x.nieve_cm?.mean ?? 0) > 0.05);
  renderStrip($("#strip"), T, [
    { label: "Temp °C", val: (i) => T[i].temp_c?.mean, color: (v) => ramp(SCALES.temp, v), text: (v) => t0(v) },
    { label: "Lluvia mm", val: (i) => T[i].lluvia_mm?.mean, color: (v) => ramp(SCALES.rain, v), text: (v) => (v < 0.05 ? "" : nf1.format(v)), ink: (v) => (v > 3 ? "#fff" : "#14191f") },
    { label: "Viento", val: (i) => T[i].viento_kmh?.mean, color: (v) => ramp(SCALES.wind, v), text: (v) => nf.format(v), ink: (v) => (v > 65 ? "#fff" : "#14191f") },
    { label: "Nubes %", val: (i) => T[i].nubes_pct?.mean, color: (v) => ramp(SCALES.cloud, v), text: (v) => nf.format(v) },
  ], axisFor);
  const defs = [
    { title: "Temperatura", sub: "°C · banda = rango entre modelos", draw: "band", unit: "°", H: 170, range: () => [Math.min(...T.map((x) => x.temp_c.min)), Math.max(...T.map((x) => x.temp_c.max))],
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

/* ---------- Inicio: resumen del día ---------- */
const riskOf = (n) => (!n ? null : n.gust_kmh >= 70 ? [2, "Viento fuerte"] : n.gust_kmh >= 45 || n.rain_24h_mm >= 10 ? [1, "Precaución"] : [0, "Estable"]);
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }) : "–");

async function home(keepPane = false) {
  sel = null; req++; last = null; lastData = null;
  renderPlaces(); renderFavs(); drawPins();
  if (places.length) ctx.fit(places.map((p) => [p.lon, p.lat]));
  if (!keepPane) ctx.pane("detail");
  const qth = LS.get("mg-qth", null), at = qth || { lat: places[0]?.lat ?? 42.7, lon: places[0]?.lon ?? 0.85 };
  const my = req;
  const hr = new Date().getHours(), greet = hr < 6 ? "Buenas noches" : hr < 13 ? "Buenos días" : hr < 21 ? "Buenas tardes" : "Buenas noches";
  ctx.colb.innerHTML = `<div class="d-head home-head"><div><p class="eyebrow">${new Date().toLocaleDateString("es", { weekday: "long", day: "numeric", month: "long" })}</p><h1>${greet}</h1></div></div>
    <section class="sec insights-sec" id="insights" aria-label="Tu día, según Guilda" hidden></section>
    <section class="sec" style="margin-top:22px"><div class="label">Tus lugares</div><div class="pgrid" id="pg">${Array.from({ length: 6 }, () => '<div class="skeleton" style="height:150px;border-radius:12px"></div>').join("")}</div></section>
    <section class="sec"><div class="label">Radio ahora <em><a href="#/radio">abrir Radio Guilda</a></em></div><div class="grid2" id="radio-now"><div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:120px"></div></div></section>
    <section class="sec"><div class="label">Atajos</div><div class="quick">
      <a href="#/routes">${icon("route")}Planificar ruta</a><a href="#/radio/repetidores">${icon("tower")}Repetidores cerca</a>
      <a href="#/radio/satelites">${icon("satellite")}Pasos de satélites</a><a href="#/radio/portatil">${icon("tent")}Salir a operar</a>
      <a href="#/radio/qsl">${icon("log")}Cuaderno QSL</a><a href="#/learn">${icon("book")}Aprende</a></div></section>`;
  loadInsights(at, my);
  const pcards = (list) => list.map((x) => { const r = riskOf(x.snap); return `<button class="pcard" data-go="${esc(x.go)}"><span class="pn">${esc(x.name)}</span><span class="pa">${esc(x.area)}</span>
      <span class="pt"><span class="num">${x.snap ? t0(x.snap.temp_c) + "°" : "–"}</span>${r ? `<span class="pill lv${r[0]}"><i></i>${r[1]}</span>` : ""}</span>
      ${x.snap ? sparkline(x.snap.spark, 180, 34) + `<span class="pm"><span>racha ${x.snap.gust_kmh} km/h</span><span>${nf1.format(x.snap.rain_24h_mm)} mm</span></span>` : ""}</button>`; }).join("");
  const q0 = getQth(), mineCard = q0?.lat != null ? [{ go: qthHash(q0), name: qthLabel(q0), area: q0.detalle || "Mi ubicación", snap: snaps.__home }] : [];
  const all = [...mineCard, ...places.map((p) => ({ go: `#/forecast/${p.slug}`, name: p.name, area: p.area, snap: p.now })), ...sortedFavs().map((f) => ({ go: favHash(f), name: f.name, area: f.sub || "Mi lugar", snap: snaps[f.key] }))];
  const cta = q0?.lat != null ? "" : `<button class="pcard cta" type="button" data-loc="go"><span class="pn">${icon("pin", 18)} Mi ubicación</span><span class="pa">Toca para usar la posición de este dispositivo y ver aquí el tiempo de donde estás.</span></button>`;
  const draw = () => { const g = $("#pg"); if (!g) return; g.innerHTML = cta + pcards(all);
    g.querySelector("[data-loc=go]")?.addEventListener("click", () => useMyLocation(true)); g.onclick = (e) => { const b = e.target.closest("[data-go]"); if (b) location.hash = b.dataset.go; }; };
  if (places.length) draw(); else api("/api/places").then((p) => { places = p; all.push(...places.map((x) => ({ go: `#/forecast/${x.slug}`, name: x.name, area: x.area, snap: x.now }))); if (my === req) draw(); });
  const [solar, astro, prop] = await Promise.all([api("/api/solar").catch(() => null), api(`/api/astro?lat=${at.lat}&lon=${at.lon}`).catch(() => null), api(`/api/propagation?lat=${at.lat}&lon=${at.lon}`).catch(() => null)]);
  if (my !== req || !$("#radio-now")) return;
  const w = prop?.peor, cat = { conducto: "lv2", "super-refracción": "lv1" }[w?.cat] || "lv0";
  const sr = astro?.orto && new Date(astro.orto), ss = astro?.ocaso && new Date(astro.ocaso);
  $("#radio-now").innerHTML = `
    <div class="card"><h3>${icon("wave", 16)} Propagación</h3>
      <div class="kv"><span>HF · Kp / SFI</span><span>${solar?.kp ?? "–"} / ${solar?.sfi ?? "–"}</span></div>
      <div class="kv"><span>Tropo VHF/UHF (72 h)</span><span class="pill ${cat}"><i></i>${w ? esc(w.cat) : "–"}</span></div>
      <p class="empty" style="margin-top:8px">${esc((solar?.resumen || [])[1] || "Datos de NOAA SWPC y modelos meteorológicos.")}</p></div>
    <div class="card sunbox"><h3>${icon("sun", 16)} Sol y Luna ${qth ? "en tu QTH" : ""}</h3>
      ${sr && ss ? sunArc(sr, ss) : ""}
      <div class="row between" style="font-size:13px"><span>Orto <b class="num">${clock(astro.orto)}</b></span><span>Ocaso <b class="num">${clock(astro.ocaso)}</b></span></div>
      ${astro ? `<div class="row" style="margin-top:10px;gap:12px">${moonSvg(astro.luna.edad_dias, 44)}<span style="font-size:13px">${esc(astro.luna.fase)}<br><span style="color:var(--ink-3)">${astro.luna.iluminacion_pct}% iluminada</span></span></div>` : ""}</div>`;
}
