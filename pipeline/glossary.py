"""Slovník názvů pro přepis: 99_System/Slovník.md (upravuje se v Obsidianu i v aplikaci → Více → Slovník).

Formát — jeden název na řádek, volitelně za šipkou, jak ho přepis špatně slyší:

    - Claude ← kloud, klaud
    - OpenRouter
    - Power BI ← pauer bí

Použití:
  1. model pro přepis dostane seznam „správný pravopis názvů“ (slovník + nejčastější názvy z registru entit),
  2. po přepisu se varianty za šipkou v textu nahradí správným názvem (i ve skloňování: „kloudu“ → „Claudu“).
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata

from pipeline.config import Vault

TEMPLATE = """# Slovník názvů

Názvy, které má přepis psát **přesně takto**. Jeden na řádek. Za šipkou `←` můžete uvést, jak je přepis
špatně slyší (oddělte čárkou) — tyto varianty se v přepisu automaticky opraví.
Nejčastější názvy nástrojů, firem a modelů z entit se přidávají samy, sem patří hlavně to, co přepis plete.

- Claude ← kloud, klaud, klód
- Claude Code ← kloud kód, klaud kód
- Gemini ← džemini, dżemini
- OpenRouter ← open routr, openroutr
- DeepSeek ← dýpsík, deep sík
- ChatGPT ← čet džípítý, chat džípítí
- OpenAI ← open ej aj
- Anthropic ← entropik, antropik
- Obsidian ← obsidián
- Power BI ← pauer bí, power bí
- Microsoft Entra
- Intune
- n8n ← n osm n, en eit en
"""


def path(vault: Vault):
    return vault.system / "Slovník.md"


def ensure(vault: Vault) -> None:
    p = path(vault)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(TEMPLATE, encoding="utf-8")


def read_text(vault: Vault) -> str:
    ensure(vault)
    return path(vault).read_text(encoding="utf-8")


def write_text(vault: Vault, text: str) -> list[dict]:
    parse(text)                                      # ověří formát (vyhodí ValueError u nesmyslu)
    path(vault).write_text(text.replace("\r\n", "\n").rstrip() + "\n", encoding="utf-8")
    return parse(text)


def parse(text: str) -> list[dict]:
    out, seen = [], set()
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if not m:
            continue
        body = m.group(1)
        parts = re.split(r"\s*(?:←|<-|<=)\s*", body, maxsplit=1)
        term = parts[0].strip().strip("`*")
        if not term or len(term) > 80:
            continue
        variants = [v.strip() for v in parts[1].split(",")] if len(parts) > 1 else []
        variants = [v for v in variants if v and v.lower() != term.lower() and len(v) <= 80]
        if term.lower() in seen:
            continue
        seen.add(term.lower())
        out.append({"term": term, "variants": variants})
    return out


def entries(vault: Vault) -> list[dict]:
    return parse(read_text(vault))


def auto_terms(vault: Vault, limit: int = 60) -> list[str]:
    """Nejčastější pojmenované nástroje / modely / firmy / služby z indexu (registr entit)."""
    from pipeline import index
    p = index.path(vault)
    if not p.exists():
        return []
    try:
        con = sqlite3.connect(p)
        rows = con.execute("""SELECT name FROM entities WHERE type IN ('software','ai_model','sluzba','organizace','hardware')
                              AND n_mentions >= 2 AND COALESCE(status,'') != 'zamítnuto'
                              ORDER BY n_mentions DESC LIMIT ?""", (limit,)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    from pipeline.entities import is_named
    junk = {"ai", "software", "ai model", "ai agent", "ai agenti", "model", "aplikace", "platforma"}
    out = []
    for (name,) in rows:
        if not name or len(name) > 40 or name.lower() in junk:
            continue
        if not any(c.isupper() or c.isdigit() for c in name) or not is_named(name, "software"):
            continue                                   # obecná slova („jazykový model“) pravopis neřeší
        out.append(name)
    return out


def prompt_terms(vault: Vault, limit: int = 120) -> list[str]:
    terms = [e["term"] for e in entries(vault)]
    low = {t.lower() for t in terms}
    for t in auto_terms(vault):
        if t.lower() not in low:
            terms.append(t)
            low.add(t.lower())
    return terms[:limit]


# ------------------------------------------------------------------ oprava textu
def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


_CACHE: dict = {}


def _rules(vault: Vault) -> list[tuple[re.Pattern, str]]:
    p = path(vault)
    key = (str(p), p.stat().st_mtime if p.exists() else 0)
    if _CACHE.get("key") == key:
        return _CACHE["rules"]
    rules = []
    pairs = [(v, e["term"]) for e in entries(vault) for v in e["variants"]]
    for v, term in sorted(pairs, key=lambda x: -len(x[0])):      # nejdřív delší („kloud kód“ před „kloud“)
        # varianta jako celé slovo/sousloví; povolena česká koncovka (kloudu → Claudu, kloudem → Claudem)
        words = [re.escape(w) for w in v.split()]
        pat = r"(?<![\w])" + r"\s+".join(words) + r"(?P<suf>[a-zá-ž]{0,3})(?![\w])"
        rules.append((re.compile(pat, re.IGNORECASE), term))
    _CACHE.update(key=key, rules=rules)
    return rules


def fix_text(vault: Vault, text: str) -> tuple[str, int]:
    n = 0
    for rx, term in _rules(vault):
        def rep(m):
            nonlocal n
            n += 1
            suf = m.group("suf") or ""
            # koncovku připojit jen k jednoslovnému názvu končícímu písmenem (Claude+u = Claudu → ponech „Claudu“)
            if suf and term[-1:].isalpha() and " " not in term:
                return (term[:-1] if term.endswith("e") and suf[:1] in "uaoiye" else term) + suf
            return term + suf if suf else term
        text = rx.sub(rep, text)
    return text, n


def fix_segments(vault: Vault, segments: list[dict]) -> int:
    total = 0
    for s in segments:
        s["text"], n = fix_text(vault, s["text"])
        total += n
    return total


def apply_to_existing(vault: Vault) -> dict:
    """Opravy ze slovníku i do už hotových přepisů (videa, poznámky, schůzky; dokumenty ne)."""
    changed, fixes = [], 0
    for f in sorted(vault.transcripts.glob("*.json")):
        if f.name.endswith((".analysis.json", ".mentions.json")) or f.name.startswith("file__"):
            continue
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        n = fix_segments(vault, t.get("segments", []))
        if n:
            f.write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            changed.append(t.get("source_id"))
            fixes += n
            try:                                    # čitelný přepis a karta (kde to jde)
                from pipeline import render, transcript
                md = f.with_suffix(".md")
                if md.exists():
                    vid = t["source_id"].split(":", 1)[1] if t["source_id"].startswith("yt:") else None
                    md.write_text(transcript.render_markdown(t, vid), encoding="utf-8")
                if (f.with_name(f.name[:-5] + ".analysis.json")).exists() and not t["source_id"].startswith("meeting:"):
                    render.run(vault, t["source_id"])
            except Exception:  # noqa: BLE001
                pass
    if changed:
        from pipeline import index
        try:
            index.build(vault)
        except Exception as e:  # noqa: BLE001 — index se přestaví při dalším běhu workeru
            print(f"  index: {e}")
    return {"sources": len(changed), "fixes": fixes}
