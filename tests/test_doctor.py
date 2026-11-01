from pipeline import doctor


def test_doctor_runs_and_flags_missing_ids(vault, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    (vault.sources / "a.md").write_text("---\ntyp: video\n---\n", encoding="utf-8")
    checks = {c.name: c for c in doctor.run(vault)}
    assert checks["karty mají source_id"].level == doctor.WARN
    assert checks["zápis do vaultu"].level == doctor.OK
    assert checks["klíč k AI modelům"].level == doctor.OK


def test_doctor_detects_duplicate_ids(vault):
    for n in ("a.md", "b.md"):
        (vault.sources / n).write_text('---\ntyp: video\nsource_id: "yt:AAAAAAAAAAA"\n---\n', encoding="utf-8")
    checks = {c.name: c for c in doctor.run(vault)}
    assert checks["unikátní source_id"].level == doctor.FAIL
