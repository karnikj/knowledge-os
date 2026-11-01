"""Poskytovatelé STT a analýzy za jednotným rozhraním (PLAN §3, §4).

Pipeline zná jen `STTProvider`; zda je za ním Gemini, Whisper nebo něco jiného,
je konfigurace. Výměna modelu nemění zbytek pipeline.
"""
from pipeline.providers.base import RawSegment, STTContext, STTProvider  # noqa: F401
