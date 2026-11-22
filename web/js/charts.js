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
    const cursor = (i) => { charts.forEach((c) => c(i)); onCursor?.(i); };
    for (const d of defs) {
      const H = d.H || 150, iw = W - M.l - M.r, ih = H - M.t - M.b;
      const box = document.createElement("div");
      box.className = "chart";
      box.innerHTML = `<h4>${d.title}<span>${d.sub || ""}</span></h4>`;
      const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": d.aria || d.title });
      box.appendChild(svg);
      host.appendChild(box);
      const readout = box.querySelector("h4 span"), sub = readout.innerHTML;
      const x = (i) => M.l + (iw * (n === 1 ? 0.5 : i)) / Math.max(n - 1, 1);
      const [lo, hi] = d.range(items);
      const ticks = niceTicks(lo, hi, d.ticks || 4), y0 = ticks[0], y1 = ticks[ticks.length - 1];
      const y = (v) => M.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih;
      ticks.forEach((t) => {
        el("line", { class: "gl", x1: M.l, x2: W - M.r, y1: y(t), y2: y(t) }, svg);
        el("text", { x: M.l - 6, y: y(t) + 3.5, "text-anchor": "end" }, svg).textContent = f0(t);
      });
      d.refs?.forEach((r) => {
        el("line", { class: "ref", x1: M.l, x2: W - M.r, y1: y(r.v), y2: y(r.v) }, svg);
        el("text", { x: W - M.r - 2, y: y(r.v) - 4, "text-anchor": "end", class: "reft" }, svg).textContent = r.label;
      });
      for (const a of axis(items, W)) {
        if (a.major) el("line", { class: "dl", x1: x(a.i), x2: x(a.i), y1: M.t, y2: H - M.b + 4 }, svg);
        el("text", { x: a.major ? x(a.i) + 4 : x(a.i), y: a.major ? H - 4 : H - 18, "text-anchor": a.major ? "start" : "middle" }, svg).textContent = a.label;
      }
      const pts = (fn) => items.map((_, i) => `${x(i)},${y(fn(i))}`).join(" ");
      if (d.draw === "band") {
        const up = items.map((_, i) => `${x(i)},${y(d.max(i))}`), dn = items.map((_, i) => `${x(i)},${y(d.min(i))}`).reverse();
        el("polygon", { class: "band", points: [...up, ...dn].join(" ") }, svg);
        el("polyline", { class: "ln", points: pts(d.mean) }, svg);
      } else if (d.draw === "bars") {
        const bw = Math.max(3, (iw / n) * 0.64);
        items.forEach((_, i) => {
          const v = d.mean(i); if (v <= 0) return;
          el("rect", { class: d.cls || "bar", x: x(i) - bw / 2, width: bw, y: y(v), height: Math.max(1.5, y(0) - y(v)), rx: 1.5 }, svg);
        });
      } else if (d.draw === "area") {
        el("polygon", { class: "area", points: `${x(0)},${y(y0)} ${pts(d.mean)} ${x(n - 1)},${y(y0)}` }, svg);
        el("polyline", { class: "ln2", points: pts(d.mean) }, svg);
      } else if (d.draw === "line") {
        el("polyline", { class: "ln", points: pts(d.mean) }, svg);
      } else if (d.draw === "wind") {
        el("polyline", { class: "gl2", points: pts(d.gust) }, svg);
        el("polyline", { class: "wl", points: pts(d.mean) }, svg);
        const every = Math.max(1, Math.round(26 / (iw / n)));
        items.forEach((_, i) => {
          const dir = d.dir?.(i);
          if (dir == null || i % every) return;
          const a = el("g", { transform: `translate(${x(i)},${H - M.b - 9}) rotate(${(dir + 180) % 360})`, class: "arrow" }, svg);
          el("path", { d: "M0,-5 L3.2,4 L0,2 L-3.2,4 Z" }, a);
        });
      }
      const cur = el("line", { class: "cur", y1: M.t, y2: H - M.b }, svg);
      const dot = el("circle", { class: "dot", r: 3.5 }, svg);
      charts.push((i) => {
        if (i == null) { cur.style.opacity = dot.style.opacity = 0; readout.innerHTML = sub; return; }
        cur.setAttribute("x1", x(i)); cur.setAttribute("x2", x(i)); cur.style.opacity = 0.35;
        dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(d.mean(i))); dot.style.opacity = 1;
        readout.innerHTML = d.tip(i);
      });
      const move = (e) => {
        const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * W;
        cursor(Math.min(n - 1, Math.max(0, Math.round(((px - M.l) / iw) * (n - 1)))));
      };
      svg.addEventListener("pointermove", move);
      svg.addEventListener("pointerdown", move);
      svg.addEventListener("pointerleave", () => cursor(null));
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
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Meteograma" }, host);
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
        el("title", {}, g).textContent = r.tip ? r.tip(i) : `${r.label}: ${r.text(v)}`;
      });
    });
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
