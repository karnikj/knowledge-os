"""Verzovaný stav kroků pipeline: 99_System/state/{source_id}.json (PLAN §1).

Každý krok nese engine, verzi promptu a hashe vstupu/výstupu, takže se dá
rozhodnout, zda krok znovu spustit, bez ručního zásahu. Stav je v souborech
(v Gitu), nikoli v lokální DB — funguje stejně na všech PC i na VPS.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pipeline.config import Vault
from pipeline.ids import safe_filename

STEPS = ("ingest", "stt", "align", "analyze", "ground", "render")
# Speciální stavy pro předání práce mezi VPS a PC (PLAN §3)
STATUSES = ("pending", "running", "done", "failed",
            "requires_local_download", "requires_local_whisper")


def _path(vault: Vault, source_id: str):
    return vault.state_dir / f"{safe_filename(source_id)}.json"


def load(vault: Vault, source_id: str) -> dict[str, Any]:
    p = _path(vault, source_id)
    if not p.exists():
        return {"source_id": source_id, "steps": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def save(vault: Vault, state: dict[str, Any]) -> None:
    p = _path(vault, state["source_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def mark(vault: Vault, source_id: str, step: str, status: str, **info: Any) -> dict[str, Any]:
    if step not in STEPS:
        raise ValueError(f"Neznámý krok: {step}")
    if status not in STATUSES:
        raise ValueError(f"Neznámý stav: {status}")
    state = load(vault, source_id)
    entry = dict(state["steps"].get(step, {}))
    entry.update(info)
    entry["status"] = status
    entry["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["steps"][step] = entry
    save(vault, state)
    return state


def needs_run(state: dict[str, Any], step: str, *, engine: str | None = None,
              prompt_version: Any = None, input_hash: str | None = None) -> bool:
    """Krok je třeba spustit, pokud není hotový nebo se změnil engine/prompt/vstup."""
    e = state.get("steps", {}).get(step)
    if not e or e.get("status") != "done":
        return True
    if engine is not None and e.get("engine") != engine:
        return True
    if prompt_version is not None and e.get("prompt_version") != prompt_version:
        return True
    if input_hash is not None and e.get("input_hash") != input_hash:
        return True
    return False


def invalidate_from(vault: Vault, source_id: str, step: str) -> None:
    """`reprocess --from <krok>`: zneplatní krok a všechny následující."""
    state = load(vault, source_id)
    for s in STEPS[STEPS.index(step):]:
        if s in state["steps"]:
            state["steps"][s]["status"] = "pending"
    save(vault, state)
