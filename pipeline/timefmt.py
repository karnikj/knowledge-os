from __future__ import annotations


def mmss(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_clock(value) -> float:
    """'01:02:03.5' | '02:03' | '75.2' | 75 → sekundy."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().replace(",", ".").split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total
