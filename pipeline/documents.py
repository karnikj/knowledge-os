"""Dokumenty ve znalostní bázi: příjem (soubor / webová stránka), duplicity a verze, AI karta.

Tok (stejný jako u videí a poznámek, jen místo přepisu je převod textu):
  soubor / URL → docparse (úseky s kapitolou a stranou) → 00_Assets/transcripts/file__<id>.json
  → [worker] AI karta (shrnutí, typ, platí pro, pojmy, entity) → karta v Obsidianu → index + vektory.

Identita: source_id = file:<prvních 16 znaků sha256 obsahu>. Stejný obsah = duplicita (odmítne se).
Nová verze = nový source_id; stará dostane `archived` + `superseded_by` a zmizí z hledání i chatu
(zůstává v Úlohách a na disku). Webové stránky se jednou týdně stáhnou znovu; změna obsahu = nová verze.
Originály: 00_Assets/documents/<id>.<přípona> (v Gitu, ať jsou i na PC).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from pipeline import docparse, ids, state
from pipeline.config import Vault
from pipeline.docparse import Unsupported

ENGINE = "doc-v1"
CARD_PROMPT_VERSION = 1
REFRESH_DAYS = 7
INBOX = "16_Inbox_Dokumenty"
DOC_TYPES = ["manuál / dokumentace", "postup / návod", "popis systému", "skill / prompt", "zákon / předpis",
             "interní norma / směrnice", "smlouva", "nabídka", "článek", "poznámky", "jiné"]


# ------------------------------------------------------------------ cesty
def tpath(vault: Vault, sid: str) -> Path:
    return vault.transcripts / f"{ids.safe_filename(sid)}.json"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _stem_name(filename: str) -> str:
    """Jméno souboru bez přípony a značek verze → pro odhad „nová verze téhož“."""
    s = _norm(Path(filename).stem)
    s = re.sub(r"[\s_\-.()\[\]]+", " ", s)
    s = re.sub(r"\b(v|ver|verze|version|rev|r)\s?\d+(\s?\d+)*\b|\b(final|finalni|draft|navrh|kopie|copy|novy|new|"
               r"aktualni|update[d]?)\b|\b\d{1,4}([ .-]\d{1,2}){0,2}\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _shingles(text: str, n: int = 5, limit: int = 4000) -> set:
    w = re.findall(r"\w+", _norm(text))[:60000]
    return {" ".join(w[i:i + n]) for i in range(0, max(0, len(w) - n + 1), 3)} if len(w) >= n else {" ".join(w)}


def load(vault: Vault, sid: str) -> dict:
    return json.loads(tpath(vault, sid).read_text(encoding="utf-8"))


def _save(vault: Vault, t: dict) -> None:
    p = tpath(vault, t["source_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def all_docs(vault: Vault) -> list[dict]:
    out = []
    for f in sorted(vault.transcripts.glob("file__*.json")):
        if f.name.endswith((".analysis.json", ".mentions.json")):
            continue
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if t.get("meta", {}).get("kind") == "document":
            out.append(t)
    return out


_META: dict[str, tuple[float, dict]] = {}


def metas(vault: Vault) -> list[dict]:
    """Lehký přehled dokumentů {source_id, meta, content_hash, segments} — čte JSON jen když se změnil (mtime)."""
    out = []
    for f in sorted(vault.transcripts.glob("file__*.json")):
        if f.name.endswith((".analysis.json", ".mentions.json")):
            continue
        try:
            mt = f.stat().st_mtime
        except OSError:
            continue
        hit = _META.get(str(f))
        if not hit or hit[0] != mt:
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if t.get("meta", {}).get("kind") != "document":
                continue
            hit = (mt, {"source_id": t["source_id"], "meta": t["meta"], "content_hash": t["content_hash"],
                        "segments": len(t["segments"]), "path": f})
            _META[str(f)] = hit
        out.append(hit[1])
    return out


def plain_text(t: dict) -> str:
    out, last = [], None
    for s in t["segments"]:
        sec = t["speakers"].get(s["speaker"], "")
        if sec and sec != last:
            out.append("## " + sec)
            last = sec
        out.append(s["text"])
    return "\n\n".join(out)


# ------------------------------------------------------------------ převod → „přepis“ dokumentu
def _build(sid: str, parsed: dict, meta: dict) -> dict:
    sections: dict[str, str] = {}
    segs = []
    for i, c in enumerate(parsed["chunks"]):
        sec = c["section"] or ""
        if sec not in sections:
            sections[sec] = f"S{len(sections)}"
        page = c["page"] or 0
        segs.append({"i": i, "start": float(page), "end": float(c.get("page_end") or page), "speaker": sections[sec],
                     "text": c["text"], "time_source": "page" if c["page"] else "none",
                     "llm_start": None, "llm_end": None, "chunk": 0})
    body = json.dumps(segs, ensure_ascii=False, sort_keys=True).encode()
    return {
        "schema_version": 1, "source_id": sid, "source_version": 1,
        "content_hash": "sha256:" + hashlib.sha256(body).hexdigest(),
        "engine": ENGINE, "stt_prompt_version": 0, "language": parsed["language"],
        "duration_s": float(parsed["pages"] or 0),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "meta": {"kind": "document", "title": parsed["title"], "format": parsed["kind"], "pages": parsed["pages"],
                 "chars": parsed["chars"], **meta},
        "speakers": {v: k for k, v in sections.items()},
        "stt_quality": {"status": "DOC_OK", "reasons": []},
        "segments": segs,
    }


def _similar(vault: Vault, filename: str, parsed: dict, url: str | None) -> list[dict]:
    """Kandidáti „nová verze téhož“ mezi aktivními dokumenty."""
    stem = _stem_name(filename) if filename else ""
    new_sh = _shingles(" ".join(c["text"] for c in parsed["chunks"]))
    out = []
    for t in all_docs(vault):
        m = t["meta"]
        if m.get("archived"):
            continue
        if url and m.get("url") == url:
            out.append({"source_id": t["source_id"], "title": m.get("title"), "score": 1.0, "why": "stejná adresa"})
            continue
        same_name = bool(stem) and stem == _stem_name(m.get("filename") or "") and len(stem) >= 3
        old_sh = _shingles(" ".join(s["text"] for s in t["segments"]))
        jac = len(new_sh & old_sh) / max(1, min(len(new_sh), len(old_sh)))
        if (same_name and jac >= 0.2) or jac >= 0.6 or (same_name and parsed["kind"] in ("md", "txt")):
            out.append({"source_id": t["source_id"], "title": m.get("title"), "added": m.get("added"),
                        "filename": m.get("filename"), "score": round(max(jac, 0.99 if same_name else 0), 2),
                        "why": "stejný název souboru" if same_name else f"shodný obsah {jac:.0%}"})
    return sorted(out, key=lambda x: -x["score"])[:3]


def ingest(vault: Vault, filename: str, data: bytes, *, visibility: str = "private", url: str | None = None,
           replace: str | None = None, force_new: bool = False, watch: bool = False, origin: str = "web") -> dict:
    """Přijme dokument. Vrací {status: ok|duplicate|maybe_version, ...}; chybu formátu vyhodí jako Unsupported.

    replace = source_id dokumentu, který tahle verze nahrazuje; force_new = uložit jako samostatný
    i přes podobnost s existujícím.
    """
    parsed = docparse.parse(filename or "dokument.txt", data)
    basis = data if not url else "\n".join(c["text"] for c in parsed["chunks"]).encode()
    digest = hashlib.sha256(basis).hexdigest()
    sid = ids.make("file", digest[:16])
    if tpath(vault, sid).exists():
        old = load(vault, sid)["meta"]
        return {"status": "duplicate", "source_id": sid, "title": old.get("title"),
                "added": old.get("added"), "archived": bool(old.get("archived"))}
    if not replace and not force_new:
        cands = _similar(vault, filename, parsed, url)
        if url and cands and cands[0]["why"] == "stejná adresa":
            replace = cands[0]["source_id"]                     # web: stejná adresa = vždy nová verze
        elif cands:
            return {"status": "maybe_version", "candidates": cands, "title": parsed["title"] or Path(filename).stem}
    ext = "." + (filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt")
    if url and parsed["kind"] == "html":
        ext = ".html"
    vault.documents.mkdir(parents=True, exist_ok=True)
    asset = vault.documents / f"{digest[:16]}{ext}"
    asset.write_bytes(data)
    title = parsed["title"] or Path(filename).stem.replace("_", " ").strip() or "Dokument"
    meta = {"title": title, "filename": filename, "url": url, "visibility": "public" if visibility == "public" else "private",
            "added": datetime.now().isoformat(timespec="seconds"), "asset": vault.rel(asset), "bytes": len(data),
            "file_hash": digest, "origin": origin, "watch": bool(url and watch),
            "checked_at": datetime.now().isoformat(timespec="seconds") if url else None}
    t = _build(sid, {**parsed, "title": title}, meta)
    if replace:
        t["meta"]["version_of"] = replace
    _save(vault, t)
    state.mark(vault, sid, "ingest", "done", engine=ENGINE, segments=len(t["segments"]))
    if replace and tpath(vault, replace).exists():
        supersede(vault, replace, sid)
    return {"status": "ok", "source_id": sid, "title": title, "pages": parsed["pages"], "segments": len(t["segments"]),
            "format": parsed["kind"], "replaced": replace}


def supersede(vault: Vault, old_sid: str, new_sid: str) -> None:
    """Stará verze → archiv (mimo hledání a chat), odkaz na novou; karta dostane status „nahrazeno“."""
    t = load(vault, old_sid)
    t["meta"].update(archived=True, superseded_by=new_sid, archived_at=datetime.now().isoformat(timespec="seconds"),
                     watch=False)
    _save(vault, t)
    nt = load(vault, new_sid)
    nt["meta"]["version_of"] = old_sid
    if t["meta"].get("url") and not nt["meta"].get("url"):
        nt["meta"]["url"] = t["meta"]["url"]
    nt["meta"].setdefault("visibility", t["meta"].get("visibility"))
    _save(vault, nt)
    from pipeline import catalog
    e = catalog.find(vault, old_sid)
    if e:
        txt = e.path.read_text(encoding="utf-8")
        if txt.startswith("---\n") and "\nstatus: nahrazeno" not in txt.split("\n---", 1)[0]:
            txt = txt.replace("\n---\n", f"\nstatus: nahrazeno\nsuperseded_by: {new_sid}\n---\n", 1)
            e.path.write_text(txt, encoding="utf-8")


def set_visibility(vault: Vault, sid: str, visibility: str) -> dict:
    t = load(vault, sid)
    t["meta"]["visibility"] = "public" if visibility == "public" else "private"
    _save(vault, t)
    return t["meta"]


# ------------------------------------------------------------------ webové stránky
def fetch_url(url: str) -> tuple[str, bytes]:
    """Stáhne stránku / soubor. Vrací (jméno souboru pro převod, data)."""
    import httpx
    if not re.match(r"^https?://", url or ""):
        raise Unsupported("Adresa musí začínat http:// nebo https://")
    try:
        r = httpx.get(url, follow_redirects=True, timeout=40,
                      headers={"User-Agent": "Mozilla/5.0 (KnowledgeOS; +osobni znalostni baze)",
                               "Accept-Language": "cs,en;q=0.8"})
    except httpx.HTTPError as e:
        raise Unsupported(f"Stránku se nepodařilo stáhnout: {e}")
    if r.status_code >= 400:
        raise Unsupported(f"Stránka vrátila chybu {r.status_code}.")
    ct = r.headers.get("content-type", "").lower()
    name = re.sub(r"[^\w.-]+", "_", url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]) or "stranka"
    if "pdf" in ct or url.lower().split("?")[0].endswith(".pdf"):
        return (name if name.lower().endswith(".pdf") else name + ".pdf"), r.content
    if "markdown" in ct or url.lower().endswith(".md"):
        return name if name.endswith(".md") else name + ".md", r.content
    if ct.startswith("text/plain"):
        return name + ".txt", r.content
    if "html" in ct or not ct:
        return name + ".html", r.content
    raise Unsupported(f"Adresa nevede na stránku ani dokument ({ct}).")


def ingest_url(vault: Vault, url: str, visibility: str = "public", watch: bool = True, **kw) -> dict:
    name, data = fetch_url(url)
    return ingest(vault, name, data, visibility=visibility, url=url, watch=watch, origin="web-url", **kw)


def refresh_due(vault: Vault, now: datetime | None = None) -> list[str]:
    now = now or datetime.now()
    out = []
    for d in metas(vault):
        m = d["meta"]
        if m.get("url") and m.get("watch") and not m.get("archived"):
            last = m.get("checked_at") or m.get("added") or ""
            if not last or datetime.fromisoformat(last) <= now - timedelta(days=REFRESH_DAYS):
                out.append(d["source_id"])
    return out


def refresh(vault: Vault, sid: str) -> dict:
    """Znovu stáhne sledovanou stránku. Beze změny → jen čas kontroly; změna → nová verze."""
    t = load(vault, sid)
    m = t["meta"]
    try:
        r = ingest_url(vault, m["url"], visibility=m.get("visibility", "public"), watch=True, replace=sid)
    except Unsupported as e:
        r = {"status": "error", "error": str(e)}
    if r["status"] != "ok":
        t = load(vault, sid)
        t["meta"]["checked_at"] = datetime.now().isoformat(timespec="seconds")
        if r["status"] == "error":
            t["meta"]["check_error"] = r["error"]
        _save(vault, t)
    return r


# ------------------------------------------------------------------ složka 16_Inbox_Dokumenty
def failed_log(vault: Vault) -> Path:
    return vault.system / "logs" / "documents_failed.jsonl"


def _log_failed(vault: Vault, name: str, error: str, origin: str = "složka") -> None:
    p = failed_log(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "name": name, "error": error,
                            "origin": origin}, ensure_ascii=False) + "\n")


def failed_list(vault: Vault) -> list[dict]:
    p = failed_log(vault)
    if not p.exists():
        return []
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
        try:
            out.append({**json.loads(line), "n": i})
        except ValueError:
            pass
    return out


def drop_failed(vault: Vault, n: int) -> bool:
    p = failed_log(vault)
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines()
    if not 0 <= n < len(lines):
        return False
    del lines[n]
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return True


def intake_inbox(vault: Vault) -> list[str]:
    """Soubory ve 16_Inbox_Dokumenty (z PC přes Git) → dokumenty. Nepodporované → podsložka _nepodporovane."""
    box = vault.root / INBOX
    if not box.exists():
        return []
    done = []
    for f in sorted(box.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.name.lower() in ("readme.md", ".gitkeep"):
            continue
        try:
            r = ingest(vault, f.name, f.read_bytes(), visibility="private", origin="složka")
            if r["status"] == "maybe_version":
                # bez uživatele se nerozhodujeme: uložit jako nový a v Úlohách nabídnout „nahradit starší verzi“
                r = ingest(vault, f.name, f.read_bytes(), visibility="private", origin="složka", force_new=True)
                t = load(vault, r["source_id"])
                t["meta"]["possible_version_of"] = [c["source_id"] for c in
                                                    _similar(vault, f.name, docparse.parse(f.name, f.read_bytes()), None)
                                                    if c["source_id"] != r["source_id"]][:3]
                _save(vault, t)
            if r["status"] == "duplicate":
                _log_failed(vault, f.name, f"duplicita — stejný dokument už v systému je („{r.get('title')}“)")
            f.unlink()
            done.append(f"dokument {f.name}")
        except Unsupported as e:
            bad = box / "_nepodporovane"
            bad.mkdir(exist_ok=True)
            shutil.move(str(f), str(bad / f.name))
            _log_failed(vault, f.name, str(e))
            done.append(f"odmítnut {f.name}")
    return done


# ------------------------------------------------------------------ AI karta
CARD_PROMPT = """Jsi knihovník osobní znalostní báze. Z dokumentu níže připrav referenční kartu. Piš česky, věcně.
Vycházej jen z textu dokumentu (i když je anglicky). Co v textu není, nech prázdné.

Dokument: {title}
Soubor / adresa: {origin}
Osnova (kapitoly): {outline}

Pole:
- doc_type: jeden z {types}
- summary: 3–6 vět — o čem dokument je a k čemu slouží
- key_points: 3–8 nejdůležitějších bodů (každý jedna věta)
- applies_to: pro jaký produkt / systém / verzi / organizaci platí (např. „Microsoft Intune“, „zákoník práce“), jinak ""
- validity: platnost / účinnost / datum vydání nebo aktualizace, pokud je v textu uvedeno, jinak ""
- terms: 3–12 klíčových pojmů (jak jsou v textu)
- entities: pojmenované osoby, organizace, produkty/software, služby, zákony/publikace z textu:
  {{name, type (osoba|organizace|software|sluzba|ai_model|hardware|publikace|projekt|misto|udalost), surface (přesně jak je v textu)}}
- topics: 2–5 obecných témat česky (např. „správa zařízení“, „pracovní právo“)

Text dokumentu (začátek{partial}):
{text}
"""

CARD_SCHEMA = {"type": "object", "properties": {
    "doc_type": {"type": "string"}, "summary": {"type": "string"},
    "key_points": {"type": "array", "items": {"type": "string"}},
    "applies_to": {"type": "string"}, "validity": {"type": "string"},
    "terms": {"type": "array", "items": {"type": "string"}},
    "entities": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "type": {"type": "string"}, "surface": {"type": "string"}},
        "required": ["name", "type"]}},
    "topics": {"type": "array", "items": {"type": "string"}}},
    "required": ["doc_type", "summary"]}


def pending_cards(vault: Vault) -> list[str]:
    """Aktivní dokumenty bez AI karty (nebo s kartou ke staršímu obsahu). Po 3 neúspěšných pokusech se přeskočí."""
    out = []
    for d in metas(vault):
        m = d["meta"]
        if m.get("archived") or m.get("card_failures", 0) >= 3:
            continue
        an = Path(str(d["path"])[:-5] + ".analysis.json")
        if not an.exists():
            out.append(d["source_id"])
            continue
        try:
            a = json.loads(an.read_text(encoding="utf-8"))
        except ValueError:
            out.append(d["source_id"])
            continue
        if a.get("transcript_hash") != d["content_hash"]:
            out.append(d["source_id"])
    return out


def card_failed(vault: Vault, sid: str, err: str) -> None:
    t = load(vault, sid)
    t["meta"]["card_failures"] = t["meta"].get("card_failures", 0) + 1
    t["meta"]["card_error"] = str(err)[:300]
    _save(vault, t)


def _sample(t: dict, budget: int = 40000) -> tuple[str, bool]:
    text = plain_text(t)
    if len(text) <= budget:
        return text, False
    head = text[:budget // 2]
    # zbytek: začátek každé kapitoly, ať karta pokryje celý dokument
    rest, used = [], len(head)
    last = None
    for s in t["segments"]:
        sec = t["speakers"].get(s["speaker"], "")
        if sec != last:
            last = sec
            piece = f"## {sec}\n{s['text'][:500]}"
            if used + len(piece) > budget:
                break
            rest.append(piece); used += len(piece)
    return head + "\n\n[…]\n\n" + "\n\n".join(rest), True


def outline(t: dict, limit: int = 120) -> list[dict]:
    """Kapitoly (první úsek každé sekce) — pro kartu a osnovu v promptu."""
    out, seen = [], set()
    for s in t["segments"]:
        sec = t["speakers"].get(s["speaker"], "")
        if sec and sec not in seen:
            seen.add(sec)
            out.append({"segment": s["i"], "title": sec, "page": int(s["start"]) or None})
            if len(out) >= limit:
                break
    return out


def make_card(vault: Vault, sid: str, provider) -> dict:
    """AI karta dokumentu → .analysis.json + .mentions.json + karta v Obsidianu."""
    from pipeline import usage  # noqa: F401  (tracker se nastaví přes provider)
    from pipeline.entities import Registry, is_named
    from pipeline.ground import mention_ok
    t = load(vault, sid)
    m = t["meta"]
    text, partial = _sample(t)
    ol = outline(t)
    prompt = CARD_PROMPT.format(title=m.get("title"), origin=m.get("url") or m.get("filename") or "",
                                outline="; ".join(o["title"] for o in ol[:60]) or "(bez kapitol)",
                                types=", ".join(DOC_TYPES), partial=", zkráceno" if partial else "", text=text)
    print(f"  karta dokumentu modelem {getattr(provider, 'engine', '?')} …")
    out = provider.generate(prompt, CARD_SCHEMA)
    reg = Registry.load(vault)
    mentions, seen = [], set()
    for e in out.get("entities") or []:
        name, etype = (e.get("name") or "").strip(), (e.get("type") or "").strip()
        surface = (e.get("surface") or name).strip()
        if not name or etype not in ("osoba", "organizace", "software", "sluzba", "ai_model", "hardware",
                                     "publikace", "projekt", "misto", "udalost") or not is_named(name, etype):
            continue
        seg = next((s for s in t["segments"] if mention_ok(surface, s["text"])), None)
        if not seg:
            continue
        eid = reg.get_or_propose(name, etype, sid, surface)
        if eid in seen or reg.rejected(eid):
            continue
        seen.add(eid)
        mentions.append({"entity": eid, "segment": seg["i"], "t": seg["start"],
                         "speaker": t["speakers"].get(seg["speaker"], ""), "surface": surface})
    topics = []
    for tp in out.get("topics") or []:
        if tp.strip():
            topics.append(reg.get_or_propose(tp.strip(), "tema", sid))
    reg.save()
    now = datetime.now().isoformat(timespec="seconds")
    analysis = {"schema_version": 1, "source_id": sid, "transcript_hash": t["content_hash"],
                "engine": getattr(provider, "engine", "?"), "prompt_version": CARD_PROMPT_VERSION, "created_at": now,
                "kind": "document", "doc_type": out.get("doc_type") or "jiné", "tldr": (out.get("summary") or "").strip(),
                "key_points": out.get("key_points") or [], "applies_to": out.get("applies_to") or "",
                "validity": out.get("validity") or "", "terms": out.get("terms") or [], "topics": topics,
                "chapters": ol, "claims": [], "unverified_claims": []}
    base = vault.transcripts / ids.safe_filename(sid)
    Path(str(base) + ".analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    Path(str(base) + ".mentions.json").write_text(json.dumps({"schema_version": 1, "source_id": sid,
                                                              "transcript_hash": t["content_hash"], "mentions": mentions},
                                                             ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    state.mark(vault, sid, "analyze", "done", engine=analysis["engine"], prompt_version=CARD_PROMPT_VERSION,
               input_hash=t["content_hash"], mentions=len(mentions))
    render_card(vault, sid)
    return analysis


def provider_for(vault: Vault, sid: str):
    """Soukromé dokumenty jen přes Gemini; veřejné (manuály, zákony, články) levným modelem."""
    from pipeline import analyze
    vis = load(vault, sid)["meta"].get("visibility", "private")
    return analyze.default_provider(vault, "youtube" if vis == "public" else "private")


# ------------------------------------------------------------------ karta v Obsidianu
def _card_path(vault: Vault, t: dict) -> Path:
    from pipeline import catalog
    from pipeline.legacy_v1 import clean_filename
    e = catalog.find(vault, t["source_id"])
    if e:
        return e.path
    m = t["meta"]
    folder = (vault.sources if m.get("visibility") == "public" else vault.internal) / "Dokumenty"
    name = clean_filename(m.get("title") or "dokument")[:90]
    p = folder / f"{name}.md"
    if p.exists():
        p = folder / f"{name} ({t['source_id'][-6:]}).md"
    return p


def render_card(vault: Vault, sid: str) -> Path:
    from pipeline.render import GEN_END, GEN_START, _Dumper
    from pipeline import catalog, frontmatter
    from pipeline.entities import Registry
    t = load(vault, sid)
    m = t["meta"]
    base = vault.transcripts / ids.safe_filename(sid)
    a_f, m_f = Path(str(base) + ".analysis.json"), Path(str(base) + ".mentions.json")
    a = json.loads(a_f.read_text(encoding="utf-8")) if a_f.exists() else {}
    mn = json.loads(m_f.read_text(encoding="utf-8")).get("mentions", []) if m_f.exists() else []
    reg = Registry.load(vault)
    e = catalog.find(vault, sid)
    old_text = e.path.read_text(encoding="utf-8") if e else ""
    old_fm = frontmatter.parse(old_text) if old_text else {}
    names = lambda types: [f"[[{reg.data[x['entity']]['name']}]]" for x in mn            # noqa: E731
                           if x["entity"] in reg.data and reg.data[x["entity"]]["type"] in types]
    fm = {k: v for k, v in old_fm.items()}
    fm.update({
        "typ": "dokument", "title": m.get("title"), "source_id": sid, "source_type": "document",
        "doc_type": a.get("doc_type"), "visibility": m.get("visibility", "private"),
        "knowledge_domain": "personal" if m.get("visibility") != "public" else "public",
        "published_at": (m.get("added") or "")[:10], "url": m.get("url") or None,
        "asset_path": m.get("asset"), "transcript_path": vault.rel(tpath(vault, sid)),
        "format": m.get("format"), "stran": m.get("pages") or None, "plati_pro": a.get("applies_to") or None,
        "platnost": a.get("validity") or None,
        "status": "nahrazeno" if m.get("archived") else "aktivní",
        "version_of": m.get("version_of"), "superseded_by": m.get("superseded_by"),
        "organizace": names(("organizace",)), "osoby": names(("osoba",)),
        "nastroje": names(("software", "sluzba", "ai_model", "hardware")),
        "temata": [f"[[{reg.data[x]['name']}]]" for x in a.get("topics", []) if x in reg.data],
    })
    fm = {k: v for k, v in fm.items() if v not in (None, "", [])}
    L = [f"# {m.get('title')}", ""]
    if m.get("archived"):
        L += [f"> [!warning] Starší verze — nahrazeno dokumentem `{m.get('superseded_by')}`. Není v hledání ani chatu.", ""]
    info = [("Typ", a.get("doc_type")), ("Platí pro", a.get("applies_to")), ("Platnost", a.get("validity")),
            ("Zdroj", m.get("url")), ("Soubor", f"[[{m.get('asset')}]]" if m.get("asset") else None),
            ("Stran", m.get("pages") or None)]
    L += [f"- **{k}:** {v}" for k, v in info if v] + [""]
    if a.get("tldr"):
        L += ["## 📌 Shrnutí", a["tldr"], ""]
    if a.get("key_points"):
        L += ["## 🔑 Klíčové body", *[f"- {k}" for k in a["key_points"]], ""]
    if a.get("chapters"):
        L += ["## 📑 Obsah", *[f"- {c['title']}" + (f" (str. {c['page']})" if c.get("page") else "")
                              for c in a["chapters"][:80]], ""]
    if a.get("terms"):
        L += ["## 🏷️ Pojmy", ", ".join(a["terms"]), ""]
    if not a:
        L += ["_AI karta se připravuje — dokument je už teď dohledatelný v Hledat i v Chatu._", ""]
    body = "\n".join(L).rstrip() + "\n"
    notes = old_text.split(GEN_END, 1)[1].strip("\n") if GEN_END in old_text else ""
    text = ("---\n" + yaml.dump(fm, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000) + "---\n"
            + GEN_START + "\n" + body + GEN_END + "\n\n" + (notes or "## ✍️ Moje poznámky\n").rstrip() + "\n")
    path = _card_path(vault, t)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    state.mark(vault, sid, "render", "done", engine="doc-card-v1", output=vault.rel(path))
    return path


# ------------------------------------------------------------------ přehled pro web
def listing(vault: Vault, include_archived: bool = False) -> list[dict]:
    out = []
    pend = set(pending_cards(vault))
    for t in metas(vault):
        m = t["meta"]
        if m.get("archived") and not include_archived:
            continue
        base = vault.transcripts / ids.safe_filename(t["source_id"])
        a_f = Path(str(base) + ".analysis.json")
        a = json.loads(a_f.read_text(encoding="utf-8")) if a_f.exists() else {}
        out.append({"source_id": t["source_id"], "title": m.get("title"), "added": m.get("added"),
                    "format": m.get("format"), "pages": m.get("pages"), "chars": m.get("chars"),
                    "visibility": m.get("visibility"), "url": m.get("url"), "watch": m.get("watch"),
                    "checked_at": m.get("checked_at"), "check_error": m.get("check_error"),
                    "archived": bool(m.get("archived")), "superseded_by": m.get("superseded_by"),
                    "version_of": m.get("version_of"), "possible_version_of": m.get("possible_version_of") or [],
                    "doc_type": a.get("doc_type"), "summary": a.get("tldr"), "applies_to": a.get("applies_to"),
                    "card_pending": t["source_id"] in pend, "card_error": m.get("card_error") if m.get("card_failures", 0) >= 3 else None,
                    "segments": t["segments"]})
    return sorted(out, key=lambda d: d.get("added") or "", reverse=True)
