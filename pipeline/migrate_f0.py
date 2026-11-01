"""F0 migrace existujících karet (idempotentní).

- doplní `source_id`, `source_version`, `video_id`, `url`, `asset_path` do frontmatteru,
- nahradí absolutní odkaz `file:///C:/.../audio.m4a` relativním embedem `![[{id}.m4a]]`.
Tělo karet (analýza, claims, přepis) se nemění.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pipeline import frontmatter, ids
from pipeline.config import Vault
from pipeline.legacy_v1 import identity_fields

_AUDIO_LINK = re.compile(
    r"(\*\s+\*\*Lokální audio stopa\*\*:)\s*\n?\[Přehrát lokální audio\]\(file:///[^)]*\)[ \t]*\n?"
)


@dataclass
class Change:
    path: Path
    source_id: str | None
    changed: bool
    note: str = ""


def migrate_text(text: str) -> tuple[str, str | None, str]:
    fm = frontmatter.parse(text)
    if text.startswith("---") and not fm:
        return text, None, "NEPLATNÝ YAML frontmatter – nezměněno, opravit ručně"
    vid = fm.get("video_id") or ids.youtube_id(text)
    if not vid:
        return text, None, "bez YouTube ID – přeskočeno"
    url = ids.youtube_url(vid)
    fields = identity_fields(vid, url)
    # nepřepisovat verzi, pokud už karta nějakou má
    if fm.get("source_version"):
        fields["source_version"] = fm["source_version"]
    new = frontmatter.set_fields(text, fields, after="typ")
    new = _AUDIO_LINK.sub(lambda m: f"{m.group(1)} ![[{vid}.m4a]]\n", new)
    return new, fields["source_id"], ""


def run(vault: Vault, dry_run: bool = False) -> list[Change]:
    changes: list[Change] = []
    for p in sorted(vault.sources.glob("*.md")):
        old = p.read_text(encoding="utf-8")
        new, sid, note = migrate_text(old)
        changed = new != old
        if changed and not dry_run:
            p.write_text(new, encoding="utf-8")
        changes.append(Change(p, sid, changed, note))
    return changes
