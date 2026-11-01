"""Průvodce první instalací: python -m pipeline setup

Projde prostředí, pomůže založit .env s klíčem (zadání skryté, do chatu ani do logu se klíč nedostane),
vytvoří složky vaultu, nabídne ukázková data a poradí, jak spustit aplikaci. Dá se spouštět opakovaně.
Je napsaný pro člověka bez IT znalostí; AI asistent ho může spustit a číst výstup, klíč ale vždy píše člověk.
"""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pipeline.config import Vault

OK, BAD, INFO = "  ✓ ", "  ✗ ", "  → "


def _say(s: str = "") -> None:
    print(s, flush=True)


def _ask(prompt: str, default: str = "") -> str:
    try:
        v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        v = ""
    return v or default


def _yes(prompt: str, default: bool = True) -> str:
    v = _ask(prompt + (" (A/n)" if default else " (a/N)"))
    return default if not v else v.lower().startswith(("a", "y"))


def check_tools() -> bool:
    ok = True
    v = sys.version_info
    if v >= (3, 10):
        _say(OK + f"Python {sys.version.split()[0]}")
    else:
        _say(BAD + f"Python {sys.version.split()[0]} je starý, potřeba 3.10 nebo novější: https://www.python.org/downloads/")
        ok = False
    for tool, hint in (("ffmpeg", "Windows: winget install Gyan.FFmpeg · macOS: brew install ffmpeg · Ubuntu: sudo apt install ffmpeg"),
                       ("git", "https://git-scm.com/downloads")):
        if shutil.which(tool):
            _say(OK + tool)
        else:
            _say(BAD + f"{tool} není nainstalovaný nebo není v PATH. {hint}")
            ok = False
    missing = []
    import importlib.util
    for mod in ("yaml", "fastapi", "uvicorn", "yt_dlp", "rapidfuzz", "httpx", "pymupdf", "fastembed", "dotenv"):
        if importlib.util.find_spec(mod) is None and importlib.util.find_spec("fitz" if mod == "pymupdf" else mod) is None:
            missing.append(mod)
    if missing:
        _say(BAD + f"chybí balíčky Pythonu ({', '.join(missing)}). Spusťte:  pip install -r requirements.txt")
        ok = False
    else:
        _say(OK + "balíčky Pythonu")
    return ok


def setup_env(vault: Vault, non_interactive: bool) -> None:
    env = vault.root / ".env"
    have = {}
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, val = line.partition("=")
                have[k.strip()] = val.strip()
    if have.get("OPENROUTER_API_KEY") or have.get("GEMINI_API_KEY"):
        _say(OK + "klíč k AI modelům je v .env")
        return
    _say(INFO + "Klíč k AI modelům zatím není. Doporučený je OpenRouter: na https://openrouter.ai/keys")
    _say("    se přihlásíte (Google/GitHub/e-mail), nabijete pár dolarů (stačí 5) a vytvoříte klíč (Create key).")
    _say("    Klíč nikomu neposílejte, ani AI asistentovi. Vložíte ho tady; nezobrazí se.")
    if non_interactive or not sys.stdin.isatty():
        _say(INFO + "Zadání klíče v tomto režimu nejde: otevřete soubor .env (vzor .env.example) a doplňte klíč ručně.")
        if not env.exists():
            shutil.copy(vault.root / ".env.example", env)
            _say(OK + "vytvořen prázdný .env ze vzoru")
        return
    key = getpass.getpass("    OPENROUTER_API_KEY (Enter = přeskočit): ").strip()
    gkey = ""
    if not key:
        gkey = getpass.getpass("    GEMINI_API_KEY z https://aistudio.google.com/ (Enter = přeskočit): ").strip()
    if not key and not gkey:
        _say(INFO + "klíč přeskočen; hledání a prohlížení půjde, přepis a chat ne. Doplníte ho kdykoli: python -m pipeline setup")
        if not env.exists():
            shutil.copy(vault.root / ".env.example", env)
        return
    text = (env.read_text(encoding="utf-8") if env.exists() else (vault.root / ".env.example").read_text(encoding="utf-8"))
    lines = [l for l in text.splitlines() if not l.startswith(("OPENROUTER_API_KEY=", "GEMINI_API_KEY="))]
    lines += [f"OPENROUTER_API_KEY={key}", f"GEMINI_API_KEY={gkey}"]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        env.chmod(0o600)
    except OSError:
        pass
    _say(OK + "klíč uložen do .env (soubor je v .gitignore, do Gitu nejde)")


def setup_dirs(vault: Vault) -> None:
    for d in vault.required_dirs() + [vault.system / "logs", vault.system / "meetings", vault.assets / "voice",
                                      vault.assets / "meetings", vault.internal / "VoiceNotes", vault.internal / "Schuzky",
                                      vault.internal / "Dokumenty", vault.sources / "Dokumenty"]:
        d.mkdir(parents=True, exist_ok=True)
    _say(OK + "složky vaultu")


def git_hint(vault: Vault) -> None:
    if not (vault.root / ".git").exists():
        _say(INFO + "Složka není Git repozitář: historie změn a záloha se nevedou. Pro provoz na serveru je Git nutný (viz docs/PRUVODCE.md).")
        return
    r = subprocess.run(["git", "-C", str(vault.root), "remote", "get-url", "origin"], capture_output=True, text=True)
    url = r.stdout.strip()
    if "knowledge-os" in url and url.rstrip("/").endswith(("karnikj/knowledge-os", "karnikj/knowledge-os.git")):
        _say(INFO + "Git ukazuje na veřejnou předlohu. Vaše data do ní nepatří: založte si vlastní SOUKROMÝ repozitář")
        _say("    (docs/PRUVODCE.md, krok „Vlastní repozitář“).")
    elif url:
        _say(OK + f"Git: vlastní repozitář ({url.split('/')[-1]})")


def main(vault: Vault | None = None, non_interactive: bool = False, demo: bool | None = None) -> int:
    vault = vault or Vault.default()
    _say("Knowledge OS: průvodce instalací")
    _say(f"Vault: {vault.root}")
    _say()
    _say("1/4 Nástroje")
    tools_ok = check_tools()
    _say()
    _say("2/4 Klíč k AI modelům")
    setup_env(vault, non_interactive)
    _say()
    _say("3/4 Složky a index")
    setup_dirs(vault)
    git_hint(vault)
    try:
        from pipeline import index
        index.build(vault)
        _say(OK + "vyhledávací index")
    except Exception as e:  # noqa: BLE001
        _say(BAD + f"index se nepodařilo postavit: {e}")
    _say()
    _say("4/4 Ukázková data")
    loader = vault.root / "demo" / "load.py"
    marker = vault.system / "state" / "demo_loaded.json"
    if marker.exists():
        _say(OK + "ukázková data [DEMO] jsou načtená (odstranění: python demo/remove.py)")
    elif loader.exists():
        want = demo if demo is not None else (False if non_interactive or not sys.stdin.isatty()
                                             else _yes("    Načíst ukázková data [DEMO] (smyšlená firma, později jdou smazat)?"))
        if want:
            r = subprocess.run([sys.executable, str(loader)], cwd=vault.root, env={**os.environ, "KOS_ROOT": str(vault.root)})
            _say(OK + "ukázková data načtena" if r.returncode == 0 else BAD + "načtení ukázkových dat selhalo")
        else:
            _say(INFO + "ukázková data přeskočena (kdykoli: python demo/load.py)")
    _say()
    if tools_ok:
        _say("Hotovo. Spuštění aplikace:")
        _say("    python -m pipeline web          → otevřete http://127.0.0.1:8080  (obsazený port: --port 8790)")
        _say("    python -m pipeline worker       → druhé okno: zpracování nahrávek, dokumentů a videí")
        _say("Při prvním spuštění se stáhne model pro hledání podle významu (~120 MB); bez internetu hledání funguje jen fulltextově.")
        _say("Kontrola kdykoli: python -m pipeline doctor")
        return 0
    _say("Nejdřív doplňte, co je označené ✗, a spusťte průvodce znovu: python -m pipeline setup")
    return 1
