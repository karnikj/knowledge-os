"""V1 tok (YouTube URL → karta v 01_Zdroje), převedený do balíčku.

Změny oproti V1:
- deduplikace z frontmatteru karet (catalog), ne z lokální history.db,
- karta dostává `source_id`, `source_version`, `video_id`, `url`, `asset_path`,
- odkaz na audio je relativní embed `![[{id}.m4a]]` (funguje na každém PC, přehrává se v Obsidianu),
- samotest už neběží při každém zpracování (nahrazeno `python -m pipeline doctor`).
DOČASNÉ — F1/F2 tento tok nahradí.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from pipeline import catalog, frontmatter, ids, legacy_gemini, youtube
from pipeline.config import Vault

def clean_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def format_date(date_str: str | None) -> str:
    if not date_str:
        return datetime.today().strftime("%Y-%m-%d")
    try:
        return datetime.strptime(date_str, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return date_str


def clean_markdown_fences(content: str) -> str:
    content = content.strip()
    if content.startswith("```markdown"):
        content = content[11:].strip()
    elif content.startswith("```"):
        content = content[3:].strip()
    if content.endswith("```"):
        content = content[:-3].strip()
    return content


def enrich_metadata(markdown_content: str, current_time: str | None = None,
                    current_date: str | None = None, published_at: str | None = None) -> str:
    """Deterministická metadata (AD-009) pod řádek `typ:`."""
    current_time = current_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_date = current_date or datetime.now().strftime("%Y-%m-%d")
    fields = ["retrieved_at", "processed_at", "knowledge_domain", "source_type", "visibility", "origin"]
    if published_at:
        fields.append("published_at")
    for field in fields:
        markdown_content = re.sub(rf"^{field}:.*$\n?", "", markdown_content, flags=re.MULTILINE)
    inject = f"\npublished_at: {published_at}" if published_at else ""
    inject += (f"\nretrieved_at: {current_date}\nprocessed_at: {current_time}"
               "\nknowledge_domain: public\nsource_type: youtube\nvisibility: public\norigin: external")
    return re.sub(r"^(typ:\s*\w+)$", rf"\1{inject}", markdown_content, flags=re.MULTILINE)


def identity_fields(video_id: str, url: str) -> dict:
    return {
        "source_id": ids.make("yt", video_id),
        "source_version": 1,
        "video_id": video_id,
        "url": ids.youtube_url(video_id) if video_id else url,
        "asset_path": f"00_Assets/audio/{video_id}.m4a",
    }


def attachments_section(video_id: str, url: str) -> str:
    return (
        "\n\n---\n\n## 🔗 Přílohy a Důkazy\n"
        f"*   **Originální video**: [Sledovat na YouTube]({url})\n"
        f"*   **Lokální audio stopa**: ![[{video_id}.m4a]]\n"
    )


def process(url: str, vault: Vault | None = None) -> str | None:
    """Zpracuje jedno video. Vrací cestu ke kartě, nebo None, pokud už existuje."""
    vault = vault or Vault.default()
    out_dir = str(vault.sources)

    info = youtube.get_video_info(url)
    video_id, title, uploader = info["id"], info["title"], info["uploader"]
    sid = ids.make("yt", video_id)
    print(f"Video: \"{title}\" – {uploader} ({sid})")

    if catalog.is_known(vault, sid):
        print(f"-> {sid} už ve vaultu je. Nic nedělám.")
        return None

    audio_path, _ = youtube.download_audio(url, vault)
    md = clean_markdown_fences(legacy_gemini.analyze_audio(audio_path))
    date = format_date(info.get("upload_date"))
    md = enrich_metadata(md, published_at=date)
    md = frontmatter.set_fields(md, identity_fields(video_id, url), after="typ")

    os.makedirs(out_dir, exist_ok=True)
    filename = f"{date} - {clean_filename(uploader)} - {clean_filename(title)}.md"
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md.rstrip() + attachments_section(video_id, ids.youtube_url(video_id)))
    print(f"Karta zapsána: {path}")
    return path
