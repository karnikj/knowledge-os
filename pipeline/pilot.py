"""F1a pilot: přepis golden setu a report kvality do 99_System/docs/PILOT_F1a.md.

Výběr (PLAN §8): krátké, dlouhé, více mluvčích, jiný jazyk (test titulků/jazyka)
a karta, kterou V1 poškodil. Po reportu následuje ruční kontrola časů a textu.
"""
from __future__ import annotations

import time
import traceback
from datetime import datetime

from pipeline import ids, transcribe
from pipeline.config import Vault
from pipeline.timefmt import mmss

# Pilotní sada pro ověření kvality přepisu: doplňte vlastní videa (YouTube ID, proč je v sadě).
# Vyplatí se mít krátké video, více mluvčích, cizí jazyk a jedno dlouhé.
PILOT: list[tuple[str, str]] = []


def run(vault: Vault, force: bool = False) -> int:
    rows, rc = [], 0
    for vid, why in PILOT:
        print(f"\n=== {vid} — {why}")
        t0 = time.time()
        try:
            t = transcribe.transcribe_youtube(vid, vault, force=force)
            q = t["stt_quality"]
            rows.append((vid, why, q, len(t["segments"]), len(t["speakers"]), time.time() - t0, None))
        except Exception as e:  # noqa: BLE001
            rc = 1
            traceback.print_exc()
            rows.append((vid, why, None, 0, 0, time.time() - t0, str(e)[:200]))

    out = [f"# Pilot F1a — {datetime.now():%Y-%m-%d %H:%M}", "",
           "| Video | Typ | Stav | Pokrytí řeči | Slov / titulky | Zarovnáno | Jistota | Segmentů | Mluvčích | Doba |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for vid, why, q, nseg, nspk, secs, err in rows:
        link = f"[[{ids.safe_filename('yt:' + vid)}.md\\|{vid}]]"
        if q is None:
            out.append(f"| {link} | {why} | ❌ {err} | | | | | | | {mmss(secs)} |")
            continue
        ratio = f"{q['vtt_word_ratio']:.0%}" if q["vtt_word_ratio"] else "bez titulků"
        conf = f"{q['alignment_confidence_mean']:.2f}" if q["alignment_confidence_mean"] else "–"
        out.append(f"| {link} | {why} | {q['status']} | {q['speech_coverage']:.0%} | {ratio} | "
                   f"{q['aligned_share']:.0%} | {conf} | {nseg} | {nspk} | {mmss(secs)} |")
        for r in q["reasons"]:
            out.append(f"|  | ↳ {r} | | | | | | | | |")
    out += ["", "## Ruční kontrola (vyplnit)",
            "- [ ] 5 náhodných kotev na video: odpovídá čas ve videu textu? (±3 s)",
            "- [ ] mluvčí správně pojmenovaní a konzistentní mezi kusy",
            "- [ ] švy mezi kusy (≈ každých 5 min): nic nechybí, nic se neopakuje",
            "- [ ] doslovnost: výplňková slova zachována, nic nezkráceno", ""]
    report = vault.system / "docs" / "PILOT_F1a.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(out), encoding="utf-8")
    print(f"\nReport: {vault.rel(report)}")
    return rc
