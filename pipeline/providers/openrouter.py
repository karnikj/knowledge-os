"""Poskytovatelé přes OpenRouter (jeden klíč → mnoho modelů; cena přesně podle účtování, usage.cost).

  OpenRouterSTT       přepis zvuku modelem s audio vstupem (např. qwen/qwen3.8-omni-flash) — veřejná videa
  OpenRouterAnalysis  analýza textu / chat (např. deepseek/deepseek-v4-flash) — stejné prompty jako Gemini

Chyby se převádějí na text s HTTP kódem, aby fungovala společná politika opakování (gemini_stt.handle_error):
429 → počkat / záložní model, 503 → backoff, 402 → nedostatek kreditu (zastavit).
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from pipeline import usage
from pipeline.providers.base import RawSegment, STTContext
from pipeline.providers.gemini_analysis import GeminiAnalysis
from pipeline.providers.gemini_stt import PROMPT, PROMPT_VERSION, GeminiSTT, RateLimiter, call_with_retries, parse_response

API = "https://openrouter.ai/api/v1"


class OpenRouterError(RuntimeError):
    def __init__(self, code: int, body: str):
        super().__init__(f"{code} {body[:500]}")
        self.code = code


def _key() -> str:
    k = os.getenv("OPENROUTER_API_KEY")
    if not k:
        try:
            from dotenv import load_dotenv
            from pipeline.config import Vault
            load_dotenv(Vault.default().root / ".env")
        except ImportError:
            pass
        k = os.getenv("OPENROUTER_API_KEY")
    if not k:
        raise ValueError("Chybí OPENROUTER_API_KEY (.env v kořeni vaultu).")
    return k


def chat(model: str, messages: list, timeout: int = 300, **extra) -> dict:
    body = {"model": model, "messages": messages, "temperature": 0, "usage": {"include": True}, **extra}
    req = urllib.request.Request(f"{API}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json",
                                          "HTTP-Referer": "https://kos.local", "X-Title": "JK-CML"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            out = json.load(f)
    except urllib.error.HTTPError as e:
        raise OpenRouterError(e.code, e.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise OpenRouterError(504, f"timeout / síť: {e}")
    if out.get("error"):
        err = out["error"]
        raise OpenRouterError(int(err.get("code") or 502), json.dumps(err, ensure_ascii=False))
    return out


def _record(provider, out: dict, audio_seconds: float = 0.0) -> None:
    u = out.get("usage") or {}
    row = usage.record("openrouter", out.get("model") or provider.engine, getattr(provider, "purpose", ""),
                       u.get("prompt_tokens", 0), u.get("completion_tokens", 0), u.get("cost") or 0.0, audio_seconds)
    tr = getattr(provider, "tracker", None)
    if tr is not None:
        tr.add(row)


def _json_from_text(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", t, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _lower_schema(s):
    """Schéma ve stylu Gemini (OBJECT/ARRAY/STRING) → JSON Schema (object/array/string)."""
    if isinstance(s, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else _lower_schema(v)) for k, v in s.items()}
    if isinstance(s, list):
        return [_lower_schema(x) for x in s]
    return s


class OpenRouterSTT(GeminiSTT):
    """Přepis úseku audia přes OpenRouter (model s audio vstupem). Stejný prompt a formát výstupu jako Gemini."""
    prompt_version = PROMPT_VERSION

    def __init__(self, model: str = "qwen/qwen3.8-omni-flash", fallbacks: list[str] | None = None,
                 min_interval: float = 1, max_retries: int = 6, backoff: float = 8, sleep=time.sleep):
        self.engine = model
        self.fallbacks = list(fallbacks or [])
        self.limiter = RateLimiter(min_interval, sleep=sleep)
        self.max_retries, self.backoff, self._sleep = max_retries, backoff, sleep
        self._client = object()

    def transcribe(self, chunk: Path, ctx: STTContext) -> list[RawSegment]:
        with tempfile.TemporaryDirectory() as td:
            mp3 = Path(td) / "c.mp3"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(chunk), "-ac", "1", "-ar", "16000",
                            "-b:a", "48k", str(mp3)], check=True)
            b64 = base64.b64encode(mp3.read_bytes()).decode()
        prompt = self.build_prompt(ctx) + (
            "\nVýstup: POUZE JSON pole objektů {\"start\": \"MM:SS\", \"end\": \"MM:SS\", \"speaker\": \"…\", "
            "\"text\": \"…\"} — bez dalšího textu a bez ```.")

        def once():
            out = chat(self.engine, [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3"}}]}], max_tokens=16000,
                       timeout=180)
            _record(self, out, ctx.chunk_duration)
            pin = (out.get("usage") or {}).get("prompt_tokens") or 0
            if ctx.chunk_duration > 20 and pin < 3 * ctx.chunk_duration:
                # poskytovatel nahrávku tiše zahodil (málo vstupních tokenů) → výsledek by byl smyšlený
                raise OpenRouterError(503, f"UNAVAILABLE: audio nepřijato ({pin} vstupních tokenů na "
                                           f"{ctx.chunk_duration:.0f} s zvuku)")
            text = out["choices"][0]["message"].get("content") or ""
            data = _json_from_text(text)
            return parse_response(json.dumps(data), ctx.chunk_duration)
        return call_with_retries(self, once, on_bad_json=lambda attempt: attempt >= 3)


class OpenRouterAnalysis(GeminiAnalysis):
    """Analýza a chat přes OpenRouter (např. DeepSeek). Prompty a schémata sdílí s GeminiAnalysis."""

    def __init__(self, model: str = "deepseek/deepseek-v4-flash", fallbacks: list[str] | None = None,
                 min_interval: float = 0.5, max_retries: int = 6, backoff: float = 8, sleep=time.sleep,
                 reasoning: bool = False):
        super().__init__(model=model, min_interval=min_interval, max_retries=max_retries, backoff=backoff,
                         client=object(), sleep=sleep, fallbacks=fallbacks)
        self.reasoning = reasoning

    def _call(self, prompt: str, schema: dict, max_tokens: int = 32768) -> dict:
        js = _lower_schema(schema)
        sysmsg = ("Odpovídáš výhradně platným JSON odpovídajícím tomuto JSON Schema (bez dalšího textu):\n"
                  + json.dumps(js, ensure_ascii=False))

        def once():
            extra = {"max_tokens": max_tokens, "response_format": {"type": "json_object"}}
            if not self.reasoning:
                extra["reasoning"] = {"enabled": False}
            out = chat(self.engine, [{"role": "system", "content": sysmsg}, {"role": "user", "content": prompt}],
                       timeout=getattr(self, "timeout_s", 300), **extra)
            _record(self, out)
            return _json_from_text(out["choices"][0]["message"].get("content") or "")

        def bad_json(attempt):
            if attempt >= 2:
                return True
            print(f"  OpenRouter: neplatný JSON (pokus {attempt}) → opakuji")
            return False
        return call_with_retries(self, once, on_bad_json=bad_json)
