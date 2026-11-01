"""F2: karta zdroje v 01_Zdroje/ z přepisu + analýzy + zmínek (schema_version 3).

- Frontmatter se sestaví deterministicky; pole, která pipeline nespravuje (vaše vlastní), se zachovají.
- Generovaný obsah je mezi <!-- gen:start --> a <!-- gen:end -->. Text za gen:end (vaše poznámky)
  přežije každé přegenerování. Staré karty V1 bez značek se nahradí celé (původní verze je v Gitu).
- Každý odstavec přepisu nese block-id ^sN (N = první segment), takže na něj jde odkazovat
  [[karta#^s123]] a časová kotva otevře video přesně na tom místě.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import yaml

from pipeline import catalog, frontmatter, ids, settings, state, transcript
from pipeline.config import Vault
from pipeline.entities import TYPES, Registry
from pipeline.legacy_v1 import clean_filename
from pipeline.timefmt import mmss

GEN_START, GEN_END = "<!-- gen:start -->", "<!-- gen:end -->"
SCHEMA_VERSION = 3
MANAGED = {"typ", "source_id", "source_version", "video_id", "url", "asset_path", "transcript_path",
           "published_at", "retrieved_at", "processed_at", "knowledge_domain", "source_type", "visibility",
           "origin", "schema_version", "prompt_version", "analyzer_version", "stt_engine", "stt_status",
           "time_source", "duration", "dominant_volatility", "mluvci", "autor", "hoste", "osoby",
           "organizace", "nastroje", "projekty", "publikace", "mista", "udalosti", "temata", "status",
           "title"}
FM_GROUPS = [("osoby", ("osoba",)), ("organizace", ("organizace",)),
             ("nastroje", ("software", "ai_model", "hardware", "sluzba")), ("projekty", ("projekt",)),
             ("publikace", ("publikace",)), ("mista", ("misto",)), ("udalosti", ("udalost",)),
             ("temata", ("tema",))]
TYPE_LABEL = {"fact": "fakt", "opinion": "názor", "prediction": "předpověď",
              "recommendation": "doporučení", "hypothesis": "hypotéza"}
POL = {"positive": "👍 pozitivní", "negative": "👎 negativní", "mixed": "± smíšený", "neutral": "neutrální"}


class _Dumper(yaml.SafeDumper):
    pass


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _str_rep(d, s):
    if _DATE.match(s):                          # datum bez uvozovek (Obsidian / Dataview ho rozpozná)
        return d.represent_scalar("tag:yaml.org,2002:timestamp", s)
    style = '"' if s.startswith("[[") or ":" in s or s.strip() != s or s == "" else None
    return d.represent_scalar("tag:yaml.org,2002:str", s, style=style)


_Dumper.add_representer(str, _str_rep)


def _link(name: str) -> str:
    return f"[[{name}]]"


def _tlink(video_id: str | None, t: float | None) -> str:
    if t is None:
        return ""
    return f"[{mmss(t)}](https://youtu.be/{video_id}?t={int(t)})" if video_id else mmss(t)


def paragraph_map(t: dict, max_seconds: float) -> tuple[list[dict], dict[int, int]]:
    paras = transcript.paragraphs(t, max_seconds)
    seg2para = {}
    for p in paras:
        for s in p["segments"]:
            seg2para[s] = p["segments"][0]
    return paras, seg2para


def build_body(t: dict, a: dict, m: dict, reg: Registry, video_id: str | None, max_seconds: float) -> str:
    paras, seg2para = paragraph_map(t, max_seconds)
    segs = {s["i"]: s for s in t["segments"]}
    q = t["stt_quality"]
    out: list[str] = []
    title = t.get("meta", {}).get("title") or t["source_id"]
    out += [f"# {title}", ""]
    if q["status"] != "STT_OK":
        out += [f"> [!warning] Kvalita přepisu: {q['status']}", *[f"> - {r}" for r in q["reasons"]], ""]

    out += ["## 📌 Rychlé shrnutí (TL;DR)", a.get("tldr", "").strip(), ""]

    out += ["## ⏱️ Kapitoly a časová osa"]
    for ch in a.get("chapters", []):
        s = segs.get(ch["segment"])
        if s:
            out.append(f"- {_tlink(video_id, s['start'])} – {ch['title']} ([[#^s{seg2para.get(s['i'], s['i'])}|→ přepis]])")
    out.append("")

    out += ["## 🔑 Klíčové body a myšlenky", *[f"- {k}" for k in a.get("key_points", [])], ""]

    def claim_block(c: dict) -> list[str]:
        spk = c.get("speaker") or "?"
        para = seg2para.get(c["segment_ids"][0]) if c["segment_ids"] else None
        ref = f" ([[#^s{para}|přepis]])" if para is not None else ""
        lines = [f"- **{_link(spk)}**: {c['claim']} — {_tlink(video_id, c['t'])}{ref}",
                 f"    - > „{c['quote']}“",
                 f"    - Typ: {TYPE_LABEL.get(c['type'], c['type'])} · volatilita: {c['volatility']}"
                 f" · ověření: {c['grounding']['method']}"]
        for at in c.get("attitudes", []):
            eid = reg.resolve(at["target"])
            name = reg.data[eid]["name"] if eid else at["target"]
            lines.append(f"    - Postoj k {_link(name)}: {POL.get(at['polarity'], at['polarity'])}")
        return lines

    facts = [c for c in a.get("claims", []) if c["type"] == "fact"]
    others = [c for c in a.get("claims", []) if c["type"] != "fact"]
    out += ["## ⚖️ Tvrzení a názory (Claims)"]
    for c in others:
        out += claim_block(c)
    out += ["", "## 📊 Klíčová fakta (Facts)"]
    for c in facts:
        out += claim_block(c)
    if a.get("unverified_claims"):
        out += ["", "## ❔ Neověřená tvrzení",
                "_Model je uvedl, ale citovaný úryvek se v přepisu nepodařilo najít. Neberte je jako doložená._"]
        for c in a["unverified_claims"]:
            out.append(f"- {c.get('speaker') or '?'}: {c['claim']} — „{c['quote']}“")
    out.append("")

    # zmínky podle typu
    counts: dict[str, list[dict]] = {}
    for mm in m.get("mentions", []):
        counts.setdefault(mm["entity"], []).append(mm)
    out += ["## 👥 Zmíněné entity"]
    for etype, label in TYPES.items():
        items = [(eid, lst) for eid, lst in counts.items() if reg.data.get(eid, {}).get("type") == etype]
        if not items:
            continue
        items.sort(key=lambda x: -len(x[1]))
        parts = []
        for eid, lst in items:
            first = min(lst, key=lambda x: x["t"])
            parts.append(f"{_link(reg.data[eid]['name'])} ({len(lst)}× od {_tlink(video_id, first['t'])})")
        out.append(f"- **{label}:** " + ", ".join(parts))
    out.append("")

    out += ["## 📝 Kompletní přepis", f"_Doslovný přepis ({t['engine']}); čas s ~ je dopočtený, ostatní zarovnané na titulky._", ""]
    for p in paras:
        name = t["speakers"].get(p["speaker"], p["speaker"])
        stamp = mmss(p["start"])
        if p["time_source"] != "aligned_vtt":
            stamp = "~" + stamp
        link = f"[{stamp}](https://youtu.be/{video_id}?t={int(p['start'])})" if video_id else stamp
        out += [f"**{link} {name}:** {p['text']} ^s{p['segments'][0]}", ""]
    return "\n".join(out).rstrip() + "\n"


def frontmatter_data(old: dict, t: dict, a: dict, m: dict, reg: Registry, sid: str, video_id: str | None,
                     vault: Vault) -> dict:
    meta = t.get("meta", {})
    ns = sid.split(":", 1)[0]
    date = str(old.get("published_at") or "")
    if not date and meta.get("upload_date"):
        u = str(meta["upload_date"])
        date = f"{u[:4]}-{u[4:6]}-{u[6:8]}" if len(u) == 8 else u
    fm: dict = {
        "typ": old.get("typ") or {"yt": "video", "voice": "hlasova_poznamka"}.get(ns, "zdroj"),
        "title": meta.get("title") or old.get("title"),
        "source_id": sid, "source_version": t.get("source_version", 1),
    }
    if video_id:
        fm.update({"video_id": video_id, "url": ids.youtube_url(video_id),
                   "asset_path": f"00_Assets/audio/{video_id}.m4a"})
    fm.update({
        "transcript_path": vault.rel(vault.transcripts / f"{ids.safe_filename(sid)}.json"),
        "published_at": date or None,
        "retrieved_at": str(old.get("retrieved_at") or datetime.now().strftime("%Y-%m-%d"))[:10],
        "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "knowledge_domain": old.get("knowledge_domain", "personal" if ns == "voice" else "public"),
        "source_type": old.get("source_type", {"yt": "youtube", "voice": "voice_note"}.get(ns, ns)),
        "visibility": old.get("visibility", "private" if ns == "voice" else "public"),
        "origin": old.get("origin", "personal" if ns == "voice" else "external"),
        "schema_version": SCHEMA_VERSION, "prompt_version": a.get("prompt_version"),
        "stt_engine": t["engine"], "stt_status": t["stt_quality"]["status"],
        "duration": mmss(t["duration_s"]), "dominant_volatility": a.get("dominant_volatility"),
        "autor": [_link(meta.get("channel") or meta.get("uploader"))] if (meta.get("channel") or meta.get("uploader")) else [],
        "mluvci": [_link(n) for n in t["speakers"].values() if not re.match(r"^(Mluvčí|Speaker) \d+$", n)],
    })
    by_type: dict[str, list[str]] = {}
    count: dict[str, int] = {}
    for mm in m.get("mentions", []):
        count[mm["entity"]] = count.get(mm["entity"], 0) + 1
    for eid in sorted(count, key=lambda e: -count[e]):
        e = reg.data.get(eid)
        if e:
            by_type.setdefault(e["type"], []).append(e["name"])
    for key, types in FM_GROUPS:
        vals = [n for ty in types for n in by_type.get(ty, [])]
        if key == "temata":
            vals = list(dict.fromkeys([reg.data[x]["name"] if x in reg.data else x for x in a.get("topics", [])] + vals))
        fm[key] = [_link(v) for v in vals]
    for k, v in old.items():                      # vlastní pole uživatele zachovat
        if k not in MANAGED:
            fm[k] = v
    return fm


def _card_path(vault: Vault, sid: str, fm: dict) -> Path:
    e = catalog.find(vault, sid)
    if e:
        return e.path
    if sid.startswith("voice:"):
        return vault.internal / "VoiceNotes" / f"{sid.split(':', 1)[1][:13]} - {clean_filename(fm.get('title') or 'poznámka')[:80]}.md"
    channel = (fm.get("autor") or ["[[?]]"])[0].strip("[]")
    return vault.sources / f"{fm.get('published_at') or 'bez-data'} - {clean_filename(channel)} - {clean_filename(fm.get('title') or sid)}.md"


def run(vault: Vault, sid: str) -> Path:
    safe = ids.safe_filename(sid)
    t = transcript.load(vault.transcripts / f"{safe}.json")
    a = json.loads((vault.transcripts / f"{safe}.analysis.json").read_text(encoding="utf-8"))
    m = json.loads((vault.transcripts / f"{safe}.mentions.json").read_text(encoding="utf-8"))
    reg = Registry.load(vault)
    video_id = sid.split(":", 1)[1] if sid.startswith("yt:") else None
    e = catalog.find(vault, sid)
    old_text = e.path.read_text(encoding="utf-8") if e else ""
    old_fm = frontmatter.parse(old_text) if old_text else {}
    fm = frontmatter_data(old_fm, t, a, m, reg, sid, video_id, vault)
    body = build_body(t, a, m, reg, video_id, settings.load(vault)["render"]["paragraph_max_seconds"])

    notes = ""
    if GEN_END in old_text:
        notes = old_text.split(GEN_END, 1)[1].strip("\n")
    if not notes:
        notes = "## ✍️ Moje poznámky\n"
    text = ("---\n" + yaml.dump(fm, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000)
            + "---\n" + GEN_START + "\n" + body + GEN_END + "\n\n" + notes.rstrip() + "\n")
    path = _card_path(vault, sid, fm)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    state.mark(vault, sid, "render", "done", engine="render-v3", output=vault.rel(path))
    print(f"  → karta {vault.rel(path)}")
    return path
