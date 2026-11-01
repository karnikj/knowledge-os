"""Převedené testy V1 (units, integrace, golden, analyzer) — tok zůstává funkční do F1/F2."""
import re
from datetime import datetime

import pytest

from pipeline import catalog, legacy_gemini, legacy_v1, youtube
from tests.conftest import GOLDEN_DIR

LLM_OUTPUT = """---
typ: video
published_at: 
retrieved_at: 
processed_at: 
dominant_volatility: medium
schema_version: 2
prompt_version: 1
analyzer_version: 1.0
knowledge_domain: 
source_type: 
visibility: 
origin: 
autor:
  - "[[Test Uploader Name]]"
---
# Test Video Title
*   **[[Test Uploader Name]]** tvrdí: *„Umělá inteligence je budoucnost.“* v čase [01:23].
    *   Typ tvrzení: opinion
    *   Volatilita: medium
    *   Jistota extrakce (Confidence): high
"""


@pytest.fixture
def mocked(vault, monkeypatch):
    monkeypatch.setattr(youtube, "get_video_info", lambda url: {
        "id": "fake_id_123", "title": "Test Video Title",
        "uploader": "Test Uploader Name", "upload_date": "20260708"})

    def fake_download(url, v=None):
        p = vault.audio / "fake_id_123.m4a"
        p.write_bytes(b"fake audio")
        return str(p), {}
    monkeypatch.setattr(youtube, "download_audio", fake_download)
    monkeypatch.setattr(legacy_gemini, "analyze_audio", lambda path: LLM_OUTPUT)
    return vault


# --- jednotkové -----------------------------------------------------------
def test_clean_filename():
    assert legacy_v1.clean_filename("Jan Novák - Jak na AI?") == "Jan Novák - Jak na AI"
    assert legacy_v1.clean_filename('Název / Souboru * s "uvozovkami" : a <znaky> |') == "Název  Souboru  s uvozovkami  a znaky"
    assert legacy_v1.clean_filename("   mezery   ") == "mezery"


def test_format_date():
    assert legacy_v1.format_date("20260708") == "2026-07-08"
    assert legacy_v1.format_date("invalid-date") == "invalid-date"
    assert legacy_v1.format_date("") == datetime.today().strftime("%Y-%m-%d")
    assert legacy_v1.format_date(None) == datetime.today().strftime("%Y-%m-%d")


def test_clean_markdown_fences():
    assert legacy_v1.clean_markdown_fences("```markdown\n# H\n```") == "# H"
    assert legacy_v1.clean_markdown_fences("```\n# H\n```") == "# H"
    assert legacy_v1.clean_markdown_fences("# H") == "# H"


def test_enrich_metadata():
    src = "---\ntyp: video\npublished_at: 2024-03-01\nretrieved_at: \nsource_type: \n---\n# C\n"
    out = legacy_v1.enrich_metadata(src, "2026-07-08 19:30:00", "2026-07-08", "2026-07-08")
    for s in ("retrieved_at: 2026-07-08", "processed_at: 2026-07-08 19:30:00", "knowledge_domain: public",
              "source_type: youtube", "visibility: public", "origin: external", "published_at: 2026-07-08"):
        assert s in out
    assert "2024-03-01" not in out
    assert out.count("---") == 2


# --- integrace ------------------------------------------------------------
def test_process_writes_card_with_identity(mocked):
    path = legacy_v1.process("https://www.youtube.com/watch?v=fake_id_123", mocked)
    assert path.endswith("2026-07-08 - Test Uploader Name - Test Video Title.md")
    content = open(path, encoding="utf-8").read()
    assert 'source_id: "yt:fake_id_123"' in content
    assert "source_type: youtube" in content
    assert "![[fake_id_123.m4a]]" in content
    assert "file:///" not in content
    assert catalog.is_known(mocked, "yt:fake_id_123")


def test_process_dedup_from_frontmatter(mocked):
    legacy_v1.process("https://www.youtube.com/watch?v=fake_id_123", mocked)
    assert legacy_v1.process("https://www.youtube.com/watch?v=fake_id_123", mocked) is None
    assert len(list(mocked.sources.glob("*.md"))) == 1


def test_golden_regression(mocked):
    path = legacy_v1.process("https://youtube.com/watch?v=fake_id_123", mocked)
    content = open(path, encoding="utf-8").read()
    main = content.split("\n\n---\n\n")[0].strip()
    main = re.sub(r"retrieved_at: \d{4}-\d{2}-\d{2}", "retrieved_at: DYNAMIC_DATE", main)
    main = re.sub(r"processed_at: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "processed_at: DYNAMIC_TIME", main)
    golden = (GOLDEN_DIR / "reference_output.md").read_text(encoding="utf-8").strip()
    assert main == golden


# --- analyzer -------------------------------------------------------------
def test_load_prompt():
    assert "Pravidla pro výstup:" in legacy_gemini.load_prompt()


def test_load_prompt_missing_file(monkeypatch):
    monkeypatch.setattr(legacy_gemini, "PROMPT_PATH", "non_existent_file.txt")
    with pytest.raises(FileNotFoundError):
        legacy_gemini.load_prompt()


def test_get_gemini_client_missing_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        legacy_gemini.get_gemini_client()
