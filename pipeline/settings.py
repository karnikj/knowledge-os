"""Konfigurace pipeline: 99_System/config/pipeline.yaml (prahy, limity, modely).

Výchozí hodnoty jsou zde; soubor v configu je jen přepisuje. Limity free tieru
nejsou architektonické konstanty (PLAN §3) — mění se tady.
"""
from __future__ import annotations

import copy
import os
from typing import Any

import yaml

from pipeline.config import Vault

DEFAULTS: dict[str, Any] = {
    "stt": {
        "model": "gemini-2.5-flash",
        "fallback_models": [],
        "chunk_seconds": 300,
        "overlap_seconds": 5,
        "min_interval_seconds": 7,      # pauza mezi voláními (RPM free tieru)
        "max_retries": 6,               # 8, 16, 32, 64, 128 s
        "backoff_seconds": 8,
        "audio_bitrate": "32k",
    },
    "align": {
        "min_confidence": 0.5,          # pod tím zůstává time_source = llm
        "window_seconds": 45,           # okno VTT kolem kusu audia
    },
    "quality": {
        "silence_db": -35,
        "silence_min_seconds": 2.0,
        "max_unexplained_gap_seconds": 45,
        "speech_coverage_ok": 0.90,        # hudba se v detekci ticha počítá jako řeč
        "speech_coverage_min": 0.75,
        "vtt_word_ratio_ok": 0.90,
        "vtt_word_ratio_min": 0.80,
        "alignment_confidence_ok": 0.60,
        "chunk_vtt_ratio_min": 0.80,     # kus s méně slovy než 80 % titulků = useknutá odpověď
        "chunk_time_coverage_min": 0.80,  # bez titulků: poslední segment musí končit za 80 % kusu
        "chunk_retries": 1,
    },
    "analysis": {
        "model": "gemini-2.5-flash",
        "fallback_models": [],
        "window_chars": 24000,          # ~15–20 min řeči na jedno volání
        "ground_window": 3,
        "min_interval_seconds": 7,
        "max_retries": 6,               # 8, 16, 32, 64, 128 s
        "backoff_seconds": 8,
    },
    "render": {
        "paragraph_max_seconds": 45,
    },
    # značka v hlavičce zápisu ze schůzky a v patičce PDF
    "branding": {
        "owner": "Knowledge OS",
        "subtitle": "",
        "logo": "/static/logo.svg",
    },
    # hledání podle významu (hybridní hledání): lokální vícejazyčný model, nic neodchází ven
    "vectors": {
        "enabled": True,
        "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "cache_dir": "~/.cache/fastembed",
        "threads": 2,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(vault: Vault | None = None) -> dict[str, Any]:
    vault = vault or Vault.default()
    p = vault.config_dir / "pipeline.yaml"
    over = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    cfg = _merge(DEFAULTS, over or {})
    if os.getenv("KOS_STT_MODEL"):
        cfg["stt"]["model"] = os.environ["KOS_STT_MODEL"]
    return cfg
