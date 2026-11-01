"""Ověření citací tvrzení v přepisu (princip převzat z knowledge_base GroundingEngine, ADR-011).

Kaskáda od nejpřísnější: STRUCTURAL (úryvek je v citovaných segmentech) → EXACT / CANONICAL /
FUZZY v okně ±3 segmentů kolem citovaných → FUZZY v celém přepisu s vyšším prahem (`weak`)
→ UNKNOWN. Deterministické, bez volání LLM. Úspěch vrací skutečné segmenty, kde úryvek je.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from pipeline.czech import stems
from pipeline.textnorm import words as norm_words


@dataclass
class Grounding:
    method: str                  # structural | exact | canonical | fuzzy | weak | unknown
    score: float
    segment_ids: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.method not in ("unknown",)


def _span_text(segs: dict[int, str], ids: list[int]) -> str:
    return " ".join(segs[i] for i in ids if i in segs)


def _contains(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def coverage(quote: str, text: str) -> float:
    """Podíl slov úryvku (po stemmingu), která jsou v textu ve stejném pořadí a blízko u sebe.

    Toleruje vynechaná výplňová slova a drobné odchylky („podle mě“ vypuštěno), ale ne
    slova rozházená po dlouhém textu: shodná slova musí ležet v úseku ≤ 3×délka úryvku + 4.
    """
    from difflib import SequenceMatcher
    q, tx = stems(quote), stems(text)
    if not q or not tx:
        return 0.0
    best = 0.0
    limit = 3 * len(q) + 4
    for start in range(0, max(1, len(tx) - len(q) + 1)):
        win = tx[start:start + limit]
        if not win or win[0] not in q:
            continue
        sm = SequenceMatcher(None, q, win, autojunk=False)
        hit = sum(b.size for b in sm.get_matching_blocks())
        best = max(best, hit / len(q))
        if best >= 1.0:
            break
    return best


def _locate(segs: dict[int, str], ids: list[int], quote: str, threshold: float) -> tuple[float, list[int]]:
    """Najde nejlepší souvislý úsek 1–3 segmentů v `ids` pro úryvek."""
    best, where = 0.0, []
    for L in (1, 2, 3):                       # nejkratší úsek má přednost
        for i in range(len(ids) - L + 1):
            chunk = ids[i:i + L]
            sc = 100 * coverage(quote, _span_text(segs, chunk))
            if sc > best + 0.5:
                best, where = sc, chunk
        if best >= threshold:
            break
    return (best, where) if best >= threshold else (best, [])


def ground(quote: str, cited: list[int], segments: list[dict], window: int = 3,
           fuzzy_threshold: float = 80, weak_threshold: float = 95) -> Grounding:
    segs = {s["i"]: s["text"] for s in segments}
    q = norm_words(quote)
    cited = [c for c in cited if c in segs]
    if not q:
        return Grounding("unknown", 0.0)

    if cited:
        span = _span_text(segs, sorted(cited))
        if _contains(norm_words(span), q):
            return Grounding("structural", 1.0, sorted(cited))
        if _contains(stems(span), stems(quote)):
            return Grounding("structural", 0.97, sorted(cited))

    order = sorted(segs)
    if cited:
        lo, hi = min(cited) - window, max(cited) + window
        near = [i for i in order if lo <= i <= hi]
        for i in near:                                   # exact v jednom segmentu okna
            if _contains(norm_words(segs[i]), q):
                return Grounding("exact", 1.0, [i])
        for i, j in zip(near, near[1:]):                 # canonical přes dva sousední
            if _contains(stems(segs[i] + " " + segs[j]), stems(quote)):
                return Grounding("canonical", 0.95, [i, j])
        sc, where = _locate(segs, near, quote, fuzzy_threshold)
        if where:
            return Grounding("fuzzy", round(sc / 100, 3), where)

    sc, where = _locate(segs, order, quote, weak_threshold)
    if where:
        return Grounding("weak", round(sc / 100, 3), where)
    return Grounding("unknown", round(sc / 100, 3))


def mention_ok(surface: str, segment_text: str) -> bool:
    """Zmínka musí v segmentu skutečně zaznít (tolerance ke skloňování a drobné chybě přepisu)."""
    from pipeline.czech import phrase_in
    if phrase_in(surface, segment_text):
        return True
    s = " ".join(norm_words(surface))
    return len(s) >= 4 and fuzz.partial_ratio(s, " ".join(norm_words(segment_text))) >= 88
