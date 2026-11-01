"""`python -m pipeline doctor` — kontrola prostředí a vaultu (PC i VPS)."""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from pipeline import catalog, frontmatter
from pipeline.config import Vault

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class Check:
    name: str
    level: str
    detail: str = ""


def _cmd(args: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=15)
        out = (r.stdout or r.stderr).strip().splitlines()
        return r.returncode == 0, out[0] if out else ""
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def run(vault: Vault, online: bool = False) -> list[Check]:
    c: list[Check] = []
    v = sys.version_info
    c.append(Check("Python ≥ 3.10", OK if v >= (3, 10) else FAIL, sys.version.split()[0]))

    for tool in ("ffmpeg", "ffprobe", "git"):
        path = shutil.which(tool)
        c.append(Check(tool, OK if path else FAIL, path or "není v PATH"))

    for mod, level in (("yaml", FAIL), ("yt_dlp", FAIL), ("google.genai", FAIL),
                       ("dotenv", WARN), ("rapidfuzz", WARN)):
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "") or getattr(getattr(m, "version", None), "__version__", "")
            c.append(Check(f"balíček {mod}", OK, str(ver)))
        except ImportError:
            c.append(Check(f"balíček {mod}", level, "chybí – pip install -r requirements.txt"))

    try:
        from dotenv import load_dotenv
        load_dotenv(vault.root / ".env")
    except ImportError:
        pass
    key = os.getenv("GEMINI_API_KEY")
    orkey = os.getenv("OPENROUTER_API_KEY")
    c.append(Check("klíč k AI modelům", OK if (key or orkey) else FAIL,
                   "OPENROUTER_API_KEY" if orkey else ("GEMINI_API_KEY" if key else "chybí OPENROUTER_API_KEY nebo GEMINI_API_KEY v .env")))
    if online and key:
        try:
            from google import genai
            from pipeline import settings as _st
            client = genai.Client(api_key=key)
            names = [m.name.split("/")[-1] for m in client.models.list()
                     if "generateContent" in (getattr(m, "supported_actions", None) or ["generateContent"])]
            c.append(Check("Gemini API dostupné", OK, f"{len(names)} modelů"))
            cfg = _st.load(vault)
            for role in ("stt", "analysis"):
                want = [cfg[role]["model"], *cfg[role].get("fallback_models", [])]
                missing = [w for w in want if w not in names]
                c.append(Check(f"model pro {role}", FAIL if cfg[role]["model"] in missing else (WARN if missing else OK),
                               ", ".join(want) + (f" — nedostupné: {', '.join(missing)}" if missing else "")))
            probe = [cfg["analysis"]["model"], *cfg["analysis"].get("fallback_models", []),
                     "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemma-4-31b-it", "gemini-3.5-transcribe"]
            for mdl in dict.fromkeys(probe):
                if os.getenv("KOS_PROBE") != "1" or mdl not in names:
                    continue
                import time as _t
                t0 = _t.time()
                try:
                    r = client.models.generate_content(model=mdl, contents=["Odpověz jedním slovem: ahoj"])
                    c.append(Check(f"test {mdl}", OK, f"{(r.text or '').strip()[:20]!r} za {_t.time() - t0:.1f} s"))
                except Exception as e:  # noqa: BLE001
                    c.append(Check(f"test {mdl}", WARN, str(e)[:90]))
            c.append(Check("dostupné modely", OK, ", ".join(sorted(n for n in names if "gemini" in n or "gemma" in n))))
        except Exception as e:  # noqa: BLE001
            c.append(Check("Gemini API dostupné", FAIL, str(e)[:120]))

    # Git
    if shutil.which("git"):
        ok, remote = _cmd(["git", "-C", str(vault.root), "remote", "get-url", "origin"])
        c.append(Check("git remote origin", OK if ok else WARN, remote if ok else "není nastaven (F0: gh repo create)"))
        ok, tracked = _cmd(["git", "-C", str(vault.root), "ls-files", "*cookies*", ".env"])
        c.append(Check("žádná tajemství v Gitu", FAIL if tracked else OK, tracked))

    # Vault
    missing = [vault.rel(d) if d.exists() else d.relative_to(vault.root).as_posix()
               for d in vault.required_dirs() if not d.exists()]
    c.append(Check("struktura složek", WARN if missing else OK, ", ".join(missing)))
    try:
        probe = vault.state_dir / ".write_probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("x")
        probe.unlink()
        c.append(Check("zápis do vaultu", OK))
    except OSError as e:
        c.append(Check("zápis do vaultu", FAIL, str(e)))
    free_gb = shutil.disk_usage(vault.root).free / 1e9
    c.append(Check("volné místo", OK if free_gb > 5 else WARN, f"{free_gb:.1f} GB"))

    entries = catalog.scan(vault)
    src = [e for e in entries if e.path.is_relative_to(vault.sources)]
    no_id = [e.path.name for e in src if not e.source_id]
    c.append(Check("karty mají source_id", WARN if no_id else OK,
                   f"{len(src)} karet" + (f"; bez ID: {len(no_id)} (spusťte migrate-f0)" if no_id else "")))
    dups = catalog.duplicates(vault)
    c.append(Check("unikátní source_id", FAIL if dups else OK, "; ".join(dups)))

    missing_audio = []
    for e in src:
        fm = frontmatter.parse(e.path.read_text(encoding="utf-8")[:8192])
        ap = fm.get("asset_path")
        if ap and not (vault.root / ap).exists() and not str(e.source_id).startswith("yt:demoVideo"):
            missing_audio.append(e.source_id)
    c.append(Check("originální audio na tomto PC", WARN if missing_audio else OK,
                   ("chybí: " + ", ".join(missing_audio)) if missing_audio else ""))
    return c


def print_report(checks: list[Check]) -> int:
    sym = {OK: "✓", WARN: "!", FAIL: "✗"}
    for ch in checks:
        line = f" {sym[ch.level]} {ch.name}"
        if ch.detail:
            line += f"  — {ch.detail}"
        print(line)
    fails = sum(ch.level == FAIL for ch in checks)
    warns = sum(ch.level == WARN for ch in checks)
    print(f"\n{len(checks)} kontrol, {fails} chyb, {warns} varování")
    return 1 if fails else 0
