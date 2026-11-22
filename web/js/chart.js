// Gráficos SVG sin dependencias. Los tramos son de 3 h; cursor sincronizado entre gráficos.
const NS = "http://www.w3.org/2000/svg";
const M = { l: 34, r: 10, t: 10, b: 36 };
const f0 = (n) => String(Math.round(n)).replace("-", "−");

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  parent?.appendChild(e);
  return e;
}
function niceTicks(lo, hi, n = 4) {
  const span = hi - lo || 1, raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step;
  const t = [];
  for (let v = a; v <= b + 1e-9; v += step) t.push(+v.toFixed(6));
  return t;
}
const parts = (iso) => ({ d: iso.slice(0, 10), h: +iso.slice(11, 13) });
const dayLabel = (d) => {
  const [y, m, dd] = d.split("-").map(Number);
  return new Date(y, m - 1, dd).toLocaleDateString("es", { weekday: "short", day: "numeric" }).replace(".", "");
};

/**
 * @param {HTMLElement} host
 * @param {object[]} steps  tramos de la API
 * @param {object[]} defs   [{id,title,unit,H,draw:'band'|'bars'|'wind',tip:(i)=>html}]
 */
const observers = new WeakMap();
export function renderCharts(host, steps, defs) {
  observers.get(host)?.disconnect();
  draw(host, steps, defs);
  let w = host.clientWidth;
  const ro = new ResizeObserver(() => {
    if (Math.abs(host.clientWidth - w) > 8 && host.clientWidth > 0) { w = host.clientWidth; draw(host, steps, defs); }
  });
  ro.observe(host);
  observers.set(host, ro);
}

function draw(host, steps, defs) {
  const W = Math.max(280, Math.round(host.clientWidth || 720));
  host.innerHTML = "";
  const n = steps.length;
  const charts = [];
  const cursor = (i) => charts.forEach((c) => c.set(i));

  for (const d of defs) {
    const H = d.H || 150, iw = W - M.l - M.r, ih = H - M.t - M.b;
    const box = document.createElement("div");
    box.className = "chart";
    box.innerHTML = `<h4>${d.title}<span>${d.sub || ""}</span></h4>`;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": d.aria || d.title });
    box.appendChild(svg);
    const readout = box.querySelector("h4 span");
    const sub = readout.innerHTML;
    host.appendChild(box);

    const x = (i) => M.l + (iw * (n === 1 ? 0.5 : i)) / Math.max(n - 1, 1);
    const [lo, hi] = d.range(steps);
    const ticks = niceTicks(lo, hi, d.ticks || 4);
    const y0 = ticks[0], y1 = ticks[ticks.length - 1];
    const y = (v) => M.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih;

    ticks.forEach((t) => {
      el("line", { class: "gl", x1: M.l, x2: W - M.r, y1: y(t), y2: y(t) }, svg);
      el("text", { x: M.l - 6, y: y(t) + 3.5, "text-anchor": "end" }, svg).textContent = f0(t);
    });
    // cambio de día (línea + etiqueta) y hora cada 6 h
    steps.forEach((s, i) => {
      const p = parts(s.time);
      if (i > 0 && p.d !== parts(steps[i - 1].time).d) {
        el("line", { class: "dl", x1: x(i), x2: x(i), y1: M.t, y2: H - M.b + 4 }, svg);
        el("text", { x: x(i) + 4, y: H - 4 }, svg).textContent = dayLabel(p.d);
      }
      if (i % 2 === 0) el("text", { x: x(i), y: H - 18, "text-anchor": "middle" }, svg).textContent = String(p.h).padStart(2, "0") + "h";
    });
    const firstChange = steps.findIndex((s, i) => i > 0 && parts(s.time).d !== parts(steps[0].time).d);
    if (firstChange > 3 || firstChange < 0) el("text", { x: M.l, y: H - 4 }, svg).textContent = dayLabel(parts(steps[0].time).d);

    if (d.draw === "band") {
      const up = steps.map((s, i) => `${x(i)},${y(d.max(s))}`);
      const dn = steps.map((s, i) => `${x(i)},${y(d.min(s))}`).reverse();
      el("polygon", { class: "band", points: [...up, ...dn].join(" ") }, svg);
      el("polyline", { class: "ln", points: steps.map((s, i) => `${x(i)},${y(d.mean(s))}`).join(" ") }, svg);
    } else if (d.draw === "bars") {
      const bw = Math.max(4, (iw / n) * 0.62);
      steps.forEach((s, i) => {
        const v = d.mean(s);
        if (v <= 0) return;
        const top = y(v);
        el("rect", { class: "bar", x: x(i) - bw / 2, width: bw, y: top, height: Math.max(1.5, y(0) - top), rx: 1 }, svg);
      });
    } else if (d.draw === "wind") {
      el("polyline", { class: "gl2", points: steps.map((s, i) => `${x(i)},${y(d.gust(s))}`).join(" ") }, svg);
      el("polyline", { class: "wl", points: steps.map((s, i) => `${x(i)},${y(d.mean(s))}`).join(" ") }, svg);
    }

    const cur = el("line", { class: "cur", y1: M.t, y2: H - M.b }, svg);
    const dot = el("circle", { class: "dot", r: 3.5 }, svg);
    const set = (i) => {
      if (i == null) { cur.style.opacity = dot.style.opacity = 0; readout.innerHTML = sub; return; }
      cur.setAttribute("x1", x(i)); cur.setAttribute("x2", x(i));
      cur.style.opacity = 0.35;
      dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(d.mean(steps[i]))); dot.style.opacity = 1;
      const p = parts(steps[i].time);
      readout.innerHTML = `${dayLabel(p.d)} ${String(p.h).padStart(2, "0")}\u2013${String((p.h + 3) % 24).padStart(2, "0")} h · ${d.tip(steps[i])}`;
    };
    charts.push({ set });
    const onMove = (e) => {
      const r = svg.getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * W;
      cursor(Math.min(n - 1, Math.max(0, Math.round(((px - M.l) / iw) * (n - 1)))));
    };
    svg.addEventListener("pointermove", onMove);
    svg.addEventListener("pointerdown", onMove);
    svg.addEventListener("pointerleave", () => cursor(null));
  }
}
