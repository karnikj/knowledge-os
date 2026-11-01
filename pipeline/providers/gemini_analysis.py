"""Analýza přepisu přes Gemini (text → JSON). Rozhraní AnalysisProvider:

    analyze_window(lines, ctx) -> dict      # kapitoly, tvrzení, zmínky, krátké shrnutí úseku
    summarize(ctx, window_summaries, chapters) -> dict   # TL;DR, klíčové body, volatilita

Model dostává přepis jako řádky `[s12 04:31 Jan Novák] text`. Časy nikdy nevrací —
jen čísla segmentů; čas doplní Python ze segmentu (AD-009, AD-012).
"""
from __future__ import annotations

import json
import os
import random
import time

from pipeline.entities import TYPES
from pipeline.providers.gemini_stt import RateLimiter, call_with_retries, record_gemini_usage

PROMPT_VERSION = 2   # v2: pojmenování obecných mluvčích, témata ne jako konkrétní entity

WINDOW_PROMPT = """Analyzuješ úsek doslovného přepisu ({kind}) pro osobní znalostní bázi. Výstup česky.
Zdroj: „{title}“ ({channel}, {date}). Úsek {part} z {parts}.

Každý řádek přepisu začíná [s<číslo> čas mluvčí]. Odkazuj VŽDY čísly segmentů (celé číslo bez „s“), nikdy časem.

Vrať:
1. `summary`: 2–4 věty, o čem úsek je.
2. `chapters`: tematické části úseku (obvykle 1–4): `segment` (kde část začíná) a výstižný `title`.
3. `claims`: podstatná tvrzení, názory, předpovědi, doporučení, hypotézy a ověřitelná fakta mluvčích.
   - `quote`: DOSLOVNÝ úryvek z přepisu (5–25 slov), přesně jak je napsán, beze změn.
   - `segment_ids`: segmenty, kde úryvek zazněl. `speaker`: jméno mluvčího z řádku.
   - `claim`: stručná věcná parafráze (1 věta). `type`: fact | opinion | prediction | recommendation | hypothesis.
   - `volatility`: low | medium | high (jak rychle může zastarat).
   - `attitudes`: postoj mluvčího k pojmenovaným entitám v tvrzení: `target` + `polarity` positive | neutral | negative | mixed.
   Vynech zdvořilosti, organizační poznámky a opakování.
4. `mentions`: KAŽDÁ zmínka pojmenované entity v úseku (i opakovaná v jiném segmentu):
   - `segment`, `surface` = přesně tak, jak je v textu (včetně skloňování nebo chyby přepisu),
   - `name` = kanonický název (1. pád, správný pravopis, např. „Antropic“ → „Anthropic“, „Pandasu“ → „pandas“),
   - `type`: {types}.
   Typ `tema` použij jen pro hlavní obecná témata (max 6 za úsek). Produkt, firmu, osobu apod. nikdy neoznačuj jako téma.
   Mluvčí sami o sobě nejsou zmínka, pokud je nikdo nejmenuje. Nevymýšlej entity, které v textu nejsou.
5. `speaker_names`: pro mluvčí označené obecně („Mluvčí 1“ …) uveď skutečné jméno, jen pokud z přepisu
   nebo kontextu jednoznačně plyne (představí se, osloví ho jménem, jde o autora kanálu v monologu).
   Jinak je vynech. Nikdy nehádej.
{known}
PŘEPIS:
{lines}
"""

SUMMARY_PROMPT = """Z dílčích shrnutí sestav celkový přehled zdroje „{title}“ ({channel}, {date}). Výstup česky, věcně.
Vrať `tldr` (odstavec 3–5 vět), `key_points` (4–8 bodů, každý „**Název**: vysvětlení“),
`topics` (3–6 hlavních obecných témat, 1. pád; ne názvy produktů či firem, ne synonyma téhož)
a `dominant_volatility` (low | medium | high).
Nepřidávej nic, co ve shrnutích není.

KAPITOLY:
{chapters}

SHRNUTÍ ÚSEKŮ:
{summaries}
"""

_STR = {"type": "STRING"}
WINDOW_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": _STR,
        "chapters": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "segment": {"type": "INTEGER"}, "title": _STR}, "required": ["segment", "title"]}},
        "claims": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "speaker": _STR, "segment_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
            "quote": _STR, "claim": _STR,
            "type": {"type": "STRING", "enum": ["fact", "opinion", "prediction", "recommendation", "hypothesis"]},
            "volatility": {"type": "STRING", "enum": ["low", "medium", "high"]},
            "attitudes": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "target": _STR, "polarity": {"type": "STRING", "enum": ["positive", "neutral", "negative", "mixed"]}},
                "required": ["target", "polarity"]}}},
            "required": ["segment_ids", "quote", "claim", "type"]}},
        "speaker_names": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "label": _STR, "name": _STR}, "required": ["label", "name"]}},
        "mentions": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "segment": {"type": "INTEGER"}, "surface": _STR, "name": _STR,
            "type": {"type": "STRING", "enum": list(TYPES)}},
            "required": ["segment", "surface", "name", "type"]}},
    },
    "required": ["summary", "chapters", "claims", "mentions"],
}
SUMMARY_SCHEMA = {
    "type": "OBJECT",
    "properties": {"tldr": _STR, "key_points": {"type": "ARRAY", "items": _STR},
                   "topics": {"type": "ARRAY", "items": _STR},
                   "dominant_volatility": {"type": "STRING", "enum": ["low", "medium", "high"]}},
    "required": ["tldr", "key_points", "topics", "dominant_volatility"],
}


class GeminiAnalysis:
    prompt_version = PROMPT_VERSION

    def __init__(self, model: str = "gemini-2.5-flash", min_interval: float = 7, max_retries: int = 6,
                 backoff: float = 8, client=None, sleep=time.sleep, fallbacks: list[str] | None = None):
        self.engine = model
        self.fallbacks = list(fallbacks or [])
        self._client = client
        self.limiter = RateLimiter(min_interval, sleep=sleep)
        self.max_retries, self.backoff, self._sleep = max_retries, backoff, sleep

    @property
    def client(self):
        if self._client is None:
            from google import genai
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                raise ValueError("Chybí GEMINI_API_KEY (.env v kořeni vaultu).")
            self._client = genai.Client(api_key=key, http_options={"timeout": getattr(self, "timeout_ms", 180000)})  # ms; zaseknutý požadavek → nový pokus / záložní model
        return self._client

    def _call(self, prompt: str, schema: dict) -> dict:
        from google.genai import types
        cfg = types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json",
                                          response_schema=schema, max_output_tokens=32768)
        def once():
            resp = self.client.models.generate_content(model=self.engine, contents=[prompt], config=cfg)
            record_gemini_usage(self, resp)
            return json.loads(resp.text)

        def bad_json(attempt):
            # s temperature 0 dá opakování stejný (useknutý) výstup → po 2 pokusech vzdát,
            # volající úsek rozdělí na menší
            if attempt >= 2:
                return True
            print(f"  Gemini: neplatný JSON (pokus {attempt}) → opakuji")
            return False
        return call_with_retries(self, once, on_bad_json=bad_json)

    def generate(self, prompt: str, schema: dict) -> dict:
        """Obecné volání se strukturovaným výstupem (zápisy ze schůzek aj.)."""
        return self._call(prompt, schema)

    def analyze_window(self, lines: list[str], ctx: dict) -> dict:
        known = ""
        if ctx.get("generic_speakers"):
            known += "Obecně označení mluvčí: " + ", ".join(ctx["generic_speakers"]) + "\n"
        if ctx.get("description"):
            known += "Popis zdroje: " + ctx["description"][:500].replace("\n", " ") + "\n"
        if ctx.get("known_entities"):
            known += ("Známé entity (použij tyto kanonické názvy, pokud jde o ně): "
                     + "; ".join(ctx["known_entities"][:150]) + "\n")
        prompt = WINDOW_PROMPT.format(
            kind=ctx.get("kind", "video"), title=ctx.get("title", ""), channel=ctx.get("channel", ""),
            date=ctx.get("date", ""), part=ctx.get("part", 1), parts=ctx.get("parts", 1),
            types=" | ".join(TYPES), known=known, lines="\n".join(lines))
        return self._call(prompt, WINDOW_SCHEMA)

    def summarize(self, ctx: dict, window_summaries: list[str], chapters: list[str]) -> dict:
        prompt = SUMMARY_PROMPT.format(title=ctx.get("title", ""), channel=ctx.get("channel", ""),
                                       date=ctx.get("date", ""), chapters="\n".join(chapters),
                                       summaries="\n\n".join(window_summaries))
        return self._call(prompt, SUMMARY_SCHEMA)

