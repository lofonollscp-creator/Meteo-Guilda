// Texto enriquecido mínimo y SEGURO para las respuestas de la IA: todo se escapa primero y solo se vuelven a poner unas pocas etiquetas conocidas.
// Admite: # títulos, **negrita**, *cursiva*, `código`, listas con - * • y 1., citas con >, tablas con |, enlaces [texto](https://…) y saltos de línea.
import { esc } from "./util.js";

const inline = (t) => esc(t)
  .replace(/`([^`\n]+)`/g, "<code>$1</code>")
  .replace(/\*\*([^*\n]+)\*\*|__([^_\n]+)__/g, (_, a, b) => `<strong>${a ?? b}</strong>`)
  .replace(/(^|[\s(])\*([^*\s][^*\n]*?)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
  .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

export function md(src) {
  const lines = String(src ?? "").replace(/\r/g, "").split("\n"), out = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    let m;
    if ((m = /^(#{1,4})\s+(.+)$/.exec(l))) { out.push(`<h4 class="md-h">${inline(m[2])}</h4>`); i++; continue; }
    if (/^\s*([-*•])\s+/.test(l) || /^\s*\d+[.)]\s+/.test(l)) {
      const ord = /^\s*\d+[.)]\s+/.test(l), re = ord ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*•]\s+(.*)$/, items = [];
      while (i < lines.length && re.test(lines[i])) { items.push(`<li>${inline(re.exec(lines[i])[1])}</li>`); i++; }
      out.push(`<${ord ? "ol" : "ul"}>${items.join("")}</${ord ? "ol" : "ul"}>`); continue;
    }
    if (/^>\s?/.test(l)) {
      const q = []; while (i < lines.length && /^>\s?/.test(lines[i])) { q.push(inline(lines[i].replace(/^>\s?/, ""))); i++; }
      out.push(`<blockquote>${q.join("<br>")}</blockquote>`); continue;
    }
    if (/^\s*\|.+\|\s*$/.test(l) && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1] || "")) {
      const head = cells(l); i += 2; const rows = [];
      while (i < lines.length && /^\s*\|.+\|\s*$/.test(lines[i])) { rows.push(cells(lines[i])); i++; }
      out.push(`<div class="md-table"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`); continue;
    }
    const p = []; while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*([-*•]|\d+[.)])\s|>)/.test(lines[i])) { p.push(inline(lines[i])); i++; }
    out.push(`<p>${p.join("<br>")}</p>`);
  }
  return out.join("");
}
