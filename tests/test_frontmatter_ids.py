from pipeline import frontmatter, ids

CARD = '''---
typ: video
published_at: 2026-05-25
autor:
  - "[[Jan Novák]]"
schema_version: 2
---

# Titulek
Text https://www.youtube.com/watch?v=demoVideo09 konec
'''


def test_parse_and_split():
    fm = frontmatter.parse(CARD)
    assert fm["typ"] == "video"
    assert fm["autor"] == ["[[Jan Novák]]"]
    raw, body = frontmatter.split(CARD)
    assert body.lstrip().startswith("# Titulek")


def test_set_fields_inserts_after_anchor_and_keeps_rest():
    out = frontmatter.set_fields(CARD, {"source_id": "yt:demoVideo09", "source_version": 1}, after="typ")
    lines = out.splitlines()
    assert lines[1] == "typ: video"
    assert lines[2] == 'source_id: "yt:demoVideo09"'
    assert lines[3] == "source_version: 1"
    assert '  - "[[Jan Novák]]"' in out          # styl seznamu zachován
    assert out.endswith("konec\n")
    assert frontmatter.parse(out)["source_id"] == "yt:demoVideo09"


def test_set_fields_replaces_existing_and_is_idempotent():
    once = frontmatter.set_fields(CARD, {"schema_version": 3})
    twice = frontmatter.set_fields(once, {"schema_version": 3})
    assert once == twice
    assert "schema_version: 3" in once and "schema_version: 2" not in once


def test_set_fields_on_text_without_frontmatter():
    out = frontmatter.set_fields("# Jen text\n", {"source_id": "note:abc"})
    assert frontmatter.parse(out)["source_id"] == "note:abc"
    assert out.endswith("# Jen text\n")


def test_youtube_id_variants():
    for u in ("https://www.youtube.com/watch?v=demoVideo09",
              "https://youtu.be/demoVideo09?t=12",
              "https://www.youtube.com/watch?feature=x&v=demoVideo09",
              "https://www.youtube.com/shorts/demoVideo09"):
        assert ids.youtube_id(u) == "demoVideo09"
    assert ids.youtube_id("https://example.com") is None


def test_source_id_rules():
    assert ids.make("yt", "abc") == "yt:abc"
    assert ids.safe_filename("yt:abc") == "yt__abc"
    import pytest
    with pytest.raises(ValueError):
        ids.make("xx", "abc")
    with pytest.raises(ValueError):
        ids.make("yt", "a:b")


def test_set_fields_preserves_body_blank_lines():
    src = "---\ntyp: video\n---\n\n# T\n\ntext\n\n"
    out = frontmatter.set_fields(src, {"source_id": "yt:x"})
    assert out == '---\ntyp: video\nsource_id: "yt:x"\n---\n\n# T\n\ntext\n\n'
