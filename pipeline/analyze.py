"""F2: analýza přepisu → tvrzení s ověřenými citacemi, zmínky entit, shrnutí.

Výstupy (v Gitu, vedle přepisu):
  00_Assets/transcripts/{sid}.analysis.json   shrnutí, kapitoly, tvrzení (+ grounding)
  00_Assets/transcripts/{sid}.mentions.json   zmínky po segmentech (ověřené, že v textu zazněly)
  99_System/config/entities.yaml              nové entity jako „navrženo“
Odpovědi modelu se cachují po úsecích (…/raw/{sid}/analysis_wNNN.json) podle enginu,
verze promptu a hashe vstupu — opakovaný běh API nevolá.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from pipeline import ids, settings, state, transcript
from pipeline.config import Vault
from pipeline.entities import Registry, is_named
from pipeline.ground import ground, mention_ok
from pipeline.timefmt import mmss

SCHEMA_VERSION = 1


def _paths(vault: Vault, sid: str) -> dict:
    safe = ids.safe_filename(sid)
    return {"transcript": vault.transcripts / f"{safe}.json",
            "analysis": vault.transcripts / f"{safe}.analysis.json",
            "mentions": vault.transcripts / f"{safe}.mentions.json",
            "raw": vault.transcripts / "raw" / safe}


def windows(segments: list[dict], max_chars: int) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for s in segments:
        if cur and size + len(s["text"]) > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += len(s["text"]) + 30
    if cur:
        out.append(cur)
    return out


def line(s: dict, speakers: dict) -> str:
    return f"[s{s['i']} {mmss(s['start'])} {speakers.get(s['speaker'], s['speaker'])}] {s['text']}"


def _cached_call(path, key: dict, fn, alt_inputs: tuple = ()):
    if path.exists():
        c = json.loads(path.read_text(encoding="utf-8"))
        ck = c.get("key") or {}
        if ck == key or (ck.get("engine") == key["engine"] and ck.get("prompt_version") == key["prompt_version"]
                         and ck.get("input") in alt_inputs):
            return c["result"]
    res = fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": key, "result": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


class PoorerAnalysis(RuntimeError):
    """Nová analýza je výrazně chudší než uložená — neukládá se (ochrana před slabším modelem)."""


def run(vault: Vault, sid: str, provider, meta: dict | None = None, force: bool = False) -> dict:
    cfg = settings.load(vault)["analysis"]
    p = _paths(vault, sid)
    t = transcript.load(p["transcript"])
    segs = t["segments"]
    seg_by_i = {s["i"]: s for s in segs}
    speakers = t["speakers"]
    meta = {**t.get("meta", {}), **(meta or {})}
    ctx = {"title": meta.get("title", ""), "channel": meta.get("channel") or meta.get("uploader", ""),
           "date": meta.get("upload_date", ""), "kind": meta.get("kind", "video")}
    print(f"  analýza (tvrzení, zmínky) modelem {getattr(provider, 'engine', '?')} …")
    import re as _re
    generic = [n for n in speakers.values() if _re.match(r"^(Mluvčí|Speaker) \d+$", n)]
    info_f = p["raw"] / "info.json"
    if info_f.exists():
        ctx["description"] = json.loads(info_f.read_text(encoding="utf-8")).get("description") or ""
    reg = Registry.load(vault)
    known = [f"{e['name']} ({e['type']})" for e in reg.data.values() if e.get("status") == "potvrzeno"]

    wins = windows(segs, cfg["window_chars"])
    results = []

    # po pojmenování mluvčích se text řádků změní — mezipaměť přijme i hash s původními označeními
    orig_names = dict(speakers)
    for gen_label, real in (t.get("speaker_names_from", {}).get("map") or {}).items():
        for code, nm in orig_names.items():
            if nm == real:
                orig_names[code] = gen_label

    def analyse(w: list[dict], tag: str, c: dict, depth: int = 0) -> list[dict]:
        lines = [line(s, speakers) for s in w]
        h = lambda ls: hashlib.sha256("\n".join(ls).encode()).hexdigest()[:16]
        key = {"engine": provider.engine, "prompt_version": provider.prompt_version, "input": h(lines)}
        alt = (h([line(s, orig_names) for s in w]),)
        try:
            return [_cached_call(p["raw"] / f"analysis_{tag}.json", key, lambda: provider.analyze_window(lines, c), alt)]
        except Exception as e:  # noqa: BLE001
            overloaded = "503" in str(e) or "UNAVAILABLE" in str(e)
            if not isinstance(e, json.JSONDecodeError) and not overloaded:
                raise
            if depth >= 3 or len(w) < 4:
                raise
            half = len(w) // 2
            why = "odpověď se nevešla" if isinstance(e, json.JSONDecodeError) else "model přetížený"
            print(f"    {why} → dělím úsek na 2 části ({len(w)} segmentů)")
            return analyse(w[:half], tag + "a", c, depth + 1) + analyse(w[half:], tag + "b", c, depth + 1)

    for n, w in enumerate(wins):
        c = dict(ctx, part=n + 1, parts=len(wins), known_entities=known, generic_speakers=generic)
        print(f"  analýza úseku {n + 1}/{len(wins)} ({len(w)} segmentů)")
        results.extend(analyse(w, f"w{n:03d}", c))

    # --- pojmenování obecných mluvčích (jen když se okna shodnou; zapisuje se do přepisu jako názvy,
    #     text ani segmenty se nemění — AD-012)
    votes: dict[str, set] = {}
    for r in results:
        for sn in r.get("speaker_names", []) or []:
            if sn.get("label") in generic and (sn.get("name") or "").strip():
                votes.setdefault(sn["label"], set()).add(sn["name"].strip())
    renamed = {lab: next(iter(v)) for lab, v in votes.items() if len(v) == 1}
    if renamed:
        for k, v in list(t["speakers"].items()):
            if v in renamed:
                t["speakers"][k] = renamed[v]
        t["speaker_names_from"] = {"method": "analysis", "engine": provider.engine, "map": renamed}
        transcript.save(t, p["transcript"])
        speakers = t["speakers"]
        print(f"  mluvčí pojmenováni: {renamed}")

    # --- kapitoly
    chapters = []
    for r in results:
        for ch in r.get("chapters", []):
            if ch.get("segment") in seg_by_i and ch.get("title"):
                chapters.append({"segment": ch["segment"], "title": ch["title"].strip()})
    chapters.sort(key=lambda c: c["segment"])

    # --- tvrzení + grounding
    claims, unverified = [], []
    for r in results:
        for c in r.get("claims", []):
            g = ground(c.get("quote", ""), [x for x in c.get("segment_ids", []) if isinstance(x, int)], segs,
                       window=cfg["ground_window"])
            seg_ids = g.segment_ids or [x for x in c.get("segment_ids", []) if x in seg_by_i]
            first = seg_by_i.get(seg_ids[0]) if seg_ids else None
            item = {
                "id": f"c{len(claims) + len(unverified) + 1:03d}",
                "speaker": (speakers.get(first["speaker"]) if first else None) or c.get("speaker"),
                "claim": c.get("claim", "").strip(), "quote": c.get("quote", "").strip(),
                "type": c.get("type", "opinion"), "volatility": c.get("volatility", "medium"),
                "segment_ids": seg_ids, "t": first["start"] if first else None,
                "attitudes": [a for a in c.get("attitudes", []) if a.get("target")],
                "grounding": {"method": g.method, "score": g.score},
            }
            (claims if g.ok else unverified).append(item)
    claims.sort(key=lambda c: (c["t"] is None, c["t"] or 0))

    # --- zmínky: ověřit, že v segmentu zazněly, rozlišit na entity
    mentions, dropped, generic_dropped = [], 0, 0
    seen = set()
    for r in results:
        for m in r.get("mentions", []):
            s = seg_by_i.get(m.get("segment"))
            surface, name = (m.get("surface") or "").strip(), (m.get("name") or "").strip()
            if not s or not surface or not name:
                dropped += 1
                continue
            if m.get("type") != "tema" and not mention_ok(surface, s["text"]):
                dropped += 1
                continue
            if not is_named(name, m.get("type", "tema")):
                generic_dropped += 1
                continue
            eid = reg.get_or_propose(name, m.get("type", "tema"), sid, surface)
            if reg.rejected(eid):
                generic_dropped += 1
                continue
            k = (eid, s["i"])
            if k in seen:
                continue
            seen.add(k)
            mentions.append({"entity": eid, "segment": s["i"], "t": s["start"],
                             "speaker": speakers.get(s["speaker"], s["speaker"]), "surface": surface})
    # postoje z tvrzení přenést na zmínky ve stejném segmentu
    for c in claims:
        for a in c["attitudes"]:
            eid = reg.resolve(a["target"])
            if not eid:
                continue
            for m in mentions:
                if m["entity"] == eid and m["segment"] in c["segment_ids"]:
                    m["attitude"] = a["polarity"]
                    m["claim"] = c["id"]
    mentions.sort(key=lambda m: (m["segment"], m["entity"]))

    # --- celkové shrnutí
    ch_lines = [f"{mmss(seg_by_i[c['segment']]['start'])} {c['title']}" for c in chapters]
    sums = [r.get("summary", "") for r in results]
    skey = {"engine": provider.engine, "prompt_version": provider.prompt_version,
            "input": hashlib.sha256(("\n".join(ch_lines) + "\n".join(sums)).encode()).hexdigest()[:16]}
    summary = _cached_call(p["raw"] / "analysis_summary.json", skey,
                           lambda: provider.summarize(ctx, sums, ch_lines))
    for topic in summary.get("topics", []):
        reg.get_or_propose(topic, "tema", sid)
    reg.save()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    analysis = {
        "schema_version": SCHEMA_VERSION, "source_id": sid, "transcript_hash": t["content_hash"],
        "engine": provider.engine, "prompt_version": provider.prompt_version, "created_at": now,
        "tldr": summary.get("tldr", ""), "key_points": summary.get("key_points", []),
        "topics": [reg.resolve(x, "tema") or x for x in summary.get("topics", [])],
        "dominant_volatility": summary.get("dominant_volatility", "medium"),
        "chapters": chapters, "claims": claims, "unverified_claims": unverified,
        "stats": {"windows": len(wins), "claims": len(claims), "unverified": len(unverified),
                  "mentions": len(mentions), "mentions_dropped": dropped, "generic_dropped": generic_dropped},
    }
    if p["analysis"].exists() and not force:
        old = json.loads(p["analysis"].read_text(encoding="utf-8"))
        oc, nc = len(old.get("claims", [])), len(claims)
        if old.get("transcript_hash") == t["content_hash"] and oc >= 10 and nc < 0.6 * oc:
            raise PoorerAnalysis(f"nová analýza ({provider.engine}) má {nc} tvrzení, uložená ({old.get('engine')}) {oc} "
                                 f"→ ponechávám uloženou. Přepsat: analyze --force")
    p["analysis"].write_text(json.dumps(analysis, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    p["mentions"].write_text(json.dumps({"schema_version": SCHEMA_VERSION, "source_id": sid,
                                         "transcript_hash": t["content_hash"], "mentions": mentions},
                                        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    state.mark(vault, sid, "analyze", "done", engine=provider.engine, prompt_version=provider.prompt_version,
               input_hash=t["content_hash"], claims=len(claims), mentions=len(mentions))
    state.mark(vault, sid, "ground", "done", engine="ground-v1", verified=len(claims), unverified=len(unverified))
    print(f"  → tvrzení {len(claims)} ověřeno, {len(unverified)} neověřeno; zmínek {len(mentions)} "
          f"(zahozeno {dropped} nedoložených)")
    return analysis


def default_provider(vault: Vault, kind: str = "private"):
    """kind: "youtube" (veřejná videa → levný model přes OpenRouter, pokud je nastaven),
    "chat" (otázky nad veřejnými zdroji), "private" (poznámky, schůzky, chat nad soukromými → Gemini)."""
    from pipeline.providers.gemini_analysis import GeminiAnalysis
    try:
        from dotenv import load_dotenv
        load_dotenv(vault.root / ".env")
    except ImportError:
        pass
    a = settings.load(vault)["analysis"]
    route = a.get(kind) if kind in ("youtube", "chat", "private") else None   # pipeline.yaml: analysis.<kind>
    purpose = {"youtube": "analýza YouTube", "chat": "chat"}.get(kind, "analýza soukromé")
    if route and route.get("provider") == "openrouter" and os.getenv("OPENROUTER_API_KEY"):
        from pipeline.providers.openrouter import OpenRouterAnalysis
        p = OpenRouterAnalysis(model=route["model"], fallbacks=route.get("fallback_models"))
    else:
        p = GeminiAnalysis(model=a["model"], min_interval=a["min_interval_seconds"],
                           max_retries=a["max_retries"], backoff=a["backoff_seconds"],
                           fallbacks=a.get("fallback_models"))
    p.purpose = purpose
    return p
