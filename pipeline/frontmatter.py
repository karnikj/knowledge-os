"""Čtení a cílená úprava YAML frontmatteru karet.

Záměrně *textová* úprava: pole se nastavují řádek po řádku, zbytek hlavičky
(včetně stylu seznamů `- "[[X]]"`, který generuje LLM) zůstává beze změny.
Tím se minimalizuje šum v Git diffu a nic se nepřeformátuje.
"""
from __future__ import annotations

import re
from typing import Any

import yaml

_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


def split(text: str) -> tuple[str | None, str]:
    """Vrátí (yaml_text, tělo). Bez frontmatteru → (None, text)."""
    m = _FM_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def parse(text: str) -> dict[str, Any]:
    raw, _ = split(text)
    if raw is None:
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _format_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    # hodnoty s dvojtečkou, # nebo úvodní speciální znaky dát do uvozovek
    if re.search(r"[:#\[\]{}&*!|>'\"%@`]|^\s|\s$", s) or s == "":
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def set_fields(text: str, fields: dict[str, Any], after: str = "typ") -> str:
    """Nastaví skalární pole ve frontmatteru.

    Existující řádek `klíč: ...` (jen skalár) se přepíše na místě; chybějící pole
    se vloží za řádek `after:` (nebo na začátek hlavičky), v pořadí slovníku.
    """
    raw, body = split(text)
    if raw is None:
        raw, body = "", text
    lines = raw.split("\n") if raw else []

    missing: list[str] = []
    for key, value in fields.items():
        line = f"{key}: {_format_scalar(value)}".rstrip()
        pat = re.compile(rf"^{re.escape(key)}:(.*)$")
        idx = next((i for i, l in enumerate(lines) if pat.match(l)), None)
        if idx is not None:
            # pokud je pod klíčem seznam/blok, odstraníme ho (nahrazujeme skalárem)
            j = idx + 1
            while j < len(lines) and (lines[j].startswith("  ") or lines[j].startswith("- ")):
                j += 1
            lines[idx:j] = [line]
        else:
            missing.append(line)

    if missing:
        anchor = next((i for i, l in enumerate(lines) if re.match(rf"^{re.escape(after)}:", l)), None)
        pos = anchor + 1 if anchor is not None else 0
        lines[pos:pos] = missing

    return "---\n" + "\n".join(lines) + "\n---\n" + body
