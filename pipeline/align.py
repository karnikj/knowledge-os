"""Sešití kusů a zarovnání přepisu na časovou mřížku YouTube titulků.

1. `stitch` — posune časy o offset kusu, rozřízne překryv v jeho polovině a
   odstraní slova zopakovaná na švu (shoda konce jednoho kusu se začátkem dalšího
   na úrovni normalizovaných slov).
2. `align_to_vtt` — sequence alignment slov přepisu na slova titulků (difflib,
   po oknech kolem každého kusu). Segment dostane `aligned_vtt` čas a jistotu
   = podíl svých slov, která se podařilo spárovat. Nezarovnané segmenty mezi
   dvěma zarovnanými dostanou čas dopočtený podle počtu slov (`interpolated`).
   Původní čas z LLM zůstává
   v `llm_start/llm_end` a nikdy se nepřepisuje (AD-012, PLAN §2).
"""
from __future__ import annotations

from difflib import SequenceMatcher

from pipeline.providers.base import RawSegment
from pipeline.textnorm import norm_word
from pipeline.vtt import Word


def _seam_cut(tail: list[str], head: list[str], min_run: int = 4, near_end: int = 12) -> int:
    """Kolik slov z `head` (začátek nového kusu) je opakováním konce `tail`.

    Hledá nejdelší společnou posloupnost slov; ta musí končit blízko konce `tail`
    (jinak jde o náhodnou shodu). Vrací počet slov head k odstranění (0 = žádný šev).
    Zbytek tailu za shodou (typicky 1–2 slova s drobnou odchylkou, např. zajímavý/zajímavě)
    odstraní i odpovídající slova v head.
    """
    if not tail or not head:
        return 0
    sm = SequenceMatcher(None, tail, head, autojunk=False)
    a, b, size = sm.find_longest_match(0, len(tail), 0, len(head))
    if size < min_run or b > near_end:          # shoda musí být na začátku nového kusu
        return 0
    rest = len(tail) - (a + size)
    if rest > near_end:
        return 0
    return min(len(head), b + size + rest)


def stitch(chunks: list[dict], tail_words: int = 80, head_words: int = 80) -> tuple[list[dict], int]:
    """chunks: [{index, start, end, segments: [RawSegment]}] seřazené. Vrací (segmenty, počet odstraněných slov).

    Řez je v polovině překryvu. Segment nového kusu se ponechá, pokud jeho konec
    přesahuje řez (dlouhý segment začínající v překryvu se tedy neztratí); slova
    zopakovaná na švu se pak odstraní porovnáním konce předchozího kusu se začátkem nového.
    """
    out: list[dict] = []
    removed = 0
    for n, ch in enumerate(chunks):
        lo = -1e9 if n == 0 else (ch["start"] + chunks[n - 1]["end"]) / 2
        hi = 1e9 if n == len(chunks) - 1 else (chunks[n + 1]["start"] + ch["end"]) / 2
        kept = []
        for s in ch["segments"]:
            a, e = ch["start"] + s.start, ch["start"] + s.end
            if a < hi and (a >= lo or e > lo + 1.0):
                kept.append({"start": max(a, lo) if a < lo else a, "end": e, "speaker": s.speaker,
                             "text": s.text, "chunk": ch["index"]})
        if out and kept:
            tail = [norm_word(w) for w in " ".join(x["text"] for x in out[-6:]).split()][-tail_words:]
            head_raw = " ".join(x["text"] for x in kept[:6]).split()[:head_words]
            k = _seam_cut(tail, [norm_word(w) for w in head_raw])
            # pojistka: víc slov, než se dá říct během překryvu (+ rezerva), není šev
            overlap_s = max(0.0, chunks[n - 1]["end"] - ch["start"])
            if k > (overlap_s + 10) * 5:
                k = 0
            if k:
                removed += k
                while k > 0 and kept:
                    ws = kept[0]["text"].split()
                    if len(ws) <= k:
                        k -= len(ws)
                        kept.pop(0)
                    else:
                        kept[0]["text"] = " ".join(ws[k:])
                        k = 0
        out.extend(kept)
    for i, s in enumerate(out):
        s["i"] = i
    return out, removed


def align_to_vtt(segments: list[dict], vtt_words: list[Word], chunk_spans: dict[int, tuple[float, float]],
                 window: float = 45, min_confidence: float = 0.5) -> None:
    """Doplní do segmentů `time_source`, `start/end`, `llm_start/llm_end`, `alignment` (na místě)."""
    for s in segments:
        s["llm_start"], s["llm_end"] = round(s["start"], 2), round(s["end"], 2)
        s["time_source"] = "llm"
        s["alignment"] = None
    if not vtt_words:
        return

    by_chunk: dict[int, list[dict]] = {}
    for s in segments:
        by_chunk.setdefault(s["chunk"], []).append(s)

    for ci, segs in by_chunk.items():
        c0, c1 = chunk_spans.get(ci, (segs[0]["start"], segs[-1]["end"]))
        vwin = [(k, w) for k, w in enumerate(vtt_words) if c0 - window <= w.start <= c1 + window]
        if not vwin:
            continue
        g_tokens: list[tuple[int, str]] = []   # (index segmentu, slovo)
        for s in segs:
            for w in s["text"].split():
                nw = norm_word(w)
                if nw:
                    g_tokens.append((s["i"], nw))
        v_norm = [norm_word(w.text) for _, w in vwin]
        sm = SequenceMatcher(None, [t for _, t in g_tokens], v_norm, autojunk=False)
        match: dict[int, int] = {}
        for a, b, size in sm.get_matching_blocks():
            for d in range(size):
                match[a + d] = vwin[b + d][0]
        counts: dict[int, int] = {}
        for si, _ in g_tokens:
            counts[si] = counts.get(si, 0) + 1
        pos = 0
        for s in segs:
            n_words = counts.get(s["i"], 0)
            idx = range(pos, pos + n_words)
            pos += n_words
            hits = [match[g] for g in idx if g in match]
            conf = len(hits) / n_words if n_words else 0.0
            if hits and conf >= min_confidence and (len(hits) >= 2 or n_words <= 2):
                s["start"] = round(vtt_words[hits[0]].start, 2)
                s["end"] = round(max(vtt_words[hits[-1]].end, vtt_words[hits[0]].start), 2)
                s["time_source"] = "aligned_vtt"
                s["alignment"] = {"source": "vtt", "confidence": round(conf, 3),
                                  "vtt_words": [hits[0], hits[-1]]}

    # 1) konzistence pořadí: rozhodují jen zarovnané segmenty (čas LLM může být úplně mimo)
    last = -1.0
    for s in segments:
        if s["time_source"] != "aligned_vtt":
            continue
        if s["start"] + 2.0 < last:
            s["start"], s["end"] = s["llm_start"], s["llm_end"]
            s["time_source"], s["alignment"] = "llm", None
        else:
            last = s["start"]
    # 2) nezarovnané úseky mezi zarovnanými dopočítat (poměrně podle počtu slov)
    interpolate_unaligned(segments)


def interpolate_unaligned(segments: list[dict]) -> None:
    anchors = [k for k, s in enumerate(segments) if s["time_source"] == "aligned_vtt"]
    if not anchors:
        return
    k = 0
    while k < len(segments):
        if segments[k]["time_source"] == "aligned_vtt":
            k += 1
            continue
        j = k
        while j < len(segments) and segments[j]["time_source"] != "aligned_vtt":
            j += 1
        run = segments[k:j]
        prev = segments[k - 1] if k > 0 else None
        nxt = segments[j] if j < len(segments) else None
        if prev and nxt:
            t0, t1 = prev["end"], max(prev["end"], nxt["start"])
            weights = [max(1, len(x["text"].split())) for x in run]
            total = sum(weights)
            t = t0
            for x, wgt in zip(run, weights):
                dur = (t1 - t0) * wgt / total
                x["start"], x["end"] = round(t, 2), round(t + dur, 2)
                x["time_source"] = "interpolated"
                t += dur
        else:
            # okraj: posunout čas LLM o odchylku nejbližší kotvy
            ref = nxt or prev
            shift = ref["start"] - ref["llm_start"]
            for x in run:
                x["start"] = round(max(0.0, x["llm_start"] + shift), 2)
                x["end"] = round(max(x["start"], x["llm_end"] + shift), 2)
                x["time_source"] = "interpolated"
        k = j
