"""F1: YouTube video → doslovný přepis s časy a mluvčími (00_Assets/transcripts/).

Kroky (každý idempotentní, stav v 99_System/state/):
  ingest  – metadata, audio (pokud chybí), titulky
  stt     – kusy 300 s → STTProvider; surové odpovědi se cachují po kusech
            (00_Assets/transcripts/raw/…), opakované spuštění API nevolá znovu
  align   – sešití kusů + zarovnání na titulky + metriky kvality
Výstup: {source}.json (strojová pravda, AD-012) + {source}.md (čitelný náhled s kotvami).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

from pipeline import audio, catalog, frontmatter, ids, settings, state, transcript, vtt, youtube
from pipeline.align import align_to_vtt, stitch
from pipeline.config import Vault
from pipeline.providers.base import RawSegment, STTContext, STTProvider


def _paths(vault: Vault, sid: str) -> dict[str, Path]:
    safe = ids.safe_filename(sid)
    return {
        "json": vault.transcripts / f"{safe}.json",
        "md": vault.transcripts / f"{safe}.md",
        "raw": vault.transcripts / "raw" / safe,
        "tmp": vault.assets / "chunks_tmp" / safe,
    }


def _meta_from_card(vault: Vault, sid: str) -> dict:
    e = catalog.find(vault, sid)
    if not e:
        return {}
    fm = frontmatter.parse(e.path.read_text(encoding="utf-8")[:8192])
    autor = fm.get("autor") or []
    channel = (autor[0] if isinstance(autor, list) and autor else str(autor)).strip("[]\" ")
    return {"title": e.path.stem.split(" - ", 2)[-1], "channel": channel,
            "upload_date": str(fm.get("published_at") or "").replace("-", ""), "description": ""}


def ingest(vault: Vault, video_id: str, offline: bool = False) -> dict:
    sid = ids.make("yt", video_id)
    p = _paths(vault, sid)
    p["raw"].mkdir(parents=True, exist_ok=True)
    info_file = p["raw"] / "info.json"
    if info_file.exists():
        info = json.loads(info_file.read_text(encoding="utf-8"))
    elif offline:
        info = _meta_from_card(vault, sid)
    else:
        info = youtube.get_video_info(ids.youtube_url(video_id))
        info_file.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")

    audio_path = vault.audio / f"{video_id}.m4a"
    if not audio_path.exists():
        if offline:
            raise FileNotFoundError(f"Chybí audio {audio_path} a běží se offline.")
        audio_path = Path(youtube.download_audio(ids.youtube_url(video_id), vault)[0])

    lang = (info.get("language") or "cs").split("-")[0]
    existing = sorted(vault.captions.glob(f"{video_id}.*.vtt"))
    cap = existing[0] if existing else vault.captions / f"{video_id}.{lang}.vtt"
    if not cap.exists() and not offline:
        try:
            cap = youtube.download_captions(video_id, vault, lang) or cap
        except Exception as e:  # noqa: BLE001  — titulky jsou volitelné
            print(f"  Titulky se nepodařilo stáhnout: {str(e)[:120]}")
    state.mark(vault, sid, "ingest", "done", engine="yt-dlp",
               captions=cap.exists(), audio=vault.rel(audio_path))
    info.setdefault("language", lang)
    return {"sid": sid, "info": info, "audio": audio_path, "captions": cap if cap.exists() else None}


def chunk_check(segs: list[RawSegment], start: float, end: float, vtt_words: list, cfg: dict) -> str | None:
    """Vrátí důvod, proč je odpověď na kus neúplná, nebo None.

    S titulky: poměr slov Gemini/titulky v rozsahu kusu (v pilotu 0,92–1,04 u zdravých kusů,
    0,22 u useknutého). Bez titulků: kde končí poslední segment vůči délce kusu.
    """
    q = cfg["quality"]
    dur = end - start
    if not segs:
        return "prázdná odpověď"
    words = sum(len(s.text.split()) for s in segs)
    vw = sum(1 for w in vtt_words if start <= w.start < end)
    if vw >= 40:
        ratio = words / vw
        if ratio < q["chunk_vtt_ratio_min"]:
            return f"jen {ratio:.0%} slov oproti titulkům v tomto kusu"
        return None
    last = max(s.end for s in segs)
    if dur > 60 and last < dur * q["chunk_time_coverage_min"] and words < dur * 1.2:
        return f"přepis končí v {last:.0f} s z {dur:.0f} s"
    return None


def _transcribe_checked(provider: STTProvider, path: Path, ctx: STTContext, start: float, end: float,
                        vtt_words: list, cfg: dict, tmp: Path) -> tuple[list[RawSegment], dict]:
    """Přepis kusu s kontrolou úplnosti: opakování, pak rozdělení na poloviny."""
    q = cfg["quality"]
    note: dict = {"attempts": 0}
    best: list[RawSegment] = []
    for _ in range(q["chunk_retries"] + 1):
        note["attempts"] += 1
        segs = provider.transcribe(path, ctx)
        problem = chunk_check(segs, start, end, vtt_words, cfg)
        if sum(len(s.text.split()) for s in segs) > sum(len(s.text.split()) for s in best):
            best = segs
        if not problem:
            return segs, note
        print(f"    ! {problem} → opakuji")
        note["problem"] = problem
    # rozdělit na dvě poloviny s překryvem a sešít
    half = (end - start) / 2
    ov = cfg["stt"]["overlap_seconds"]
    parts = []
    for k, (a, b) in enumerate(((0.0, half + ov), (half - ov, end - start))):
        sub = tmp / f"{path.stem}_part{k}.ogg"
        audio.cut(path, sub, a, b - a)
        sub_ctx = STTContext(**{**ctx.__dict__, "chunk_duration": b - a, "chunk_start": ctx.chunk_start + a})
        parts.append({"index": k, "start": a, "end": b, "segments": provider.transcribe(sub, sub_ctx)})
    merged, _ = stitch(parts)
    segs = [RawSegment(m["start"], m["end"], m["speaker"], m["text"]) for m in merged]
    problem = chunk_check(segs, start, end, vtt_words, cfg)
    note["split"] = True
    if problem and sum(len(s.text.split()) for s in best) > sum(len(s.text.split()) for s in segs):
        segs = best
    note["problem"] = problem
    print(f"    {'! stále neúplné: ' + problem if problem else 'po rozdělení v pořádku'}")
    return segs, note


def run_stt(vault: Vault, sid: str, audio_path: Path, info: dict, provider: STTProvider, cfg: dict,
            force: bool = False, vtt_words: list | None = None) -> list[dict]:
    p = _paths(vault, sid)
    p["raw"].mkdir(parents=True, exist_ok=True)
    sc = cfg["stt"]
    from pipeline import glossary
    try:
        terms = glossary.prompt_terms(vault)
    except Exception:  # noqa: BLE001 — slovník je pomůcka, ne podmínka
        terms = []
    vtt_words = vtt_words or []
    chunks = audio.make_chunks(audio_path, p["tmp"], sc["chunk_seconds"], sc["overlap_seconds"], sc["audio_bitrate"])
    results: list[dict] = []
    speakers: list[str] = []
    tail = ""
    for ch in chunks:
        cache = p["raw"] / f"chunk_{ch.index:03d}.json"
        cached = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
        valid = cached and cached.get("engine") == provider.engine \
            and cached.get("prompt_version") == provider.prompt_version \
            and abs(cached.get("start", -1) - ch.start) < 0.01 and not force
        if valid:
            segs = [RawSegment(**s) for s in cached["segments"]]
            # neúplný kus z cache se zkusí znovu (jen pokud předtím nebyl už rozdělen)
            problem = chunk_check(segs, ch.start, ch.end, vtt_words, cfg)
            if problem and not cached.get("check", {}).get("split"):
                print(f"  kus {ch.index + 1}/{len(chunks)}: v cache neúplný ({problem}) → přepisuji znovu")
                valid = False
            else:
                print(f"  kus {ch.index + 1}/{len(chunks)}: z cache ({len(segs)} segmentů)")
        if not valid:
            print(f"  kus {ch.index + 1}/{len(chunks)}: přepisuji {ch.start:.0f}–{ch.end:.0f} s modelem {provider.engine} …")
            ctx = STTContext(title=info.get("title", ""), channel=info.get("channel") or info.get("uploader") or "",
                             description=info.get("description") or "", known_speakers=speakers[:], known_terms=terms,
                             previous_text_tail=tail, chunk_index=ch.index, chunk_count=len(chunks),
                             chunk_duration=ch.end - ch.start, chunk_start=ch.start, language=info.get("language") or "cs")
            segs, note = _transcribe_checked(provider, ch.path, ctx, ch.start, ch.end, vtt_words, cfg, p["tmp"])
            cache.write_text(json.dumps({
                "engine": provider.engine, "prompt_version": provider.prompt_version,
                "index": ch.index, "start": ch.start, "end": ch.end, "check": note,
                "segments": [asdict(s) for s in segs]}, ensure_ascii=False, indent=1), encoding="utf-8")
        for s in segs:
            if s.speaker not in speakers:
                speakers.append(s.speaker)
        if segs:
            tail = " ".join(s.text for s in segs[-3:])
        results.append({"index": ch.index, "start": ch.start, "end": ch.end, "segments": segs})
    state.mark(vault, sid, "stt", "done", engine=provider.engine, prompt_version=provider.prompt_version,
               chunks=len(chunks))
    return results


def finalize(vault: Vault, sid: str, video_id: str | None, chunk_results: list[dict], audio_path: Path,
             captions: Path | None, provider: STTProvider, cfg: dict, info: dict) -> dict:
    p = _paths(vault, sid)
    segs, removed = stitch(chunk_results)
    vtt_words = vtt.load(captions)[1] if captions else []
    spans = {c["index"]: (c["start"], c["end"]) for c in chunk_results}
    align_to_vtt(segs, vtt_words, spans, cfg["align"]["window_seconds"], cfg["align"]["min_confidence"])
    from pipeline import glossary
    try:
        n = glossary.fix_segments(vault, segs)      # „kloud“ → „Claude“ podle 99_System/Slovník.md
        if n:
            print(f"  slovník: opraveno {n}× (názvy)")
    except Exception:  # noqa: BLE001
        pass
    dur = audio.duration(audio_path)
    q = cfg["quality"]
    sil = audio.silences(audio_path, q["silence_db"], q["silence_min_seconds"])
    qi = transcript.quality(segs, dur, sil, len(vtt_words), removed, q)
    meta = {k: info.get(k) for k in ("title", "channel", "uploader", "upload_date") if info.get(k)}
    if captions:
        meta["captions"] = vault.rel(captions)
    t = transcript.build(sid, engine=provider.engine, prompt_version=provider.prompt_version,
                         duration=dur, segments=segs, quality_info=qi, meta=meta,
                         language=info.get("language") or "cs")
    transcript.save(t, p["json"])
    p["md"].write_text(transcript.render_markdown(t, video_id, cfg["render"]["paragraph_max_seconds"]),
                       encoding="utf-8")
    status = "done" if qi["status"] != "STT_INCOMPLETE" else "requires_local_whisper"
    state.mark(vault, sid, "align", status, engine=provider.engine, quality=qi["status"],
               output_hash=t["content_hash"])
    shutil.rmtree(p["tmp"], ignore_errors=True)
    return t


def default_provider(cfg: dict, kind: str = "private") -> STTProvider:
    """kind "youtube": veřejná videa → levný audio model přes OpenRouter (je-li nastaven), jinak Gemini."""
    import os
    from pipeline.providers.gemini_stt import GeminiSTT
    try:
        from dotenv import load_dotenv
        load_dotenv(Vault.default().root / ".env")
    except ImportError:
        pass
    sc = cfg["stt"]
    route = sc.get(kind) if kind in ("youtube", "private") else None     # pipeline.yaml: stt.youtube / stt.private
    if route and route.get("provider") == "openrouter" and os.getenv("OPENROUTER_API_KEY"):
        from pipeline.providers.openrouter import OpenRouterSTT
        p = OpenRouterSTT(model=route["model"], fallbacks=route.get("fallback_models"))
    else:
        p = GeminiSTT(model=sc["model"], min_interval=sc["min_interval_seconds"],
                      max_retries=sc["max_retries"], backoff=sc["backoff_seconds"],
                      fallbacks=sc.get("fallback_models"))
    p.purpose = "přepis YouTube" if kind == "youtube" else "přepis soukromé"
    return p


def transcribe_youtube(url_or_id: str, vault: Vault | None = None, provider: STTProvider | None = None,
                       force: bool = False, offline: bool = False) -> dict:
    vault = vault or Vault.default()
    cfg = settings.load(vault)
    video_id = ids.youtube_id(url_or_id) or url_or_id
    if len(video_id) != 11:
        raise ValueError(f"Nerozpoznané YouTube ID: {url_or_id}")
    provider = provider or default_provider(cfg, "youtube")
    ing = ingest(vault, video_id, offline=offline)
    print(f"{ing['sid']}: {ing['info'].get('title', '')} | titulky: {'ano' if ing['captions'] else 'ne'}")
    vtt_words = vtt.load(ing["captions"])[1] if ing["captions"] else []
    res = run_stt(vault, ing["sid"], ing["audio"], ing["info"], provider, cfg, force=force, vtt_words=vtt_words)
    t = finalize(vault, ing["sid"], video_id, res, ing["audio"], ing["captions"], provider, cfg, ing["info"])
    q = t["stt_quality"]
    print(f"  → {q['status']}  segmentů {len(t['segments'])}, pokrytí řeči {q['speech_coverage']:.0%}, "
          f"zarovnáno {q['aligned_share']:.0%}" + (f", slov/titulky {q['vtt_word_ratio']:.0%}" if q['vtt_word_ratio'] else ""))
    for r in q["reasons"]:
        print(f"     ! {r}")
    return t


def transcribe_file(vault: Vault, sid: str, audio_path: Path, info: dict, provider: STTProvider | None = None,
                    force: bool = False) -> dict:
    """Přepis libovolného lokálního audia (hlasová poznámka, nahrávka, import) — bez titulků."""
    cfg = settings.load(vault)
    provider = provider or default_provider(cfg)
    res = run_stt(vault, sid, Path(audio_path), info, provider, cfg, force=force, vtt_words=[])
    t = finalize(vault, sid, None, res, Path(audio_path), None, provider, cfg, info)
    q = t["stt_quality"]
    print(f"  → {q['status']}  segmentů {len(t['segments'])}, pokrytí řeči {q['speech_coverage']:.0%}")
    return t
