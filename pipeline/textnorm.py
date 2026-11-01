"""Normalizace slov pro porovnávání (zarovnání, sešití, grounding)."""
from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w]+", re.UNICODE)


def norm_word(w: str) -> str:
    w = unicodedata.normalize("NFKD", w.lower())
    w = "".join(c for c in w if not unicodedata.combining(c))
    return _PUNCT.sub("", w)


def words(text: str) -> list[str]:
    return [n for n in (norm_word(w) for w in text.split()) if n]
