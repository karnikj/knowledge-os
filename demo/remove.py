"""Odstraní ukázková data načtená skriptem demo/load.py: python demo/remove.py"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = Path(os.getenv("KOS_ROOT") or HERE.parent).resolve()
MARK = ROOT / "99_System/state/demo_loaded.json"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from pipeline.config import Vault
    if not MARK.exists():
        print("Ukázková data nejsou načtená (chybí 99_System/state/demo_loaded.json).")
        return 1
    mark = json.loads(MARK.read_text(encoding="utf-8"))
    n = 0
    for f in mark["files"]:
        p = ROOT / f
        if p.exists():
            p.unlink()
            n += 1
        d = p.parent
        while d != ROOT and d.exists() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    reg_path = ROOT / "99_System/config/entities.yaml"
    reg = yaml.safe_load(reg_path.read_text(encoding="utf-8")) or {}
    for k in mark["entities"]:
        if k in reg and reg[k].get("status") == "navrženo":
            del reg[k]
    from pipeline.entities import Registry
    r = Registry.load(Vault(ROOT))
    r.data = reg
    r.save()
    MARK.unlink()
    from pipeline import index
    index.build(Vault(ROOT))
    print(f"Odstraněno {n} souborů a {len(mark['entities'])} entit. Index přestavěn.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
