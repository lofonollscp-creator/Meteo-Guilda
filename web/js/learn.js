import { $, $$, esc } from "./util.js";
import { GROUPS, TOPICS } from "./learn-data.js";
import { icon } from "./icons.js";

let ctx;
export function mount(c) {
  ctx = c;
  ctx.cola.innerHTML = `${ctx.brand("learn", "Meteorología, radioafición y su relación")}
    <div class="search" style="padding-bottom:10px">${icon("search", 16)}<input id="lq" type="search" placeholder="Buscar en los temas…" aria-label="Buscar temas"></div>
    <div class="menu" id="topics"></div>`;
  paintMenu("");
  $("#lq").addEventListener("input", (e) => paintMenu(e.target.value));
  $("#topics").addEventListener("click", (e) => { const b = e.target.closest("[data-t]"); if (b) location.hash = `#/learn/${b.dataset.t}`; });
}
let cur = "";
function paintMenu(q) {
  const t = q.trim().toLowerCase();
  const list = TOPICS.filter((x) => !t || `${x.t} ${x.lead} ${x.html.replace(/<[^>]+>/g, " ")}`.toLowerCase().includes(t));
  $("#topics").innerHTML = GROUPS.map((g, gi) => { const items = list.filter((x) => x.g === gi); return items.length ? `<div class="group-label">${g}</div>${items.map((x) => `<button type="button" data-t="${x.id}" aria-current="${x.id === cur}"><span>${esc(x.t)}<small>${esc(x.lead)}</small></span></button>`).join("")}` : ""; }).join("") || '<p class="empty" style="padding:18px">Ningún tema coincide.</p>';
}
export function show(parts) {
  const t = TOPICS.find((x) => x.id === parts[0]);
  cur = t?.id || "";
  paintMenu($("#lq")?.value || "");
  ctx.pane(!t && matchMedia("(max-width:760px)").matches ? "list" : "detail");
  if (!t) {
    ctx.colb.innerHTML = `<div class="article"><h1>Aprende meteorología y radioafición</h1><p class="lead">Guías breves para entender los datos que ves en Meteo Guilda y para sacarles partido con una emisora.</p>
      ${GROUPS.map((g, gi) => `<h2>${g}</h2><ul>${TOPICS.filter((x) => x.g === gi).map((x) => `<li><a href="#/learn/${x.id}"><b>${esc(x.t)}</b></a> — ${esc(x.lead)}</li>`).join("")}</ul>`).join("")}</div>`;
    return;
  }
  const i = TOPICS.indexOf(t), prev = TOPICS[i - 1], next = TOPICS[i + 1];
  ctx.colb.innerHTML = `<article class="article"><div class="lbl">${GROUPS[t.g]}</div><h1>${esc(t.t)}</h1><p class="lead">${esc(t.lead)}</p>${t.html}
    <nav class="row" style="justify-content:space-between;margin-top:36px;padding-top:16px;border-top:1px solid var(--line)">${prev ? `<a class="navlink" href="#/learn/${prev.id}">${icon("left", 15)} ${esc(prev.t)}</a>` : "<span></span>"}${next ? `<a class="navlink" href="#/learn/${next.id}">${esc(next.t)} ${icon("right", 15)}</a>` : ""}</nav></article>`;
  ctx.colb.scrollTop = 0;
}
