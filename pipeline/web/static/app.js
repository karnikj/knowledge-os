"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const mmss = t => { t = Math.floor(t||0); const h = Math.floor(t/3600), m = Math.floor(t%3600/60), s = t%60;
  return (h ? h+":"+String(m).padStart(2,"0") : String(m).padStart(2,"0")) + ":" + String(s).padStart(2,"0"); };
const ATT = {positive:"pozitivní", negative:"negativní", mixed:"smíšený", neutral:"neutrální"};
async function api(path, opt = {}) {
  const r = await fetch(path, {headers: {"Content-Type": "application/json"}, ...opt});
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// Krátké potvrzení dole na obrazovce (zmizí samo)
function toast(text, bad) {
  let el = document.getElementById("toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; el.setAttribute("role", "status"); document.body.append(el); }
  el.textContent = text; el.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(el._t); el._t = setTimeout(() => el.className = "toast", 3200);
}

// ---------- záložky ----------
// Navigace s historií: tlačítko Zpět/Vpřed v telefonu i prohlížeči přepíná záložky, zavírá nabídku Více
// a ze schůzky vrací na seznam. push=false při návratu přes Zpět (historii neměníme).
function nav(hash, push = true) {
  if (location.hash === hash) return;
  if (push && !(history.state && history.state.sheet)) history.pushState({kos: 1}, "", hash);
  else history.replaceState({kos: 1}, "", hash);
}
function show(tab, push = true) {
  if (recGuard(tab)) return;
  document.querySelectorAll(".tabs button, .bottomnav button[data-tab], .sheet button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  const more = document.querySelector(".bottomnav [data-more]");
  if (more) more.classList.toggle("on", ["pridat", "entity", "ulohy", "dokumenty", "slovnik"].includes(tab));
  $("#moresheet").hidden = true;
  window.scrollTo(0, 0);
  document.querySelectorAll(".tab").forEach(s => s.classList.toggle("on", s.id === tab));
  nav("#" + tab, push);
  if (tab === "pridat") loadSources();
  if (tab === "entity" && !$("#elist").dataset.loaded) loadEntities();
  if (tab === "hlas") loadNotes();
  if (tab === "ulohy") loadJobs();
  if (tab === "dokumenty") loadDocs();
  if (tab === "slovnik") loadGlossary();
  if (tab === "schuzky" && typeof loadMeetings === "function") loadMeetings();
}
document.querySelectorAll(".tabs button, .bottomnav button[data-tab], .sheet button").forEach(b => b.onclick = () => show(b.dataset.tab));
function openSheet(open) {
  if (open === !$("#moresheet").hidden) return;
  if (open) { $("#moresheet").hidden = false; history.pushState({kos: 1, sheet: 1}, "", location.hash); }
  else if (history.state && history.state.sheet) history.back();   // zavře přes popstate
  else $("#moresheet").hidden = true;
}
document.querySelector(".bottomnav [data-more]").onclick = e => { e.stopPropagation(); openSheet($("#moresheet").hidden); };
$("#moresheet").onclick = e => { if (e.target.id === "moresheet") openSheet(false); };
// Zpět / Vpřed
window.addEventListener("popstate", async () => {
  if (recActive() && !(history.state && history.state.guard)) {   // Zpět během nahrávání → zůstat a zeptat se
    history.pushState({kos: 1, guard: 1}, "", GUARD.hash || location.hash);
    openGuard("back"); return;
  }
  if (!$("#moresheet").hidden) { $("#moresheet").hidden = true; if (history.state && history.state.sheet) return; }
  if (typeof MT !== "undefined" && MT.dirty && typeof saveMeeting === "function") await saveMeeting();
  const [tab, sub] = (location.hash || "#").slice(1).split("/");
  const target = tab || (matchMedia("(max-width: 767px)").matches ? "hlas" : "hledat");
  const cur = (document.querySelector(".tab.on") || {}).id;
  const inDetail = cur === "schuzky" && !$("#medit").hidden;
  if (!sub && target === cur && !inDetail) return;   // jen se zavřela nabídka Více
  if (target === "schuzky" && sub && typeof openMeeting === "function") {
    if (!$("#schuzky").classList.contains("on")) show("schuzky", false);
    openMeeting(sub, false);
  } else show(target, false);
});

// ---------- hledání ----------
function mark(text, q) {
  let h = esc(text);
  q.split(/\s+/).filter(w => w.length > 2).forEach(w => {
    const stem = w.slice(0, Math.max(3, w.length - 2)).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    h = h.replace(new RegExp("(" + stem + "\\p{L}*)", "giu"), "<mark>$1</mark>");
  });
  return h;
}
function hitCard(h, q, extra = "") {
  if (h.source_type === "document") {
    const file = h.url ? esc(h.url) : `/api/documents/${encodeURIComponent(h.source_id)}/file` + (h.t ? `#page=${Math.round(h.t)}` : "");
    const lbl = h.url ? "zdroj na webu ↗" : h.t ? "str. " + Math.round(h.t) : "originál";
    return `<div class="card hit"><div class="meta"><b>📄 ${esc(h.speaker || h.title)}</b>${extra}
      <a href="${file}" target="_blank" rel="noopener">${lbl}</a></div>
      <p>${mark(h.text, q)}</p><div class="meta"><span>${esc(h.title)}</span></div></div>`;
  }
  const link = h.url ? `<a href="${h.url}" target="_blank" rel="noopener">▶ ${mmss(h.t)}</a>` : mmss(h.t);
  const tilde = h.time_source && h.time_source !== "aligned_vtt" ? "~" : "";
  return `<div class="card hit"><div class="meta"><b>${esc(h.speaker)}</b>${extra}
    <span>${tilde}${link}</span><span>${esc(h.published_at || "")}</span></div>
    <p>${mark(h.text, q)}</p>
    <div class="meta"><span>${esc(h.title)}</span>
    <button class="ghost" onclick="ctx('${h.source_id}',${h.seg},this)">souvislosti</button></div><div></div></div>`;
}
window.ctx = async (sid, seg, btn) => {
  const box = btn.parentElement.nextElementSibling;
  if (box.innerHTML) { box.innerHTML = ""; return; }
  const r = await api(`/api/segments/${encodeURIComponent(sid)}/${seg}?context=3`);
  box.innerHTML = r.items.map(s => `<p class="hint" style="${s.seg===seg?"color:var(--ink)":""}"><b>${mmss(s.t)} ${esc(s.speaker)}:</b> ${esc(s.text)}</p>`).join("");
};
// ---------- zeptat se (odpověď jen ze zdrojů, s citacemi) ----------
// typ zdroje → barva čísla citace (dokument modrá, schůzka zelená, poznámka žlutá, video červená)
const KIND_NAME = {k_doc: "dokument", k_meet: "schůzka", k_voice: "hlasová poznámka", k_video: "video"};
const srcKind = c => ({document: "k_doc", meeting: "k_meet", voice: "k_voice"})[c.source_type] || "k_video";
function kindLegend(cites) {
  const ks = [...new Set(cites.map(srcKind))];
  return ks.length ? `<span class="klegend">${ks.map(k => `<span><i class="kdot ${k}"></i>${KIND_NAME[k]}</span>`).join("")}</span>` : "";
}
function mdLite(t, cites) {
  const byN = Object.fromEntries(cites.map(c => [c.n, c]));
  return esc(t).split(/\n{2,}/).map(par => {
    const lines = par.split("\n");
    const isList = lines.every(l => /^\s*[-*•]\s+/.test(l));
    const fmt = l => l.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/\[(\d+)\]/g, (m, n) => byN[n] ? `<a class="cite ${srcKind(byN[n])}" href="#" data-cit="${n}" title="${esc(KIND_NAME[srcKind(byN[n])] + ": " + byN[n].title)}">${n}</a>` : "");
    return isList ? "<ul>" + lines.map(l => "<li>" + fmt(l.replace(/^\s*[-*•]\s+/, "")) + "</li>").join("") + "</ul>"
                  : "<p>" + lines.map(fmt).join("<br>") + "</p>";
  }).join("");
}
// ---------- chat (AI, placené tokeny) ----------
const LEVEL_NAME = {light: "Light", standard: "Standard", full: "Full"};
const czk = v => (v || 0).toLocaleString("cs-CZ", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + " Kč";
const ntok = v => v >= 1e6 ? (v / 1e6).toFixed(2).replace(".", ",") + " mil." : v >= 1e3 ? Math.round(v / 1e3) + " tis." : String(v || 0);
function usageLine(u, r) {
  const who = (u.models || []).map(m => m.split("/").pop()).join(", ") || "—";
  return `<div class="uline"><span>${ntok(u.in)} tok. vstup · ${ntok(u.out)} tok. výstup</span><span><b>${u.cost_usd ? czk(u.cost_czk) : "zdarma"}</b></span>
    <span class="hint">${esc(who)}${r.private ? " · obsahuje vaše soukromé zdroje" : ""}</span></div>`;
}
function citeWhere(c) {
  if (c.source_type === "document") {
    if (c.url) return `<span>📄 ${esc(c.section || "")}</span><a href="${esc(c.url)}" target="_blank" rel="noopener">zdroj na webu ↗</a>`;
    const file = `/api/documents/${encodeURIComponent(c.source_id)}/file` + (c.page ? `#page=${c.page}` : "");
    return `<span>📄 ${esc(c.section || "")}</span><a href="${file}" target="_blank" rel="noopener">${c.page ? "str. " + c.page : "originál"}</a>`;
  }
  return `<span>${esc(c.speaker)}</span>` + (c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">▶ ${mmss(c.t)}</a>` : `<span>${mmss(c.t)}</span>`);
}
function citesHtml(cites) {
  return cites.map(c => `<div class="card hit" data-cit="${c.n}">
    <div class="meta"><b class="citn ${srcKind(c)}">${c.n}</b> <b>${esc(c.title)}</b> <span>${esc(c.source_type === "document" ? "" : c.date || "")}</span>
      ${citeWhere(c)}</div>
    <p>${esc(c.text.length > 600 ? c.text.slice(0, 600) + "…" : c.text)}</p></div>`).join("");
}
async function runChat(q, level, page, box) {
  const wait = document.createElement("p"); wait.className = "hint"; wait.textContent = "Hledám ve zdrojích a skládám odpověď… (obvykle 5–30 s)";
  box.append(wait);
  try {
    const r = await api("/api/chat", {method: "POST", body: JSON.stringify({question: q, level, page,
      since: $("#c-since").value || null, until: $("#c-until").value || null, source_id: box.dataset.sid || null})});
    wait.remove();
    const cites = r.citations || [];
    const part = document.createElement("div"); part.className = "qapart";
    const range = level === "full" && r.range ? `<p class="hint">Zmínky ${r.range[0]}–${r.range[1]} z ${r.total_snippets} relevantních úryvků${r.checked > r.total_snippets ? ` (AI prověřila ${r.checked} nalezených, ${r.checked - r.total_snippets} nesouvisejících vyřadila)` : ""}</p>` : "";
    part.innerHTML = `${range}<div class="card answer">${r.enough === false ? `<p class="chip mixed">zdroje odpovídají jen částečně</p>` : ""}
      ${mdLite(r.answer || "", cites)}${r.note ? `<p class="hint">ⓘ ${esc(r.note)}</p>` : ""}${usageLine(r.usage || {}, r)}</div>
      ${cites.length ? `<details class="srcbox"${level === "full" ? "" : " open"}><summary>Zdroje (${cites.length}) ${kindLegend(cites)}</summary>${citesHtml(cites)}</details>` : ""}`;
    box.append(part);
    if (r.has_more) {
      const b = document.createElement("button"); b.className = "ghost"; b.type = "button";
      b.textContent = `Dalších 15 zmínek (zbývá ${r.total_snippets - r.range[1]})`;
      b.onclick = () => { b.remove(); runChat(q, level, page + 1, box); };
      box.append(b);
    }
    loadUsage();
  } catch (err) { wait.remove(); box.insertAdjacentHTML("beforeend", `<p class="msg">Chyba: ${esc(err.message)}</p>`); }
}
$("#cform").onsubmit = e => {
  e.preventDefault();
  const q = $("#cq").value.trim(); if (!q) return;
  const level = document.querySelector("input[name=clevel]:checked").value;
  const box = document.createElement("div"); box.className = "qa";
  if (DOC.scope) box.dataset.sid = DOC.scope.sid;
  box.innerHTML = `<div class="qhead"><span class="chip">${LEVEL_NAME[level]}</span>${DOC.scope ? ` <span class="chip mixed">dokument: ${esc(DOC.scope.title)}</span>` : ""} <b>${esc(q)}</b></div>`;
  $("#cres").prepend(box);
  runChat(q, level, 0, box);
};
// klepnutí na číslo citace → rozbalí zdroje této odpovědi a ukáže příslušný úryvek (s odkazem na originál)
$("#cres").addEventListener("click", e => {
  const a = e.target.closest("a.cite[data-cit]"); if (!a) return;
  e.preventDefault();
  const part = a.closest(".qapart"); if (!part) return;
  const card = part.querySelector(`.hit[data-cit="${a.dataset.cit}"]`); if (!card) return;
  const det = card.closest("details"); if (det) det.open = true;
  card.scrollIntoView({behavior: "smooth", block: "center"});
  card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash");
});
$("#cq").addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#cform").requestSubmit(); });

// ---------- spotřeba AI (záhlaví) ----------
async function loadUsage() {
  try {
    const u = await api("/api/usage"); const t = u.total;
    const mon = new Date(u.month + "-01").toLocaleDateString("cs-CZ", {month: "long"});
    $("#usagebtn").innerHTML = `<small>${esc(mon)}</small> <span class="utok">${ntok(t.in + t.out)} tok. · </span><b>${czk(t.cost_czk)}</b>`;
    $("#usagebox").innerHTML = `<b>Spotřeba AI – ${esc(mon)}</b><table class="utab"><tr><th>Účel / model</th><th>Volání</th><th>Tokeny</th><th>Cena</th></tr>` +
      u.by.map(b => `<tr><td>${esc(b.purpose || "—")}<br><small>${esc(b.model)}</small></td><td>${b.calls}</td><td>${ntok(b.in + b.out)}</td>
        <td>${b.cost_usd ? czk(b.cost_czk) : "zdarma"}</td></tr>`).join("") +
      `<tr><td><b>Celkem</b></td><td>${t.calls}</td><td>${ntok(t.in + t.out)}</td><td><b>${czk(t.cost_czk)}</b></td></tr></table>
      <p class="hint">Gemini = bezplatný klíč (tokeny se počítají, cena 0). OpenRouter = skutečná cena, kurz ${u.usd_czk} Kč/$.</p>`;
  } catch { $("#usagebtn").textContent = "spotřeba —"; }
}
$("#usagebtn").onclick = () => { $("#usagebox").hidden = !$("#usagebox").hidden; };
document.addEventListener("click", e => { if (!e.target.closest("#usagebtn, #usagebox")) $("#usagebox").hidden = true; });
loadUsage(); setInterval(loadUsage, 60000);

$("#sform").onsubmit = async e => {
  e.preventDefault();
  const q = $("#q").value.trim(), p = new URLSearchParams({q, limit: 100});
  for (const [k, id] of [["speaker","#f-speaker"],["source_type","#f-type"],["since","#f-since"],["until","#f-until"]])
    if ($(id).value) p.set(k, $(id).value);
  $("#sres").innerHTML = `<p class="hint">Hledám…</p>`;
  try {
    const r = await api("/api/search?" + p);
    const fac = r.facets.sources.slice(0, 6).map(s => `<span class="chip">${esc(s.title.slice(0,40))} · ${s.n}</span>`).join(" ");
    $("#sres").innerHTML = `<p class="hint">${r.total} zásahů${r.total > r.items.length ? ` (zobrazeno ${r.items.length})` : ""}</p>
      <div class="bar">${fac}</div>` + r.items.map(h => hitCard(h, q)).join("");
  } catch (err) { $("#sres").innerHTML = `<p class="msg">Chyba: ${esc(err.message)}</p>`; }
};

// ---------- entity ----------
// Rejstřík osob, firem, nástrojů a modelů z přepisů a dokumentů. Návrhy AI („navrženo“) jde potvrdit,
// zamítnout (šum — skryje se a AI ji znovu nenavrhne), sloučit s duplicitou nebo jim změnit typ.
let etimer;
async function loadEntities() {
  const f = $("#etype").value;
  const p = new URLSearchParams({q: $("#eq").value, limit: 200});
  if (f === "") p.set("group", "main");
  else if (f === "__rej") p.set("status", "zamítnuto");
  else if (f !== "__all") p.set("type", f);
  const list = await api("/api/entities?" + p);
  $("#elist").dataset.loaded = 1;
  const rej = f === "__rej";
  $("#elist").innerHTML = (list.length ? `<div class="card" style="padding:0">` + list.map(e =>
    `<div class="ent" data-id="${esc(e.id)}"><span>${esc(e.name)} <small>${esc(TYPES[e.type]||e.type)}${e.status==="navrženo"?" · navrženo":e.status==="potvrzeno"?" · ✓":""}</small></span>
     <span class="entr">${rej ? "" : `<span class="num">${e.n_mentions}× / ${e.n_sources}</span>`}
       <button class="ghost small erej" data-a="${rej ? "restore" : "reject"}" title="${rej ? "Obnovit entitu" : "Zamítnout — není to entita"}">${rej ? "Obnovit" : "✕"}</button></span></div>`).join("") + `</div>`
    : `<p class="hint">${rej ? "Žádné zamítnuté entity." : "Nic nenalezeno."}</p>`);
}
$("#elist").addEventListener("click", async e => {
  const b = e.target.closest("button.erej"), row = e.target.closest(".ent"); if (!row) return;
  if (!b) return openEntity(row.dataset.id);
  e.stopPropagation(); b.disabled = true;
  try {
    await setEntityStatus(row.dataset.id, b.dataset.a === "reject" ? "zamítnuto" : "navrženo");
    row.remove();
    toast(b.dataset.a === "reject" ? "Zamítnuto — najdete ji ve filtru „zamítnuté“" : "✓ Entita obnovena");
  } catch (err) { b.disabled = false; toast("Chyba: " + err.message, true); }
});
const setEntityStatus = (id, status) => api(`/api/entities/${encodeURIComponent(id)}`, {method: "PATCH", body: JSON.stringify({status})});
let TYPES = {};
api("/api/entity-types").then(t => { TYPES = t;
  $("#etype").innerHTML = `<option value="">hlavní (bez témat a míst)</option><option value="__all">všechny typy</option>`
    + Object.entries(t).map(([k,v]) => `<option value="${k}">${esc(v)}</option>`).join("") + `<option value="__rej">zamítnuté</option>`; });
$("#eq").oninput = () => { clearTimeout(etimer); etimer = setTimeout(loadEntities, 250); };
$("#etype").onchange = loadEntities;
window.openEntity = async (id, filters = {}) => {
  const p = new URLSearchParams(filters);
  const r = await api(`/api/entities/${encodeURIComponent(id)}/mentions?` + p);
  const e = r.entity, att = Object.entries(r.by_attitude).map(([k,n]) => `<span class="chip ${k}">${esc(ATT[k]||"bez postoje")} ${n}</span>`).join(" ");
  const rej = e.status === "zamítnuto";
  $("#elist").hidden = true; $("#ecleanbox").hidden = true; $("#edetail").hidden = false;
  $("#edetail").innerHTML = `<button class="ghost" onclick="closeEntity()">← zpět</button>
    <h2 style="margin:10px 0 2px">${esc(e.name)}</h2>
    <p class="hint">${rej ? `<span class="chip negative">zamítnuto</span> ` : e.status === "potvrzeno" ? `<span class="chip positive">potvrzeno</span> ` : `<span class="chip">navrženo AI</span> `}${r.total} zmínek v ${r.sources} zdrojích${e.aliases.length ? " · aliasy: " + esc(e.aliases.join(", ")) : ""}</p>
    <div class="card eedit">
      <label>Typ <select id="ed-type">${Object.entries(TYPES).map(([k,v]) => `<option value="${k}" ${k===e.type?"selected":""}>${esc(v)}</option>`).join("")}</select></label>
      <div class="bar">
        ${rej ? `<button class="ghost small" data-e="restore">Obnovit</button>`
              : `${e.status!=="potvrzeno" ? `<button class="ghost small" data-e="confirm">✓ Potvrdit</button>` : ""}
                 <button class="ghost small" data-e="merge">Sloučit s…</button>
                 <button class="ghost small" data-e="reject">Zamítnout (není to entita)</button>`}
      </div><div id="emerge"></div></div>
    <div class="bar">${att}</div>
    <div class="grid2"><label>Od <input type="date" id="ed-since" value="${filters.since||""}"></label>
      <label>Postoj <select id="ed-att"><option value="">vše</option>${Object.entries(ATT).map(([k,v]) => `<option value="${k}" ${filters.attitude===k?"selected":""}>${v}</option>`).join("")}</select></label></div>
    <div class="bar"><button class="ghost" onclick="filterEntity('${id}')">Filtrovat</button></div>` +
    r.items.map(h => hitCard(h, e.name, `<span>«${esc(h.surface)}»</span>${h.attitude ? `<span class="chip ${h.attitude}">${ATT[h.attitude]}</span>` : ""}`)).join("");
  $("#ed-type").onchange = async ev => {
    try { await api(`/api/entities/${encodeURIComponent(id)}`, {method: "PATCH", body: JSON.stringify({type: ev.target.value})});
      toast("✓ Typ změněn na „" + TYPES[ev.target.value] + "“"); $("#elist").dataset.loaded = ""; }
    catch (err) { toast("Chyba: " + err.message, true); }
  };
  $("#edetail .eedit").onclick = async ev => {
    const b = ev.target.closest("button[data-e]"); if (!b) return;
    const a = b.dataset.e;
    if (a === "merge") return mergePicker(e);
    b.disabled = true;
    try {
      await setEntityStatus(id, a === "confirm" ? "potvrzeno" : a === "reject" ? "zamítnuto" : "navrženo");
      toast(a === "confirm" ? "✓ Potvrzeno" : a === "reject" ? "Zamítnuto — zmizí ze seznamu i z výsledků" : "✓ Obnoveno");
      if (a === "reject") { closeEntity(true); } else openEntity(id);
    } catch (err) { b.disabled = false; toast("Chyba: " + err.message, true); }
  };
};
function mergePicker(e) {
  const box = $("#emerge");
  box.innerHTML = `<label>Sloučit „${esc(e.name)}“ do… <input id="mq" type="search" placeholder="začněte psát název, např. Claude" autocomplete="off"></label><div id="mres"></div>`;
  const run = async () => {
    const q = $("#mq").value.trim();
    if (q.length < 2) { $("#mres").innerHTML = ""; return; }
    const list = (await api("/api/entities?" + new URLSearchParams({q, limit: 8}))).filter(x => x.id !== e.id);
    $("#mres").innerHTML = list.map(x => `<button type="button" class="mpick" data-id="${esc(x.id)}" data-name="${esc(x.name)}">
      ${esc(x.name)} <small>${esc(TYPES[x.type]||x.type)} · ${x.n_mentions}×</small></button>`).join("") || `<p class="hint">Nic nenalezeno.</p>`;
  };
  let t; $("#mq").oninput = () => { clearTimeout(t); t = setTimeout(run, 200); }; $("#mq").focus();
  $("#mres").onclick = async ev => {
    const b = ev.target.closest(".mpick"); if (!b) return;
    if (!b.classList.contains("sure")) { $("#mres").querySelectorAll(".sure").forEach(x => x.classList.remove("sure"));
      b.classList.add("sure"); b.insertAdjacentHTML("beforeend", ` <b class="msure">— klepněte znovu pro sloučení</b>`); return; }
    try { await api(`/api/entities/${encodeURIComponent(e.id)}/merge`, {method: "POST", body: JSON.stringify({into: b.dataset.id})});
      toast(`✓ „${e.name}“ sloučeno do „${b.dataset.name}“`); $("#elist").dataset.loaded = "";
      setTimeout(() => openEntity(b.dataset.id), 1500); }
    catch (err) { toast("Chyba: " + err.message, true); }
  };
}
window.filterEntity = id => openEntity(id, Object.fromEntries(Object.entries({since: $("#ed-since").value, attitude: $("#ed-att").value}).filter(([,v]) => v)));
window.closeEntity = (reload = false) => { $("#edetail").hidden = true; $("#elist").hidden = false;
  if (reload || !$("#elist").dataset.loaded) setTimeout(loadEntities, reload ? 1500 : 0); };
// úklid s AI: návrhy k odkliknutí
const CLEAN = {items: []};
$("#ecleanbtn").onclick = async () => {
  const box = $("#ecleanbox"), b = $("#ecleanbtn");
  box.hidden = false; b.disabled = true;
  box.innerHTML = `<p class="hint">Spouštím…</p>`;
  try { await api("/api/entities/cleanup/suggest", {method: "POST"}); pollClean(); }
  catch (err) { box.innerHTML = `<p class="msg">Chyba: ${esc(err.message)}</p>`; b.disabled = false; }
};
async function pollClean() {
  const box = $("#ecleanbox"), b = $("#ecleanbtn");
  let st;
  try { st = await api("/api/entities/cleanup"); } catch (err) { st = {status: "running"}; }
  if (st.status === "running") {
    box.innerHTML = `<p class="hint">AI prochází navržené entity a hledá šum, duplicity a špatné typy…
      ${st.total ? `hotovo ${st.done} z ${st.total} částí` : ""}${st.elapsed ? ` · ${st.elapsed} s` : ""} (obvykle do minuty)</p>`;
    clearTimeout(CLEAN.t); CLEAN.t = setTimeout(pollClean, 2500); return;
  }
  b.disabled = false;
  if (st.status === "error") { box.innerHTML = `<p class="msg">Úklid se nepovedl: ${esc(st.error || "")}. Zkuste to znovu.</p>`; return; }
  CLEAN.items = st.items || [];
  drawClean();
  if (st.failed) box.insertAdjacentHTML("afterbegin", `<p class="msg">${st.failed} část(i) se nepodařilo projít (model neodpověděl) — návrhy nejsou úplné, úklid můžete spustit znovu.</p>`);
}
function drawClean() {
  const box = $("#ecleanbox"), it = CLEAN.items;
  if (!it.length) { box.innerHTML = `<p class="hint">AI nenašla nic k úklidu. 👍</p><div class="bar"><button class="ghost small" data-c="close">Zavřít</button></div>`; return; }
  const sec = (act, title, fn) => { const rows = it.map((x, i) => [x, i]).filter(([x]) => x.action === act);
    return rows.length ? `<h3>${title} <small>(${rows.length})</small></h3>` + rows.map(([x, i]) =>
      `<label class="crow"><input type="checkbox" data-i="${i}" checked><span>${fn(x)} <small>${x.mentions}× · ${esc(x.why || "")}</small></span></label>`).join("") : ""; };
  box.innerHTML = `<p class="hint">Návrhy AI. Odškrtněte, s čím nesouhlasíte, a dejte Provést. Zamítnuté entity jde později obnovit (filtr „zamítnuté“).</p>
    ${sec("reject", "Zamítnout — není to entita", x => `<b>${esc(x.name)}</b> <small>${esc(TYPES[x.type]||x.type)}</small>`)}
    ${sec("merge", "Sloučit duplicity", x => `<b>${esc(x.name)}</b> → <b>${esc(x.into_name)}</b>`)}
    ${sec("retype", "Opravit typ", x => `<b>${esc(x.name)}</b>: ${esc(TYPES[x.type]||x.type)} → <b>${esc(TYPES[x.new_type]||x.new_type)}</b>`)}
    <div class="bar"><button class="primary" data-c="apply">Provést vybrané</button><button class="ghost" data-c="close">Zavřít</button></div>`;
}
$("#ecleanbox").addEventListener("click", async e => {
  const b = e.target.closest("button[data-c]"); if (!b) return;
  if (b.dataset.c === "close") { $("#ecleanbox").hidden = true; return; }
  const sel = [...$("#ecleanbox").querySelectorAll("input[data-i]:checked")].map(x => CLEAN.items[+x.dataset.i]);
  if (!sel.length) return toast("Nic není vybráno", true);
  b.disabled = true; b.textContent = "Provádím…";
  try {
    const r = await api("/api/entities/cleanup/apply", {method: "POST", body: JSON.stringify({items: sel})});
    toast(`✓ Zamítnuto ${r.reject}, sloučeno ${r.merge}, typ opraven ${r.retype}`);
    $("#ecleanbox").hidden = true; setTimeout(loadEntities, 2500);
  } catch (err) { b.disabled = false; b.textContent = "Provést vybrané"; toast("Chyba: " + err.message, true); }
});

// ---------- přidat ----------
$("#aform").onsubmit = async e => {
  e.preventDefault();
  const url = $("#aurl").value;
  const r = await api("/api/sources", {method: "POST", body: JSON.stringify({url})});
  $("#amsg").hidden = false; $("#amsg").textContent = r.message;
  if (r.was_removed) {
    const b = document.createElement("button"); b.className = "ghost"; b.textContent = "Přesto přidat";
    b.onclick = async () => { const r2 = await api("/api/sources", {method: "POST", body: JSON.stringify({url, force: true})});
      $("#amsg").textContent = r2.message; if (r2.ok) $("#aurl").value = ""; loadSources(); };
    $("#amsg").append(" ", b);
  }
  if (r.ok) $("#aurl").value = "";
  loadSources();
};
// Odstranění zdroje: první klik se zeptá, druhý do 5 s smaže (bez systémového dialogu)
window.removeSrc = async (btn, sid, after) => {
  if (!btn.dataset.armed) {
    btn.dataset.armed = "1"; btn.textContent = "Opravdu odstranit? Klikněte znovu";
    btn.classList.add("danger");
    setTimeout(() => { if (btn.isConnected) { delete btn.dataset.armed; btn.textContent = "Odstranit"; btn.classList.remove("danger"); } }, 5000);
    return;
  }
  btn.disabled = true; btn.textContent = "Odstraňuji…";
  try { await api(`/api/sources/${encodeURIComponent(sid)}`, {method: "DELETE"}); }
  catch (err) { btn.textContent = "Chyba: " + err.message; return; }
  (after || loadSources)();
};
async function loadSources() {
  const r = await api("/api/sources?limit=30");
  const w = r.worker || {};
  const qp = r.pause;
  const pauseTxt = qp ? `<div class="card"><span class="chip mixed">čeká</span> Zpracování je pozastavené do ${esc(qp.until.slice(11, 16))}
     (${/limit|quota|RESOURCE_EXHAUSTED/i.test(qp.reason) ? "vyčerpaný denní limit Gemini" : "Gemini je přetížené"}).
     <button class="ghost small" onclick="resumeWorker()">Zkusit hned</button></div>` : "";
  const q = pauseTxt + r.queue.map(u => `<div class="card"><span class="chip">ve frontě</span> ${esc(u)}
     <div class="bar"><button class="ghost small" onclick="requeue(this, '${esc(u)}')">Spustit přepis znovu</button>
       <button class="ghost small" onclick="dequeue(this, '${esc(u)}')">Odebrat z fronty</button></div></div>`).join("");
  const busy = w.job ? `<div class="card"><span class="chip mixed">zpracovává se</span> ${esc(w.job)}</div>` : "";
  const failed = r.failed.map(f => `<div class="card"><span class="chip negative">chyba</span> ${esc(f.url)}<p class="hint">${esc(f.error)}</p>
     <button class="ghost" onclick="retrySrc('${esc(f.url)}')">Zkusit znovu</button></div>`).join("");
  const done = r.items.map(s => `<div class="card"><span class="chip ${s.stt_status==="STT_OK"?"positive":"mixed"}">hotovo</span>
     ${esc(s.title)}<p class="hint">${esc(s.published_at)} · ${s.n_claims} tvrzení · ${s.n_mentions} zmínek${s.url ? ` · <a href="${esc(s.url)}" target="_blank">video</a>` : ""}</p>
     <button class="ghost small" onclick="removeSrc(this, '${esc(s.source_id)}')">Odstranit</button></div>`).join("");
  const local = (r.queue_local || []).map(u => `<div class="card"><span class="chip mixed">čeká na PC</span> ${esc(u)}
     <p class="hint">YouTube blokuje stahování z VPS. Zpracuje se na PC (runner / <code>python -m pipeline worker --local --once</code>).</p></div>`).join("");
  $("#alist").innerHTML = busy + q + local + failed + done;
}
window.requeue = async (btn, url) => {
  btn.disabled = true; btn.textContent = "Spouštím…";
  try { await api("/api/queue/retry?url=" + encodeURIComponent(url), {method: "POST"});
        btn.textContent = "Spuštěno — server začne do 10 s"; setTimeout(loadSources, 4000); }
  catch (e) { btn.textContent = "Chyba: " + e.message; } };
window.resumeWorker = async () => { await api("/api/worker/resume", {method: "POST"}); loadSources(); };
window.dequeue = async (btn, url) => {
  if (!btn.dataset.armed) { btn.dataset.armed = "1"; btn.textContent = "Opravdu odebrat? Klikněte znovu"; btn.classList.add("danger"); return; }
  await api("/api/queue?url=" + encodeURIComponent(url), {method: "DELETE"}); loadSources(); };
window.retrySrc = async url => { await api("/api/sources/retry", {method: "POST", body: JSON.stringify({url})}); loadSources(); };

// ---------- pojistka: nahrávání nejde omylem opustit ----------
const GUARD = {hash: null};
function recActive() {
  if (["rec", "paused", "review", "stopping"].includes(typeof vstate !== "undefined" ? vstate : "idle")) return "hlas";
  if (typeof MT !== "undefined" && MT.rec && MT.rec.busy()) return "schuzky";
  return null;
}
// Voláno při přepnutí záložky / ← Zápisy: true = odchod zastaven (ukázal se dialog)
function recGuard(target) {
  const where = recActive();
  if (!where) return false;
  if (target === where && where === "hlas") return false;   // Hlas → Hlas nic neopouští
  openGuard(target); return true;                            // u schůzky i „Schůzky“ znamená odejít z detailu
}
function armGuard() {      // při startu nahrávání: krok v historii navíc, aby Zpět neopustil stránku
  GUARD.hash = location.hash;
  if (!(history.state && history.state.guard)) history.pushState({kos: 1, guard: 1}, "", location.hash);
}
// Po klepnutí (interakce uživatele) přidat krok znovu, aby další Zpět zase jen ukázal dialog
function rearmGuard() { if (recActive()) history.pushState({kos: 1, guard: 1}, "", GUARD.hash || location.hash); }
// Jakékoli klepnutí během nahrávání obnoví pojistku, pokud ji Zpět spotřeboval
document.addEventListener("pointerdown", () => {
  if (recActive() && !(history.state && history.state.guard)) rearmGuard();
}, true);
function openGuard(pending) {
  const where = recActive();
  if (!where) return;
  let box = document.getElementById("recguard");
  if (!box) { box = document.createElement("div"); box.id = "recguard"; box.className = "guard"; document.body.append(box); }
  const voice = where === "hlas";
  const st = voice ? vstate : MT.rec.state();
  const secs = voice ? recSecs() : MT.rec.secs();
  const sent = st === "review";
  box.innerHTML = `<div class="guardbox" role="alertdialog" aria-modal="true" aria-labelledby="gtitle">
    <h3 id="gtitle">${sent ? "Nahrávka ještě není odeslaná" : "Probíhá nahrávání"} <span class="hint">${mmss(secs)}</span></h3>
    <p>Tento krok by ${sent ? "nahrávku opustil" : "ukončil nahrávání"}. Co chcete udělat?</p>
    <button class="primary" data-g="stay">${sent ? "Zpět k nahrávce" : "Pokračovat v nahrávání"}</button>
    <button class="ghost" data-g="save">${voice ? "Uložit a odeslat k přepisu" : "Uložit a odeslat ke zpracování"}</button>
    <button class="ghost gdrop" data-g="drop">Zahodit nahrávku</button></div>`;
  box.hidden = false;
  box.onclick = async e => {
    const b = e.target.closest("[data-g]");
    if (!b && e.target === box) { box.hidden = true; rearmGuard(); return; }
    if (!b) return;
    if (b.dataset.g === "stay") { box.hidden = true; rearmGuard(); return; }
    if (b.dataset.g === "drop" && !b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Opravdu zahodit? Klepněte znovu"; b.classList.add("danger"); return; }
    box.querySelectorAll("button").forEach(x => x.disabled = true);
    b.textContent = b.dataset.g === "save" ? "Ukládám…" : "Zahazuji…";
    try {
      if (voice) { b.dataset.g === "save" ? await voiceSave() : await voiceDrop(); }
      else { b.dataset.g === "save" ? await MT.rec.save() : await MT.rec.discard(); }
      toast(b.dataset.g === "save" ? "✓ Nahrávka uložena a odeslána" : "Nahrávka zahozena");
      box.hidden = true;
      // dokončit krok, kvůli kterému se dialog ukázal
      if (pending === "back") { if (history.state && history.state.guard) history.back(); }
      else if (pending) {
        if (history.state && history.state.guard) history.replaceState({kos: 1}, "", location.hash);
        setTimeout(() => pending === "schuzky-list" ? backToList() : show(pending), voice ? 0 : 1600);
      }
    } catch (err) { toast("Nepovedlo se: " + err.message, true); box.hidden = true; }
  };
  box.querySelector("[data-g=stay]").focus();
}
document.addEventListener("keydown", e => { const g = document.getElementById("recguard"); if (e.key === "Escape" && g && !g.hidden) g.hidden = true; });

// ---------- aplikace v pozadí během nahrávání ----------
document.addEventListener("visibilitychange", () => {
  const where = recActive();
  if (document.hidden) {
    // Hlas i schůzka nahrávají dál (telefon v kapse, handsfree, zamčená obrazovka) — ale se stálým oznámením
    const st = where === "hlas" ? vstate : where === "schuzky" ? MT.rec.state() : null;
    if (st === "rec" || st === "paused") recNotify(where, st);
  } else recNotify(null);
});
// Stálé oznámení „Nahrává se …“, když je aplikace v pozadí (klepnutím zpět do aplikace)
async function recNotify(where, st) {
  try {
    const reg = await navigator.serviceWorker.ready;
    if (!where) { (await reg.getNotifications({tag: "kos-rec"})).forEach(n => n.close()); return; }
    if (Notification.permission !== "granted") return;
    const what = where === "hlas" ? "hlasová poznámka" : "schůzka";
    await reg.showNotification(st === "paused" ? `Nahrávání pozastaveno (${what})` : `Nahrává se ${what}`, {tag: "kos-rec",
      body: "Klepnutím se vrátíte do aplikace Knowledge OS.",
      icon: "/static/icon.svg", requireInteraction: true, silent: true, data: {url: location.pathname + location.hash}});
  } catch {}
}
async function askNotify() {   // při startu nahrávání schůzky (klepnutí = smí se zeptat)
  try { if ("Notification" in window && Notification.permission === "default") await Notification.requestPermission(); } catch {}
}

// ---------- hlas (MediaRecorder po 30 s, fronta v IndexedDB) ----------
const DB = new Promise((res, rej) => { const o = indexedDB.open("kos-voice", 1);
  o.onupgradeneeded = () => o.result.createObjectStore("chunks", {keyPath: "key"}); o.onsuccess = () => res(o.result); o.onerror = () => rej(o.error); });
const idb = async (mode, fn) => { const db = await DB; return new Promise((res, rej) => { const tx = db.transaction("chunks", mode); const r = fn(tx.objectStore("chunks")); tx.oncomplete = () => res(r && r.result); tx.onerror = () => rej(tx.error); }); };
let rec = null, session = null, n = 0, stream = null, wake = null, tick = null, blobs = [], mime = "";
let vstate = "idle", elapsed = 0, runStart = 0;
const recSecs = () => elapsed + (vstate === "rec" ? (Date.now() - runStart) / 1000 : 0);
const VSTATE_TEXT = {idle: "Připraveno", rec: "Nahrávám…", paused: "Pozastaveno",
  stopping: "Ukončuji…", review: "Hotovo — poslechněte si a odešlete"};
function setVState(st) {
  vstate = st;
  $("#vstart").hidden = st !== "idle";
  $("#vpause").hidden = $("#vstop").hidden = !(st === "rec" || st === "paused");
  $("#vpause").innerHTML = st === "paused" ? "<span>▶</span>Pokračovat" : "<span>❚❚</span>Pauza";
  $("#vstop").classList.toggle("live", st === "rec");
  $("#vreview").hidden = st !== "review";
  $("#vtime").classList.toggle("live", st === "rec");
  $("#vstate").textContent = VSTATE_TEXT[st];
  document.querySelector(".device")?.setAttribute("data-state", st);
}
async function holdWake() { try { wake = await navigator.wakeLock.request("screen"); } catch {} }
function dropWake() { try { wake && wake.release(); } catch {} wake = null; }
async function uploader() {
  const all = await idb("readonly", s => s.getAll());
  for (const c of all.sort((a, b) => a.key.localeCompare(b.key))) {
    try {
      if (c.kind === "chunk") {
        const r = await fetch(`/api/voice/sessions/${c.session}/chunks/${c.n}`, {method: "PUT", body: c.blob});
        if (!r.ok) throw new Error(r.status);
      } else if (c.kind === "discard") {
        const r = await fetch(`/api/voice/sessions/${c.session}`, {method: "DELETE"});
        if (!r.ok && r.status !== 404) throw new Error(r.status);
      } else if (c.kind === "finish") {
        const pending = all.filter(x => x.kind === "chunk" && x.session === c.session);
        if (pending.length) continue;
        await api(`/api/voice/sessions/${c.session}/finish`, {method: "POST", body: JSON.stringify({chunks: c.chunks, title: c.title})});
        setTimeout(loadNotes, 1500);
      }
      await idb("readwrite", s => s.delete(c.key));
    } catch { break; }       // bez sítě: zkusí se znovu později
  }
  const left = (await idb("readonly", s => s.getAll())).filter(x => x.kind === "chunk").length;
  if (left) $("#vinfo").textContent = `Čeká na odeslání: ${left} kusů (odešle se po obnovení signálu).`;
}
setInterval(uploader, 5000);
async function startRec() {
  stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}});
  mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find(m => MediaRecorder.isTypeSupported(m)) || "";
  session = (await api("/api/voice/sessions", {method: "POST", headers: {"x-mime": mime}})).session;
  n = 0; blobs = []; elapsed = 0;
  rec = new MediaRecorder(stream, mime ? {mimeType: mime, audioBitsPerSecond: 32000} : {});
  const sid = session;
  rec.ondataavailable = async ev => { if (!ev.data.size) return; blobs.push(ev.data);
    const k = n++; await idb("readwrite", s => s.put({key: `${sid}-${String(k).padStart(5,"0")}`, kind: "chunk", session: sid, n: k, blob: ev.data})); uploader(); };
  rec.onstop = () => {
    stream.getTracks().forEach(t => t.stop()); clearInterval(tick); dropWake();
    $("#vplay").src = URL.createObjectURL(new Blob(blobs, {type: (rec.mimeType || mime || "audio/webm").split(";")[0]}));
    $("#vtime").textContent = mmss(elapsed);
    setVState("review");
  };
  rec.start(30000);
  runStart = Date.now(); setVState("rec"); holdWake(); armGuard(); askNotify();
  tick = setInterval(() => { $("#vtime").textContent = mmss(recSecs()); }, 250);
}
function resetRec() {
  recNotify(null);
  if ($("#vplay").src) URL.revokeObjectURL($("#vplay").src);
  $("#vplay").removeAttribute("src"); $("#vplay").load();
  rec = null; session = null; blobs = []; elapsed = 0; $("#vtitle").value = ""; $("#vtime").textContent = "00:00";
  setVState("idle");
}
$("#vstart").onclick = async () => {
  try { await startRec(); }
  catch (err) { $("#vinfo").textContent = "Mikrofon nelze spustit: " + err.message + " (povolte mikrofon; aplikace musí běžet přes HTTPS)."; }
};
$("#vpause").onclick = () => {
  if (vstate === "rec") { try { rec.requestData(); } catch {} rec.pause(); elapsed += (Date.now() - runStart) / 1000; setVState("paused"); }
  else if (vstate === "paused") { rec.resume(); runStart = Date.now(); setVState("rec"); holdWake(); }
};
$("#vstop").onclick = () => {
  if (vstate === "rec") elapsed += (Date.now() - runStart) / 1000;
  if (vstate === "rec" || vstate === "paused") { setVState("stopping"); rec.stop(); }
};
$("#vsend").onclick = async () => {
  const sid = session, chunks = n, title = $("#vtitle").value;
  await idb("readwrite", s => s.put({key: `${sid}-zfinish`, kind: "finish", session: sid, chunks, title}));
  resetRec();
  $("#vinfo").textContent = "Odesláno k přepisu. Text se objeví níže za chvíli (u delší nahrávky pár minut).";
  uploader(); setTimeout(loadNotes, 1500);
};
const waitVoice = () => new Promise(res => { const w = setInterval(() => { if (vstate === "review" || vstate === "idle") { clearInterval(w); res(); } }, 100); });
async function voiceSave() { if (vstate === "rec" || vstate === "paused") { $("#vstop").onclick(); await waitVoice(); } if (vstate === "review") await $("#vsend").onclick(); }
async function voiceDrop() {
  if (vstate === "rec" || vstate === "paused") { $("#vstop").onclick(); await waitVoice(); }
  if (session) { await discardSession(session); resetRec(); $("#vinfo").textContent = "Nahrávka zahozena."; setTimeout(loadNotes, 1500); }
}
async function discardSession(sid) {
  const all = await idb("readonly", s => s.getAll());
  for (const c of all.filter(x => x.session === sid)) await idb("readwrite", s => s.delete(c.key));
  await idb("readwrite", s => s.put({key: `${sid}-zdiscard`, kind: "discard", session: sid}));
  uploader();
}
$("#vdiscard").onclick = async () => {
  const b = $("#vdiscard");
  if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Opravdu zahodit? Klikněte znovu"; b.classList.add("danger");
    setTimeout(() => { delete b.dataset.armed; b.textContent = "Zahodit"; b.classList.remove("danger"); }, 5000); return; }
  delete b.dataset.armed; b.textContent = "Zahodit"; b.classList.remove("danger");
  await discardSession(session); resetRec();
  $("#vinfo").textContent = "Nahrávka zahozena.";
  setTimeout(loadNotes, 1500);
};
// Neodeslané nahrávky z dřívějška (aplikace zavřená před Odeslat): odeslat / zahodit
window.sendPending = async (sid, chunks) => {
  await api(`/api/voice/sessions/${sid}/finish`, {method: "POST", body: JSON.stringify({chunks})}); loadNotes(); };
window.discardPending = async (btn, sid) => {
  if (!btn.dataset.armed) { btn.dataset.armed = "1"; btn.textContent = "Opravdu? Klikněte znovu"; btn.classList.add("danger"); return; }
  await discardSession(sid); setTimeout(loadNotes, 1000); };
window.addEventListener("beforeunload", e => { if (["rec", "paused", "review"].includes(vstate)) { e.preventDefault(); e.returnValue = ""; } });
setVState("idle");
async function loadNotes() {
  const [list, pend, st] = await Promise.all([api("/api/voice/notes"), api("/api/voice/pending"),
                                              api("/api/status").catch(() => ({}))]);
  const qp = st.quota_pause;
  const errs = pend.some(p => p.error);
  const ptxt = pend.filter(p => p.session !== session && !p.meeting).map(p => `<div class="card"><span class="chip ${p.error ? "negative" : "mixed"}">${p.error ? "chyba" : (p.finished ? "přepisuje se" : "neodesláno")}</span>
     ${esc(p.title || p.session)} <span class="hint">${p.chunks} kusů</span>${p.error ? `<p class="hint">${esc(p.error)}</p>` : ""}
     ${p.missing && p.missing.length ? `<p class="hint">Chybí kusy ${esc(p.missing.join(", "))} — dorazí z telefonu, až bude signál.</p>` : ""}
     ${(p.finished || p.error) ? `<div class="bar"><button class="ghost small" onclick="discardPending(this, '${esc(p.session)}')">Zahodit nahrávku</button></div>` : ""}
     ${!p.finished && !p.error ? `<div class="bar"><span class="hint">Neodeslaná nahrávka.</span>
        <button class="ghost small" onclick="sendPending('${esc(p.session)}', ${p.chunks})">Odeslat k přepisu</button>
        <button class="ghost small" onclick="discardPending(this, '${esc(p.session)}')">Zahodit</button></div>` : ""}</div>`).join("")
     + (errs ? `<button class="ghost" onclick="voiceRetry()">Zkusit znovu zpracovat</button>` : "");
  if (pend.some(p => p.finished && !p.error)) setTimeout(loadNotes, 10000);
  const qbanner = qp ? `<div class="card"><span class="chip mixed">čeká</span> Denní bezplatný limit Gemini je vyčerpaný.
     Zpracování se automaticky zkusí znovu v ${esc(qp.until.slice(11, 16))} (limit se obnovuje kolem 9:00).</div>` : "";
  $("#vpending").innerHTML = qbanner + ptxt;
  $("#vnotessum").textContent = `Poslední poznámky (${list.length})`;
  $("#vlist").innerHTML = list.map((v, i) => `<div class="card note"><div class="meta"><b>${esc(v.title || v.created)}</b><span class="hint">${mmss(v.duration)}</span>
       <button class="ghost" onclick="copyNote(${i})">Kopírovat</button>
       <button class="ghost small" onclick="removeSrc(this, '${esc(v.source_id)}', loadNotes)">Odstranit</button></div>
       ${v.summary ? `<p class="hint">${esc(v.summary)}</p>` : ""}<pre>${esc(v.text)}</pre></div>`).join("") || `<p class="hint">Zatím žádné poznámky.</p>`;
  window._notes = list;
}
window.voiceRetry = async () => { await api("/api/voice/retry", {method: "POST"}); loadNotes(); };
window.copyNote = async i => {
  const t = window._notes[i].text;
  try { await navigator.clipboard.writeText(t); } catch { const ta = document.createElement("textarea"); ta.value = t; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
};

// ---------- úlohy (co běží, co selhalo, co je hotové) ----------
const JOB = {g: "", timer: null, seq: 0};
const JGROUP = {running: "Běží a čeká", failed: "Selhalo", done: "Zpracováno"};
const JCHIP = {running: "mixed", failed: "negative", done: "positive"};
const JACT = {
  requeue: ["Spustit hned", "ghost small"], dequeue: ["Odebrat z fronty", "ghost small", true],
  preview: ["Náhled", "ghost small"], retry_failed: ["Zkusit znovu", "ghost small"],
  drop_doc_failed: ["Smazat ze seznamu", "ghost small", true], supersede: ["Nahradit starší verzi", "ghost small"], drop_failed: ["Smazat ze seznamu", "ghost small", true],
  voice_retry: ["Zkusit znovu", "ghost small"], send: ["Odeslat k přepisu", "ghost small"],
  discard: ["Zahodit nahrávku", "ghost small", true], open_meeting: ["Otevřít", "ghost small"],
  delete_source: ["Smazat ze systému", "ghost small", true], delete_meeting: ["Smazat zápis", "ghost small", true],
};
function setBadge(n) {
  document.querySelectorAll(".badge").forEach(b => { b.hidden = !n; b.textContent = n || ""; });
}
function jobCard(it, i) {
  const when = it.at ? String(it.at).replace("T", " ").slice(0, 16) : "";
  const meta = [it.kind_label, when, it.detail].filter(Boolean).map(esc).join(" · ");
  const acts = (it.actions || []).map(a => JACT[a] ? `<button class="${JACT[a][1]}" data-act="${a}" data-i="${i}">${JACT[a][0]}</button>` : "").join("");
  const link = it.url && /^https?:/.test(it.url) ? ` <a href="${esc(it.url)}" target="_blank" rel="noopener">odkaz</a>` : "";
  return `<div class="card job"><div class="jhead"><span class="chip ${JCHIP[it.group]}">${esc(it.state)}</span><b>${esc(it.title)}</b></div>
    <p class="hint">${meta}${link}</p>
    ${it.error ? `<p class="jerr">${esc(it.error)}</p>` : ""}
    ${it.log && it.log.length ? `<ol class="jlog">${it.log.slice(-8).map((l, k, a) => `<li class="${k === a.length - 1 ? "now" : ""}${/čekám|přetížen|!|chyb|neplatn|503|429/i.test(l.text) ? " warn" : ""}"><time>${esc(l.at.slice(0, 5))}</time> ${esc(l.text)}</li>`).join("")}</ol>` : ""}
    ${it.hit ? `<p class="jhit">„${esc(it.hit.length > 180 ? it.hit.slice(0, 180) + "…" : it.hit)}“</p>` : ""}
    ${acts ? `<div class="bar">${acts}</div>` : ""}</div>`;
}
async function loadJobs() {
  clearTimeout(JOB.timer);
  const my = ++JOB.seq, q = $("#jq").value.trim();
  let r;
  try { r = await api("/api/jobs?q=" + encodeURIComponent(q)); }
  catch (e) { $("#jlist").innerHTML = `<p class="msg">Nepodařilo se načíst: ${esc(e.message)}</p>`; return; }
  if (my !== JOB.seq) return;
  document.querySelectorAll(".jfilters b[data-c]").forEach(b => b.textContent = r.counts[b.dataset.c] || 0);
  setBadge(r.counts.failed);
  const kind = $("#jkind").value;
  JOB.items = r.items.filter(it => (!JOB.g || it.group === JOB.g) && (!kind || it.kind === kind || (kind === "youtube" && it.kind === "job")));
  const pause = r.pause ? `<div class="card"><span class="chip mixed">pauza</span> Zpracování je pozastavené do ${esc(r.pause.until.slice(11, 16))}.
      <button class="ghost small" data-act="resume">Zkusit hned</button></div>` : "";
  let html = "";
  for (const g of ["running", "failed", "done"]) {
    const rows = JOB.items.map((it, i) => [it, i]).filter(([it]) => it.group === g);
    if (!rows.length) continue;
    html += `<h3 class="jgroup">${JGROUP[g]} <span class="hint">${rows.length}</span></h3>` + rows.map(([it, i]) => jobCard(it, i)).join("");
  }
  $("#jlist").innerHTML = pause + (html || `<p class="hint">${q ? "Nic neodpovídá hledání." : "Žádné úlohy."}</p>`);
  if (r.counts.running && $("#ulohy").classList.contains("on")) JOB.timer = setTimeout(loadJobs, 10000);
}
// Náhled zdroje přímo v kartě: nahrávka (u hlasových poznámek), shrnutí, přepis
async function togglePreview(b, it) {
  const card = b.closest(".card");
  const open = card.querySelector(".jprev");
  if (open) { open.remove(); b.textContent = "Náhled"; return; }
  clearTimeout(JOB.timer);  // během čtení náhledu neobnovovat seznam
  b.disabled = true; b.textContent = "Načítám…";
  let r;
  try { r = await api("/api/preview/" + encodeURIComponent(it.source_id)); }
  catch (e) { b.disabled = false; b.textContent = "Náhled"; toast("Náhled nejde načíst: " + e.message, true); return; }
  const box = document.createElement("div");
  box.className = "jprev";
  box.innerHTML = (r.audio ? `<audio controls preload="metadata" src="${esc(r.audio)}"></audio>` : "")
    + (r.summary ? `<p class="jsum"><b>Shrnutí:</b> ${esc(r.summary)}</p>` : "")
    + `<div class="jtext">${esc(r.text || "(přepis je prázdný)").replace(/^## (.*)$/gm, "<b class=\"jsec\">$1</b>")}${r.truncated ? "\n\n… (zkráceno)" : ""}</div>`
    + `<div class="bar"><button class="ghost small" type="button">Kopírovat text</button></div>`;
  box.querySelector(".bar button").onclick = async ev => {
    try { await navigator.clipboard.writeText(r.text); toast("✓ Text zkopírován"); }
    catch { toast("Kopírování se nepovedlo", true); }
  };
  b.closest(".bar").before(box);
  b.disabled = false; b.textContent = "Skrýt náhled";
}
$("#jq").addEventListener("input", () => { clearTimeout(JOB.deb); JOB.deb = setTimeout(loadJobs, 300); });
$("#jkind").onchange = loadJobs;
document.querySelectorAll(".jfilters button").forEach(b => b.onclick = () => {
  JOB.g = b.dataset.g; document.querySelectorAll(".jfilters button").forEach(x => x.classList.toggle("on", x === b)); loadJobs(); });
$("#jlist").addEventListener("click", async e => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const act = b.dataset.act, it = JOB.items[+b.dataset.i];
  if (act === "resume") { await api("/api/worker/resume", {method: "POST"}); return loadJobs(); }
  if (act === "preview") return togglePreview(b, it);
  if (JACT[act][2] && !b.dataset.armed) {
    b.dataset.armed = "1"; const t = b.textContent; b.textContent = "Opravdu? Klikněte znovu"; b.classList.add("danger");
    setTimeout(() => { if (b.isConnected) { delete b.dataset.armed; b.textContent = t; b.classList.remove("danger"); } }, 5000);
    return;
  }
  b.disabled = true;
  const enc = encodeURIComponent;
  try {
    if (act === "requeue") await api("/api/queue/retry?url=" + enc(it.url), {method: "POST"});
    else if (act === "dequeue") await api("/api/queue?url=" + enc(it.url), {method: "DELETE"});
    else if (act === "retry_failed") await api("/api/sources/retry", {method: "POST", body: JSON.stringify({url: it.url})});
    else if (act === "drop_failed") await api("/api/queue/failed?url=" + enc(it.url), {method: "DELETE"});
    else if (act === "voice_retry") await api("/api/voice/retry", {method: "POST"});
    else if (act === "send") await api(`/api/voice/sessions/${enc(it.session)}/finish`, {method: "POST", body: JSON.stringify({chunks: it.chunks || 0})});
    else if (act === "discard") await api(`/api/voice/sessions/${enc(it.session)}`, {method: "DELETE"});
    else if (act === "delete_source") { b.textContent = "Mažu…"; await api(`/api/sources/${enc(it.source_id)}`, {method: "DELETE"}); }
    else if (act === "delete_meeting") await api(`/api/meetings/${enc(it.meeting)}`, {method: "DELETE"});
    else if (act === "drop_doc_failed") await api(`/api/documents/failed/${it.n}`, {method: "DELETE"});
    else if (act === "supersede") await api(`/api/documents/${enc(it.source_id)}/supersede`, {method: "POST", body: JSON.stringify({old: it.possible_version_of[0]})});
    else if (act === "open_meeting") { show("schuzky"); if (typeof openMeeting === "function") openMeeting(it.meeting); return; }
  } catch (err) { b.disabled = false; b.textContent = "Chyba: " + err.message; return; }
  loadJobs();
});
api("/api/jobs").then(r => setBadge(r.counts.failed)).catch(() => {});

// ---------- dokumenty ----------
const DOC = {items: [], pending: [], scope: null};
const FMT = {pdf: "PDF", docx: "Word", md: "Markdown", txt: "text", html: "web"};
const dvis = () => document.querySelector("input[name=dvis]:checked").value;
function docMsg(html, cls = "") {
  const el = document.createElement("div"); el.className = "card dres " + cls; el.innerHTML = html;
  $("#dmsg").prepend(el); return el;
}
function resultHtml(r) {
  const name = esc(r.title || r.filename || "");
  if (r.status === "ok") return `<span class="chip positive">přidáno</span> <b>${name}</b>
      <p class="hint">${[FMT[r.format] || r.format, r.pages ? r.pages + " str." : "", r.segments + " úseků",
        r.replaced ? "nahradil starší verzi" : ""].filter(Boolean).join(" · ")}. AI karta se připraví během pár minut, hledat v něm jde hned.</p>`;
  if (r.status === "duplicate") return `<span class="chip">už v systému</span> <b>${name}</b>
      <p class="hint">Stejný dokument byl přidán ${esc((r.added || "").slice(0, 10))}${r.archived ? " (jako starší verze v archivu)" : ""}. Nic se nezměnilo.</p>`;
  if (r.status === "error") return `<span class="chip negative">nepodporováno</span> <b>${esc(r.filename || "")}</b><p class="hint">${esc(r.error)}</p>`;
  return esc(r.status);
}
function versionPrompt(r) {
  const c = r.candidates || [];
  const box = docMsg(`<span class="chip mixed">nová verze?</span> <b>${esc(r.filename || r.title || "")}</b>
    <p>Vypadá to jako nová verze dokumentu, který už v systému je:</p>
    ${c.map(x => `<div class="dcand"><b>${esc(x.title)}</b> <span class="hint">přidán ${esc((x.added || "").slice(0, 10))} · ${esc(x.why)}</span>
      <button class="primary small" data-a="replace" data-sid="${esc(x.source_id)}">Nahradit touto novou verzí</button></div>`).join("")}
    <div class="bar"><button class="ghost small" data-a="new">Uložit jako samostatný dokument</button>
      <button class="ghost small" data-a="cancel">Zrušit</button></div>`, "dver");
  box.dataset.token = r.token;
  box.onclick = async e => {
    const b = e.target.closest("button[data-a]"); if (!b) return;
    box.querySelectorAll("button").forEach(x => x.disabled = true);
    try {
      const d = await api("/api/documents/decide", {method: "POST", body: JSON.stringify({token: r.token, action: b.dataset.a, replace: b.dataset.sid})});
      box.className = "card dres"; box.innerHTML = d.status === "cancelled" ? `<span class="chip">zrušeno</span> ${esc(r.filename || "")}` : resultHtml(d);
      loadDocs();
    } catch (err) { box.insertAdjacentHTML("beforeend", `<p class="msg">Chyba: ${esc(err.message)}</p>`); }
  };
}
async function uploadFiles(files, replace = null) {
  if (!files.length) return;
  const fd = new FormData();
  [...files].forEach(f => fd.append("file", f));
  fd.append("visibility", dvis());
  if (replace) fd.append("replace", replace);
  const wait = docMsg(`<span class="hint">Nahrávám a převádím ${files.length > 1 ? files.length + " soubory" : "„" + esc(files[0].name) + "“"}…</span>`);
  try {
    const r = await fetch("/api/documents", {method: "POST", body: fd});
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    wait.remove();
    for (const x of (await r.json()).results) x.status === "maybe_version" ? versionPrompt(x) : docMsg(resultHtml(x));
    loadDocs();
  } catch (err) { wait.innerHTML = `<p class="msg">Chyba: ${esc(err.message)}</p>`; }
}
$("#dfile").onchange = e => { uploadFiles(e.target.files); e.target.value = ""; };
const dz = $("#ddrop");
["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("over"); }));
dz.addEventListener("drop", e => uploadFiles(e.dataTransfer.files));
$("#durlbtn").onclick = async () => {
  const url = $("#durl").value.trim(); if (!url) return;
  const b = $("#durlbtn"); b.disabled = true; b.textContent = "Stahuji…";
  try {
    const r = await api("/api/documents/url", {method: "POST", body: JSON.stringify({url, visibility: dvis(), watch: $("#dwatch").checked})});
    r.status === "maybe_version" ? versionPrompt(r) : docMsg(resultHtml(r));
    $("#durl").value = ""; loadDocs();
  } catch (err) { docMsg(`<span class="chip negative">nejde načíst</span> ${esc(url)}<p class="hint">${esc(err.message)}</p>`); }
  b.disabled = false; b.textContent = "Přidat stránku";
};
$("#durl").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); $("#durlbtn").click(); } });

function docCard(d, i) {
  const meta = [d.doc_type, FMT[d.format] || d.format, d.pages ? d.pages + " str." : "", d.applies_to ? "platí pro: " + d.applies_to : "",
    (d.added || "").slice(0, 10)].filter(Boolean).map(esc).join(" · ");
  const chips = `${d.archived ? `<span class="chip">starší verze</span>` : ""}<span class="chip ${d.visibility === "public" ? "" : "mixed"}">${d.visibility === "public" ? "veřejný" : "soukromý"}</span>
    ${d.card_pending ? `<span class="chip">karta se připravuje</span>` : ""}${d.url && d.watch ? `<span class="chip">hlídá změny</span>` : ""}`;
  return `<div class="card doc" data-i="${i}"><div class="jhead">${chips}<b>${esc(d.title)}</b></div>
    <p class="hint">${meta}${d.url ? ` · <a href="${esc(d.url)}" target="_blank" rel="noopener">zdroj</a>` : ""}</p>
    ${d.summary ? `<p class="dsum">${esc(d.summary)}</p>` : ""}
    ${d.card_error ? `<p class="jerr">AI kartu se nepodařilo vytvořit: ${esc(d.card_error)}</p>` : ""}
    ${d.check_error ? `<p class="jerr">Poslední kontrola stránky: ${esc(d.check_error)}</p>` : ""}
    ${(d.possible_version_of || []).length ? `<p class="hint">Možná novější verze jiného dokumentu. <button class="ghost small" data-d="supersede">Nahradit starší verzi</button></p>` : ""}
    <div class="bar">
      <button class="ghost small" data-d="preview">Náhled</button>
      ${d.archived ? "" : `<button class="ghost small" data-d="ask">Zeptat se na tento dokument</button>`}
      <a class="ghost small btnlink" href="${d.url ? esc(d.url) : `/api/documents/${encodeURIComponent(d.source_id)}/file`}" target="_blank" rel="noopener">${d.url ? "Otevřít web ↗" : "Originál"}</a>
      ${d.archived ? "" : `<button class="ghost small" data-d="replace">Nahradit novou verzí</button>`}
      ${d.url && !d.archived ? `<button class="ghost small" data-d="refresh">Zkontrolovat změny</button>` : ""}
      ${d.archived ? "" : `<button class="ghost small" data-d="vis">${d.visibility === "public" ? "Označit jako soukromý" : "Označit jako veřejný"}</button>`}
      <button class="ghost small" data-d="delete">Smazat</button>
    </div></div>`;
}
async function loadDocs() {
  let r;
  try { r = await api("/api/documents?archived=" + ($("#darch").checked ? "true" : "false")); }
  catch (e) { $("#dlist").innerHTML = `<p class="msg">Nepodařilo se načíst: ${esc(e.message)}</p>`; return; }
  DOC.items = r.items;
  const v = r.vectors || {};
  $("#dvec").textContent = v.chunks ? (v.embedded >= v.chunks ? `Hledání podle významu: připraveno (${v.chunks} úseků ve znalostní bázi).`
    : `Hledání podle významu se připravuje: ${v.embedded} z ${v.chunks} úseků…`) : "";
  // nevyřízené „nová verze?“ (např. po sdílení z telefonu)
  const shown = new Set([...document.querySelectorAll(".dver")].map(x => x.dataset.token));
  for (const p of r.pending || []) if (!shown.has(p.token)) versionPrompt({...p.result, token: p.token, filename: p.filename});
  drawDocs();
  if (r.items.some(d => d.card_pending) && $("#dokumenty").classList.contains("on")) { clearTimeout(DOC.t); DOC.t = setTimeout(loadDocs, 15000); }
}
function drawDocs() {
  const q = mnormD($("#dq").value.trim());
  const list = DOC.items.map((d, i) => [d, i]).filter(([d]) => !q ||
    q.split(/\s+/).every(w => mnormD(`${d.title} ${d.doc_type || ""} ${d.applies_to || ""} ${d.summary || ""} ${d.format}`).includes(w)));
  $("#dfiltcount").textContent = q || $("#darch").checked ? ` · aktivní (${list.length} z ${DOC.items.length})` : "";
  $("#dlist").innerHTML = list.map(([d, i]) => docCard(d, i)).join("") ||
    `<p class="hint">${DOC.items.length ? "Nic neodpovídá hledání." : "Zatím žádné dokumenty. Přidejte soubor nebo adresu stránky nahoře."}</p>`;
}
const mnormD = s => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
$("#dq").addEventListener("input", drawDocs);
$("#darch").onchange = loadDocs;
$("#dlist").addEventListener("click", async e => {
  const b = e.target.closest("button[data-d]"); if (!b) return;
  const d = DOC.items[+b.closest(".doc").dataset.i], a = b.dataset.d, enc = encodeURIComponent;
  if (a === "preview") return togglePreview(b, d);
  if (a === "ask") { setChatScope(d); return show("chat"); }
  if (a === "replace") { DOC.replace = d.source_id; return $("#dreplace").click(); }
  if (a === "delete") return removeSrc(b, d.source_id, loadDocs);
  b.disabled = true;
  try {
    if (a === "vis") await api(`/api/documents/${enc(d.source_id)}/visibility`, {method: "POST", body: JSON.stringify({visibility: d.visibility === "public" ? "private" : "public"})});
    if (a === "supersede") await api(`/api/documents/${enc(d.source_id)}/supersede`, {method: "POST", body: JSON.stringify({old: d.possible_version_of[0]})});
    if (a === "refresh") { const r = await api(`/api/documents/${enc(d.source_id)}/refresh`, {method: "POST"});
      toast(r.status === "ok" ? "✓ Stránka se změnila — uložena nová verze" : r.status === "duplicate" ? "Stránka se od minula nezměnila" : (r.error || r.status), r.status === "error"); }
    loadDocs();
  } catch (err) { b.disabled = false; toast("Chyba: " + err.message, true); }
});
$("#dreplace").onchange = e => { const f = e.target.files; if (f.length) uploadFiles([f[0]], DOC.replace); e.target.value = ""; };
// chat jen nad jedním dokumentem
function setChatScope(d) {
  DOC.scope = d ? {sid: d.source_id, title: d.title} : null;
  $("#cscope").hidden = !d;
  $("#cscopetitle").textContent = d ? `„${d.title}“` : "";
  $("#cq").placeholder = d ? "Zeptejte se na tento dokument… (např. Jak se nastavuje…? Co říká kapitola o…?)"
    : "Zeptejte se… (např. Co je OpenAI a k čemu se využívá? Kdo mluvil o modelu Claude a jak ho hodnotil?)";
}
$("#cscopeclear").onclick = () => setChatScope(null);

// ---------- slovník názvů ----------
async function loadGlossary() {
  const r = await api("/api/glossary");
  $("#gtext").value = r.text; $("#gpath").textContent = r.path;
  $("#ginfo").textContent = `${r.entries.length} názvů, ${r.entries.reduce((a, e) => a + e.variants.length, 0)} variant k opravě`;
  $("#gautoc").textContent = r.auto.length;
  $("#gauto").innerHTML = r.auto.map(t => `<span class="chip">${esc(t)}</span>`).join(" ");
}
$("#gsave").onclick = async () => {
  const b = $("#gsave"); b.disabled = true;
  try { const r = await api("/api/glossary", {method: "PUT", body: JSON.stringify({text: $("#gtext").value})});
        toast(`✓ Slovník uložen (${r.entries.length} názvů). Použije se u dalších přepisů.`); loadGlossary(); }
  catch (e) { toast("Nepodařilo se uložit: " + e.message, true); }
  b.disabled = false;
};
$("#gapply").onclick = async () => {
  const b = $("#gapply");
  if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Uložit a opravit všechny přepisy? Klepněte znovu"; b.classList.add("danger");
    setTimeout(() => { delete b.dataset.armed; b.textContent = "Opravit i starší přepisy"; b.classList.remove("danger"); }, 5000); return; }
  delete b.dataset.armed; b.classList.remove("danger"); b.disabled = true; b.textContent = "Opravuji…";
  try {
    await api("/api/glossary", {method: "PUT", body: JSON.stringify({text: $("#gtext").value})});
    const r = await api("/api/glossary/apply", {method: "POST"});
    toast(r.fixes ? `✓ Opraveno ${r.fixes}× ve ${r.sources} zdrojích` : "Ve starších přepisech nebylo co opravit");
  } catch (e) { toast("Chyba: " + e.message, true); }
  b.disabled = false; b.textContent = "Opravit i starší přepisy";
};

// ---------- stav ----------

api("/api/speakers").then(l => $("#speakers").innerHTML = l.map(s => `<option value="${esc(s.speaker)}">`).join(""));
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
const sh = new URLSearchParams(location.search).get("shared");
if (sh) { $("#amsg").hidden = false; $("#amsg").textContent = sh;
  if (location.hash.startsWith("#dokumenty")) setTimeout(() => docMsg(esc(sh)), 300); }
// Výchozí záložka: na telefonu Hlas (rychlé nahrání poznámky), na počítači Hledat
const PHONE = matchMedia("(max-width: 767px)").matches;
window.INIT_HASH = location.hash;   // meetings.js si z něj vezme případné #schuzky/<id>
history.replaceState({kos: 1}, "", location.hash || (PHONE ? "#hlas" : "#hledat"));
show(location.hash.slice(1).split("/")[0], false);
// Logo = Domů: telefon → Hlas, počítač → Hledat
$("#home").onclick = e => { e.preventDefault(); show(matchMedia("(max-width: 767px)").matches ? "hlas" : "hledat"); };

// Nápověda "i": klik/ťuk přepíná, klik jinam zavře (na počítači i po najetí myší).
document.addEventListener("click", e => {
  const b = e.target.closest(".ibtn");
  document.querySelectorAll(".info.open").forEach(x => { if (!b || x !== b.parentElement) { x.classList.remove("open"); x.firstElementChild.setAttribute("aria-expanded", "false"); } });
  if (b) { const on = b.parentElement.classList.toggle("open"); b.setAttribute("aria-expanded", on ? "true" : "false"); }
});
