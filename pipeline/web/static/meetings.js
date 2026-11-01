"use strict";
// ---------- Schůzky: šablony → zápis → nahrávka → AI vyplní → předpokoj → schváleno (+ PDF) ----------
// Využívá pomocné funkce z app.js: $, esc, mmss, api, idb, uploader.
const MT = {types: {}, templates: [], cur: null, dirty: false, saveTimer: null, rec: null, poll: null};
const MSTATUS = {draft: ["rozpracováno", ""], recording: ["nahrává se", "mixed"], processing: ["zpracovává se", "mixed"],
  review: ["ke kontrole", "mixed"], approved: ["schváleno", "positive"], error: ["chyba", "negative"]};

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el[k] = v; else if (k === "html") el.innerHTML = v;
    else if (k === "value") el.value = v; else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(c));
  return el;
}
const fmtDate = iso => iso ? `${+iso.slice(8, 10)}. ${+iso.slice(5, 7)}. ${iso.slice(0, 4)}` : "";
const chip = st => h("span", {class: "chip " + (MSTATUS[st] || ["", ""])[1]}, (MSTATUS[st] || [st])[0]);

function mview(name) {
  ["mlist", "medit", "mtpl"].forEach(v => $("#" + v).hidden = v !== name);
  if (name !== "medit") { clearInterval(MT.poll); MT.poll = null; }
  window.scrollTo(0, 0);
}

// ---------- seznam ----------
// „← Zápisy“: když jsme přišli ze seznamu, jen krok zpět v historii (tlačítko Zpět pak nevrací do zápisu)
function backToList() {
  if (typeof recGuard === "function" && recGuard("schuzky-list")) return;
  if (history.state && history.state.fromList) history.back();
  else { nav("#schuzky"); loadMeetings(); }
}
async function loadMeetings() {
  if (location.hash.startsWith("#schuzky/")) history.replaceState({kos: 1}, "", "#schuzky");
  mview("mlist");
  const [list, tp] = await Promise.all([api("/api/meetings"), api("/api/meeting-templates")]);
  MT.types = tp.types; MT.templates = tp.templates;
  const groups = [["K odsouhlasení", l => l.status === "review"],
                  ["Rozpracované", l => ["draft", "recording", "processing", "error"].includes(l.status)],
                  ["Schválené", l => l.status === "approved"]];
  MT.list = list;
  drawMeetingList();
}
const mnorm = s => String(s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
function drawMeetingList() {
  const list = MT.list || [];
  const q = mnorm($("#mq").value.trim()), since = $("#msince").value, until = $("#muntil").value;
  const groups = [["K odsouhlasení", l => l.status === "review"],
                  ["Rozpracované", l => ["draft", "recording", "processing", "error"].includes(l.status)],
                  ["Schválené", l => l.status === "approved"]];
  const match = l => (!q || q.split(/\s+/).every(w => mnorm(`${l.title} ${l.template} ${(l.people || []).join(" ")} ${l.search || ""}`).includes(w)))
    && (!since || l.created.slice(0, 10) >= since) && (!until || l.created.slice(0, 10) <= until);
  const box = $("#mlistbox"); box.innerHTML = "";
  const filtered = list.filter(match);
  const active = [q, since, until].filter(Boolean).length;
  $("#mfiltcount").textContent = active ? ` · aktivní (${filtered.length} z ${list.length})` : "";
  if (list.length && !filtered.length) box.append(h("p", {class: "hint"}, "Žádný zápis neodpovídá hledání."));
  for (const [title, f] of groups) {
    const items = filtered.filter(f);
    if (!items.length) continue;
    box.append(h("h3", {}, `${title} (${items.length})`));
    for (const m of items) {
      const extra = m.status === "review" && m.review_until ? ` · bez kontroly se uloží ${fmtDate(m.review_until)}`
        : m.status === "approved" ? ` · ${fmtDate(m.approved_at)}${m.auto_approved ? " (automaticky)" : ""}` : "";
      box.append(h("div", {class: "card mitem", onclick: () => { openMeeting(m.id); history.replaceState({kos: 1, fromList: 1}, "", location.hash); }},
        h("div", {class: "meta"}, chip(m.status), h("b", {}, m.title)),
        h("p", {class: "hint"}, `${fmtDate(m.created)} · ${m.template}${extra}`),
        (m.people || []).length ? h("p", {class: "hint mpeople"}, m.people.join(" · ")) : null,
        m.error ? h("p", {class: "hint"}, m.error) : null));
    }
  }
  if (!list.length) box.append(h("p", {class: "hint"}, "Zatím žádné zápisy. Založte nový — vyplníte, co víte, a schůzku nahrajete."));
}
// Nový zápis: volba ze dvou pevných šablon
document.querySelectorAll("#mchooser [data-tpl]").forEach(b => b.onclick = async () => {
  b.disabled = true;
  try {
    const m = await api("/api/meetings", {method: "POST", body: JSON.stringify({template: b.dataset.tpl})});
    openMeeting(m.id);
  } catch (e) { toast("Zápis nejde založit: " + e.message, true); }
  b.disabled = false;
});
["#mq", "#msince", "#muntil"].forEach(s => $(s).addEventListener("input", () => drawMeetingList()));

// ---------- detail zápisu ----------
async function openMeeting(id, push = true) {
  if (!MT.cur || MT.cur.id !== id) MT.preview = false;
  nav("#schuzky/" + id, push);
  mview("medit");
  const m = await api(`/api/meetings/${id}`);
  MT.cur = m; MT.dirty = false; snapBase();
  renderMeeting();
  clearInterval(MT.poll);
  if (["processing", "recording"].includes(m.status))
    MT.poll = setInterval(async () => {
      const n = await api(`/api/meetings/${id}`).catch(() => null);
      if (n && n.status !== MT.cur.status && !MT.rec?.busy()) {
        if (MT.dirty) await saveMeeting();  // nejdřív uložit rozepsané, server to sloučí s výsledkem AI
        openMeeting(id);
      }
    }, 10000);
}
function editable() { return ["draft", "recording", "processing", "review", "error"].includes(MT.cur.status); }
function touch() {
  MT.dirty = true; $("#msavestate").textContent = "Neuloženo…";
  clearTimeout(MT.saveTimer); MT.saveTimer = setTimeout(saveMeeting, 1500);
}
function snapBase() {
  MT.base = JSON.parse(JSON.stringify({values: MT.cur.values, speakers: MT.cur.speakers || {}, suggestions: MT.cur.suggestions || {}}));
}
async function saveMeeting() {
  if (!MT.dirty || !editable()) return true;
  clearTimeout(MT.saveTimer);
  try {
    const base = MT.base || {values: {}};
    const values = {};
    for (const [k, v] of Object.entries(MT.cur.values)) if (JSON.stringify(v) !== JSON.stringify(base.values[k])) values[k] = v;
    const body = {values, base_status: MT.cur.status};
    if (JSON.stringify(MT.cur.speakers || {}) !== JSON.stringify(base.speakers || {})) body.speakers = MT.cur.speakers;
    if (JSON.stringify(MT.cur.suggestions || {}) !== JSON.stringify(base.suggestions || {})) body.suggestions = MT.cur.suggestions;
    const saved = await api(`/api/meetings/${MT.cur.id}`, {method: "PUT", body: JSON.stringify(body)});
    MT.dirty = false; $("#msavestate").textContent = "Uloženo";
    if (saved.status !== MT.cur.status) { openMeeting(MT.cur.id); return true; }  // AI mezitím doplnila zápis → ukázat
    snapBase();
    return true;
  } catch (e) { $("#msavestate").textContent = "Nepodařilo se uložit: " + e.message; MT.saveErr = e.message; return false; }
}
const segT = i => (MT.cur.transcript || []).find(s => s.i === i);
function seek(t) {
  const a = $("#maudio"); if (!a) return;
  a.currentTime = Math.max(0, t - 1); a.play().catch(() => {});
}
function srcLinks(segs) {
  if (!segs || !segs.length || !MT.cur.transcript) return null;
  return h("span", {class: "srcs"}, segs.slice(0, 4).map(i => {
    const s = segT(i); if (!s) return null;
    return h("button", {type: "button", class: "src", title: `${spk(s.speaker)}: ${s.text}`, onclick: () => { seek(s.t); showQuote(s); }},
      "▶ " + mmss(s.t));
  }));
}
function spk(id) { return (MT.cur.speakers || {})[id] || (MT.cur.speaker_labels || {})[id] || id; }
function showQuote(s) {
  const q = $("#mquote"); q.hidden = false;
  q.innerHTML = `<b>${esc(spk(s.speaker))}</b> <span class="hint">${mmss(s.t)}</span><br>${esc(s.text)}`;
}
function people() {
  const out = [];
  for (const f of MT.cur.template.fields) if (f.type === "people") (MT.cur.values[f.id] || []).forEach(p => p.text && out.push(p.text));
  Object.values(MT.cur.speakers || {}).forEach(n => n && !out.includes(n) && out.push(n));
  return out;
}
function autoGrow(t) { t.style.height = "auto"; t.style.height = (t.scrollHeight + 2) + "px"; }
function input(val, ph, oninput, multi) {
  const el = multi ? h("textarea", {rows: 2, placeholder: ph}) : h("input", {placeholder: ph});
  el.value = val || "";
  el.disabled = !editable();
  el.oninput = () => { oninput(el.value); if (multi) autoGrow(el); touch(); };
  if (multi) setTimeout(() => autoGrow(el));
  return el;
}

function fieldEditor(f) {
  const vals = MT.cur.values;
  const wrap = h("div", {class: "mfield"}, h("label", {class: "mlabel"}, f.label));
  const ro = !editable();
  if (["short", "text", "datetime"].includes(f.type)) {
    const v = vals[f.id] || (vals[f.id] = {text: "", segs: [], ai: false});
    const row = h("div", {class: "mrow" + (v.ai ? " ai" : "")},
      input(v.text, f.type === "datetime" ? "např. 24. 9. 2026 14:00" : "", t => { v.text = t; v.ai = false; }, f.type === "text"),
      v.ai ? h("span", {class: "aibadge"}, "AI") : null, srcLinks(v.segs));
    wrap.append(row);
    const sug = (MT.cur.suggestions || {})[f.id];
    if (sug && !ro) {
      wrap.append(h("div", {class: "msug"}, h("div", {class: "hint"}, "Návrh AI:"), h("div", {}, sug.text), srcLinks(sug.segs),
        h("div", {class: "bar"},
          h("button", {type: "button", class: "ghost small", onclick: () => { vals[f.id] = {...sug, ai: true}; delete MT.cur.suggestions[f.id]; touch(); renderMeeting(); }}, "Nahradit"),
          h("button", {type: "button", class: "ghost small", onclick: () => { v.text = (v.text + "\n" + sug.text).trim(); delete MT.cur.suggestions[f.id]; touch(); renderMeeting(); }}, "Připojit"),
          h("button", {type: "button", class: "ghost small", onclick: () => { delete MT.cur.suggestions[f.id]; touch(); renderMeeting(); }}, "Zahodit"))));
    }
    return wrap;
  }
  const items = vals[f.id] || (vals[f.id] = []);
  const cols = {people: [["text", "Jméno, firma"]], list: [["text", "Bod"]], topics: [["bod", "Bod"], ["text", "Co se řešilo", true]],
                tasks: [["co", "Co", true], ["kdo", "Kdo"], ["termin", "Do kdy"]]}[f.type];
  const list = h("div", {class: "mitems " + f.type});
  items.forEach((it, idx) => {
    const cells = cols.map(([k, ph, multi]) => {
      const el = input(it[k], ph, t => { it[k] = t; it.ai = false; }, multi);
      if (k === "kdo") { el.setAttribute("list", "mpeople"); }
      return el;
    });
    list.append(h("div", {class: "mrow" + (it.ai ? " ai" : "")}, h("div", {class: "mcells"}, cells),
      h("div", {class: "mside"}, it.ai ? h("span", {class: "aibadge"}, "AI") : null, srcLinks(it.segs),
        ro ? null : h("button", {type: "button", class: "ghost small", title: "Odebrat", onclick: () => { items.splice(idx, 1); touch(); renderMeeting(); }}, "✕"))));
  });
  wrap.append(list);
  if (!ro) wrap.append(h("button", {type: "button", class: "ghost small", onclick: () => {
    const it = {segs: [], ai: false}; cols.forEach(([k]) => it[k] = ""); items.push(it); touch(); renderMeeting();
    setTimeout(() => { const r = wrap.querySelectorAll(".mrow"); r[r.length - 1]?.querySelector("input,textarea")?.focus(); });
  }}, "+ přidat"));
  return wrap;
}

// „Mluvčí 1“ → přiřazené jméno ve všech textech zápisu (stejně jako na serveru při schválení)
function renameSpeakers() {
  const pairs = Object.entries(MT.cur.speaker_labels || {})
    .map(([sp, lab]) => [lab, ((MT.cur.speakers || {})[sp] || "").trim()]).filter(([lab, name]) => lab && name && lab !== name);
  if (!pairs.length) return 0;
  const rx = pairs.map(([lab, name]) => [new RegExp(lab.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?!\\d)", "g"), name]);
  let n = 0;
  const fix = t => { for (const [r, name] of rx) t = t.replace(r, () => { n++; return name; }); return t; };
  for (const bucket of [MT.cur.values || {}, MT.cur.suggestions || {}])
    for (const v of Object.values(bucket)) {
      if (v && typeof v.text === "string" && !Array.isArray(v)) v.text = fix(v.text);
      else if (Array.isArray(v)) v.forEach(it => ["text", "bod", "co", "kdo"].forEach(k => { if (typeof it[k] === "string") it[k] = fix(it[k]); }));
    }
  if (n) touch();
  return n;
}
function speakersBlock() {
  const labels = MT.cur.speaker_labels || {};
  if (!Object.keys(labels).length) return null;
  const tr = MT.cur.transcript || [];
  const box = h("div", {class: "card"}, h("h3", {}, "Kdo je kdo"),
    h("p", {class: "hint"}, "Přiřaďte mluvčím z nahrávky jména. Po schválení se použijí v přepisu i ve vyhledávání."));
  for (const [id, lab] of Object.entries(labels)) {
    const segs = tr.filter(s => s.speaker === id);
    const sample = segs.find(s => s.text.length > 40) || segs[0];
    const inp = input((MT.cur.speakers || {})[id], "jméno", t => { MT.cur.speakers[id] = t; });
    inp.addEventListener("change", () => { if (renameSpeakers()) renderMeeting(); });  // po dopsání jména
    inp.setAttribute("list", "mpeople");
    box.append(h("div", {class: "mspk"}, h("div", {}, h("b", {}, lab), h("span", {class: "hint"}, ` · ${segs.length}× mluví`),
      sample ? h("div", {class: "hint quote"}, "„" + sample.text.slice(0, 140) + (sample.text.length > 140 ? "…" : "") + "“ ",
        h("button", {type: "button", class: "src", onclick: () => seek(sample.t)}, "▶ " + mmss(sample.t))) : null), inp));
  }
  return box;
}

function renderMeeting() {
  const m = MT.cur, box = $("#meditbox");
  box.innerHTML = "";
  const title = (m.values.nazev && m.values.nazev.text) || "Nový zápis";
  box.append(h("div", {class: "mhead"}, h("button", {class: "ghost small", onclick: async () => { await saveMeeting(); backToList(); }}, "← Zápisy"),
    chip(m.status), h("span", {id: "msavestate", class: "hint"}, "")));
  if (!(m.status === "approved" || MT.preview))  // v dokumentu je název i šablona už v hlavičce
    box.append(h("h2", {class: "mtitle"}, title), h("p", {class: "hint"}, `Šablona: ${m.template.name}`));
  box.append(h("datalist", {id: "mpeople"}, people().map(p => h("option", {value: p}))));
  if (m.status === "review") {
    box.append(h("div", {class: "msg"}, h("b", {}, "Předpokoj: "), "zkontrolujte a upravte zápis, upřesněte mluvčí, úkoly a termíny. ",
      "Položky od AI jsou označené a mají odkaz ▶ do nahrávky. Bez schválení se zápis uloží tak, jak je, ",
      h("b", {}, fmtDate(m.review_until)), "."));
  }
  if (m.status === "processing") box.append(h("div", {class: "msg"}, "Nahrávka se zpracovává: přepis a vyplnění šablony. ",
    "U hodinové schůzky to trvá zhruba 10–20 minut; stránka se obnoví sama. Mezitím můžete dál psát."));
  if (m.status === "error") box.append(h("div", {class: "msg neg"}, "Zpracování selhalo: " + (m.error || "") +
    " — nahrávka je uložená, zkuste „Zkusit znovu zpracovat“ na záložce Hlas, nebo nahrajte znovu."));
  if (m.has_audio) box.append(h("audio", {id: "maudio", controls: true, preload: "none", src: `/api/meetings/${m.id}/audio`}));
  box.append(h("div", {id: "mquote", class: "card quotebox", hidden: true}));
  if (m.status === "review") { const s = speakersBlock(); if (s) box.append(s); }

  if (["draft", "error"].includes(m.status) || (m.status === "recording" && MT.rec?.busy())) box.append(recorderBlock());
  if (m.status === "recording" && !MT.rec?.busy()) box.append(h("div", {class: "msg"},
    "Nahrávka je rozpracovaná (aplikace se zavřela před odesláním). Najdete ji na záložce Hlas mezi neodeslanými — ",
    "tam ji odešlete ke zpracování nebo zahoďte."));

  if (m.status === "approved" || MT.preview) {
    // hotový zápis (nebo náhled rozpracovaného) jako čitelný dokument v identitě JK
    const doc = h("div", {class: "mdoc"}, h("p", {class: "hint"}, "Načítám zápis…"));
    box.append(doc);
    api(`/api/meetings/${m.id}/doc`).then(r => { doc.innerHTML = r.html; })
      .catch(e => { doc.innerHTML = ""; doc.append(h("p", {class: "msg neg"}, "Zápis nejde zobrazit: " + e.message)); });
  } else {
    const form = h("div", {class: "mform"});
    m.template.fields.forEach(f => form.append(fieldEditor(f)));
    box.append(form);
  }

  if (m.status === "review" && m.transcript) {
    box.append(h("details", {class: "card"}, h("summary", {}, `Celý přepis (${m.transcript.length} úseků)`),
      h("div", {class: "mtrans"}, m.transcript.map(s => h("p", {}, h("button", {type: "button", class: "src", onclick: () => seek(s.t)}, mmss(s.t)),
        " ", h("b", {}, spk(s.speaker) + ": "), s.text)))));
  }
  const bar = h("div", {class: "bar mactions"});
  if (editable()) {
    bar.append(h("button", {class: "ghost", onclick: async e => {
      const b = e.currentTarget; b.disabled = true; b.textContent = "Ukládám…";
      const ok = await saveMeeting();
      b.disabled = false; b.textContent = "Uložit rozpracované";
      toast(ok ? "✓ Zápis je uložený. Můžete se k němu kdykoli vrátit v Schůzkách." : "Nepodařilo se uložit: " + (MT.saveErr || "zkuste to znovu"), !ok);
    }}, "Uložit rozpracované"));
    bar.append(h("button", {class: "ghost", onclick: async () => {
      if (!MT.preview) await saveMeeting();
      MT.preview = !MT.preview; renderMeeting(); window.scrollTo(0, 0);
    }}, MT.preview ? "Zpět k úpravám" : "Náhled zápisu"));
    if (m.status === "review" || m.status === "draft")
      bar.append(h("button", {class: "primary", onclick: approveMeeting},
        m.status === "draft" ? "Schválit bez nahrávky" : "Schválit a uložit trvale"));
    if (["draft", "error"].includes(m.status)) bar.append(h("button", {class: "ghost small", onclick: e => deleteMeeting(e.target)}, "Smazat zápis"));
  }
  if (m.status === "approved") {
    bar.append(h("a", {class: "primary btnlink", href: `/api/meetings/${m.id}/pdf`, target: "_blank"}, "Stáhnout PDF"));
    bar.append(h("span", {class: "hint"}, `Uloženo ${fmtDate(m.approved_at)}${m.auto_approved ? " automaticky (bez kontroly)" : ""} · ${m.card || ""}`));
  }
  box.append(bar);
}
async function approveMeeting(e) {
  const b = e.target;
  if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Opravdu schválit? Pak už nepůjde měnit — klikněte znovu";
    setTimeout(() => { if (b.isConnected) { delete b.dataset.armed; renderMeeting(); } }, 6000); return; }
  b.disabled = true; b.textContent = "Ukládám…";
  try {
    MT.cur = await api(`/api/meetings/${MT.cur.id}/approve`, {method: "POST", body: JSON.stringify(
      {values: MT.cur.values, speakers: MT.cur.speakers, suggestions: MT.cur.suggestions})});
    MT.dirty = false; openMeeting(MT.cur.id);
  } catch (err) { b.disabled = false; b.textContent = "Chyba: " + err.message; }
}
async function deleteMeeting(b) {
  if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Opravdu smazat? Klikněte znovu"; b.classList.add("danger"); return; }
  await api(`/api/meetings/${MT.cur.id}`, {method: "DELETE"}); loadMeetings();
}

// ---------- nahrávání schůzky (stejné ovládání jako u hlasu) ----------
function recorderBlock() {
  if (!MT.rec) MT.rec = makeMeetRecorder();
  MT.rec.mid = MT.cur.id;
  return MT.rec.el;
}
function makeMeetRecorder() {
  let rec = null, stream = null, session = null, n = 0, blobs = [], st = "idle", elapsed = 0, runStart = 0, tick = null, wake = null, mime = "";
  const time = h("div", {class: "rectime small"}, "00:00"), info = h("div", {class: "hint"}, "Nahrávání schůzky");
  const bStart = h("button", {type: "button", class: "rc rc-rec", html: "<span>●</span>Nahrát"});
  const bPause = h("button", {type: "button", class: "rc", hidden: true, html: "<span>❚❚</span>Pauza"});
  const bStop = h("button", {type: "button", class: "rc rc-stop", hidden: true, html: "<span>■</span>Stop"});
  const play = h("audio", {controls: true});
  const bSend = h("button", {type: "button", class: "primary"}, "Odeslat ke zpracování");
  const bDrop = h("button", {type: "button", class: "ghost"}, "Zahodit nahrávku");
  const review = h("div", {class: "review", hidden: true}, play, h("div", {class: "row"}, bSend, bDrop));
  const el = h("div", {class: "card rec"}, time, info, h("div", {class: "reccontrols"}, bStart, bPause, bStop), review,
    h("p", {class: "hint"}, "Telefon položte doprostřed stolu. Nahrávka se průběžně zálohuje; ke zpracování jde až po „Odeslat“."));
  const secs = () => elapsed + (st === "rec" ? (Date.now() - runStart) / 1000 : 0);
  const TXT = {idle: "Nahrávání schůzky", rec: "Nahrávám…", paused: "Pozastaveno", stopping: "Ukončuji…",
    review: "Hotovo — poslechněte si začátek a odešlete ke zpracování."};
  function set(s) {
    st = s; bStart.hidden = s !== "idle"; bPause.hidden = bStop.hidden = !(s === "rec" || s === "paused");
    bPause.innerHTML = s === "paused" ? "<span>▶</span>Pokračovat" : "<span>❚❚</span>Pauza";
    review.hidden = s !== "review"; time.classList.toggle("live", s === "rec"); info.textContent = TXT[s];
  }
  bStart.onclick = async () => {
    try {
      await saveMeeting();
      stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
      mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find(x => MediaRecorder.isTypeSupported(x)) || "";
      session = (await api("/api/voice/sessions", {method: "POST", headers: {"x-mime": mime, "x-meeting": R.mid}})).session;
      n = 0; blobs = []; elapsed = 0;
      rec = new MediaRecorder(stream, mime ? {mimeType: mime, audioBitsPerSecond: 32000} : {});
      const sid = session;
      rec.ondataavailable = async ev => { if (!ev.data.size) return; blobs.push(ev.data); const k = n++;
        await idb("readwrite", s => s.put({key: `${sid}-${String(k).padStart(5, "0")}`, kind: "chunk", session: sid, n: k, blob: ev.data})); uploader(); };
      rec.onstop = () => { stream.getTracks().forEach(t => t.stop()); clearInterval(tick); try { wake && wake.release(); } catch {}
        play.src = URL.createObjectURL(new Blob(blobs, {type: (rec.mimeType || mime || "audio/webm").split(";")[0]}));
        time.textContent = mmss(elapsed); set("review"); };
      rec.start(30000); runStart = Date.now(); set("rec"); armGuard(); askNotify();
      try { wake = await navigator.wakeLock.request("screen"); } catch {}
      tick = setInterval(() => time.textContent = mmss(secs()), 250);
      MT.cur.status = "recording";
    } catch (err) { info.textContent = "Nahrávání nelze spustit: " + err.message; }
  };
  bPause.onclick = () => {
    if (st === "rec") { try { rec.requestData(); } catch {} rec.pause(); elapsed += (Date.now() - runStart) / 1000; set("paused"); }
    else if (st === "paused") { rec.resume(); runStart = Date.now(); set("rec"); }
  };
  bStop.onclick = () => { if (st === "rec") elapsed += (Date.now() - runStart) / 1000; if (st === "rec" || st === "paused") { set("stopping"); rec.stop(); } };
  bSend.onclick = async () => {
    await saveMeeting();
    await idb("readwrite", s => s.put({key: `${session}-zfinish`, kind: "finish", session, chunks: n, title: ""}));
    uploader(); reset(); info.textContent = "Odesláno ke zpracování.";
    setTimeout(() => { if (!R.leaving) openMeeting(R.mid); R.leaving = false; }, 2500);
  };
  bDrop.onclick = async () => {
    if (!bDrop.dataset.armed) { bDrop.dataset.armed = "1"; bDrop.textContent = "Opravdu zahodit? Klikněte znovu"; bDrop.classList.add("danger"); return; }
    delete bDrop.dataset.armed; bDrop.textContent = "Zahodit nahrávku"; bDrop.classList.remove("danger");
    await discardSession(session); reset(); setTimeout(() => openMeeting(R.mid), 1500);
  };
  function reset() { recNotify(null); if (play.src) URL.revokeObjectURL(play.src); play.removeAttribute("src"); rec = null; session = null; blobs = []; elapsed = 0; time.textContent = "00:00"; set("idle"); }
  window.addEventListener("beforeunload", e => { if (["rec", "paused", "review"].includes(st)) { e.preventDefault(); e.returnValue = ""; } });
  const waitReview = () => new Promise(res => { const w = setInterval(() => { if (st === "review" || st === "idle") { clearInterval(w); res(); } }, 100); });
  const R = {el, mid: null, busy: () => ["rec", "paused", "stopping", "review"].includes(st),
    state: () => st, secs: () => secs(),
    // pro pojistku proti nechtěnému odchodu (app.js → recGuard)
    save: async () => { R.leaving = true; if (st === "rec" || st === "paused") { bStop.onclick(); await waitReview(); } if (st === "review") await bSend.onclick(); },
    discard: async () => { R.leaving = true; if (st === "rec" || st === "paused") { bStop.onclick(); await waitReview(); }
      if (session) { await discardSession(session); reset(); } R.leaving = false; }};
  return R;
}

// ---------- editor šablon ----------
async function openTemplates(selectId) {
  mview("mtpl");
  const tp = await api("/api/meeting-templates");
  MT.types = tp.types; MT.templates = tp.templates;
  const box = $("#mtplbox"); box.innerHTML = "";
  let tpl = JSON.parse(JSON.stringify(MT.templates.find(t => t.id === selectId) || MT.templates[0]));
  const sel = h("select", {onchange: () => openTemplates(sel.value)},
    MT.templates.map(t => h("option", {value: t.id, selected: t.id === tpl.id}, t.name)));
  box.append(h("div", {class: "mhead"}, h("button", {class: "ghost small", onclick: loadMeetings}, "← Zápisy"), sel,
    h("button", {class: "ghost small", onclick: () => { tpl = {id: "", name: "Nová šablona", fields: [{label: "Shrnutí", type: "text", ai: ""}]}; draw(); }}, "+ Nová šablona")));
  const body = h("div"); box.append(body);
  function draw() {
    body.innerHTML = "";
    const name = h("input", {value: tpl.name}); name.oninput = () => tpl.name = name.value;
    body.append(h("label", {}, "Název šablony", name),
      h("p", {class: "hint"}, "Pole se ve formuláři zobrazí v tomto pořadí. „Pokyn pro AI“ říká, co má AI do pole z nahrávky vyplnit."));
    tpl.fields.forEach((f, i) => {
      const lab = h("input", {value: f.label, placeholder: "Název pole"}); lab.oninput = () => f.label = lab.value;
      const typ = h("select", {onchange: () => f.type = typ.value},
        Object.entries(MT.types).map(([k, v]) => h("option", {value: k, selected: k === f.type}, v)));
      const ai = h("textarea", {rows: 2, placeholder: "Pokyn pro AI (nepovinné)"}); ai.value = f.ai || ""; ai.oninput = () => f.ai = ai.value;
      const mv = d => () => { const j = i + d; if (j < 0 || j >= tpl.fields.length) return; [tpl.fields[i], tpl.fields[j]] = [tpl.fields[j], tpl.fields[i]]; draw(); };
      body.append(h("div", {class: "card tfield"}, h("div", {class: "row"}, lab, typ), ai,
        h("div", {class: "bar"}, h("button", {class: "ghost small", onclick: mv(-1)}, "↑"), h("button", {class: "ghost small", onclick: mv(1)}, "↓"),
          h("button", {class: "ghost small", onclick: () => { tpl.fields.splice(i, 1); draw(); }}, "Odebrat pole"))));
    });
    const msg = h("p", {class: "hint"});
    body.append(h("button", {class: "ghost", onclick: () => { tpl.fields.push({label: "", type: "text", ai: ""}); draw(); }}, "+ Přidat pole"),
      h("div", {class: "bar mactions"},
        h("button", {class: "primary", onclick: async () => {
          try {
            const saved = tpl.id ? await api(`/api/meeting-templates/${tpl.id}`, {method: "PUT", body: JSON.stringify(tpl)})
                                 : await api("/api/meeting-templates", {method: "POST", body: JSON.stringify(tpl)});
            msg.textContent = "Uloženo. Nové zápisy použijí tuto podobu šablony (rozpracované zápisy se nemění).";
            setTimeout(() => openTemplates(saved.id), 900);
          } catch (e) { msg.textContent = "Chyba: " + e.message; } }}, "Uložit šablonu"),
        tpl.id ? h("button", {class: "ghost small", onclick: async e => {
          const b = e.target; if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Opravdu smazat šablonu?"; return; }
          try { await api(`/api/meeting-templates/${tpl.id}`, {method: "DELETE"}); openTemplates(); } catch (err) { msg.textContent = err.message; } }}, "Smazat šablonu") : null),
      msg);
  }
  draw();
}

// Aplikace otevřená rovnou na #schuzky: app.js volá show() dřív, než se tento soubor načte
{ const [tab, sub] = (window.INIT_HASH || location.hash).slice(1).split("/");
  if (tab === "schuzky") { if (sub) openMeeting(sub, false); else loadMeetings(); } }
