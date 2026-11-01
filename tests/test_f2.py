"""F2: stemmer, entity, grounding, analýza s napodobeným modelem, karta, mentions/search."""
import json

import pytest

from pipeline import analyze, render, search, state, transcript
from pipeline.czech import phrase_in, stem
from pipeline.entities import Registry
from pipeline.ground import ground, mention_ok

SEGS = [
    {"i": 0, "start": 0.0, "end": 5.0, "speaker": "S0", "text": "Dobrý den, vítejte u dalšího dílu, dnes tu mám Martina Jirsáka.",
     "time_source": "aligned_vtt", "llm_start": 0, "llm_end": 5, "chunk": 0},
    {"i": 1, "start": 5.0, "end": 12.0, "speaker": "S1", "text": "Na čištění dat jsem použil Pandas a v Pandasu jsem si napsal testy.",
     "time_source": "aligned_vtt", "llm_start": 5, "llm_end": 12, "chunk": 0},
    {"i": 2, "start": 12.0, "end": 20.0, "speaker": "S1", "text": "Antropic podle mě dělá nejlepší modely na kód, Claude je fakt dobrej.",
     "time_source": "aligned_vtt", "llm_start": 12, "llm_end": 20, "chunk": 0},
    {"i": 3, "start": 20.0, "end": 28.0, "speaker": "S0", "text": "A Power BI jste zkoušeli? My máme reporty v Power BI a je to pomalé.",
     "time_source": "llm", "llm_start": 20, "llm_end": 28, "chunk": 0},
]


def test_stemmer_handles_czech_inflection():
    assert stem("Pandasu") == stem("pandas") == "pandas"
    assert stem("Anthropicem") == "anthropic"
    assert phrase_in("Power BI", "reporty v Power BI a")
    assert phrase_in("pandas", "v Pandasu jsem")
    assert not phrase_in("pandas", "panda žere bambus")


def test_registry_resolves_aliases_and_proposes_new(vault):
    reg = Registry.load(vault)
    eid = reg.get_or_propose("Anthropic", "organizace", "yt:x", surface="Antropic")
    assert eid == "anthropic" and reg.data[eid]["status"] == "navrženo"
    assert "Antropic" in reg.data[eid]["aliases"]
    assert reg.resolve("Antropic") == "anthropic"
    assert reg.get_or_propose("anthropicu", "organizace", "yt:y") == "anthropic"   # skloňování
    reg.save()
    assert Registry.load(vault).resolve("Anthropic") == "anthropic"


def test_same_name_different_type_is_one_entity(vault):
    reg = Registry.load(vault)
    a = reg.get_or_propose("Claude Code", "software", "yt:x")
    b = reg.get_or_propose("Claude Code", "ai_model", "yt:y")
    assert a == b and reg.data[a]["type"] == "software" and reg.data[a]["types_seen"] == ["ai_model"]
    assert reg.get_or_propose("Claude Code", "tema", "yt:z") == a          # téma téhož jména = entita


def test_generic_words_are_not_entities():
    from pipeline.entities import is_named
    assert not is_named("firma", "organizace") and not is_named("matka", "osoba")
    assert not is_named("model", "ai_model") and not is_named("městský úřad", "organizace")
    assert is_named("Anthropic", "organizace") and is_named("pandas", "software") and is_named("GPT-5", "ai_model")


def test_grounding_cascade():
    g = ground("použil Pandas a v Pandasu", [1], SEGS)
    assert g.method == "structural" and g.segment_ids == [1]
    g = ground("použil Pandas a v Pandasu", [0], SEGS)          # špatně citovaný segment, ale v okně
    assert g.method == "exact" and g.segment_ids == [1]
    g = ground("Antropic dělá nejlepší modely na kód", [2], SEGS)  # parafráze s vynechaným slovem
    assert g.method in ("fuzzy", "canonical") and g.segment_ids == [2]
    assert ground("tohle nikdo neřekl ani omylem", [1], SEGS).method == "unknown"


def test_mention_must_occur_in_segment():
    assert mention_ok("Pandasu", SEGS[1]["text"])
    assert mention_ok("Antropic", SEGS[2]["text"])
    assert not mention_ok("Tableau", SEGS[3]["text"])


class FakeAnalysis:
    engine = "fake-analysis"
    prompt_version = 1

    def __init__(self):
        self.calls = 0

    def analyze_window(self, lines, ctx):
        self.calls += 1
        assert lines[1].startswith("[s1 00:05 Martin Jirsák]")
        return {
            "summary": "Rozhovor o nástrojích pro práci s daty.",
            "chapters": [{"segment": 0, "title": "Úvod"}, {"segment": 1, "title": "Nástroje"}],
            "claims": [
                {"speaker": "Martin Jirsák", "segment_ids": [2], "quote": "Antropic podle mě dělá nejlepší modely na kód",
                 "claim": "Anthropic dělá nejlepší modely pro kód.", "type": "opinion", "volatility": "high",
                 "attitudes": [{"target": "Anthropic", "polarity": "positive"}]},
                {"speaker": "Moderátor", "segment_ids": [3], "quote": "Power BI je pomalé",
                 "claim": "Power BI je pomalé.", "type": "opinion", "volatility": "medium",
                 "attitudes": [{"target": "Power BI", "polarity": "negative"}]},
                {"speaker": "X", "segment_ids": [1], "quote": "Excel je nejlepší tabulkový program na světě",
                 "claim": "Excel je nejlepší.", "type": "opinion", "volatility": "low", "attitudes": []},
            ],
            "mentions": [
                {"segment": 0, "surface": "Martina Jirsáka", "name": "Martin Jirsák", "type": "osoba"},
                {"segment": 1, "surface": "Pandas", "name": "pandas", "type": "software"},
                {"segment": 1, "surface": "Pandasu", "name": "pandas", "type": "software"},
                {"segment": 2, "surface": "Antropic", "name": "Anthropic", "type": "organizace"},
                {"segment": 2, "surface": "Claude", "name": "Claude", "type": "ai_model"},
                {"segment": 3, "surface": "Power BI", "name": "Power BI", "type": "software"},
                {"segment": 3, "surface": "Tableau", "name": "Tableau", "type": "software"},   # halucinace
            ],
        }

    def summarize(self, ctx, sums, chapters):
        return {"tldr": "Martin Jirsák mluví o nástrojích.", "key_points": ["**Pandas**: čištění dat"],
                "topics": ["Datová analytika"], "dominant_volatility": "high"}


@pytest.fixture
def analysed(vault):
    t = transcript.build("yt:demoVideo01", engine="fake-stt", prompt_version=1, duration=28,
                         segments=[dict(s, speaker={"S0": "Moderátor", "S1": "Martin Jirsák"}[s["speaker"]]) for s in SEGS],
                         quality_info=transcript.quality([], 28, [], 0, 0, {"speech_coverage_min": 0, "speech_coverage_ok": 0,
                                                                            "max_unexplained_gap_seconds": 99,
                                                                            "vtt_word_ratio_min": 0, "vtt_word_ratio_ok": 0,
                                                                            "alignment_confidence_ok": 0}),
                         meta={"title": "Vibe coding bez chaosu", "channel": "AI v kostce", "upload_date": "20260701"})
    t["stt_quality"]["status"] = "STT_OK"; t["stt_quality"]["reasons"] = []
    transcript.save(t, vault.transcripts / "yt__demoVideo01.json")
    (vault.sources / "2026-07-01 - AI v kostce - Vibe coding.md").write_text(
        '---\ntyp: video\nsource_id: "yt:demoVideo01"\npublished_at: 2026-07-01\nmoje_hodnoceni: 5\n---\n# stará V1 karta\n', encoding="utf-8")
    prov = FakeAnalysis()
    a = analyze.run(vault, "yt:demoVideo01", prov)
    return vault, a, prov


def test_analyze_grounds_claims_and_validates_mentions(analysed):
    vault, a, prov = analysed
    assert [c["claim"] for c in a["claims"]] == ["Anthropic dělá nejlepší modely pro kód.", "Power BI je pomalé."]
    assert a["claims"][0]["t"] == 12.0 and a["claims"][0]["speaker"] == "Martin Jirsák"
    assert [c["claim"] for c in a["unverified_claims"]] == ["Excel je nejlepší."]
    m = json.loads((vault.transcripts / "yt__demoVideo01.mentions.json").read_text(encoding="utf-8"))["mentions"]
    ents = {(x["entity"], x["segment"]) for x in m}
    assert ("pandas", 1) in ents and ("anthropic", 2) in ents and ("power-bi", 3) in ents
    assert not any(x["entity"] == "tableau" for x in m)                     # nedoložená zmínka zahozena
    assert sum(1 for x in m if x["entity"] == "pandas") == 1                 # 2 tvary v 1 segmentu = 1 zmínka
    pbi = next(x for x in m if x["entity"] == "power-bi")
    assert pbi["attitude"] == "negative"
    reg = Registry.load(vault)
    assert reg.data["anthropic"]["aliases"] == ["Antropic"]
    assert state.load(vault, "yt:demoVideo01")["steps"]["analyze"]["status"] == "done"
    # druhý běh: z cache
    prov2 = FakeAnalysis()
    analyze.run(vault, "yt:demoVideo01", prov2)
    assert prov2.calls == 0


def test_render_card_keeps_user_fields_and_notes(analysed):
    vault, a, _ = analysed
    path = render.run(vault, "yt:demoVideo01")
    text = path.read_text(encoding="utf-8")
    assert path.name == "2026-07-01 - AI v kostce - Vibe coding.md"            # stejný soubor
    assert "moje_hodnoceni: 5" in text                                        # vlastní pole zachováno
    assert "schema_version: 3" in text and '- "[[Martin Jirsák]]"' in text
    assert '- "[[pandas]]"' in text and '- "[[Power BI]]"' in text
    assert "[00:12](https://youtu.be/demoVideo01?t=12)" in text
    assert "^s1" in text and "Neověřená tvrzení" in text and "stará V1 karta" not in text
    # poznámka uživatele za gen:end přežije přegenerování
    path.write_text(text.replace("## ✍️ Moje poznámky\n", "## ✍️ Moje poznámky\nDůležité pro projekt X.\n"), encoding="utf-8")
    text2 = render.run(vault, "yt:demoVideo01").read_text(encoding="utf-8")
    assert "Důležité pro projekt X." in text2 and text2.count("<!-- gen:start -->") == 1


def test_mentions_and_search_cli_level(analysed):
    vault, _, _ = analysed
    render.run(vault, "yt:demoVideo01")
    eids, hits = search.mentions(vault, "Pandasu")
    assert eids == ["pandas"] and len(hits) == 1 and hits[0].url.endswith("?t=5")
    _, hits = search.mentions(vault, "Antropic", since="2026-06-01")
    assert len(hits) == 1 and hits[0].speaker == "Martin Jirsák"
    _, hits = search.mentions(vault, "Anthropic", since="2026-08-01")
    assert hits == []
    _, hits = search.mentions(vault, "power bi", attitude="negative")
    assert len(hits) == 1
    hits = search.search(vault, "pandas", speaker="Jirsák")
    assert [h.segment for h in hits] == [1]
    assert search.search(vault, "reporty", speaker="Jirsák") == []


def test_window_split_on_truncated_json(vault):
    """Odpověď, která se nevejde (neplatný JSON), vede k rozdělení úseku, ne k pádu."""
    import json as _j
    t = transcript.build("yt:AAAAAAAAAAA", engine="x", prompt_version=1, duration=28,
                         segments=[dict(s, speaker="A") for s in SEGS] * 1,
                         quality_info={"status": "STT_OK", "reasons": []}, meta={})
    transcript.save(t, vault.transcripts / "yt__AAAAAAAAAAA.json")

    class Big(FakeAnalysis):
        def analyze_window(self, lines, ctx):
            self.calls += 1
            if len(lines) > 2:
                raise _j.JSONDecodeError("useknuto", "", 0)
            return {"summary": "s", "chapters": [], "claims": [], "mentions": []}
    prov = Big()
    a = analyze.run(vault, "yt:AAAAAAAAAAA", prov)
    assert prov.calls == 3 and a["stats"]["windows"] == 1


def test_poorer_analysis_is_not_saved(analysed):
    vault, a, _ = analysed
    class Poor(FakeAnalysis):
        engine = "weak-model"
        def analyze_window(self, lines, ctx):
            r = super().analyze_window(lines, ctx); r["claims"] = r["claims"][:0]; return r
    # uložená má 2 tvrzení < 10 → ochrana se neuplatní; nasimulujeme bohatší uloženou
    p = vault.transcripts / "yt__demoVideo01.analysis.json"
    d = json.loads(p.read_text(encoding="utf-8")); d["claims"] = d["claims"] * 10
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(analyze.PoorerAnalysis):
        analyze.run(vault, "yt:demoVideo01", Poor())
    assert len(json.loads(p.read_text(encoding="utf-8"))["claims"]) == 20
    analyze.run(vault, "yt:demoVideo01", Poor(), force=True)
    assert json.loads(p.read_text(encoding="utf-8"))["claims"] == []
