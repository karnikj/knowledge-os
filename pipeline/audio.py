"""ffmpeg: délka audia, detekce ticha, dělení na kusy pro STT, opus kopie.

Detekce řeči je záměrně přes ffmpeg `silencedetect` (žádný PyTorch) — stačí pro
metriku `speech_coverage` a běží i na VPS se 4 GB RAM.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg není v PATH (python -m pipeline doctor)")
    return exe


def duration(path: Path) -> float:
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe není v PATH")
    r = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def silences(path: Path, noise_db: float = -35, min_seconds: float = 2.0) -> list[tuple[float, float]]:
    """Úseky ticha [(start, end)] podle ffmpeg silencedetect."""
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-af",
                        f"silencedetect=noise={noise_db}dB:d={min_seconds}", "-f", "null", "-"],
                       capture_output=True, text=True)
    out: list[tuple[float, float]] = []
    start = None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        try:
            out.append((start, duration(path)))
        except Exception:  # noqa: BLE001
            pass
    return out


@dataclass
class Chunk:
    index: int
    start: float      # offset v originálním audiu (s)
    end: float
    path: Path


def plan_chunks(total: float, chunk_s: float, overlap_s: float) -> list[tuple[float, float]]:
    if total <= 0:
        return []
    spans: list[tuple[float, float]] = []
    s = 0.0
    while s < total:
        e = min(total, s + chunk_s)
        # krátký zbytek (< 1/4 kusu) přilepíme k poslednímu kusu
        if total - e < chunk_s / 4:
            e = total
        spans.append((s, e))
        if e >= total:
            break
        s = e - overlap_s
    return spans


def make_chunks(src: Path, out_dir: Path, chunk_s: float, overlap_s: float,
                bitrate: str = "32k") -> list[Chunk]:
    """Rozřeže audio na kusy ogg/opus 16 kHz mono (malé, vhodné pro inline upload)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Chunk] = []
    for i, (s, e) in enumerate(plan_chunks(duration(src), chunk_s, overlap_s)):
        p = out_dir / f"chunk_{i:03d}.ogg"
        if not p.exists():
            subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                            "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(src),
                            "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", bitrate, str(p)],
                           check=True)
        chunks.append(Chunk(i, s, e, p))
    return chunks


def to_opus(src: Path, dst: Path, bitrate: str = "32k") -> Path:
    """Úsporná archivní kopie (VPS)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                        "-ac", "1", "-c:a", "libopus", "-b:a", bitrate, str(dst)], check=True)
    return dst


def cut(src: Path, dst: Path, start: float, length: float, bitrate: str = "32k") -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}",
                    "-t", f"{length:.3f}", "-i", str(src), "-ac", "1", "-ar", "16000",
                    "-c:a", "libopus", "-b:a", bitrate, str(dst)], check=True)
    return dst
