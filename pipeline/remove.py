"""Odstranění zdroje ze znalostní báze (video, hlasová poznámka, dokument).

Smaže vše, co ke zdroji patří: kartu, přepis (JSON + MD), analýzu, zmínky, mezivýsledky (raw),
stav, titulky a audio. Entity, které zmiňoval jen tento zdroj a nikdo je nepotvrdil, zmizí z registru.
Zdroj se zapíše do 99_System/removed.txt, aby ho fronta znovu nepřidala (opakované přidání = vědomé `--force`).

Git: smazání je běžný commit → do 30 dní (a v historii i později) jde vrátit `git revert`.
Úplné vymazání z historie (citlivá nahrávka) je samostatná, ruční operace — viz docs.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from pipeline import catalog, ids
from pipeline.config import Vault
from pipeline.entities import Registry


def removed_file(vault: Vault) -> Path:
    return vault.system / "removed.txt"


def removed_ids(vault: Vault) -> set[str]:
    p = removed_file(vault)
    if not p.exists():
        return set()
    return {l.split("\t")[0] for l in p.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")}


def files_for(vault: Vault, source_id: str) -> list[Path]:
    ns, ident = source_id.split(":", 1)
    base = ids.safe_filename(source_id)
    out: list[Path] = []
    e = catalog.find(vault, source_id)
    if e:
        out.append(e.path)
    out += sorted(vault.transcripts.glob(f"{base}.*"))
    out += [vault.transcripts / "raw" / base, vault.state_dir / f"{base}.json"]
    if ns == "yt":
        out += sorted(vault.audio.glob(f"{ident}.*")) + sorted(vault.audio_opus.glob(f"{ident}*"))
        out += sorted(vault.captions.glob(f"{ident}.*"))
    elif ns == "voice":
        out += sorted((vault.assets / "voice").glob(f"{ident}.*"))
    elif ns == "meeting":
        out += sorted((vault.assets / "meetings").glob(f"{ident}.*"))
        out += [vault.system / "meetings" / f"{ident}.json"]
    elif ns == "file":
        out += sorted(vault.documents.glob(f"{ident}*"))
    return [p for p in out if p.exists()]


def _used_entities(vault: Vault) -> set[str]:
    used: set[str] = set()
    for f in vault.transcripts.glob("*.mentions.json"):
        try:
            used |= {m.get("entity") for m in json.loads(f.read_text(encoding="utf-8")).get("mentions", [])}
        except (ValueError, OSError):
            pass
    return used


def prune_entities(vault: Vault) -> list[str]:
    """Smaže navržené (nepotvrzené) entity bez jediné zmínky, pokud na ně nic neodkazuje jako na rodiče."""
    reg = Registry.load(vault)
    used = _used_entities(vault)
    parents = {e.get("parent") for e in reg.data.values()}
    gone = [k for k, e in reg.data.items()
            if k not in used and k not in parents and e.get("status") != "potvrzeno"]
    for k in gone:
        del reg.data[k]
    if gone:
        reg.save()
    return gone


def remove(vault: Vault, source_id: str, reason: str = "", dry_run: bool = False) -> dict:
    files = files_for(vault, source_id)
    if not files:
        raise FileNotFoundError(f"Zdroj {source_id} ve vaultu není.")
    e = catalog.find(vault, source_id)
    title = e.title if e else ""
    if dry_run:
        return {"source_id": source_id, "title": title, "files": [vault.rel(p) for p in files]}
    restored = _restore_previous_version(vault, source_id)
    for p in files:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    rf = removed_file(vault)
    rf.parent.mkdir(parents=True, exist_ok=True)
    if not rf.exists():
        rf.write_text("# Odstraněné zdroje — fronta je znovu nepřidá (source_id, kdy, název, důvod)\n", encoding="utf-8")
    with open(rf, "a", encoding="utf-8") as f:
        f.write(f"{source_id}\t{datetime.now().isoformat(timespec='seconds')}\t{(title or '')[:150]}\t"
                f"{reason.replace(chr(10), ' ')[:200]}\n")
    pruned = prune_entities(vault)
    from pipeline import index
    try:
        index.build(vault)
    except Exception as ex:  # noqa: BLE001 — index se přestaví při dalším běhu
        print(f"  index: {ex}")
    return {"source_id": source_id, "title": title, "files": [vault.rel(p) for p in files],
            "entities_removed": pruned, "restored_version": restored}


def _restore_previous_version(vault: Vault, source_id: str) -> str | None:
    """Smazání nejnovější verze dokumentu → předchozí verze se vrátí z archivu do hledání a chatu."""
    if not source_id.startswith("file:"):
        return None
    from pipeline import documents
    try:
        old = documents.load(vault, source_id)["meta"].get("version_of")
        if not old or not documents.tpath(vault, old).exists():
            return None
        t = documents.load(vault, old)
        if t["meta"].get("superseded_by") != source_id:
            return None
        for k in ("archived", "superseded_by", "archived_at"):
            t["meta"].pop(k, None)
        documents._save(vault, t)
        try:
            documents.render_card(vault, old)
        except Exception:  # noqa: BLE001
            pass
        return old
    except (OSError, ValueError, KeyError):
        return None


def unblock(vault: Vault, source_id: str) -> None:
    rf = removed_file(vault)
    if rf.exists():
        keep = [l for l in rf.read_text(encoding="utf-8").splitlines() if l.split("\t")[0] != source_id]
        rf.write_text("\n".join(keep) + "\n", encoding="utf-8")
