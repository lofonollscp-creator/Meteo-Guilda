// Texto enriquecido SEGURO para las respuestas de la IA: todo se escapa primero y solo se vuelven a poner etiquetas conocidas.
// Admite lo que suelen escribir los modelos: títulos (#…######), **negrita**, *cursiva*, ***ambas***, ~~tachado~~, `código` y bloques ```,
// listas con viñeta, numeradas, anidadas y de tareas, citas, separadores (---), tablas con o sin barras exteriores (alineación :--:,
// celdas con \|, filas incompletas), enlaces [texto](https://… o #/…), direcciones sueltas y saltos <br>.
import { esc } from "./util.js";

const SAFE_URL = /^(https?:\/\/[^\s<>"']+|#\/[^\s<>"']*)$/;
const link = (text, url) => (SAFE_URL.test(url)
  ? `<a href="${url}"${url.startsWith("#") ? "" : ' target="_blank" rel="noopener noreferrer"'}>${text}</a>` : text);

/** Formato dentro de una línea. Recibe texto SIN escapar. */
export function inline(src) {
  const keep = [], hold = (h) => `\u0000${keep.push(h) - 1}\u0000`;
  let t = String(src ?? "").replace(/\u0000/g, "");
  t = t.replace(/`([^`\n]+)`/g, (_, c) => hold(`<code>${esc(c)}</code>`));                                   // código: intacto
  t = t.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_, a, u) => hold(link(inline(a), esc(u))));                 // [texto](url)
  t = t.replace(/(^|[\s(])(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g, (_, p, u) => p + hold(link(esc(u), esc(u))));   // url suelta
  t = esc(t).replace(/&lt;br\s*\/?&gt;/gi, "<br>");
  t = t.replace(/\*\*\*(?=\S)([^\n]*?\S)\*\*\*/g, "<strong><em>$1</em></strong>")
    .replace(/\*\*(?=\S)([^\n]*?\S)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^\w])__(?=\S)([^\n]*?\S)__(?!\w)/g, "$1<strong>$2</strong>")
    .replace(/(^|[^\w*])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/g, "$1<em>$2</em>")
    .replace(/~~(?=\S)([^\n]*?\S)~~/g, "<del>$1</del>");
  return t.replace(/\u0000(\d+)\u0000/g, (_, n) => keep[+n]);
}

/* ---------- tablas ---------- */
const SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;
const splitRow = (l) => { let s = l.trim().replace(/\\\|/g, "\u0001"); if (s.startsWith("|")) s = s.slice(1); if (s.endsWith("|")) s = s.slice(0, -1); return s.split("|").map((c) => c.trim().replace(/\u0001/g, "|")); };
const isNum = (c) => /^[−+-]?\s?\d[\d.,\s]*\s?(%|°C?|ºC?|°F|km\/h|m\/s|nudos|kn|mm|cm|m|km|hPa|MHz|kHz|GHz|W|dB|dBm|h|min|s)?$/i.test(c.trim()) && c.trim().length < 18;
const isTableStart = (lines, i) => lines[i].includes("|") && SEP.test(lines[i + 1] || "") && (lines[i + 1].includes("|") || splitRow(lines[i]).length === 1) && /-/.test(lines[i + 1]);
function table(lines, i) {
  const head = splitRow(lines[i]), al = splitRow(lines[i + 1]).map((s) => (/^:-+:$/.test(s) ? "center" : /-+:$/.test(s) ? "right" : /^:-+/.test(s) ? "left" : ""));
  const rows = []; i += 2;
  while (i < lines.length && lines[i].trim() && lines[i].includes("|") && !SEP.test(lines[i])) { rows.push(splitRow(lines[i])); i++; }
  const n = Math.max(head.length, ...rows.map((r) => r.length));
  const numCol = Array.from({ length: n }, (_, k) => rows.length > 0 && rows.every((r) => !r[k] || r[k] === "–" || r[k] === "-" || isNum(r[k])) && rows.some((r) => r[k] && isNum(r[k])));
  const cls = (k) => { const a = al[k] || (numCol[k] ? "right" : ""); return `${numCol[k] ? ' class="n"' : ""}${a ? ` style="text-align:${a}"` : ""}`; };
  const cell = (tag, r) => Array.from({ length: n }, (_, k) => `<${tag}${cls(k)}>${inline(r[k] ?? "")}</${tag}>`).join("");
  return [`<div class="md-table" role="region" aria-label="Tabla" tabindex="0"><table><thead><tr>${cell("th", head)}</tr></thead><tbody>${rows.map((r) => `<tr>${cell("td", r)}</tr>`).join("")}</tbody></table></div>`, i];
}

/* ---------- listas (anidadas por sangría) ---------- */
const ITEM = /^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$/;
const ind = (l) => l.match(/^\s*/)[0].replace(/\t/g, "    ").length;
function list(lines, i) {
  const base = ind(lines[i]), ord = /\d/.test(ITEM.exec(lines[i])[2]), start = ord ? parseInt(ITEM.exec(lines[i])[2], 10) : 1, items = [];
  while (i < lines.length) {
    const m = ITEM.exec(lines[i]);
    if (!m || ind(lines[i]) < base) break;
    if (ind(lines[i]) > base) { const [sub, j] = list(lines, i); if (items.length) items[items.length - 1].kids.push(sub); else items.push({ txt: "", kids: [sub] }); i = j; continue; }
    if (/\d/.test(m[2]) !== ord) break;
    const it = { txt: m[3], kids: [] }; i++;
    while (i < lines.length && lines[i].trim() && !ITEM.test(lines[i]) && ind(lines[i]) > base && !isTableStart(lines, i)) { it.txt += "\n" + lines[i].trim(); i++; }   // línea que continúa el punto
    items.push(it);
    if (i < lines.length && !lines[i].trim() && i + 1 < lines.length && ITEM.test(lines[i + 1]) && ind(lines[i + 1]) >= base) i++;   // lista «suelta» con líneas en blanco
  }
  const li = items.map(({ txt, kids }) => {
    const task = /^\[([ xX])\]\s+/.exec(txt);
    const body = task ? `<span class="md-task${task[1] === " " ? "" : " ok"}" aria-hidden="true"></span>${inline(txt.slice(task[0].length)).replace(/\n/g, "<br>")}` : inline(txt).replace(/\n/g, "<br>");
    return `<li${task ? ' class="task"' : ""}>${body}${kids.join("")}</li>`;
  }).join("");
  return [ord ? `<ol${start !== 1 ? ` start="${start}"` : ""}>${li}</ol>` : `<ul>${li}</ul>`, i];
}

const HR = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;

/** Markdown → HTML seguro. */
export function md(src) {
  const lines = String(src ?? "").replace(/\r/g, "").split("\n"), out = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    let m;
    if ((m = /^\s*(```|~~~)\s*([\w+-]*)\s*$/.exec(l))) {                                   // bloque de código
      const fence = m[1], code = []; i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence)) { code.push(lines[i]); i++; }
      i++; out.push(`<pre class="md-pre"><code>${esc(code.join("\n"))}</code></pre>`); continue;
    }
    if ((m = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/.exec(l))) { const lv = m[1].length <= 2 ? 3 : 4; out.push(`<h${lv} class="md-h md-h${m[1].length}">${inline(m[2])}</h${lv}>`); i++; continue; }
    if (HR.test(l)) { out.push("<hr>"); i++; continue; }
    if (isTableStart(lines, i)) { const [h, j] = table(lines, i); out.push(h); i = j; continue; }
    if (ITEM.test(l)) { const [h, j] = list(lines, i); out.push(h); i = j; continue; }
    if (/^\s*>\s?/.test(l)) {
      const q = []; while (i < lines.length && /^\s*>\s?/.test(lines[i])) { q.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      out.push(`<blockquote>${md(q.join("\n"))}</blockquote>`); continue;
    }
    if ((m = /^\s*\*\*([^*]+?):?\*\*:?\s*$/.exec(l)) && m[1].length < 80) { out.push(`<h4 class="md-h md-h4">${inline(m[1])}</h4>`); i++; continue; }   // «**Título**» solo en su línea
    const p = [];
    while (i < lines.length && lines[i].trim() && !ITEM.test(lines[i]) && !/^\s{0,3}(#{1,6}\s|>|```|~~~)/.test(lines[i]) && !isTableStart(lines, i) && !HR.test(lines[i])) { p.push(inline(lines[i])); i++; }
    if (p.length) out.push(`<p>${p.join("<br>")}</p>`); else { out.push(`<p>${inline(lines[i])}</p>`); i++; }
  }
  return out.join("");
}
