"""Evidence spotřeby AI: každé volání modelu = jeden řádek v 99_System/logs/usage/YYYY-MM.jsonl.

Gemini (bezplatný klíč) → tokeny se počítají, cena 0. OpenRouter → cena přesně podle účtování (usage.cost v USD).
Web ukazuje měsíční součet v záhlaví a u každé odpovědi chatu tokeny + cenu.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

USD_CZK = float(os.getenv("KOS_USD_CZK", "23"))
_LOCK = threading.Lock()


def _dir() -> Path:
    from pipeline.config import Vault
    return Vault.default().system / "logs" / "usage"


def record(provider: str, model: str, purpose: str, prompt_tokens: int = 0, completion_tokens: int = 0,
           cost_usd: float = 0.0, audio_seconds: float = 0.0, extra: dict | None = None) -> dict:
    row = {"at": datetime.now().isoformat(timespec="seconds"), "provider": provider, "model": model,
           "purpose": purpose or "", "in": int(prompt_tokens or 0), "out": int(completion_tokens or 0),
           "cost_usd": round(float(cost_usd or 0), 6)}
    if audio_seconds:
        row["audio_s"] = round(audio_seconds, 1)
    if extra:
        row.update(extra)
    try:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        with _LOCK, open(d / f"{row['at'][:7]}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return row


def summary(month: str | None = None) -> dict:
    month = month or datetime.now().strftime("%Y-%m")
    p = _dir() / f"{month}.jsonl"
    tot = {"calls": 0, "in": 0, "out": 0, "cost_usd": 0.0}
    by: dict[str, dict] = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            key = f"{r['provider']} · {r['model']} · {r.get('purpose', '')}"
            b = by.setdefault(key, {"provider": r["provider"], "model": r["model"], "purpose": r.get("purpose", ""),
                                    "calls": 0, "in": 0, "out": 0, "cost_usd": 0.0})
            for t in (tot, b):
                t["calls"] += 1; t["in"] += r.get("in", 0); t["out"] += r.get("out", 0)
                t["cost_usd"] += r.get("cost_usd", 0)
    tot["cost_usd"] = round(tot["cost_usd"], 4)
    tot["cost_czk"] = round(tot["cost_usd"] * USD_CZK, 2)
    rows = sorted(by.values(), key=lambda b: -b["cost_usd"] - b["in"] / 1e9)
    for b in rows:
        b["cost_usd"] = round(b["cost_usd"], 4); b["cost_czk"] = round(b["cost_usd"] * USD_CZK, 2)
    return {"month": month, "total": tot, "by": rows, "usd_czk": USD_CZK}


class Tracker:
    """Sčítá spotřebu jedné operace (např. jedné odpovědi chatu)."""
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, row: dict) -> None:
        self.rows.append(row)

    def total(self) -> dict:
        t = {"in": sum(r["in"] for r in self.rows), "out": sum(r["out"] for r in self.rows),
             "cost_usd": round(sum(r["cost_usd"] for r in self.rows), 6),
             "models": sorted({r["model"] for r in self.rows})}
        t["cost_czk"] = round(t["cost_usd"] * USD_CZK, 3)
        return t
