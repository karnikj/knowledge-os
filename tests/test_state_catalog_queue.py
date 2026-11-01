from pipeline import catalog, queue, state


def _card(vault, name, sid, date="2026-07-01"):
    p = vault.sources / name
    p.write_text(f'---\ntyp: video\nsource_id: "{sid}"\npublished_at: {date}\n---\n# X\n', encoding="utf-8")
    return p


def test_state_roundtrip_and_needs_run(vault):
    sid = "yt:AAAAAAAAAAA"
    st = state.load(vault, sid)
    assert state.needs_run(st, "stt")
    state.mark(vault, sid, "stt", "done", engine="gemini-2.5-flash", prompt_version=1, input_hash="h1")
    st = state.load(vault, sid)
    assert not state.needs_run(st, "stt", engine="gemini-2.5-flash", prompt_version=1, input_hash="h1")
    assert state.needs_run(st, "stt", prompt_version=2)          # nový prompt
    assert state.needs_run(st, "stt", input_hash="h2")           # nový vstup
    assert state.needs_run(st, "stt", engine="whisper")          # jiný engine
    assert (vault.state_dir / "yt__AAAAAAAAAAA.json").exists()   # žádná ':' v názvu souboru


def test_state_invalidate_from(vault):
    sid = "yt:BBBBBBBBBBB"
    for s in ("stt", "analyze", "render"):
        state.mark(vault, sid, s, "done")
    state.invalidate_from(vault, sid, "analyze")
    st = state.load(vault, sid)
    assert st["steps"]["stt"]["status"] == "done"
    assert st["steps"]["analyze"]["status"] == "pending"
    assert st["steps"]["render"]["status"] == "pending"


def test_state_rejects_unknown_values(vault):
    import pytest
    with pytest.raises(ValueError):
        state.mark(vault, "yt:x", "nonsense", "done")
    with pytest.raises(ValueError):
        state.mark(vault, "yt:x", "stt", "weird")


def test_catalog_scan_and_duplicates(vault):
    _card(vault, "a.md", "yt:AAAAAAAAAAA")
    _card(vault, "b.md", "yt:BBBBBBBBBBB")
    assert catalog.is_known(vault, "yt:AAAAAAAAAAA")
    assert not catalog.is_known(vault, "yt:CCCCCCCCCCC")
    assert catalog.duplicates(vault) == {}
    _card(vault, "c.md", "yt:AAAAAAAAAAA")
    assert "yt:AAAAAAAAAAA" in catalog.duplicates(vault)


def test_queue_add_dedup(vault):
    _card(vault, "a.md", "yt:AAAAAAAAAAA")
    assert "už ve vaultu" in queue.add(vault, "https://youtu.be/AAAAAAAAAAA")
    assert "Přidáno" in queue.add(vault, "https://www.youtube.com/watch?v=BBBBBBBBBBB&t=5")
    assert "už ve frontě" in queue.add(vault, "https://youtu.be/BBBBBBBBBBB")
    assert queue.read(vault) == ["https://www.youtube.com/watch?v=BBBBBBBBBBB"]
    queue.remove(vault, "https://youtu.be/BBBBBBBBBBB")
    assert queue.read(vault) == []
    assert "Nerozpoznaná" in queue.add(vault, "https://example.com")
