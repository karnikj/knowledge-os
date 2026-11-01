from pipeline import frontmatter, migrate_f0

OLD = '''---
typ: video
published_at: 2026-06-19
retrieved_at: 2026-07-10
source_type: youtube
autor:
  - "[[AI v kostce]]"
---

# Kamila

## 🔗 Přílohy a Důkazy
*   **Originální video**: [Sledovat na YouTube](https://www.youtube.com/watch?v=demoVideo06)
*   **Lokální audio stopa**: 
[Přehrát lokální audio](file:///C:/vault/00_Assets/audio/Kamila - její apky.m4a)
'''


def test_migrate_text_adds_identity_and_fixes_audio_link():
    new, sid, note = migrate_f0.migrate_text(OLD)
    assert sid == "yt:demoVideo06"
    fm = frontmatter.parse(new)
    assert fm["source_id"] == "yt:demoVideo06"
    assert fm["video_id"] == "demoVideo06"
    assert fm["url"] == "https://www.youtube.com/watch?v=demoVideo06"
    assert fm["asset_path"] == "00_Assets/audio/demoVideo06.m4a"
    assert fm["source_version"] == 1
    assert fm["autor"] == ["[[AI v kostce]]"]
    assert "file:///" not in new
    assert "*   **Lokální audio stopa**: ![[demoVideo06.m4a]]" in new


def test_migrate_is_idempotent(vault):
    p = vault.sources / "x.md"
    p.write_text(OLD, encoding="utf-8")
    first = migrate_f0.run(vault)
    second = migrate_f0.run(vault)
    assert first[0].changed and not second[0].changed


def test_migrate_dry_run_does_not_write(vault):
    p = vault.sources / "x.md"
    p.write_text(OLD, encoding="utf-8")
    ch = migrate_f0.run(vault, dry_run=True)
    assert ch[0].changed
    assert p.read_text(encoding="utf-8") == OLD


def test_card_without_youtube_id_is_skipped(vault):
    (vault.sources / "n.md").write_text("---\ntyp: poznamka\n---\ntext\n", encoding="utf-8")
    ch = migrate_f0.run(vault)
    assert not ch[0].changed and "přeskočeno" in ch[0].note


def test_card_with_broken_yaml_is_not_touched(vault):
    broken = "---\ntyp: video\nnastroje:\n  - \"[[GPT\n\n---\nhttps://youtu.be/AAAAAAAAAAA\n"
    p = vault.sources / "b.md"
    p.write_text(broken, encoding="utf-8")
    ch = migrate_f0.run(vault)
    assert not ch[0].changed and "NEPLATNÝ" in ch[0].note
    assert p.read_text(encoding="utf-8") == broken
