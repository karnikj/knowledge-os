"""YouTube přes yt-dlp: metadata a audio. (F1 doplní titulky a opus kopii.)"""
from __future__ import annotations

import os
from pathlib import Path

from pipeline.config import Vault

# Pozn.: 'nocheckcertificate' bylo v V1 kvůli chybám SSL na Windows. Správné řešení je
# `pip install pip-system-certs`; volbu lze vypnout proměnnou KOS_YTDLP_VERIFY_SSL=1.
_BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "nocheckcertificate": os.getenv("KOS_YTDLP_VERIFY_SSL") != "1",
}


def get_video_info(url: str) -> dict:
    """Metadata bez stahování."""
    import yt_dlp

    with yt_dlp.YoutubeDL(dict(_BASE_OPTS)) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "channel": info.get("channel"),
        "url": url,
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date"),     # YYYYMMDD
        "description": info.get("description"),
        "language": info.get("language"),
    }


def download_audio(url: str, vault: Vault | None = None) -> tuple[str, dict]:
    """Stáhne audio do 00_Assets/audio/{video_id}.m4a. Vrací (cesta, info)."""
    import yt_dlp

    vault = vault or Vault.default()
    vault.audio.mkdir(parents=True, exist_ok=True)
    info = get_video_info(url)
    video_id = info["id"]
    opts = dict(_BASE_OPTS)
    opts.update({
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(vault.audio / f"{video_id}.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    final = vault.audio / f"{video_id}.m4a"
    if not final.exists():
        found = sorted(Path(vault.audio).glob(f"{video_id}.*"))
        if not found:
            raise FileNotFoundError(f"Audio pro {video_id} se nepodařilo najít po stažení.")
        final = found[0]
    return str(final), info


def download_captions(video_id: str, vault: Vault | None = None, lang: str = "cs") -> Path | None:
    """Stáhne titulky do 00_Assets/captions/{id}.{lang}.vtt.

    Přednost mají ručně nahrané titulky, jinak automatické. Vrací None, pokud nejsou.
    """
    import yt_dlp

    vault = vault or Vault.default()
    vault.captions.mkdir(parents=True, exist_ok=True)
    target = vault.captions / f"{video_id}.{lang}.vtt"
    if target.exists():
        return target
    opts = dict(_BASE_OPTS)
    opts.update({
        "skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
        # "{lang}-orig" = původní automatické titulky; samotné "{lang}" může být strojový překlad
        "subtitleslangs": [f"{lang}-orig", lang], "subtitlesformat": "vtt",
        "outtmpl": str(vault.captions / f"{video_id}.%(ext)s"),
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    found = sorted(vault.captions.glob(f"{video_id}.*.vtt"), key=lambda c: ("-orig" not in c.name, c.name))
    for extra in found[1:]:
        extra.unlink()
    if found and found[0] != target:
        found[0].replace(target)
    return target if target.exists() else None
