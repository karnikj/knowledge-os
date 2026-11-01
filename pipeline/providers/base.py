from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class RawSegment:
    """Segment od STT; časy jsou relativní ke kusu audia."""
    start: float
    end: float
    speaker: str
    text: str


@dataclass
class STTContext:
    title: str = ""
    channel: str = ""
    description: str = ""
    known_speakers: list[str] = field(default_factory=list)
    known_terms: list[str] = field(default_factory=list)     # slovník názvů (pipeline.glossary)
    previous_text_tail: str = ""
    chunk_index: int = 0
    chunk_count: int = 1
    chunk_duration: float = 0.0
    chunk_start: float = 0.0
    language: str = "cs"


class STTProvider(Protocol):
    engine: str        # zapisuje se do stavu a přepisu, např. "gemini-2.5-flash"
    prompt_version: int

    def transcribe(self, chunk: Path, ctx: STTContext) -> list[RawSegment]: ...
