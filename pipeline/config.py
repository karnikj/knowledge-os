"""Cesty ve vaultu. Vše je relativní ke kořeni vaultu, nikdy k aktuálnímu adresáři.

Kořen = proměnná prostředí KOS_ROOT, jinak nadřazená složka balíčku `pipeline/`.
Testy si vytvářejí vlastní `Vault(tmp_path)`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Vault:
    root: Path

    @classmethod
    def default(cls) -> "Vault":
        env = os.getenv("KOS_ROOT")
        root = Path(env) if env else Path(__file__).resolve().parent.parent
        return cls(root.resolve())

    # --- složky vaultu -------------------------------------------------
    @property
    def sources(self) -> Path:          # karty zdrojů
        return self.root / "01_Zdroje"

    @property
    def internal(self) -> Path:
        return self.root / "14_Internal"

    @property
    def assets(self) -> Path:
        return self.root / "00_Assets"

    @property
    def audio(self) -> Path:
        return self.assets / "audio"

    @property
    def audio_opus(self) -> Path:
        return self.assets / "audio_opus"

    @property
    def transcripts(self) -> Path:
        return self.assets / "transcripts"

    @property
    def captions(self) -> Path:
        return self.assets / "captions"

    @property
    def documents(self) -> Path:
        return self.assets / "documents"

    @property
    def system(self) -> Path:
        return self.root / "99_System"

    @property
    def state_dir(self) -> Path:
        return self.system / "state"

    @property
    def index_dir(self) -> Path:
        return self.system / "index"

    @property
    def prompts(self) -> Path:
        return self.system / "prompts"

    @property
    def config_dir(self) -> Path:
        return self.system / "config"

    @property
    def queue_file(self) -> Path:
        return self.system / "queue.txt"

    INBOXES = ("15_Inbox_Text", "16_Inbox_Dokumenty", "17_Inbox_Prepisy", "18_Inbox_Hlas")

    def required_dirs(self) -> list[Path]:
        return [
            self.sources, self.internal, self.audio, self.transcripts, self.captions,
            self.documents, self.state_dir, self.index_dir, self.prompts, self.config_dir,
            *[self.root / d for d in self.INBOXES],
        ]

    def rel(self, path: Path) -> str:
        """Cesta relativní ke kořeni, vždy s lomítky (stejná na Windows i Linuxu)."""
        return Path(path).resolve().relative_to(self.root).as_posix()
