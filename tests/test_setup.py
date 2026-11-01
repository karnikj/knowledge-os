"""Průvodce instalací: bez otázek, bez sítě."""
import shutil
from pathlib import Path

from pipeline import setup
from pipeline.config import Vault


def test_setup_non_interactive_creates_env_and_dirs(tmp_path, monkeypatch, capsys):
    root = tmp_path / "vault"
    root.mkdir()
    (root / ".env.example").write_text("OPENROUTER_API_KEY=\nGEMINI_API_KEY=\n", encoding="utf-8")
    (root / "99_System" / "config").mkdir(parents=True)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    v = Vault(root)
    rc = setup.main(v, non_interactive=True)
    out = capsys.readouterr().out
    assert (root / ".env").exists() and "OPENROUTER_API_KEY=" in (root / ".env").read_text(encoding="utf-8")
    assert (root / "14_Internal" / "VoiceNotes").is_dir() and (root / "99_System" / "index" / "kos.sqlite").exists()
    assert "průvodce instalací" in out and ("Hotovo" in out or "✗" in out)
    assert rc in (0, 1)


def test_setup_detects_existing_key(tmp_path, capsys):
    root = tmp_path / "vault"
    (root / "99_System" / "config").mkdir(parents=True)
    (root / ".env").write_text("OPENROUTER_API_KEY=x\n", encoding="utf-8")
    setup.setup_env(Vault(root), non_interactive=True)
    assert "klíč k AI modelům je v .env" in capsys.readouterr().out
