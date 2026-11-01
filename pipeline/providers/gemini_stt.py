"""STT přes Gemini (free tier): jeden kus audia → segmenty s časy a mluvčími.

- audio jde inline (kus 300 s opus 32 kbps ≈ 1,2 MB), bez File API,
- `response_schema` vynutí JSON pole segmentů,
- sekvenční volání s minimálním odstupem + retry s exponenciálním odkladem.
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path

from pipeline.providers.base import RawSegment, STTContext
from pipeline.timefmt import mmss, parse_clock

PROMPT_VERSION = 2   # v2: časy jako MM:SS (Gemini s nimi pracuje přesněji než se sekundami)

PROMPT = """Jsi přepisovač. Přepiš toto audio DOSLOVNĚ v jazyce, kterým se mluví (převážně čeština).

Pravidla:
- Nic nezkracuj, neshrnuj, nepřeformulovávej a nevynechávej. Zachovej výplňková slova (ehm, jako, prostě), opakování i opravy.
- Doplň jen interpunkci a velká písmena.
- Rozděl text na segmenty po jednotlivých promluvách, max. cca 30 sekund; při změně mluvčího vždy nový segment.
- `start` a `end` ve formátu MM:SS od začátku TOHOTO úseku audia (00:00 až {duration_mmss}). Musí být rostoucí a odpovídat skutečnému okamžiku, kdy zazní první a poslední slovo segmentu. Časy odvozuj z audia, ne z počtu slov.
- `speaker`: jméno mluvčího, pokud z audia nebo z kontextu jednoznačně plyne (představí se, oslovení, jméno v názvu nebo popisu videa); jinak "Mluvčí 1", "Mluvčí 2" … Stejnou osobu označuj vždy stejně. Nikdy si jméno nevymýšlej.
- Hudbu, jingly a ticho nepřepisuj. Nesrozumitelné místo označ [nesrozumitelné].

Kontext (jen pro určení jmen a odborných výrazů, NEPŘEPISUJ ho):
- Název: {title}
- Kanál: {channel}
- Popis: {description}
- Mluvčí z předchozích úseků: {speakers}
- Správný pravopis názvů, které mohou zaznít (piš je přesně takto): {terms}
- Toto je úsek {part} z {parts} (začíná v čase {offset} originálu). Konec předchozího úseku: „{tail}“
"""

SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "start": {"type": "STRING", "description": "MM:SS od začátku úseku"},
            "end": {"type": "STRING", "description": "MM:SS od začátku úseku"},
            "speaker": {"type": "STRING"},
            "text": {"type": "STRING"},
        },
        "required": ["start", "end", "speaker", "text"],
    },
}


class RateLimiter:
    def __init__(self, min_interval: float, clock=time.monotonic, sleep=time.sleep):
        self.min_interval = min_interval
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            delta = self._clock() - self._last
            if delta < self.min_interval:
                self._sleep(self.min_interval - delta)
        self._last = self._clock()


class BillingError(RuntimeError):
    """Vyčerpaný předplacený kredit / chybějící billing (HTTP 402) — opakování nepomůže."""


class QuotaExhausted(RuntimeError):
    """Denní limit bezplatné kvóty vyčerpán u všech modelů — worker to zkusí znovu později sám."""


def classify(err: Exception) -> str | None:
    """Druh chyby Gemini: billing | daily_quota | rate | missing | overloaded | transient | None (neopakovat)."""
    s = str(err)
    low = s.lower()
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if code == 402 or "prepayment" in low or "credits are depleted" in low:
        return "billing"
    if code == 429 or "429" in s or "resource_exhausted" in low:
        # denní limit free tieru (GenerateRequestsPerDayPerProjectPerModel-FreeTier) ≠ minutový limit
        return "daily_quota" if ("perday" in low or "per day" in low) else "rate"
    if "404" in s and "model" in low:
        return "missing"
    if code == 503 or "503" in s or "unavailable" in low or "high demand" in low or "overloaded" in low:
        return "overloaded"
    if code in (408, 500, 502, 504) or any(k in low for k in ("deadline", "timeout", "timed out", "500 ",
                                                                "502", "504", "internal", "connection")):
        return "transient"
    return None


def _retryable(err: Exception) -> bool:          # zpětná kompatibilita
    k = classify(err)
    if k == "billing":
        raise BillingError("Gemini: vyčerpaný kredit nebo problém s platbou u API klíče "
                           "(AI Studio → Billing). Zpracování zastaveno.") from err
    return k in ("rate", "overloaded", "transient", "daily_quota")


def _retry_hint(err: Exception) -> float:
    m = re.search(r"retry in ([0-9.]+)s", str(err)) or re.search(r"retryDelay'?: '?([0-9.]+)s", str(err))
    return float(m.group(1)) if m else 0.0


def handle_error(provider, err: Exception, attempt: int, delay: float) -> float | None:
    """Rozhodne, co dál po chybě volání. Vrací None = přepnuto na záložní model (hned znovu),
    číslo = počkat tolik sekund a zkusit znovu. Jinak vyhodí výjimku.

    Politika: přetížení/výpadek → 8, 16, 32, 64, 128 s (exponenciálně), od 2. pokusu záložní model;
    minutový limit → počkat, kolik Google řekne; denní limit → hned záložní model, když dojdou → QuotaExhausted.
    """
    kind = classify(err)
    if kind == "billing":
        raise BillingError("Gemini: vyčerpaný kredit nebo problém s platbou u API klíče "
                           "(AI Studio → Billing). Zpracování zastaveno.") from err
    if kind in ("daily_quota", "missing") or (kind in ("overloaded", "transient") and attempt >= 2):
        if provider.fallbacks:
            old, provider.engine = provider.engine, provider.fallbacks.pop(0)
            why = {"daily_quota": "denní limit vyčerpán", "missing": "nedostupný"}.get(kind, "přetížený")
            print(f"  Gemini: model {old} {why} → přepínám na {provider.engine}")
            return None
        if kind == "daily_quota":
            raise QuotaExhausted("Gemini: denní bezplatný limit je vyčerpaný u všech modelů. "
                                 "Zpracování počká a zkusí se znovu automaticky.") from err
    if kind is None or kind == "missing" or attempt >= provider.max_retries:
        raise err
    return max(delay, _retry_hint(err) + 1) if kind == "rate" else delay


def record_gemini_usage(provider, resp, audio_seconds: float = 0.0) -> None:
    """Zapíše spotřebu volání Gemini (bezplatný klíč → cena 0) do měsíční evidence."""
    from pipeline import usage
    um = getattr(resp, "usage_metadata", None)
    pin = getattr(um, "prompt_token_count", 0) or 0
    out = (getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0)
    row = usage.record("gemini", provider.engine, getattr(provider, "purpose", ""), pin, out, 0.0, audio_seconds)
    tr = getattr(provider, "tracker", None)
    if tr is not None:
        tr.add(row)


def call_with_retries(provider, fn, on_bad_json=None):
    """Společná smyčka volání pro přepis i analýzu."""
    delay, attempt, switches = provider.backoff, 1, 0
    while True:
        provider.limiter.wait()
        try:
            return fn()
        except json.JSONDecodeError:
            if on_bad_json is None or on_bad_json(attempt):
                raise
            attempt += 1
            continue
        except Exception as e:  # noqa: BLE001
            wait = handle_error(provider, e, attempt, delay)
            if wait is None:                      # přepnuto na jiný model — pokusy od začátku
                switches += 1
                if switches > 10:
                    raise
                delay, attempt = provider.backoff, 1
                continue
            print(f"  Gemini ({provider.engine}): {str(e)[:100]} → čekám {wait:.0f} s "
                  f"(pokus {attempt}/{provider.max_retries})")
            provider._sleep(wait + random.uniform(0, min(3, wait / 4)))
            delay *= 2
            attempt += 1


def parse_response(text: str, duration: float) -> list[RawSegment]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("segments", [])
    segs: list[RawSegment] = []
    for d in data:
        t = str(d.get("text", "")).strip()
        if not t:
            continue
        s = max(0.0, parse_clock(d.get("start", 0)))
        e = parse_clock(d.get("end", s))
        if duration:
            s, e = min(s, duration), min(max(e, s), duration)
        segs.append(RawSegment(s, e, str(d.get("speaker") or "Mluvčí 1").strip(), t))
    segs.sort(key=lambda x: x.start)
    return segs


class GeminiSTT:
    prompt_version = PROMPT_VERSION

    def __init__(self, model: str = "gemini-2.5-flash", min_interval: float = 7,
                 max_retries: int = 6, backoff: float = 8, client=None, sleep=time.sleep,
                 fallbacks: list[str] | None = None):
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
            self._client = genai.Client(api_key=key, http_options={"timeout": 300000})  # ms; zaseknutý požadavek → nový pokus / záložní model
        return self._client

    def build_prompt(self, ctx: STTContext) -> str:
        return PROMPT.format(
            duration_mmss=mmss(ctx.chunk_duration), title=ctx.title or "-", channel=ctx.channel or "-",
            description=(ctx.description or "-")[:600].replace("\n", " "),
            speakers=", ".join(ctx.known_speakers) or "zatím žádní",
            terms=", ".join(getattr(ctx, "known_terms", []) or []) or "-",
            part=ctx.chunk_index + 1, parts=ctx.chunk_count,
            offset=mmss(ctx.chunk_start),
            tail=(ctx.previous_text_tail or "")[-200:],
        )

    def transcribe(self, chunk: Path, ctx: STTContext) -> list[RawSegment]:
        from google.genai import types

        audio = Path(chunk).read_bytes()
        contents = [types.Part.from_bytes(data=audio, mime_type="audio/ogg"), self.build_prompt(ctx)]
        config = types.GenerateContentConfig(
            temperature=0.0, response_mime_type="application/json", response_schema=SCHEMA)
        def once():
            resp = self.client.models.generate_content(model=self.engine, contents=contents, config=config)
            record_gemini_usage(self, resp, ctx.chunk_duration)
            return parse_response(resp.text, ctx.chunk_duration)
        return call_with_retries(self, once, on_bad_json=lambda attempt: attempt >= self.max_retries)

