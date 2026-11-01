"""Lehký český stemmer pro vyhledávání a ověřování zmínek.

Záměrně konzervativní (odtrhává jen běžné pádové koncovky), aby „Pandasu“ → „pandas“,
„Anthropicem“ → „anthropic“, „Márou“ → „mar“, ale krátká slova a zkratky zůstala.
Pracuje nad slovy už normalizovanými `textnorm.norm_word` (bez diakritiky, malá písmena).
"""
from __future__ import annotations

from pipeline.textnorm import norm_word

# od nejdelších; po normalizaci bez diakritiky
_SUFFIXES = (
    "atech", "etem", "atum", "ovych", "ovymi", "ovym", "ovou",
    "ech", "ich", "ami", "emi", "imi", "ovi", "ove", "ovy", "ova", "ovu",
    "em", "om", "ou", "am", "ach", "at", "um", "ym", "ho", "mu",
    "a", "e", "i", "o", "u", "y",
)


def stem(word: str) -> str:
    w = norm_word(word)
    if len(w) <= 4 or w.isdigit():
        return w
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def stems(text: str) -> list[str]:
    return [s for s in (stem(w) for w in text.split()) if s]


def phrase_in(phrase: str, text: str) -> bool:
    """Je víceslovná fráze (po stemmingu) obsažena v textu? Tolerantní ke skloňování."""
    p = stems(phrase)
    t = stems(text)
    if not p:
        return False
    n = len(p)
    for i in range(len(t) - n + 1):
        if all(_match(p[k], t[i + k]) for k in range(n)):
            return True
    return False


def _match(a: str, b: str) -> bool:
    if a == b:
        return True
    # skloňování, které stemmer nezachytil: delší = kratší + běžná koncovka (ne libovolný prefix)
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long_.startswith(short) and long_[len(short):] in _SUFFIXES
