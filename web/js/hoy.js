// «Hoy» (rediseño Concurso 2026): titular de la previsión con su sello, cifras clave, 48 h con el abanico de modelos y mejor momento por actividad.
// Todo sale de la API real (/api/forecast, /api/air, /api/activities, /api/activity); si algo falla, ese bloque se oculta y el resto sigue.
import { $, api, esc, nf1, t0, wd } from "./util.js";
import { icon } from "./icons.js";

const CONF_N = { baja: 1, media: 2, alta: 3 };
const SPARK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z"/></svg>';

/** Sello de IA del sistema de diseño: qué es, confianza en tres niveles y de dónde sale. */
export const seal = (label, conf, sources) => `<div class="aiseal"><span class="ai-tag">${icon("spark", 13)}<span>${esc(label)}</span></span>
  ${conf ? `<span class="conf" title="Confianza: acuerdo entre modelos y calidad de los datos"><span class="bars" data-n="${CONF_N[conf] || 1}" aria-hidden="true"><i></i><i></i><i></i></span>Confianza ${esc(conf)}</span>` : ""}
  ${sources ? `<span class="dot" aria-hidden="true">·</span><span>${esc(sources)}</span>` : ""}</div>`;

/** Pinta el bloque superior de Hoy en los contenedores #hoy-brief, #hoy-kpis, #hoy-chart y #hoy-best. `alive()` dice si la vista sigue abierta. */
export async function paint(ctx, at, name, href, alive, names = {}) {
  const q = `lat=${at.lat}&lon=${at.lon}`;
  const [fc, air] = await Promise.all([api(`/api/forecast?${q}`).catch(() => null), api(`/api/air?${q}`).catch(() => null)]);
  if (!alive()) return;
  const brief = $("#hoy-brief"), kp = $("#hoy-kpis"), ch = $("#hoy-chart");
  if (!fc?.datos?.tramos?.length) { brief.innerHTML = `<p class="empty">No se pudo cargar la previsión ahora mismo.</p>`; kp.hidden = ch.hidden = true; return; }
  const c = fc.datos, p = fc.pronostico || {}, T = c.tramos, nm = c.modelos?.length || 0;
  const ia = fc.origen && fc.origen !== "reglas";
  brief.innerHTML = `${seal(ia ? "Tu día, leído por la IA" : "Tu día, en resumen", p.confianza, `${nm} modelos · ${name}`)}
    <h1 class="hoy-h1">${esc(p.resumen || "Sin resumen disponible.")}</h1>
    <div class="hoy-acts">
      <button class="chip" type="button" data-hoy="why">¿Por qué lo dice?</button>
      <a class="chip" href="${esc(href)}">Ver el informe completo</a>
      ${"speechSynthesis" in window ? '<button class="chip" type="button" data-hoy="say">Escuchar resumen</button>' : ""}
    </div>`;
  brief.onclick = (e) => {
    const b = e.target.closest("[data-hoy]"); if (!b) return;
    if (b.dataset.hoy === "why") ctx.ask?.(`Explícame la previsión de hoy y mañana en ${name}: qué dicen los modelos, en qué discrepan y qué datos lo sostienen.`);
    if (b.dataset.hoy === "say") { speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(p.resumen || ""); u.lang = "es-ES"; speechSynthesis.speak(u); }
  };
  ctx.top?.({ fresh: `Actualizado ${new Date(fc.generado * 1000).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })} · ${nm} modelos` });

  // cifras clave
  const n = T[0], d0 = c.dias?.[0], rain48 = T.slice(0, 16).reduce((s, x) => s + (x.lluvia_mm?.mean || 0), 0);
  const gust24 = Math.max(...T.slice(0, 8).map((x) => x.racha_kmh?.mean ?? 0));
  const k = [
    ["Ahora", `${t0(n.temp_c?.mean)}°`, "", d0 ? `Hoy entre ${t0(d0.temp_min_c)}° y ${t0(d0.temp_max_c)}°` : ""],
    ["Viento", t0(n.viento_kmh?.mean), "km/h", `${n.viento_dir_txt ? "Del " + n.viento_dir_txt + " · " : ""}rachas hasta ${t0(gust24)} en 24 h`],
    ["Lluvia 48 h", nf1.format(Math.round(rain48 * 10) / 10), "mm", rain48 >= 0.2 ? `Máx. de un modelo: ${nf1.format(Math.round(T.slice(0, 16).reduce((s, x) => s + (x.lluvia_mm?.max || 0), 0) * 10) / 10)} mm` : "Sin lluvia prevista"],
  ];
  if (air?.actual) k.push(["Aire y UV", air.actual.aqi_texto ? air.actual.aqi_texto[0].toUpperCase() + air.actual.aqi_texto.slice(1) : "–", "", `ICA ${air.actual.aqi ?? "–"} · UV máx. ${air.uv_max_hoy?.valor != null ? nf1.format(air.uv_max_hoy.valor) : "–"}`]);
  kp.innerHTML = k.map(([l, v, u, s]) => `<div class="kpi"><span class="kpl">${l}</span><span class="kpv">${esc(v)}${u ? `<small> ${u}</small>` : ""}</span><span class="kps">${esc(s)}</span></div>`).join("");

  // gráfico: 48 h o 5 días
  const draw = (span) => {
    const S = T.slice(0, span === 48 ? 17 : T.length), W = 1000, H = 220;
    const lo = Math.floor(Math.min(...S.map((x) => x.temp_c.min)) - 1), hi = Math.ceil(Math.max(...S.map((x) => x.temp_c.max)) + 1);
    const X = (i) => ((i / (S.length - 1)) * W).toFixed(1), Y = (v) => (H - ((v - lo) / (hi - lo || 1)) * H).toFixed(1);
    const line = S.map((x, i) => `${i ? "L" : "M"}${X(i)} ${Y(x.temp_c.mean)}`).join(" ");
    const band = `M${S.map((x, i) => `${X(i)} ${Y(x.temp_c.max)}`).join(" L")} L${[...S].reverse().map((x, j) => `${X(S.length - 1 - j)} ${Y(x.temp_c.min)}`).join(" L")} Z`;
    const rmax = Math.max(1, ...S.map((x) => x.lluvia_mm?.mean || 0)), bw = W / S.length;
    const bars = S.map((x, i) => (x.lluvia_mm?.mean > 0.05 ? `M${(i * bw + bw * 0.25).toFixed(1)} ${H} V${(H - (x.lluvia_mm.mean / rmax) * H * 0.5).toFixed(1)} h${(bw * 0.5).toFixed(1)} V${H} Z` : "")).join(" ");
    const step = span === 48 ? 2 : 8, labs = S.filter((_, i) => i % step === 0).map((x) => span === 48 ? x.time.slice(11, 13) + " h" : wd(x.time.slice(0, 10)));
    ch.querySelector(".hc-plot").innerHTML = `<div class="hc-y num"><span>${hi}°</span><span>${Math.round((hi + lo) / 2)}°</span><span>${lo}°</span></div>
      <div class="hc-main"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Temperatura media de ${nm} modelos con su abanico y lluvia, próximas ${span === 48 ? "48 horas" : "5 días"}">
        <path d="M0 0H${W}M0 ${H / 2}H${W}M0 ${H}H${W}" class="g"/><path d="${bars}" class="r"/><path d="${band}" class="b"/><path d="${line}" class="t"/></svg>
        <div class="hc-x num">${labs.map((l) => `<span>${esc(l)}</span>`).join("")}</div></div>`;
    ch.querySelector("h2").textContent = span === 48 ? "Próximas 48 horas" : "Próximos 5 días";
    ch.querySelectorAll("[data-span]").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.span === span)));
  };
  ch.hidden = false;
  ch.innerHTML = `<div class="hc-head"><div><h2>Próximas 48 horas</h2><p>Temperatura media con el abanico de los ${nm} modelos y lluvia por tramo de 3 h</p></div>
    <div class="seg" role="group" aria-label="Periodo"><button type="button" data-span="48" aria-pressed="true">48 h</button><button type="button" data-span="120" aria-pressed="false">5 días</button></div></div>
    <div class="hc-plot"></div>
    <div class="hc-leg"><span><i class="lt"></i>Temperatura (media)</span><span><i class="lb"></i>Abanico de modelos</span><span><i class="lr"></i>Lluvia (mm)</span></div>
    <p class="hc-src">Fuente: ${esc((c.modelos || []).map((m) => names[m] || m).join(", ") || `${nm} modelos`)} vía Open-Meteo · hora local</p>`;
  ch.querySelectorAll("[data-span]").forEach((b) => b.addEventListener("click", () => draw(+b.dataset.span)));
  draw(48);
  paintBest(at, alive);
}

/** «Mejor momento para»: la primera ventana buena de cada actividad, con su franja dibujada sobre las 24 h. */
async function paintBest(at, alive) {
  const box = $("#hoy-best"); if (!box) return;
  let acts; try { acts = await api("/api/activities"); } catch { box.hidden = true; return; }
  const res = await Promise.all(acts.map((a) => api(`/api/activity?activity=${a.id}&lat=${at.lat}&lon=${at.lon}`).then((r) => [a, r]).catch(() => [a, null])));
  if (!alive() || !$("#hoy-best")) return;
  const today = new Date().toISOString().slice(0, 10);
  const rows = res.map(([a, r]) => {
    const w = r?.ventanas?.[0];
    if (!w) return `<div class="best"><span>${esc(a.nombre)}</span><span class="bar"></span><span class="num bw">sin ventana</span></div>`;
    const h0 = +w.desde.slice(11, 13), h1 = +w.hasta.slice(11, 13) || 24, len = h1 > h0 ? h1 - h0 : 24 - h0 + h1;
    const day = w.desde.slice(0, 10) === today ? "" : new Date(w.desde.slice(0, 10) + "T12:00").toLocaleDateString("es", { weekday: "short" }).replace(".", "") + " ";
    return `<div class="best" title="${esc(a.nombre)}: ${w.puntuacion}/100"><span>${esc(a.nombre)}</span><span class="bar"><i class="${a.id === "estrellas" ? "ai" : ""}" style="left:${(h0 / 24) * 100}%;width:${Math.max(3, (Math.min(len, 24 - h0) / 24) * 100)}%"></i></span><span class="num bw">${esc(day)}${w.desde.slice(11, 13)}–${w.hasta.slice(11, 13)} h</span></div>`;
  });
  box.innerHTML = `<h2>Mejor momento para</h2><p class="sub">Primera ventana buena de cada actividad; la barra marca la franja sobre las 24 h.</p><div class="bests">${rows.join("")}</div>`;
}
export { SPARK };
/** Corta una petición lenta (p. ej. los pasos de satélites si el servidor de órbitas tarda): la pantalla no se queda esperando. */
export const tmo = (pr, ms = 6000) => Promise.race([pr, new Promise((_, ko) => setTimeout(() => ko(new Error("tiempo agotado")), ms))]);
