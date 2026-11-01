"""Parser WebVTT, včetně YouTube auto-titulků se slovními časy.

YouTube auto-titulky mají „rolující“ podobu: každý cue zopakuje předchozí řádek
a nová slova nesou inline časy `<00:00:01.500><c> slovo</c>`. Z nich skládáme
**časovou mřížku slov** (slovo, čas začátku). U ručních titulků bez inline časů
se časy slov rozprostřou rovnoměrně po délce cue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pipeline.timefmt import parse_clock

_CUE_TIME = re.compile(r"^(\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})\s+-->\s+"
                       r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})")
_INLINE_TS = re.compile(r"<(\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})>")
_TAG = re.compile(r"</?[^>]+>")


@dataclass
class Word:
    text: str
    start: float
    end: float
    cue: int


@dataclass
class Cue:
    i: int
    start: float
    end: float
    text: str


def _clean(s: str) -> str:
    s = _TAG.sub("", s).replace("&nbsp;", " ").replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
    return re.sub(r"\s+", " ", s).strip()


def parse(text: str) -> tuple[list[Cue], list[Word]]:
    blocks = re.split(r"\r?\n\r?\n", text.replace("﻿", ""))
    raw: list[tuple[float, float, list[str]]] = []
    for b in blocks:
        lines = [l for l in b.splitlines() if l.strip()]
        for k, l in enumerate(lines):
            m = _CUE_TIME.match(l.strip())
            if m:
                raw.append((parse_clock(m.group(1)), parse_clock(m.group(2)), lines[k + 1:]))
                break

    has_inline = any(_INLINE_TS.search(l) for _, _, ls in raw for l in ls)
    cues: list[Cue] = []
    words: list[Word] = []

    if has_inline:
        # YouTube: nová slova jsou jen na řádcích s inline časy
        for s, e, lines in raw:
            for l in lines:
                if not _INLINE_TS.search(l):
                    continue
                ci = len(cues)
                parts = _INLINE_TS.split(l)
                # parts: [text0, ts1, text1, ts2, text2, ...]; text0 začíná v čase cue
                t = s
                seg_words: list[tuple[str, float]] = []
                for j, part in enumerate(parts):
                    if j % 2 == 1:
                        t = parse_clock(part)
                        continue
                    for w in _clean(part).split():
                        seg_words.append((w, t))
                if not seg_words:
                    continue
                for n, (w, ws) in enumerate(seg_words):
                    we = seg_words[n + 1][1] if n + 1 < len(seg_words) else e
                    words.append(Word(w, ws, max(we, ws), ci))
                cues.append(Cue(ci, s, e, " ".join(w for w, _ in seg_words)))
    else:
        prev = ""
        for s, e, lines in raw:
            txt = _clean(" ".join(lines))
            if not txt or txt == prev:
                continue
            prev = txt
            ci = len(cues)
            toks = txt.split()
            step = (e - s) / max(len(toks), 1)
            for n, w in enumerate(toks):
                words.append(Word(w, s + n * step, s + (n + 1) * step, ci))
            cues.append(Cue(ci, s, e, txt))

    # monotónní časy slov (YouTube občas vrátí drobné překryvy)
    for n in range(1, len(words)):
        if words[n].start < words[n - 1].start:
            words[n].start = words[n - 1].start
    return cues, words


def load(path: Path) -> tuple[list[Cue], list[Word]]:
    return parse(Path(path).read_text(encoding="utf-8", errors="replace"))
