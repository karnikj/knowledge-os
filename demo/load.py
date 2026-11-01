"""Načte ukázková data (TEST DATA) do vaultu: python demo/load.py   [--force]

Smyšlená firma Alfa Trade s.r.o., tři hlasové poznámky, zápis ze schůzky, dvě směrnice a jeden „rozhovor“.
Soubory se zkopírují do vaultu, entity se přidají do registru a přestaví se index. Co bylo přidáno, je zapsáno
v 99_System/state/demo_loaded.json, takže `python demo/remove.py` všechno zase odstraní.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = Path(os.getenv("KOS_ROOT") or HERE.parent).resolve()
MARK = ROOT / "99_System/state/demo_loaded.json"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from pipeline.config import Vault
    force = "--force" in sys.argv
    if MARK.exists() and not force:
        print(f"Ukázková data už jsou načtená ({MARK.relative_to(ROOT)}). Odstranit: python demo/remove.py")
        return 1
    files = [l.strip() for l in (HERE / "MANIFEST.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    clash = [f for f in files if (ROOT / f).exists()]
    if clash and not force:
        print("Tyto soubory už ve vaultu jsou (spusťte s --force pro přepsání):\n  " + "\n  ".join(clash))
        return 1
    for f in files:
        dst = ROOT / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / "vault" / f, dst)
    reg_path = ROOT / "99_System/config/entities.yaml"
    reg = yaml.safe_load(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {}
    reg = reg or {}
    demo = yaml.safe_load((HERE / "entities.yaml").read_text(encoding="utf-8")) or {}
    added = [k for k in demo if k not in reg]
    for k in added:
        reg[k] = demo[k]
    from pipeline.entities import Registry
    r = Registry.load(Vault(ROOT))
    r.data = reg
    r.save()
    MARK.parent.mkdir(parents=True, exist_ok=True)
    MARK.write_text(json.dumps({"files": files, "entities": added}, ensure_ascii=False, indent=1), encoding="utf-8")
    from pipeline import index
    index.build(Vault(ROOT))
    print(f"Načteno {len(files)} souborů a {len(added)} entit. Index přestavěn. "
          f"Vše má v názvu [DEMO]; odstranění: python demo/remove.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
