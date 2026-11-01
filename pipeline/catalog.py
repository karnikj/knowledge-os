"""Katalog zdrojů odvozený z frontmatteru karet (nahrazuje history.db).

Pravda je v Markdownu (Principle 1): katalog se vždy postaví znovu skenem
`01_Zdroje/` a `14_Internal/`. Díky tomu deduplikace funguje na každém PC
i na VPS bez sdílení databáze.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline import frontmatter
from pipeline.config import Vault


@dataclass
class Entry:
    source_id: str | None
    path: Path
    title: str | None
    published_at: str | None
    source_type: str | None
    url: str | None


def _cards(vault: Vault):
    for folder in (vault.sources, vault.internal):
        if folder.exists():
            yield from sorted(folder.rglob("*.md"))


def scan(vault: Vault) -> list[Entry]:
    out: list[Entry] = []
    for p in _cards(vault):
        with open(p, encoding="utf-8") as f:
            head = f.read(8192)   # frontmatter je vždy na začátku
        fm = frontmatter.parse(head)
        if not fm:
            continue
        out.append(Entry(
            source_id=fm.get("source_id"),
            path=p,
            title=fm.get("title") or p.stem,
            published_at=str(fm["published_at"]) if fm.get("published_at") else None,
            source_type=fm.get("source_type"),
            url=fm.get("url"),
        ))
    return out


def find(vault: Vault, source_id: str) -> Entry | None:
    return next((e for e in scan(vault) if e.source_id == source_id), None)


def is_known(vault: Vault, source_id: str) -> bool:
    return find(vault, source_id) is not None


def duplicates(vault: Vault) -> dict[str, list[Path]]:
    seen: dict[str, list[Path]] = {}
    for e in scan(vault):
        if e.source_id:
            seen.setdefault(e.source_id, []).append(e.path)
    return {k: v for k, v in seen.items() if len(v) > 1}
