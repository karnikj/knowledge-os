"""Hlasové poznámky z telefonu (PWA): 30s kusy → jedna nahrávka → přepis → karta v 14_Internal/VoiceNotes.

Klient (MediaRecorder s timeslice 30 s) posílá kusy jednoho souvislého záznamu; server je v pořadí
spojí (bajtově — tvoří jeden platný WebM/OGG stream) a převede na opus 16 kHz mono.

Relace: 18_Inbox_Hlas/<session>/  chunk_00000.bin …, meta.json {created, mime, finished, title?}
Po zpracování: audio → 00_Assets/voice/<id>.opus, relace se smaže, source_id = voice:<id>.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from pipeline import audio, ids
from pipeline.config import Vault

SESSION_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9]{4}$")


def inbox(vault: Vault) -> Path:
    p = vault.root / "18_Inbox_Hlas"
    p.mkdir(parents=True, exist_ok=True)
    return p


def new_session(vault: Vault, mime: str = "audio/webm", meeting: str | None = None) -> str:
    """Nová nahrávací relace. meeting = ID zápisu ze schůzky, ke kterému nahrávka patří."""
    import secrets
    sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    d = inbox(vault) / sid
    d.mkdir()
    meta = {"created": datetime.now().isoformat(timespec="seconds"), "mime": mime, "finished": False}
    if meeting:
        meta["meeting"] = meeting
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return sid


def session_meta(vault: Vault, session: str) -> dict:
    return json.loads((_session_dir(vault, session) / "meta.json").read_text(encoding="utf-8"))


def _session_dir(vault: Vault, session: str) -> Path:
    if not SESSION_RE.match(session):
        raise ValueError("neplatné ID relace")
    d = inbox(vault) / session
    if not d.is_dir():
        raise FileNotFoundError(session)
    return d


def put_chunk(vault: Vault, session: str, n: int, data: bytes) -> int:
    if not 0 <= n < 100000 or len(data) > 20_000_000:
        raise ValueError("neplatný kus")
    d = _session_dir(vault, session)
    (d / f"chunk_{n:05d}.bin").write_bytes(data)          # idempotentní: opakované nahrání přepíše
    return len(list(d.glob("chunk_*.bin")))


def finish(vault: Vault, session: str, expected_chunks: int | None = None, title: str | None = None) -> dict:
    d = _session_dir(vault, session)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    have = sorted(int(p.stem.split("_")[1]) for p in d.glob("chunk_*.bin"))
    missing = [i for i in range(expected_chunks or 0) if i not in have]
    meta.update({"finished": not missing, "expected": expected_chunks, "missing": missing,
                 "title": (title or "").strip()[:120] or None,
                 "finished_at": datetime.now().isoformat(timespec="seconds")})
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def discard(vault: Vault, session: str) -> None:
    """Zahodit rozpracovanou nahrávku (uživatel stiskl Zahodit) — nic se nepřepisuje ani neukládá."""
    shutil.rmtree(_session_dir(vault, session))


def ready_sessions(vault: Vault) -> list[str]:
    out = []
    for d in sorted(inbox(vault).iterdir()):
        m = d / "meta.json"
        if d.is_dir() and m.exists() and json.loads(m.read_text(encoding="utf-8")).get("finished"):
            out.append(d.name)
    return out


def pending(vault: Vault) -> list[dict]:
    """Nedokončené / chybné relace (pro web a `voice-retry`)."""
    out = []
    for d in sorted(inbox(vault).iterdir()):
        m = d / "meta.json"
        if d.is_dir() and m.exists():
            meta = json.loads(m.read_text(encoding="utf-8"))
            out.append({"session": d.name, "chunks": len(list(d.glob("chunk_*.bin"))), **meta})
    return out


def retry(vault: Vault) -> list[str]:
    """Relace, jejichž zpracování selhalo (např. výpadek Gemini), vrátit do zpracování."""
    out = []
    for r in pending(vault):
        if r.get("error") and not r.get("missing"):
            m = inbox(vault) / r["session"] / "meta.json"
            meta = json.loads(m.read_text(encoding="utf-8"))
            meta.pop("error", None)
            meta["finished"] = True
            m.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            out.append(r["session"])
    return out


def assemble(vault: Vault, session: str, folder: str = "voice", name: str | None = None,
             namespace: str = "voice") -> tuple[str, Path, dict]:
    """Spojí kusy a převede na opus. Vrací (source_id, cesta k audiu, meta)."""
    d = _session_dir(vault, session)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    raw = d / "recording.raw"
    with open(raw, "wb") as out:
        for p in sorted(d.glob("chunk_*.bin")):
            out.write(p.read_bytes())
    vault.assets.joinpath(folder).mkdir(parents=True, exist_ok=True)
    dst = vault.assets / folder / f"{name or session}.opus"
    subprocess.run([audio._ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
                    "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k", str(dst)], check=True)
    return ids.make(namespace, name or session), dst, meta


def process(vault: Vault, session: str, stt_provider=None, analysis_provider=None) -> dict:
    """Celé zpracování jedné relace (volá worker)."""
    from pipeline import analyze, render, transcribe
    sid, path, meta = assemble(vault, session)
    created = meta.get("created", "")[:16].replace("T", " ")
    info = {"title": meta.get("title") or f"Hlasová poznámka {created}", "channel": "Já",
            "upload_date": created[:10].replace("-", ""), "kind": "voice",
            "description": "Osobní hlasová poznámka (myšlenky, nápady, úkoly) nadiktovaná do telefonu."}
    t = transcribe.transcribe_file(vault, sid, path, info, stt_provider)
    try:
        analyze.run(vault, sid, analysis_provider or analyze.default_provider(vault),
                    meta={**info, "kind": "osobní hlasová poznámka"})
        card = render.run(vault, sid)
    except Exception as e:  # noqa: BLE001 — přepis je hlavní; analýzu worker doplní později
        from pipeline.providers.gemini_stt import QuotaExhausted
        print(f"  analýza hlasové poznámky selhala: {e}")
        if isinstance(e, QuotaExhausted):
            shutil.rmtree(_session_dir(vault, session), ignore_errors=True)
            raise
        card = None
    shutil.rmtree(_session_dir(vault, session), ignore_errors=True)
    return {"source_id": sid, "text": plain_text(t), "card": vault.rel(card) if card else None}


def missing_analysis(vault: Vault) -> list[str]:
    """Hlasové poznámky s přepisem, ale bez analýzy/karty (např. analýza narazila na limit)."""
    skip = vault.system / "logs" / "analysis_skip.txt"
    skipped = set(skip.read_text(encoding="utf-8").split()) if skip.exists() else set()
    out = []
    for f in sorted(vault.transcripts.glob("voice__*.json")):
        if f.name.endswith((".analysis.json", ".mentions.json")):
            continue
        sid = f.stem.replace("__", ":", 1)
        if not f.with_name(f.stem + ".analysis.json").exists() and sid not in skipped:
            out.append(sid)
    return out


def complete_analysis(vault: Vault, sid: str, analysis_provider=None) -> None:
    from pipeline import analyze, render, transcript
    t = transcript.load(vault.transcripts / (ids.safe_filename(sid) + ".json"))
    meta = {**(t.get("meta") or {}), "kind": "osobní hlasová poznámka"}
    try:
        analyze.run(vault, sid, analysis_provider or analyze.default_provider(vault), meta=meta)
        render.run(vault, sid)
    except Exception as e:  # noqa: BLE001
        from pipeline.providers.gemini_stt import QuotaExhausted
        if isinstance(e, QuotaExhausted):
            raise
        skip = vault.system / "logs" / "analysis_skip.txt"
        skip.parent.mkdir(parents=True, exist_ok=True)
        with open(skip, "a", encoding="utf-8") as f:
            f.write(sid + "\n")
        print(f"  doplnění analýzy {sid} selhalo: {e}")


def plain_text(t: dict) -> str:
    return "\n\n".join(p["text"] for p in __import__("pipeline.transcript", fromlist=["x"]).paragraphs(t, 120))


def recent_notes(vault: Vault, limit: int = 20) -> list[dict]:
    from pipeline import transcript
    out = []
    for f in sorted(vault.transcripts.glob("voice__*.json"), reverse=True):
        if f.name.endswith((".analysis.json", ".mentions.json")):
            continue
        t = transcript.load(f)
        an = f.with_name(f.name[:-5] + ".analysis.json")
        a = json.loads(an.read_text(encoding="utf-8")) if an.exists() else {}
        out.append({"source_id": t["source_id"], "created": t["source_id"].split(":", 1)[1][:15],
                    "title": t.get("meta", {}).get("title"), "duration": t["duration_s"],
                    "summary": a.get("tldr", ""), "text": plain_text(t)})
        if len(out) >= limit:
            break
    pending = [{"session": s, "status": "zpracovává se"} for s in ready_sessions(vault)]
    return pending + out
