"""Fronta YouTube URL: 99_System/queue.txt (jeden řádek = jedna URL, # = komentář).

Soubor je v Gitu, takže URL lze přidat z kteréhokoli PC; VPS (F3) frontu zpracuje.
"""
from __future__ import annotations

from pipeline import catalog, ids
from pipeline.config import Vault

from pathlib import Path


def local_file(vault: Vault) -> Path:
    """Videa, která VPS nestáhne (YouTube blokuje datacentrum) → zpracuje PC: `pipeline worker --local`."""
    return vault.system / "queue_local.txt"


def read(vault: Vault, path: Path | None = None) -> list[str]:
    path = path or vault.queue_file
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.lstrip().startswith("#")]


def add(vault: Vault, url: str, path: Path | None = None, force: bool = False) -> str:
    path = path or vault.queue_file
    vid = ids.youtube_id(url)
    if not vid:
        return f"Nerozpoznaná YouTube URL: {url}"
    norm = ids.youtube_url(vid)
    from pipeline import remove
    if ids.make("yt", vid) in remove.removed_ids(vault):
        if not force:
            return f"yt:{vid} jste dříve odstranil ze znalostní báze – nepřidáno (znovu přidat: --force)."
        remove.unblock(vault, ids.make("yt", vid))
    if catalog.is_known(vault, ids.make("yt", vid)):
        return f"yt:{vid} už ve vaultu je – nepřidáno."
    if any(ids.youtube_id(u) == vid for u in read(vault, path)):
        return f"yt:{vid} už ve frontě je."
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(norm + "\n")
    return f"Přidáno do fronty: {norm}"


def remove(vault: Vault, url: str, path: Path | None = None) -> None:
    path = path or vault.queue_file
    vid = ids.youtube_id(url)
    keep = [l for l in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
            if ids.youtube_id(l) != vid]
    path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")


def move_to_front(vault: Vault, url: str) -> bool:
    """Přesune URL na začátek fronty (tlačítko „Spustit přepis znovu“)."""
    items = read(vault)
    vid = ids.youtube_id(url)
    hit = [u for u in items if ids.youtube_id(u) == vid]
    if not hit:
        return False
    rest = [u for u in items if ids.youtube_id(u) != vid]
    vault.queue_file.write_text("\n".join(hit[:1] + rest) + "\n", encoding="utf-8")
    return True
