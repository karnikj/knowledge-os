"""Interní formát přepisu, metriky kvality a čitelný Markdown náhled (PLAN §2, §3.5)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.textnorm import words as norm_words
from pipeline.timefmt import mmss

SCHEMA_VERSION = 1


def _union(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _length(spans) -> float:
    return sum(b - a for a, b in spans)


def _intersect(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    i = j = 0
    tot = 0.0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            tot += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return tot


def quality(segments: list[dict], duration: float, silences: list[tuple[float, float]],
            vtt_word_count: int, seam_duplicates: int, q: dict) -> dict:
    covered = _union([(s["start"], s["end"]) for s in segments])
    sil = _union(silences)
    speech = []
    t = 0.0
    for a, b in sil:
        if a > t:
            speech.append((t, a))
        t = max(t, b)
    if t < duration:
        speech.append((t, duration))
    speech_len = _length(speech)
    speech_cov = _intersect(covered, speech) / speech_len if speech_len else 1.0

    # nevysvětlená mezera = úsek řeči delší než limit, který žádný segment nepokrývá
    gaps = []
    t = 0.0
    for a, b in covered + [(duration, duration)]:
        if a > t:
            gaps.append((t, a))
        t = max(t, b)
    unexplained = [g for g in gaps if _intersect([g], speech) > q["max_unexplained_gap_seconds"]]
    gemini_words = sum(len(norm_words(s["text"])) for s in segments)
    ratio = (gemini_words / vtt_word_count) if vtt_word_count else None
    aligned = [s["alignment"]["confidence"] for s in segments if s.get("alignment")]
    align_share = len(aligned) / len(segments) if segments else 0.0

    reasons = []
    status = "STT_OK"
    if not segments:
        status, reasons = "STT_INCOMPLETE", ["žádné segmenty"]
    else:
        if speech_cov < q["speech_coverage_min"]:
            status = "STT_INCOMPLETE"; reasons.append(f"pokrytí řeči {speech_cov:.0%}")
        elif speech_cov < q["speech_coverage_ok"]:
            reasons.append(f"pokrytí řeči {speech_cov:.0%}")
        if unexplained:
            status = "STT_INCOMPLETE"
            reasons.append("díra v přepisu: " + ", ".join(f"{mmss(a)}–{mmss(b)}" for a, b in unexplained[:3]))
        if ratio is not None:
            if ratio < q["vtt_word_ratio_min"]:
                status = "STT_INCOMPLETE"; reasons.append(f"slov oproti titulkům {ratio:.0%}")
            elif ratio < q["vtt_word_ratio_ok"]:
                reasons.append(f"slov oproti titulkům {ratio:.0%}")
            if align_share < q["alignment_confidence_ok"]:
                reasons.append(f"zarovnáno jen {align_share:.0%} segmentů")
        if reasons and status == "STT_OK":
            status = "STT_WARNING"

    return {
        "status": status,
        "reasons": reasons,
        "audio_coverage": round(_length(covered) / duration, 3) if duration else 0.0,
        "speech_coverage": round(speech_cov, 3),
        "unexplained_gaps": [[round(a, 1), round(b, 1)] for a, b in unexplained],
        "overlap_duplicates": seam_duplicates,
        "words": gemini_words,
        "vtt_words": vtt_word_count or None,
        "vtt_word_ratio": round(ratio, 3) if ratio is not None else None,
        "aligned_share": round(align_share, 3),
        "alignment_confidence_mean": round(sum(aligned) / len(aligned), 3) if aligned else None,
    }


def build(source_id: str, *, engine: str, prompt_version: int, duration: float, segments: list[dict],
          quality_info: dict, language: str = "cs", source_version: int = 1, meta: dict | None = None) -> dict:
    speakers: dict[str, str] = {}
    for s in segments:
        name = s["speaker"]
        if name not in speakers.values():
            speakers[f"S{len(speakers)}"] = name
    rev = {v: k for k, v in speakers.items()}
    segs = []
    for s in segments:
        segs.append({
            "i": s["i"], "start": round(s["start"], 2), "end": round(s["end"], 2),
            "speaker": rev[s["speaker"]], "text": s["text"],
            "time_source": s["time_source"], "llm_start": s["llm_start"], "llm_end": s["llm_end"],
            **({"alignment": s["alignment"]} if s.get("alignment") else {}),
            "chunk": s["chunk"],
        })
    body = json.dumps(segs, ensure_ascii=False, sort_keys=True).encode()
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id,
        "source_version": source_version,
        "content_hash": "sha256:" + hashlib.sha256(body).hexdigest(),
        "engine": engine,
        "stt_prompt_version": prompt_version,
        "language": language,
        "duration_s": round(duration, 2),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "meta": meta or {},
        "speakers": speakers,
        "stt_quality": quality_info,
        "segments": segs,
    }


def save(transcript: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(transcript, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def paragraphs(t: dict, max_seconds: float = 75) -> list[dict]:
    """Odstavce: změna mluvčího nebo překročení délky."""
    out: list[dict] = []
    for s in t["segments"]:
        if out and out[-1]["speaker"] == s["speaker"] and s["start"] - out[-1]["start"] < max_seconds:
            out[-1]["text"] += " " + s["text"]
            out[-1]["segments"].append(s["i"])
        else:
            out.append({"start": s["start"], "speaker": s["speaker"], "text": s["text"],
                        "segments": [s["i"]], "time_source": s["time_source"]})
    return out


def render_markdown(t: dict, video_id: str | None = None, max_seconds: float = 75) -> str:
    q = t["stt_quality"]
    lines = [f"<!-- přepis {t['source_id']} · {t['engine']} · {q['status']} -->", ""]
    for p in paragraphs(t, max_seconds):
        name = t["speakers"].get(p["speaker"], p["speaker"])
        stamp = mmss(p["start"])
        if p["time_source"] != "aligned_vtt":
            stamp = "~" + stamp          # odhad (LLM / dopočet), nezarovnáno na titulky
        link = f"[{stamp}](https://youtu.be/{video_id}?t={int(p['start'])})" if video_id else stamp
        lines.append(f"**{link} {name}:** {p['text']} ^s{p['segments'][0]}")
        lines.append("")
    return "\n".join(lines)
