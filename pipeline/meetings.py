"""Zápisy ze schůzek: šablona → rozpracovaný zápis → nahrávka → AI vyplní → předpokoj (kontrola) → schváleno.

Soubory:
  99_System/templates/meetings/<id>.yaml    šablony zápisu (v Gitu, upravují se v aplikaci)
  99_System/meetings/<id>.json              stav zápisu: draft | recording | processing | review | approved | error
  00_Assets/meetings/<id>.opus              nahrávka schůzky
  00_Assets/transcripts/meeting__<id>.json  přepis (source_id meeting:<id>) — doklad ke každé položce zápisu
  14_Internal/Schuzky/YYYY-MM-DD - Název.md schválený zápis (Obsidian, vyhledávání)

Pravidla:
  - co uživatel napsal sám, AI nepřepisuje (u textových polí nabídne jen návrh, u seznamů doplní chybějící položky),
  - každá položka od AI nese `segs` = čísla segmentů přepisu (dohledatelnost do nahrávky),
  - neodsouhlasený zápis se po REVIEW_DAYS dnech uloží tak, jak je (auto_approved: true).
"""
from __future__ import annotations

import html as _html
import json
import re
import secrets
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from pipeline import ids, settings
from pipeline.config import Vault

REVIEW_DAYS = 14
STATUSES = ("draft", "recording", "processing", "review", "approved", "error")
FIELD_TYPES = {
    "short": "krátký text",
    "text": "delší text",
    "datetime": "datum a čas",
    "people": "osoby",
    "list": "seznam bodů",
    "topics": "projednané body (bod + popis)",
    "tasks": "úkoly (co / kdo / do kdy)",
}
TEXT_TYPES = ("short", "text", "datetime")
MID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9]{4}$")
TID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

# Dvě pevně dané šablony (v aplikaci se neupravují; mění se tady v kódu).
_F = lambda i, label, t, ai="": {"id": i, "label": label, "type": t, "ai": ai}  # noqa: E731
TEMPLATE_SCHUZKA = {
    "id": "schuzka",
    "name": "Zápis ze schůzky",
    "fields": [
        _F("nazev", "Název / téma schůzky", "short", "Krátký věcný název podle hlavního tématu, pokud ho uživatel nezadal."),
        _F("firma", "Firma / klient", "short", "Firma (klient, partner), se kterou se jednání vede — jen pokud zazní nebo je zřejmá."),
        _F("datum", "Datum a čas", "datetime"),
        _F("misto", "Místo / forma", "short", "Jen pokud zazní (např. osobně u klienta, online Teams, zasedačka)."),
        _F("ucastnici", "Účastníci", "people",
           "Každý účastník zvlášť ve tvaru „Jméno Příjmení, firma“. Firmu uveď, jen pokud zazní nebo je zřejmá z kontextu."),
        _F("cil", "Program / cíl schůzky", "text",
           "Stručně, co se má projednat. Obvykle vyplní uživatel před schůzkou — pokud je prázdné, doplň podle úvodu schůzky."),
        _F("shrnuti", "Shrnutí", "text", "Krátké shrnutí celého jednání (3–6 vět). Na konci jasně uveď hlavní výsledek schůzky."),
        _F("body", "Projednané body", "topics",
           "Jednotlivá témata. Pod každým stručný věcný zápis: fakta, názory a informace, které na schůzce skutečně "
           "zazněly (kdo co uvedl). Nic nedomýšlej."),
        _F("rozhodnuti", "Rozhodnutí a závěry", "list",
           "Co bylo dohodnuto, co schváleno, co odloženo; případně kdo s čím souhlasil. Každou položku začni slovem "
           "Dohodnuto / Schváleno / Odloženo. Ne návrhy a nápady."),
        _F("ukoly", "Úkoly a další kroky", "tasks",
           "Každý úkol, který na schůzce zazněl: kdo má co udělat, zaslat, doplnit, upřesnit, definovat nebo dodat "
           "(dokumenty, data, nabídky). Co = konkrétně co, kdo = jméno, do kdy = termín, pokud zazněl "
           "(i přibližný, např. do pátku); jinak nech prázdné. Nic nevymýšlej."),
        _F("otevrene", "Otevřené otázky", "list",
           "Otázky, které zůstaly nevyřešené; informace, které je potřeba doplnit; témata odložená na další jednání."),
        _F("dalsi", "Další jednání", "text", "Jen pokud bylo domluveno: termín a účel (co se bude řešit). Jinak nech prázdné."),
    ],
}
TEMPLATE_PORADA = {
    "id": "porada",
    "name": "Zápis z porady",
    "fields": [
        _F("nazev", "Název porady", "short", "Např. „Týdenní porada vedení“ — podle toho, jak porada zazní; jinak krátce podle hlavního tématu."),
        _F("datum", "Datum a čas", "datetime"),
        _F("misto", "Místo / forma", "short", "Jen pokud zazní (např. zasedačka, online)."),
        _F("ucastnici", "Přítomní", "people", "Každý přítomný zvlášť ve tvaru „Jméno Příjmení, role/útvar“ (roli jen pokud zazní)."),
        _F("omluveni", "Omluvení / nepřítomní", "people", "Jen lidé, o kterých zazní, že chybí nebo se omluvili."),
        _F("program", "Program", "list", "Body programu. Obvykle je vyplní uživatel předem — jinak podle toho, co se probíralo."),
        _F("kontrola", "Kontrola úkolů z minulé porady", "list",
           "Úkoly z minula, o kterých se mluvilo, se stavem: „úkol — kdo — splněno / trvá / posunuto na …“."),
        _F("shrnuti", "Shrnutí", "text", "3–5 vět: co bylo na poradě hlavní a k čemu se došlo."),
        _F("body", "Projednané body", "topics",
           "Jednotlivá témata. Pod každým stručný věcný zápis: fakta, stav, názory a informace, které skutečně zazněly "
           "(kdo co uvedl). Nic nedomýšlej."),
        _F("rozhodnuti", "Rozhodnutí", "list",
           "Co bylo rozhodnuto, schváleno nebo odloženo. Každou položku začni slovem Rozhodnuto / Schváleno / Odloženo."),
        _F("ukoly", "Úkoly", "tasks",
           "Každý úkol, který zazněl: kdo má co udělat, zaslat, doplnit, upřesnit nebo připravit. Co = konkrétně co, "
           "kdo = jméno, do kdy = termín, pokud zazněl (i přibližný); jinak prázdné. Nic nevymýšlej."),
        _F("otevrene", "Otevřené body", "list", "Co zůstalo nevyřešené nebo se přesouvá na příští poradu."),
        _F("dalsi", "Příští porada", "text", "Termín a hlavní témata příští porady, jen pokud zazněly."),
    ],
}
TEMPLATES = [TEMPLATE_SCHUZKA, TEMPLATE_PORADA]
DEFAULT_TEMPLATE = TEMPLATE_SCHUZKA


# ---------------------------------------------------------------- šablony
def templates_dir(vault: Vault) -> Path:
    return vault.system / "templates" / "meetings"


def meetings_dir(vault: Vault) -> Path:
    return vault.system / "meetings"


def _slug(text: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:40] or "pole"


def list_templates(vault: Vault) -> list[dict]:
    return [json.loads(json.dumps(t)) for t in TEMPLATES]


def get_template(vault: Vault, tid: str) -> dict:
    for t in TEMPLATES:
        if t["id"] == tid:
            return json.loads(json.dumps(t))
    raise FileNotFoundError(tid)


def validate_template(tpl: dict) -> dict:
    name = str(tpl.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("Šablona musí mít název.")
    tid = str(tpl.get("id") or "").strip() or _slug(name)
    if not TID_RE.match(tid):
        raise ValueError("neplatné ID šablony")
    fields, seen = [], set()
    for f in tpl.get("fields") or []:
        label = str(f.get("label") or "").strip()[:80]
        if not label:
            continue
        ftype = f.get("type") if f.get("type") in FIELD_TYPES else "text"
        fid = str(f.get("id") or "").strip() or _slug(label)
        fid = _slug(fid)
        base, n = fid, 2
        while fid in seen:
            fid, n = f"{base}_{n}", n + 1
        seen.add(fid)
        fields.append({"id": fid, "label": label, "type": ftype, "ai": str(f.get("ai") or "").strip()[:400]})
    if not fields:
        raise ValueError("Šablona musí mít aspoň jedno pole.")
    return {"id": tid, "name": name, "fields": fields}


def save_template(vault: Vault, tpl: dict) -> dict:
    raise ValueError("Šablony jsou pevně dané (Zápis ze schůzky, Zápis z porady); mění se v kódu aplikace.")


def delete_template(vault: Vault, tid: str) -> None:
    raise ValueError("Šablony jsou pevně dané (Zápis ze schůzky, Zápis z porady); mění se v kódu aplikace.")


# ---------------------------------------------------------------- zápisy
def empty_value(ftype: str):
    return {"text": "", "segs": [], "ai": False} if ftype in TEXT_TYPES else []


def is_empty(v) -> bool:
    if isinstance(v, dict):
        return not str(v.get("text") or "").strip()
    return not v


def _path(vault: Vault, mid: str) -> Path:
    if not MID_RE.match(mid or ""):
        raise ValueError("neplatné ID zápisu")
    return meetings_dir(vault) / f"{mid}.json"


def load(vault: Vault, mid: str) -> dict:
    p = _path(vault, mid)
    if not p.exists():
        raise FileNotFoundError(mid)
    return json.loads(p.read_text(encoding="utf-8"))


def save(vault: Vault, m: dict) -> dict:
    m["updated_at"] = datetime.now().isoformat(timespec="seconds")
    p = _path(vault, m["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    return m


def title_of(m: dict) -> str:
    v = m.get("values", {}).get("nazev")
    t = (v.get("text") if isinstance(v, dict) else "") or ""
    return t.strip() or f"Schůzka {m['created'][:10]}"


def new_meeting(vault: Vault, template_id: str | None = None) -> dict:
    tpl = get_template(vault, template_id or DEFAULT_TEMPLATE["id"])
    now = datetime.now()
    mid = now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    values = {f["id"]: empty_value(f["type"]) for f in tpl["fields"]}
    for f in tpl["fields"]:
        if f["type"] == "datetime":
            values[f["id"]] = {"text": f"{now.day}. {now.month}. {now.year} {now:%H:%M}", "segs": [], "ai": False}
            break
    m = {"id": mid, "template": tpl, "status": "draft", "created": now.isoformat(timespec="seconds"),
         "values": values, "suggestions": {}, "speakers": {}, "session": None, "source_id": None,
         "error": None, "review_until": None, "approved_at": None, "auto_approved": False, "card": None}
    return save(vault, m)


def list_meetings(vault: Vault) -> list[dict]:
    out = []
    d = meetings_dir(vault)
    for p in sorted(d.glob("*.json"), reverse=True) if d.exists() else []:
        m = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": m["id"], "title": title_of(m), "status": m["status"], "created": m["created"],
                    "template": m["template"]["name"], "review_until": m.get("review_until"),
                    "approved_at": m.get("approved_at"), "auto_approved": m.get("auto_approved"),
                    "card": m.get("card"), "error": m.get("error"),
                    "people": _people(m), "search": _search_text(m)})
    return out


def _search_text(m: dict) -> str:
    """Všechen text zápisu (název, účastníci, firmy, body, úkoly…) pro hledání v seznamu schůzek."""
    out = [m["template"]["name"]]
    for v in (m.get("values") or {}).values():
        if isinstance(v, dict):
            out.append(v.get("text") or "")
        elif isinstance(v, list):
            out += [" ".join(str(it.get(k) or "") for k in ("text", "bod", "co", "kdo", "termin")) for it in v]
    return " ".join(x for x in out if x)[:20000]


def _clean_value(ftype: str, v):
    if ftype in TEXT_TYPES:
        v = v if isinstance(v, dict) else {"text": str(v or "")}
        return {"text": str(v.get("text") or "")[:20000], "segs": [int(x) for x in v.get("segs") or []][:50],
                "ai": bool(v.get("ai"))}
    keys = {"people": ("text",), "list": ("text",), "topics": ("bod", "text"),
            "tasks": ("co", "kdo", "termin")}[ftype]
    items = []
    for it in (v or [])[:200]:
        if isinstance(it, str):
            it = {keys[0]: it}
        row = {k: str(it.get(k) or "")[:5000] for k in keys}
        if not any(row.values()):
            continue
        row["segs"] = [int(x) for x in it.get("segs") or []][:50]
        row["ai"] = bool(it.get("ai"))
        if ftype == "tasks":
            row["hotovo"] = bool(it.get("hotovo"))
        items.append(row)
    return items


def update(vault: Vault, mid: str, values: dict | None = None, speakers: dict | None = None,
           suggestions: dict | None = None, base_status: str | None = None) -> dict:
    m = load(vault, mid)
    if m["status"] == "approved":
        raise ValueError("Schválený zápis už nelze měnit.")
    types = {f["id"]: f["type"] for f in m["template"]["fields"]}
    # Uživatel psal během zpracování a AI mezitím zápis vyplnila → nic z AI nezahodit.
    stale = base_status in ("draft", "recording", "processing", "error") and m["status"] == "review"
    if values is not None:
        for fid, v in values.items():
            if fid not in types:
                continue
            new = _clean_value(types[fid], v)
            cur = m["values"].get(fid)
            if stale and types[fid] in TEXT_TYPES:
                if cur and cur.get("ai") and cur["text"].strip() and cur["text"].strip() != new["text"].strip():
                    m.setdefault("suggestions", {}).setdefault(fid, cur)
            elif stale and cur:
                key = "co" if types[fid] == "tasks" else ("bod" if types[fid] == "topics" else "text")
                have = {str(it.get(key, "")).strip().lower() for it in new}
                new += [it for it in cur if it.get("ai") and str(it.get(key, "")).strip().lower() not in have]
            m["values"][fid] = new
    if speakers is not None:
        sp = {str(k)[:60]: str(v or "").strip()[:80] for k, v in speakers.items()}
        m["speakers"] = {**(m.get("speakers") or {}), **{k: v for k, v in sp.items() if v}} if stale else sp
    if suggestions is not None and not stale:
        m["suggestions"] = {k: v for k, v in suggestions.items() if k in types}
    return save(vault, m)


def delete_meeting(vault: Vault, mid: str) -> None:
    m = load(vault, mid)
    if m["status"] == "approved":
        raise ValueError("Schválený zápis se maže přes Odstranit zdroj (celé, včetně přepisu).")
    _path(vault, mid).unlink()


def attach_session(vault: Vault, mid: str, session: str) -> dict:
    m = load(vault, mid)
    if m["status"] not in ("draft", "error", "recording"):
        raise ValueError("Ke zpracovanému zápisu už nelze nahrávat.")
    m.update(session=session, status="recording", error=None)
    return save(vault, m)


def mark_processing(vault: Vault, mid: str) -> None:
    try:
        m = load(vault, mid)
    except (FileNotFoundError, ValueError):
        return
    if m["status"] in ("draft", "recording", "error"):
        m.update(status="processing", error=None)
        save(vault, m)


# ---------------------------------------------------------------- zpracování nahrávky
def _people(m: dict) -> list[str]:
    out = []
    for f in m["template"]["fields"]:
        if f["type"] == "people":
            out += [it["text"] for it in m["values"].get(f["id"]) or [] if it.get("text")]
    return out


def process(vault: Vault, mid: str, session: str, stt_provider=None, fill_provider=None) -> dict:
    """Nahrávka → přepis → AI vyplní šablonu → předpokoj (review). Volá worker."""
    from pipeline import transcribe, voice
    m = load(vault, mid)
    m.update(status="processing", error=None)
    save(vault, m)
    sid, path, _ = voice.assemble(vault, session, folder="meetings", name=mid, namespace="meeting")
    people = _people(m)
    info = {"title": title_of(m), "channel": "Schůzka", "upload_date": m["created"][:10].replace("-", ""),
            "kind": "meeting",
            "description": "Nahrávka pracovní schůzky." + (f" Účastníci: {', '.join(people)}." if people else "")}
    t = transcribe.transcribe_file(vault, sid, path, info, stt_provider)
    m = load(vault, mid)
    m["source_id"] = sid
    save(vault, m)
    fp = fill_provider or _default_fill_provider(vault)
    print(f"  vyplňuji šablonu zápisu modelem {getattr(fp, 'engine', '?')} …")
    out = ask_fill(m, t, fp)
    print("  šablona vyplněna → zápis čeká na kontrolu")
    m = load(vault, mid)  # čerstvá verze: co uživatel mezitím upravil, zůstane
    m["source_id"] = sid
    apply_fill(m, t, out)
    m.update(status="review", session=None, processed_at=datetime.now().isoformat(timespec="seconds"),
             review_until=(datetime.now() + timedelta(days=REVIEW_DAYS)).isoformat(timespec="seconds"))
    save(vault, m)
    shutil.rmtree(voice.inbox(vault) / session, ignore_errors=True)
    return m


def _default_fill_provider(vault: Vault):
    from pipeline import analyze
    return analyze.default_provider(vault)


def _item_schema(ftype: str) -> dict:
    segs = {"type": "array", "items": {"type": "integer"}}
    if ftype in TEXT_TYPES:
        return {"type": "object", "properties": {"text": {"type": "string"}, "segs": segs}, "required": ["text"]}
    props = {"people": ["text"], "list": ["text"], "topics": ["bod", "text"], "tasks": ["co", "kdo", "termin"]}[ftype]
    return {"type": "array", "items": {"type": "object",
                                       "properties": {**{k: {"type": "string"} for k in props}, "segs": segs},
                                       "required": props[:1]}}


def fill_schema(tpl: dict) -> dict:
    return {"type": "object", "properties": {
        "speakers": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "name": {"type": "string"}}, "required": ["label", "name"]}},
        "fields": {"type": "object", "properties": {f["id"]: _item_schema(f["type"]) for f in tpl["fields"]}},
    }, "required": ["fields"]}


FILL_PROMPT = """Jsi pečlivý zapisovatel. Z přepisu schůzky vyplň šablonu zápisu. Piš česky, věcně a stručně.

Pravidla:
- Vycházej POUZE z přepisu a z údajů, které zadal uživatel. Nic si nedomýšlej; co v přepisu není, nech prázdné.
- U každé položky uveď "segs" = čísla segmentů přepisu (číslo v hranaté závorce), ze kterých vychází.
- Údaje zadané uživatelem jsou správné — respektuj je a doplň jen to, co chybí.
- Úkoly: "co" = konkrétní činnost; "kdo" = jméno odpovědné osoby (ne „Mluvčí 2“, pokud jméno z přepisu plyne);
  "termin" = termín tak, jak zazněl (např. „do pátku“, „15. 10.“), jinak prázdný řetězec.
- V textech zápisu piš jména lidí, ne „Mluvčí 1“, pokud je jméno z přepisu nebo ze zadaných účastníků zřejmé.
- Účastníky zapisuj ve tvaru „Jméno Příjmení, firma“ (firmu jen pokud zazní nebo je zřejmá).
- Mluvčí: přiřaď označením mluvčích z přepisu skutečná jména, jen pokud to z přepisu plyne (oslovení,
  představení, kontext) nebo jde jednoznačně o zadaného účastníka. Jinak name = "".

Schůzka: {title}
Zadaní účastníci: {people}

Údaje, které už zadal uživatel:
{user_values}

Pole šablony (id — typ: popis. pokyn):
{fields}

Přepis (formát: [číslo segmentu] čas mluvčí: text):
{transcript}
"""


def _fmt_t(sec: float) -> str:
    sec = int(sec or 0)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def ask_fill(m: dict, t: dict, provider) -> dict:
    """Zeptá se AI (bere v úvahu, co už uživatel vyplnil); vrací surový výstup."""
    tpl = m["template"]
    user = {f["id"]: m["values"].get(f["id"]) for f in tpl["fields"] if not is_empty(m["values"].get(f["id"]))}
    user_txt = json.dumps({k: (v["text"] if isinstance(v, dict) else [{kk: vv for kk, vv in it.items()
                                                                        if kk not in ("segs", "ai", "hotovo")}
                                                                       for it in v])
                           for k, v in user.items()}, ensure_ascii=False, indent=1) if user else "(nic)"
    fields_txt = "\n".join(f"- {f['id']} — {FIELD_TYPES[f['type']]}: {f['label']}. {f.get('ai') or ''}".rstrip()
                           for f in tpl["fields"])
    lines = "\n".join(f"[{s['i']}] {_fmt_t(s['start'])} {t['speakers'].get(s['speaker'], s['speaker'])}: {s['text']}"
                      for s in t["segments"])
    prompt = FILL_PROMPT.format(title=title_of(m), people="; ".join(_people(m)) or "(nezadáni)",
                                user_values=user_txt, fields=fields_txt, transcript=lines)
    return provider.generate(prompt, fill_schema(tpl))


def apply_fill(m: dict, t: dict, out: dict) -> dict:
    merge_ai(m, out, {s["i"] for s in t["segments"]})
    # mluvčí: klíč = ID v přepisu (S0, S1…), speaker_labels = původní označení („Mluvčí 1“ / jméno z přepisu)
    labels = {sp: t["speakers"].get(sp, sp) for sp in sorted({s["speaker"] for s in t["segments"]})}
    guess = {d.get("label"): (d.get("name") or "").strip() for d in out.get("speakers") or []}
    old = m.get("speakers") or {}
    m["speaker_labels"] = labels
    m["speakers"] = {sp: old.get(sp) or guess.get(lab) or guess.get(sp) or "" for sp, lab in labels.items()}
    return m



def fill(m: dict, t: dict, provider) -> dict:
    return apply_fill(m, t, ask_fill(m, t, provider))

def merge_ai(m: dict, out: dict, valid_segs: set[int]) -> None:
    """AI výstup → hodnoty zápisu. Uživatelův text se nepřepisuje; u seznamů se doplní nové položky."""
    ai = out.get("fields") or {}
    m.setdefault("suggestions", {})
    for f in m["template"]["fields"]:
        fid, ftype = f["id"], f["type"]
        if fid not in ai:
            continue
        v = _clean_value(ftype, ai[fid])
        if ftype in TEXT_TYPES:
            v["segs"] = [x for x in v["segs"] if x in valid_segs]
            v["ai"] = True
            if not v["text"].strip():
                continue
            if is_empty(m["values"].get(fid)):
                m["values"][fid] = v
            elif v["text"].strip() != m["values"][fid]["text"].strip():
                m["suggestions"][fid] = v
            continue
        cur = m["values"].get(fid) or []
        key = "co" if ftype == "tasks" else ("bod" if ftype == "topics" else "text")
        have = {str(it.get(key, "")).strip().lower() for it in cur}
        for it in v:
            it["segs"] = [x for x in it["segs"] if x in valid_segs]
            it["ai"] = True
            if str(it.get(key, "")).strip().lower() not in have:
                cur.append(it)
        m["values"][fid] = cur


# ---------------------------------------------------------------- schválení
def _safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\n\r\t]+', " ", text).strip()[:90] or "Schůzka"


def approve(vault: Vault, mid: str, auto: bool = False) -> dict:
    m = load(vault, mid)
    if m["status"] == "approved":
        return m
    if m["status"] in ("recording", "processing"):
        raise ValueError("Nahrávka se ještě zpracovává.")
    if m.get("source_id"):
        _apply_speakers(vault, m)
    rename_speakers_in_values(m)
    m.update(status="approved", approved_at=datetime.now().isoformat(timespec="seconds"), auto_approved=auto,
             review_until=None)
    path = render_markdown(vault, m)
    m["card"] = vault.rel(path)
    save(vault, m)
    try:
        from pipeline import index
        index.build(vault)
    except Exception as e:  # noqa: BLE001
        print(f"  index: {e}")
    return m


def _apply_speakers(vault: Vault, m: dict) -> None:
    from pipeline import transcript
    p = vault.transcripts / (ids.safe_filename(m["source_id"]) + ".json")
    if not p.exists():
        return
    t = json.loads(p.read_text(encoding="utf-8"))
    for lab, name in (m.get("speakers") or {}).items():
        if name:
            t["speakers"][lab] = name
    p.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        md = p.with_suffix(".md")
        md.write_text(transcript.render_markdown(t), encoding="utf-8")
    except Exception:  # noqa: BLE001 — Markdown přepisu je jen pohled
        pass


def rename_speakers_in_values(m: dict) -> int:
    """V textech zápisu nahradí označení „Mluvčí 1“ apod. jménem, které uživatel mluvčímu přiřadil."""
    pairs = [(lab, (m.get("speakers") or {}).get(sp, "").strip())
             for sp, lab in (m.get("speaker_labels") or {}).items()]
    pairs = [(lab, name) for lab, name in pairs if lab and name and lab != name]
    if not pairs:
        return 0
    pats = [(re.compile(re.escape(lab) + r"(?!\d)"), name) for lab, name in pairs]
    n = 0

    def fix(text: str) -> str:
        nonlocal n
        for rx, name in pats:
            text, k = rx.subn(name, text)
            n += k
        return text

    for bucket in (m.get("values") or {}, m.get("suggestions") or {}):
        for fid, v in bucket.items():
            if isinstance(v, dict) and isinstance(v.get("text"), str):
                v["text"] = fix(v["text"])
            elif isinstance(v, list):
                for it in v:
                    for k in ("text", "bod", "co", "kdo"):
                        if isinstance(it.get(k), str):
                            it[k] = fix(it[k])
    return n


def auto_approve_due(vault: Vault) -> list[str]:
    now = datetime.now().isoformat(timespec="seconds")
    done = []
    d = meetings_dir(vault)
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        m = json.loads(p.read_text(encoding="utf-8"))
        if m["status"] == "review" and m.get("review_until") and m["review_until"] <= now:
            approve(vault, m["id"], auto=True)
            done.append(m["id"])
    return done


def _speaker_name(m: dict, label: str) -> str:
    return (m.get("speakers") or {}).get(label) or label


def _item_text(ftype: str, it: dict) -> str:
    if ftype == "tasks":
        return " — ".join(x for x in (it.get("co"), it.get("kdo"), it.get("termin") and f"do: {it['termin']}") if x)
    if ftype == "topics":
        return f"**{it.get('bod', '')}** — {it.get('text', '')}".strip(" —")
    return it.get("text", "")


def render_markdown(vault: Vault, m: dict) -> Path:
    tpl = m["template"]
    title = title_of(m)
    date = m["created"][:10]
    people = _people(m)
    fm = {"source_id": m.get("source_id") or f"meeting:{m['id']}", "typ": "zapis_schuzky", "title": title,
          "published_at": date, "template": tpl["name"], "ucastnici": people,
          "status": "schváleno automaticky (bez kontroly)" if m.get("auto_approved") else "schváleno",
          "approved_at": m.get("approved_at"), "source_type": "meeting", "visibility": "private",
          "knowledge_domain": "personal", "origin": "personal", "meeting_id": m["id"]}
    out = [f"# {title}", ""]
    if m.get("auto_approved"):
        out += ["> [!warning] Zápis nebyl zkontrolován — uložen automaticky po 14 dnech tak, jak ho vyplnila AI.", ""]
    for f in tpl["fields"]:
        if f["id"] == "nazev":
            continue
        v = m["values"].get(f["id"])
        if is_empty(v):
            continue
        out.append(f"## {f['label']}")
        if f["type"] in TEXT_TYPES:
            out.append(v["text"].strip())
        elif f["type"] == "tasks":
            out += [f"- [{'x' if it.get('hotovo') else ' '}] {_item_text('tasks', it)}" for it in v]
        else:
            out += [f"- {_item_text(f['type'], it)}" for it in v]
        out.append("")
    if m.get("source_id"):
        out += ["---", f"Přepis nahrávky: [[{ids.safe_filename(m['source_id'])}]]"]
    body = "\n".join(out).rstrip() + "\n"
    folder = vault.internal / "Schuzky"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{date} - {_safe_name(title)}.md"
    if path.exists() and m["id"] not in path.read_text(encoding="utf-8")[:2000]:
        path = folder / f"{date} - {_safe_name(title)} ({m['id'][-4:]}).md"
    path.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=1000) + "---\n" + body,
                    encoding="utf-8")
    return path


# ---------------------------------------------------------------- dokument (web + PDF)
STATIC = Path(__file__).parent / "web" / "static"
PDF_FONTS = "".join(
    f'@font-face{{font-family:"{fam}";font-weight:{w};src:url("fonts/{file}-{w}.ttf")}}'
    for fam, file, ws in (("Inter", "Inter", (400, 600, 700)), ("Space Grotesk", "SpaceGrotesk", (600, 700))) for w in ws)
PDF_PAGE = """
@page { size: A4; margin: 16mm 16mm 18mm 16mm;
  @bottom-left { content: "__BRAND_FOOTER__"; font: 7.5pt "Inter"; color: #475569; letter-spacing: .04em; }
  @bottom-right { content: counter(page) " / " counter(pages); font: 7.5pt "Inter"; color: #475569; } }
html, body { margin: 0; background: #fff; }
.jkdoc { box-shadow: none; border-radius: 0; padding: 6mm 0 0; font-size: 10pt; }
.jkdoc h1 { font-size: 21pt; } .jd-goal { font-size: 11.5pt; } .jd-sec > h2 { font-size: 13pt; }
.jd-lead p { font-size: 10.5pt; } .jd-tasks { font-size: 9.5pt; } .jd-topics h3 { font-size: 11pt; }
.jd-meta { display: flex; flex-wrap: wrap; } .jd-meta div { width: 33%; padding-right: 4mm; margin-bottom: 3mm; }
.jd-meta .jd-people { width: 100%; }
"""


def _fmt_dt(a: str | None) -> str:
    a = a or ""
    return f"{int(a[8:10])}. {int(a[5:7])}. {a[:4]} {a[11:16]}".strip() if len(a) >= 10 else a


def doc_html(m: dict, ai_marks: bool = True) -> str:
    """Zápis jako čitelný dokument (HTML fragment <article class="jkdoc">). ai_marks: označit, co napsala AI."""
    e = _html.escape
    tpl = m["template"]
    fields = tpl["fields"]

    def tag(v) -> str:
        return '<span class="jd-ai" title="Doplnila AI z nahrávky">AI</span>' if ai_marks and v.get("ai") else ""

    def cls(v) -> str:
        return ' class="ai"' if ai_marks and v.get("ai") else ""

    def paras(v) -> str:
        ps = [e(t) for t in v["text"].split("\n") if t.strip()]
        if ps:
            ps[-1] += tag(v)  # štítek AI na konec textu
        return "".join(f"<p{cls(v)}>{t}</p>" for t in ps)

    head_ids = {f["id"] for f in fields if f["type"] in ("short", "datetime", "people")}
    goal = m["values"].get("cil")
    meta = []
    for f in fields:
        v = m["values"].get(f["id"])
        if f["id"] not in head_ids or f["id"] == "nazev" or is_empty(v):
            continue
        if f["type"] == "people":
            val = ", ".join(e(it["text"]) + tag(it) for it in v if it.get("text"))
            meta.append(f'<div class="jd-people"><dt>{e(f["label"])}</dt><dd>{val}</dd></div>')
        else:
            meta.append(f'<div><dt>{e(f["label"])}</dt><dd{cls(v)}>{e(v["text"])}{tag(v)}</dd></div>')

    rest = [f for f in fields if f["id"] not in head_ids and f["id"] != "cil" and not is_empty(m["values"].get(f["id"]))]
    secs = []
    for n, f in enumerate(rest, 1):
        v, t = m["values"][f["id"]], f["type"]
        h2 = f'<h2><span class="jd-num">{n:02d}</span>{e(f["label"])}</h2>'
        if t in TEXT_TYPES:
            body = paras(v)
            secs.append(f'<section class="jd-sec{" jd-lead" if f["id"] == "shrnuti" else ""}">{h2}{body}</section>')
        elif t == "tasks":
            rows = "".join(
                f'<tr class="{"done " if it.get("hotovo") else ""}{"ai" if ai_marks and it.get("ai") else ""}">'
                f'<td>{e(it.get("co", ""))}{tag(it)}</td><td class="who">{e(it.get("kdo", ""))}</td>'
                f'<td class="when">{e(it.get("termin", ""))}</td></tr>' for it in v)
            doc_req = "podklad" in f["id"] + f["label"].lower()
            first, who = ("Podklad", "Kdo dodá") if doc_req else ("Úkol", "Kdo")
            secs.append(f'<section class="jd-sec{"" if doc_req else " jd-todo"}">{h2}<table class="jd-tasks"><thead><tr><th>{first}</th><th>{who}</th>'
                        f'<th>Do kdy</th></tr></thead><tbody>{rows}</tbody></table></section>')
        elif t == "topics":
            items = "".join(f'<li{cls(it)}><h3>{e(it.get("bod", ""))}{tag(it)}</h3><p>{e(it.get("text", ""))}</p></li>'
                            for it in v)
            secs.append(f'<section class="jd-sec">{h2}<ol class="jd-topics">{items}</ol></section>')
        else:
            kind = "jd-check" if f["id"] == "rozhodnuti" else ("jd-steps" if f["id"] == "program" else "")
            items = "".join(f'<li{cls(it)}>{e(it.get("text", ""))}{tag(it)}</li>' for it in v)
            secs.append(f'<section class="jd-sec">{h2}<ul class="jd-list {kind}">{items}</ul></section>')

    status = m.get("status")
    if status == "approved":
        left = f"Zápis schválen {_fmt_dt(m.get('approved_at'))}" + (" automaticky (bez kontroly)" if m.get("auto_approved") else "")
    else:
        left = "Rozpracovaný zápis — zatím neschválený"
    right = "Připraveno z nahrávky s pomocí AI" if m.get("source_id") else ""
    warn = ('<p class="jd-warn">Zápis nebyl zkontrolován — uložen automaticky po 14 dnech tak, jak ho vyplnila AI.</p>'
            if m.get("auto_approved") else "")
    goal_html = (f'<p class="jd-goal{" ai" if ai_marks and goal.get("ai") else ""}">{e(goal["text"])}{tag(goal)}</p>'
                 if not is_empty(goal) else "")
    eyebrow = e(tpl["name"]) if tpl["name"].strip().lower().startswith(("zápis", "zapis")) else f"Zápis ze schůzky · {e(tpl['name'])}"
    brand = branding()
    return (f'<article class="jkdoc"><header>'
            f'<div class="jd-brand"><img src="{e(brand["logo"])}" alt=""><div><b>{e(brand["owner"])}</b>'
            f'<span>{e(brand["subtitle"])}</span></div></div>'
            f'<p class="jd-eyebrow">{eyebrow}</p><h1>{e(title_of(m))}</h1>{goal_html}'
            f'<dl class="jd-meta">{"".join(meta)}</dl>{warn}</header>{"".join(secs)}'
            f'<footer class="jd-foot"><span>{e(left)}</span><span>{e(right)}</span></footer></article>')


def branding() -> dict:
    """Značka v hlavičce zápisu a v patičce PDF (99_System/config/pipeline.yaml → branding)."""
    b = dict(settings.DEFAULTS["branding"])
    try:
        b.update(settings.load().get("branding") or {})
    except Exception:  # noqa: BLE001 — bez konfigurace platí výchozí
        pass
    return {k: str(v or "") for k, v in b.items()}


def html_doc(m: dict) -> str:
    """Celá stránka pro PDF (bez označení AI)."""
    css = (STATIC / "jkdoc.css").read_text(encoding="utf-8")
    brand = branding()
    body = doc_html(m, ai_marks=False).replace(f'src="{_html.escape(brand["logo"])}"', 'src="logo.svg"')
    footer = " · ".join(x for x in (brand["owner"], brand["subtitle"]) if x).replace('"', "")
    page_css = PDF_PAGE.replace("__BRAND_FOOTER__", footer)
    return (f"<!doctype html><html lang='cs'><head><meta charset='utf-8'><title>{_html.escape(title_of(m))}</title>"
            f"<style>{PDF_FONTS}{css}{page_css}</style></head><body>{body}</body></html>")


def pdf_bytes(m: dict) -> bytes:
    from weasyprint import HTML
    return HTML(string=html_doc(m), base_url=str(STATIC) + "/").write_pdf()


def pdf_filename(m: dict) -> str:
    import unicodedata
    base = unicodedata.normalize("NFKD", f"Zapis {m['created'][:10]} {title_of(m)}").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9 ._-]+", "", base).strip().replace(" ", "_")[:80] + ".pdf"
