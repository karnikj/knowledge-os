"""Identita zdrojů: `source_id = namespace:stabilní_id` (PLAN §2)."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

_YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)

NAMESPACES = ("yt", "file", "notta", "voice", "note", "import", "meeting")


def youtube_id(url_or_text: str) -> str | None:
    m = _YT_RE.search(url_or_text or "")
    return m.group(1) if m else None


def youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def make(namespace: str, ident: str) -> str:
    if namespace not in NAMESPACES:
        raise ValueError(f"Neznámý namespace source_id: {namespace}")
    if not ident or ":" in ident:
        raise ValueError(f"Neplatný identifikátor: {ident!r}")
    return f"{namespace}:{ident}"


def file_hash(path: Path, length: int | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    d = h.hexdigest()
    return d[:length] if length else d


def safe_filename(source_id: str) -> str:
    """source_id → jméno souboru použitelné i na Windows (`:` je zakázaná)."""
    return source_id.replace(":", "__")
