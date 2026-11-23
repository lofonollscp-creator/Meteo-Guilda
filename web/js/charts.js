// Gráficos SVG propios (sin dependencias): bandas, barras, viento con flechas, áreas, líneas, meteograma y perfil.
const NS = "http://www.w3.org/2000/svg";
const M = { l: 36, r: 10, t: 12, b: 36 };
const f0 = (n) => String(Math.round(n)).replace("-", "−");

export function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  parent?.appendChild(e);
  return e;
}
function niceTicks(lo, hi, n = 4) {
  const span = hi - lo || 1, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step, t = [];
  for (let v = a; v <= b + 1e-9; v += step) t.push(+v.toFixed(6));
  return t;
}

/** Curva suave (spline monótona: no se pasa de los puntos ni inventa picos) que pasa por todos los puntos [x,y]. */
function smooth(pts) {
  const n = pts.length; if (n < 3) return pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const dx = [], m = [], t = [];
  for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; m[i] = (pts[i + 1][1] - pts[i][1]) / (dx[i] || 1); }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) { if (m[i] === 0) { t[i] = t[i + 1] = 0; continue; } const a = t[i] / m[i], b = t[i + 1] / m[i], h = Math.hypot(a, b); if (h > 3) { t[i] = (3 * a / h) * m[i]; t[i + 1] = (3 * b / h) * m[i]; } }
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < n - 1; i++) { const h = dx[i] / 3; d += `C${(pts[i][0] + h).toFixed(1)},${(pts[i][1] + t[i] * h).toFixed(1)} ${(pts[i + 1][0] - h).toFixed(1)},${(pts[i + 1][1] - t[i + 1] * h).toFixed(1)} ${pts[i + 1][0].toFixed(1)},${pts[i + 1][1].toFixed(1)}`; }
  return d;
}
let gid = 0;
function gradient(defs, cls) {
  const id = `cg${++gid}`, g = el("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
  el("stop", { offset: "0%", class: `gs ${cls} a` }, g); el("stop", { offset: "100%", class: `gs ${cls} b` }, g);
  return `url(#${id})`;
}

/* ---------- Burbuja de lectura: flota SOBRE el gráfico (nunca bajo el dedo) y, en pantallas táctiles, se queda al levantar el dedo ---------- */
function makeBubble(cw) { const b = document.createElement("div"); b.className = "cbubble"; b.setAttribute("aria-hidden", "true"); cw.appendChild(b); return b; }
function showBubble(b, cw, svg, W, xSvg, html) {
  b.innerHTML = html; b.classList.add("on");
  const px = (xSvg / W) * svg.getBoundingClientRect().width, half = b.offsetWidth / 2, left = Math.min(Math.max(px, half + 2), cw.clientWidth - half - 2);
  b.style.left = left + "px"; b.style.setProperty("--ar", `${half + (px - left)}px`);
}
/** Conecta los eventos de puntero de un svg: move(i|null, touch) con el índice bajo el puntero. */
function pointerIndex(svg, W, n, iw, onIdx, onEnd) {
  const idx = (e) => { const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * W; return Math.min(n - 1, Math.max(0, Math.round(((px - M.l) / iw) * (n - 1)))); };
  svg.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse") { try { svg.setPointerCapture(e.pointerId); } catch {} } onIdx(idx(e)); });
  svg.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse" || e.buttons || e.pressure > 0) onIdx(idx(e)); });
  svg.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") onEnd(); });
}
/** Observa el ancho del contenedor y vuelve a dibujar. */
const observers = new WeakMap();
function responsive(host, draw) {
  observers.get(host)?.disconnect();
  draw();
  let w = host.clientWidth;
  const ro = new ResizeObserver(() => { if (host.clientWidth > 0 && Math.abs(host.clientWidth - w) > 8) { w = host.clientWidth; draw(); } });
  ro.observe(host);
  observers.set(host, ro);
}
const widthOf = (host) => Math.max(280, Math.round(host.clientWidth || 720));

/**
 * Gráficos con eje X común y cursor sincronizado.
 * items: puntos del eje (tramos o muestras). axis(items,W): [{i,label,major}] etiquetas del eje X.
 * defs: [{title, sub, draw:'band'|'bars'|'wind'|'area'|'line', H, ticks, range(items), mean(i), min, max, gust, dir, tip(i), color}]
 */
export function renderCharts(host, items, defs, axis, onCursor) {
  responsive(host, () => {
    const W = widthOf(host), n = items.length, charts = [];
    host.innerHTML = "";
    const cursor = (i, src) => { charts.forEach((c) => c(i, c === src)); onCursor?.(i); };
    for (const d of defs) {
      const lane = d.draw === "wind" ? 18 : 0;   // carril propio para las flechas: no se pisan con la línea
      const H = (d.H || 150) + lane, iw = W - M.l - M.r, ih = H - M.t - M.b - lane;
      const box = document.createElement("div");
      box.className = "chart";
      box.innerHTML = `<h4>${d.title}<span>${d.sub || ""}</span></h4>`;
      const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": d.aria || d.title });
      const cw = document.createElement("div");
      cw.className = "cw"; cw.appendChild(svg);
      box.appendChild(cw);
      host.appendChild(box);
      const bubble = makeBubble(cw);
      const x = (i) => M.l + (iw * (n === 1 ? 0.5 : i)) / Math.max(n - 1, 1);
      const [lo, hi] = d.range(items);
      const ticks = niceTicks(lo, hi, d.ticks || 4), y0 = ticks[0], y1 = ticks[ticks.length - 1];
      const y = (v) => M.t + lane + ih - ((v - y0) / (y1 - y0 || 1)) * ih;
      const defsEl = el("defs", {}, svg);
      if (typeof items[0]?.time === "string" && n > 2) {          // la noche (21–6 h) se ve más oscura: se entiende de un vistazo qué es de día
        const half = iw / Math.max(n - 1, 1) / 2;
        items.forEach((it, i) => { const h = +it.time.slice(11, 13); if (h >= 21 || h < 6) el("rect", { class: "night", x: Math.max(M.l, x(i) - half), width: Math.min(W - M.r, x(i) + half) - Math.max(M.l, x(i) - half), y: M.t + lane, height: ih }, svg); });
      }
      ticks.forEach((t) => {
        el("line", { class: "gl", x1: M.l, x2: W - M.r, y1: y(t), y2: y(t) }, svg);
        el("text", { x: M.l - 6, y: y(t) + 3.5, "text-anchor": "end" }, svg).textContent = d.tickFmt ? d.tickFmt(t) : f0(t);
      });
      d.refs?.forEach((r) => {
        el("line", { class: "ref", x1: M.l, x2: W - M.r, y1: y(r.v), y2: y(r.v) }, svg);
        el("text", { x: W - M.r - 2, y: y(r.v) - 4, "text-anchor": "end", class: "reft" }, svg).textContent = r.label;
      });
      for (const a of axis(items, W)) {
        if (a.major) el("line", { class: "dl", x1: x(a.i), x2: x(a.i), y1: M.t, y2: H - M.b + 4 }, svg);
        el("text", { x: a.major ? x(a.i) + 4 : x(a.i), y: a.major ? H - 4 : H - 18, "text-anchor": a.major ? "start" : "middle" }, svg).textContent = a.label;
      }
      const P = (fn) => items.map((_, i) => [x(i), y(fn(i))]);
      const pts = (fn) => P(fn).map((p) => p.join(",")).join(" ");
      const peaks = (fn) => {                                      // máximo y mínimo señalados con su valor
        if (n < 8) return;
        const v = items.map((_, i) => fn(i)); let a = 0, b = 0; v.forEach((q, i) => { if (q > v[a]) a = i; if (q < v[b]) b = i; });
        if (v[a] - v[b] < (y1 - y0) * 0.12) return;
        [[a, -9], [b, 15]].forEach(([i, dy]) => {
          el("circle", { class: "pk", cx: x(i), cy: y(v[i]), r: 3 }, svg);
          const tx = Math.min(W - M.r - 12, Math.max(M.l + 12, x(i)));
          el("text", { class: "pkt", x: tx, y: y(v[i]) + dy, "text-anchor": "middle" }, svg).textContent = f0(v[i]) + (d.unit || "");
        });
      };
      if (d.draw === "band") {
        const up = P(d.max), dn = P(d.min).reverse();
        el("path", { class: "band", d: `${smooth(up)}L${dn[0][0]},${dn[0][1]}${smooth(dn).slice(1).replace(/^[^C]*/, "")}Z`, style: `fill:${gradient(defsEl, "acc")}` }, svg);
        el("path", { class: "ln", d: smooth(P(d.mean)) }, svg);
        peaks(d.mean);
      } else if (d.draw === "bars") {
        const bw = Math.max(3, (iw / n) * 0.64);
        items.forEach((_, i) => {
          const v = d.mean(i); if (v <= 0) return;
          el("rect", { class: d.cls || "bar", x: x(i) - bw / 2, width: bw, y: y(v), height: Math.max(2, y(0) - y(v)), rx: Math.min(3, bw / 2) }, svg);
        });
      } else if (d.draw === "area") {
        const c = P(d.mean);
        el("path", { class: "area", d: `${smooth(c)}L${x(n - 1)},${y(y0)}L${x(0)},${y(y0)}Z`, style: `fill:${gradient(defsEl, "ink")}` }, svg);
        el("path", { class: "ln2", d: smooth(c) }, svg);
      } else if (d.draw === "line") {
        el("path", { class: "ln", d: smooth(P(d.mean)) }, svg);
        peaks(d.mean);
      } else if (d.draw === "wind") {
        const g = P(d.gust), m = P(d.mean);
        el("path", { class: "wfill", d: `${smooth(g)}L${g[n - 1][0]},${m[n - 1][1]}${smooth(m.slice().reverse()).slice(1).replace(/^[^C]*/, "")}Z`, style: `fill:${gradient(defsEl, "wind")}` }, svg);
        el("path", { class: "gl2", d: smooth(g) }, svg);
        el("path", { class: "wl", d: smooth(m) }, svg);
        const every = Math.max(1, Math.round(26 / (iw / n)));
        items.forEach((_, i) => {
          const dir = d.dir?.(i);
          if (dir == null || i % every) return;
          const a = el("g", { transform: `translate(${x(i)},${M.t + 7}) rotate(${(dir + 180) % 360})`, class: "arrow" }, svg);
          el("path", { d: "M0,-5 L3.2,4 L0,2 L-3.2,4 Z" }, a);
        });
      }
      const cur = el("line", { class: "cur", y1: M.t, y2: H - M.b }, svg);
      const dot = el("circle", { class: "dot", r: 3.5 }, svg);
      const me = (i, active) => {
        if (i == null) { cur.style.opacity = dot.style.opacity = 0; bubble.classList.remove("on"); return; }
        cur.setAttribute("x1", x(i)); cur.setAttribute("x2", x(i)); cur.style.opacity = 0.35;
        dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(d.mean(i))); dot.style.opacity = 1;
        if (active) showBubble(bubble, cw, svg, W, x(i), d.tip(i)); else bubble.classList.remove("on");
      };
      charts.push(me);
      pointerIndex(svg, W, n, iw, (i) => cursor(i, me), () => cursor(null));
    }
  });
}

/* ---------- Meteograma: filas de celdas coloreadas ---------- */
const lerp = (a, b, t) => a + (b - a) * t;
export function ramp(stops, v) {
  if (v <= stops[0][0]) return `rgb(${stops[0][1].join(",")})`;
  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) {
      const [v0, c0] = stops[i - 1], [v1, c1] = stops[i], t = (v - v0) / (v1 - v0 || 1);
      return `rgb(${c0.map((c, k) => Math.round(lerp(c, c1[k], t))).join(",")})`;
    }
  }
  const c = stops[stops.length - 1][1];
  return `rgb(${c.join(",")})`;
}
export const SCALES = {
  temp: [[-10, [64, 98, 160]], [0, [118, 160, 205]], [8, [170, 205, 190]], [16, [232, 218, 150]], [24, [236, 170, 90]], [32, [214, 94, 70]], [40, [160, 40, 60]]],
  rain: [[0, [236, 240, 244]], [0.2, [190, 212, 236]], [1, [110, 150, 205]], [3, [60, 90, 170]], [8, [45, 50, 130]]],
  cloud: [[0, [250, 246, 232]], [40, [222, 224, 226]], [100, [150, 158, 168]]],
  wind: [[0, [238, 240, 238]], [20, [226, 214, 170]], [40, [226, 160, 80]], [65, [200, 80, 60]], [100, [140, 30, 60]]],
};
/** rows: [{label, val(i), color(v), text(v)}] */
export function renderStrip(host, items, rows, axis) {
  responsive(host, () => {
    const W = widthOf(host), n = items.length, lw = 62, rh = 24, gap = 3, hd = 22;
    const cw = (W - lw) / n, H = hd + rows.length * (rh + gap);
    host.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "cw"; host.appendChild(wrap);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Meteograma" }, wrap), bubble = makeBubble(wrap);
    const mark = el("rect", { y: hd - 2, height: rows.length * (rh + gap) + 2, width: cw, rx: 3, class: "smark", opacity: 0 }, svg);
    for (const a of axis(items, W)) if (!a.major) el("text", { x: lw + a.i * cw + cw / 2, y: 12, "text-anchor": "middle" }, svg).textContent = a.label;
    rows.forEach((r, k) => {
      const y = hd + k * (rh + gap);
      el("text", { x: 0, y: y + rh / 2 + 4, class: "rl" }, svg).textContent = r.label;
      items.forEach((_, i) => {
        const v = r.val(i);
        if (v == null) return;
        const g = el("g", {}, svg);
        el("rect", { x: lw + i * cw + 0.75, y, width: Math.max(1, cw - 1.5), height: rh, rx: 3, fill: r.color(v) }, g);
        if (cw >= 27) el("text", { x: lw + i * cw + cw / 2, y: y + rh / 2 + 3.5, "text-anchor": "middle", class: "cell", fill: r.ink ? r.ink(v) : "#14191f" }, g).textContent = r.text(v);
      });
    });
    // lectura al tocar o pasar el ratón: hora y valores de todas las filas, en una burbuja sobre el meteograma
    const at = (e) => { const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * W; return Math.floor((px - lw) / cw); };
    const show = (e) => {
      const i = at(e); if (i < 0 || i >= n) return;
      const d = new Date(items[i].time), when = `${d.toLocaleDateString("es", { weekday: "short", day: "numeric" }).replace(".", "")} ${String(d.getHours()).padStart(2, "0")}h`;
      const vals = rows.map((r) => { const v = r.val(i); return v == null ? "" : `${r.label.replace(/ ?(°C|mm|%)$/, "")} <b>${r.text(v) || "0"}</b>`; }).filter(Boolean).join(" · ");
      mark.setAttribute("x", lw + i * cw); mark.setAttribute("opacity", 0.18);
      showBubble(bubble, wrap, svg, W, lw + i * cw + cw / 2, `${when} · ${vals}`);
    };
    svg.addEventListener("pointerdown", (e) => { show(e); });
    svg.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse" || e.buttons) show(e); });
    svg.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") { bubble.classList.remove("on"); mark.setAttribute("opacity", 0); } });
  });
}

/* ---------- Contraste de modelos: una línea por modelo + la media ---------- */
export const MODEL_COLORS = ["#2a6fdb", "#d9482b", "#2e9e5b", "#8f55c7", "#a1662a", "#d6489c", "#12a3b5", "#a4a31c"];
/**
 * series: [{id, label, color, vals[]}], mean[]; opts: {unit, fmt(v), H, ticks, hidden:Set(id), zero}. Devuelve nada; redibuja en host.
 */
export function renderModels(host, items, series, mean, axis, opts = {}) {
  responsive(host, () => {
    const W = widthOf(host), n = items.length, H = opts.H || 190, iw = W - M.l - M.r, ih = H - M.t - M.b, hidden = opts.hidden || new Set();
    host.innerHTML = "";
    const cw = document.createElement("div");
    cw.className = "cw"; host.appendChild(cw);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Contraste de modelos" }, cw), bubble = makeBubble(cw);
    const vis = series.filter((s) => !hidden.has(s.id)), all = [...vis.flatMap((s) => s.vals), ...mean].filter((v) => v != null);
    const lo = opts.zero ? 0 : Math.min(...all), hi = Math.max(...all, opts.zero ? 1 : lo + 1);
    const ticks = niceTicks(lo, hi, opts.ticks || 4), y0 = ticks[0], y1 = ticks.at(-1);
    const x = (i) => M.l + (iw * (n === 1 ? 0.5 : i)) / Math.max(n - 1, 1), y = (v) => M.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih;
    ticks.forEach((tk) => { el("line", { class: "gl", x1: M.l, x2: W - M.r, y1: y(tk), y2: y(tk) }, svg); el("text", { x: M.l - 6, y: y(tk) + 3.5, "text-anchor": "end" }, svg).textContent = f0(tk); });
    for (const a of axis(items, W)) {
      if (a.major) el("line", { class: "dl", x1: x(a.i), x2: x(a.i), y1: M.t, y2: H - M.b + 4 }, svg);
      el("text", { x: a.major ? x(a.i) + 4 : x(a.i), y: a.major ? H - 4 : H - 18, "text-anchor": a.major ? "start" : "middle" }, svg).textContent = a.label;
    }
    const line = (vals, cls, color) => {
      // los huecos (None) cortan la línea
      let seg = [];
      const flush = () => { if (seg.length > 1) el("polyline", { class: cls, points: seg.join(" "), ...(color ? { stroke: color } : {}) }, svg); seg = []; };
      vals.forEach((v, i) => { if (v == null) flush(); else seg.push(`${x(i)},${y(v)}`); });
      flush();
    };
    vis.forEach((s) => line(s.vals, "ml", s.color));
    line(mean, "ml mean");
    const cur = el("line", { class: "cur", y1: M.t, y2: H - M.b }, svg);
    const show = (i) => {
      cur.setAttribute("x1", x(i)); cur.setAttribute("x2", x(i)); cur.style.opacity = 0.35;
      const f = opts.fmt || f0, vs = vis.map((s) => [s, s.vals[i]]).filter(([, v]) => v != null).sort((a, b) => a[1] - b[1]);
      const ext = vs.length > 1 ? `<br>${opts.lowLabel || "menor"}: ${vs[0][0].label} <b>${f(vs[0][1])}</b> · ${opts.highLabel || "mayor"}: ${vs.at(-1)[0].label} <b>${f(vs.at(-1)[1])}</b>` : "";
      showBubble(bubble, cw, svg, W, x(i), `${opts.when?.(i) || ""} · media <b>${f(mean[i])}</b>${ext}`);
    };
    pointerIndex(svg, W, n, iw, show, () => { cur.style.opacity = 0; bubble.classList.remove("on"); });
  });
}

/* ---------- Perfil de elevación coloreado por riesgo ---------- */
export function renderProfile(host, route, onCursor) {
  responsive(host, () => {
    const W = widthOf(host), H = 190, iw = W - M.l - M.r, ih = H - M.t - 26;
    const P = route.perfil, tot = route.distancia_km;
    host.innerHTML = '<div class="chart"><h4>Perfil y riesgo<span></span></h4></div>';
    const box = host.firstChild, readout = box.querySelector("span"), sub = "";
    if (!P.length) { box.insertAdjacentHTML("beforeend", '<p class="empty">Perfil de elevación no disponible.</p>'); return; }
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Perfil de elevación de la ruta" }, box);
    const eles = P.map((p) => p.ele), lo = Math.min(...eles), hi = Math.max(...eles);
    const ticks = niceTicks(lo - 20, hi + 20, 3), y0 = ticks[0], y1 = ticks.at(-1);
    const x = (d) => M.l + (d / tot) * iw, y = (e) => M.t + ih - ((e - y0) / (y1 - y0 || 1)) * ih;
    ticks.forEach((t) => { el("line", { class: "gl", x1: M.l, x2: W - M.r, y1: y(t), y2: y(t) }, svg); el("text", { x: M.l - 6, y: y(t) + 3.5, "text-anchor": "end" }, svg).textContent = f0(t); });
    const S = route.muestras, lvAt = (d) => { let k = 0; for (let i = 0; i < S.length; i++) if (S[i].d_km <= d + 1e-6) k = i; return S[k].nivel ?? 0; };
    // segmentos coloreados según el nivel de la muestra más cercana anterior
    for (let i = 1; i < P.length; i++) {
      const a = P[i - 1], b = P[i], lv = lvAt((a.d_km + b.d_km) / 2);
      el("polygon", { class: `seg lv${lv}`, points: `${x(a.d_km)},${y(a.ele)} ${x(b.d_km)},${y(b.ele)} ${x(b.d_km)},${y(y0)} ${x(a.d_km)},${y(y0)}` }, svg);
    }
    el("polyline", { class: "ln", points: P.map((p) => `${x(p.d_km)},${y(p.ele)}`).join(" ") }, svg);
    const step = tot > 40 ? 10 : tot > 15 ? 5 : tot > 6 ? 2 : 1;
    for (let d = 0; d <= tot + 1e-6; d += step) el("text", { x: x(d), y: H - 8, "text-anchor": "middle" }, svg).textContent = `${d} km`;
    S.forEach((s) => el("circle", { class: `sdot lv${s.nivel ?? 0}`, cx: x(s.d_km), cy: y(P.reduce((b, p) => (Math.abs(p.d_km - s.d_km) < Math.abs(b.d_km - s.d_km) ? p : b)).ele), r: 4 }, svg));
    const cur = el("line", { class: "cur", y1: M.t, y2: M.t + ih }, svg), dot = el("circle", { class: "dot", r: 3.5 }, svg);
    const move = (e) => {
      const r = svg.getBoundingClientRect(), d = Math.min(tot, Math.max(0, (((e.clientX - r.left) / r.width) * W - M.l) / iw * tot));
      const p = P.reduce((b, q) => (Math.abs(q.d_km - d) < Math.abs(b.d_km - d) ? q : b));
      cur.setAttribute("x1", x(p.d_km)); cur.setAttribute("x2", x(p.d_km)); cur.style.opacity = 0.35;
      dot.setAttribute("cx", x(p.d_km)); dot.setAttribute("cy", y(p.ele)); dot.style.opacity = 1;
      const s = S.reduce((b, q) => (Math.abs(q.d_km - d) < Math.abs(b.d_km - d) ? q : b));
      readout.innerHTML = `<b>${p.d_km.toFixed(1)} km</b> · ${p.ele} m · ${s.t ? new Date(s.t).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }) : ""}`;
      onCursor?.(p.d_km);
    };
    const leave = () => { cur.style.opacity = dot.style.opacity = 0; readout.innerHTML = sub; onCursor?.(null); };
    svg.addEventListener("pointermove", move); svg.addEventListener("pointerdown", move); svg.addEventListener("pointerleave", leave);
  });
}

export const sparkline = (vals, w = 84, h = 20) => {
  const v = vals.filter((x) => x != null);
  if (v.length < 2) return "";
  const lo = Math.min(...v), hi = Math.max(...v);
  const pts = v.map((y, i) => `${(i / (v.length - 1)) * w},${h - 2 - ((y - lo) / (hi - lo || 1)) * (h - 4)}`);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="M${pts.join("L")}"/></svg>`;
};

/* ---------- Rosa de los vientos (frecuencia y velocidad media por sector de 22,5°) ---------- */
const SECT = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"];
export function renderRose(host, steps) {
  const n = 16, cnt = Array(n).fill(0), sp = Array(n).fill(0);
  steps.forEach((s) => { if (s.viento_dir == null) return; const k = Math.round(s.viento_dir / 22.5) % n; cnt[k]++; sp[k] += s.viento_kmh?.mean ?? 0; });
  const total = cnt.reduce((a, b) => a + b, 0);
  if (!total) { host.innerHTML = '<p class="empty">Sin datos de dirección.</p>'; return; }
  const S = 240, c = S / 2, R = 92, max = Math.max(...cnt);
  host.innerHTML = "";
  const svg = el("svg", { viewBox: `0 0 ${S} ${S}`, role: "img", "aria-label": "Rosa de los vientos" }, host);
  [0.33, 0.66, 1].forEach((f) => el("circle", { cx: c, cy: c, r: R * f, class: "rose-ring" }, svg));
  for (let k = 0; k < n; k++) {
    if (!cnt[k]) continue;
    const r = R * Math.sqrt(cnt[k] / max), a0 = ((k - 0.42) * 22.5 - 90) * Math.PI / 180, a1 = ((k + 0.42) * 22.5 - 90) * Math.PI / 180;
    const p = (a, rr) => `${c + rr * Math.cos(a)},${c + rr * Math.sin(a)}`;
    const w = el("path", { d: `M${c},${c} L${p(a0, r)} A${r},${r} 0 0 1 ${p(a1, r)} Z`, fill: ramp(SCALES.wind, sp[k] / cnt[k]), class: "petal" }, svg);
    el("title", {}, w).textContent = `${SECT[k]}: ${Math.round((cnt[k] / total) * 100)}% del tiempo, ${Math.round(sp[k] / cnt[k])} km/h de media`;
  }
  [["N", 0], ["E", 90], ["S", 180], ["O", 270]].forEach(([t, d]) => {
    const a = (d - 90) * Math.PI / 180;
    el("text", { x: c + (R + 14) * Math.cos(a), y: c + (R + 14) * Math.sin(a) + 4, "text-anchor": "middle", class: "rose-l" }, svg).textContent = t;
  });
}

/* ---------- Fase lunar dibujada (sin emojis) ---------- */
export function moonSvg(age, size = 56) {
  const P = 29.530588853, k = Math.cos((2 * Math.PI * age) / P), r = 22, c = 28, waxing = age < P / 2;
  const sweep = k > 0 ? 0 : 1, rx = Math.abs(k) * r;
  const lit = `M${c},${c - r} A${r},${r} 0 0 1 ${c},${c + r} A${rx},${r} 0 0 ${sweep} ${c},${c - r}Z`;
  return `<svg viewBox="0 0 56 56" width="${size}" height="${size}" role="img" aria-label="Fase lunar"><circle cx="${c}" cy="${c}" r="${r}" class="moon-dark"/><path d="${lit}" class="moon-lit" ${waxing ? "" : `transform="translate(56 0) scale(-1 1)"`}/><circle cx="${c}" cy="${c}" r="${r}" class="moon-ring"/></svg>`;
}
export function sunArc(sunrise, sunset, now = new Date()) {
  // arco de luz del día: posición actual del Sol entre orto y ocaso
  const W = 220, H = 80, t0 = sunrise.getTime(), t1 = sunset.getTime(), f = Math.min(1, Math.max(0, (now - t0) / (t1 - t0)));
  const x = (u) => 10 + u * (W - 20), y = (u) => 66 - Math.sin(u * Math.PI) * 52;
  const pts = Array.from({ length: 41 }, (_, i) => `${x(i / 40)},${y(i / 40)}`).join(" ");
  const day = now >= t0 && now <= t1;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Recorrido del Sol"><line x1="6" x2="${W - 6}" y1="66" y2="66" class="gl"/><polyline points="${pts}" fill="none" class="sun-path"/>${day ? `<circle cx="${x(f)}" cy="${y(f)}" r="6" class="sun-dot"/>` : ""}</svg>`;
}
