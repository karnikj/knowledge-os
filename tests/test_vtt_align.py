from pipeline import vtt
from pipeline.align import _seam_cut, align_to_vtt, stitch
from pipeline.audio import plan_chunks
from pipeline.providers.base import RawSegment

# Zkrácený vzor YouTube auto-titulků (rolující řádky + inline časy slov)
YT_VTT = """WEBVTT
Kind: captions
Language: cs

00:00:00.160 --> 00:00:02.990 align:start position:0%
 
dobrý<00:00:00.480><c> den</c><00:00:00.800><c> vítejte</c><00:00:01.360><c> u</c><00:00:01.520><c> dalšího</c>

00:00:02.990 --> 00:00:03.000 align:start position:0%
dobrý den vítejte u dalšího
 

00:00:03.000 --> 00:00:05.990 align:start position:0%
dobrý den vítejte u dalšího
dílu<00:00:03.400><c> podcastu</c><00:00:04.100><c> AI</c><00:00:04.300><c> v</c><00:00:04.400><c> kostce</c>
"""


def test_parse_youtube_rolling_captions_word_times():
    cues, words = vtt.parse(YT_VTT)
    assert [w.text for w in words] == ["dobrý", "den", "vítejte", "u", "dalšího",
                                       "dílu", "podcastu", "AI", "v", "kostce"]
    assert words[0].start == 0.16 and words[1].start == 0.48
    assert words[5].start == 3.0 and words[6].start == 3.4
    assert all(words[i].start <= words[i + 1].start for i in range(len(words) - 1))


def test_parse_plain_vtt_spreads_words():
    txt = "WEBVTT\n\n00:00:10.000 --> 00:00:14.000\nahoj světe jak se\n\n00:00:14.000 --> 00:00:16.000\nmáš dnes\n"
    cues, words = vtt.parse(txt)
    assert len(cues) == 2 and len(words) == 6
    assert words[0].start == 10.0 and abs(words[1].start - 11.0) < 1e-9 and words[4].start == 14.0


def test_plan_chunks_overlap_and_tail_merge():
    assert plan_chunks(600, 300, 5) == [(0.0, 300), (295, 600)]
    spans = plan_chunks(1000, 300, 5)
    assert spans[0] == (0.0, 300) and spans[1][0] == 295
    assert spans[-1][1] == 1000
    assert plan_chunks(310, 300, 5) == [(0.0, 310)]    # krátký zbytek přilepen


def test_seam_cut_detects_repeated_words():
    assert _seam_cut("a b c d e f".split(), "c d e f g h".split()) == 4
    assert _seam_cut("a b c".split(), "x y z".split()) == 0
    # shoda uprostřed tailu (daleko od konce) není šev
    assert _seam_cut(("d e f " + "x " * 20).split(), "d e f g".split()) == 0


def test_stitch_keeps_long_segment_starting_in_overlap():
    """Reálný případ z pilotu (demoVideo05): kus 5 začal dlouhým 57s segmentem v překryvu.
    Dřív ho sešití celý zahodilo a vznikla 54s „díra“."""
    c0 = _ch(0, 0, 300, [(280, 298, "A", "já jsem byl na jedný přednášce bylo to minulý týden řekněme to je jedno a tam velmi zajímavý")])
    c1 = _ch(1, 295, 600, [(0, 57, "A", "jedný přednášce bylo to minulý týden řekněme to je jedno a tam velmi zajímavě právě mluvili o cloudu a onpremu"),
                           (57, 68, "B", "a proto je tu dneska ta část")])
    segs, removed = stitch([c0, c1])
    text = " ".join(s["text"] for s in segs)
    assert "mluvili o cloudu a onpremu" in text               # dlouhý segment zachován
    assert text.count("bylo to minulý týden") == 1            # bez duplicity
    assert removed >= 10


def _ch(i, s, e, segs):
    return {"index": i, "start": s, "end": e, "segments": [RawSegment(*x) for x in segs]}


def test_stitch_offsets_cuts_overlap_and_removes_seam_duplicates():
    c0 = _ch(0, 0, 30, [(0, 10, "A", "jedna dva tři"), (20, 30, "A", "tady končí první kus a pak")])
    # druhý kus začíná v 25 s; segment na 0 s leží v překryvu (před řezem 27,5) → zahozen,
    # segment na 3 s opakuje „a pak“ a pokračuje → duplicitní slova odstraněna
    c1 = _ch(1, 25, 60, [(0, 2, "A", "končí první"), (3, 8, "A", "tady končí první kus a pak druhý kus pokračuje"),
                         (10, 20, "B", "odpověď hosta")])
    segs, removed = stitch([c0, c1])
    texts = [s["text"] for s in segs]
    assert texts[0] == "jedna dva tři"
    assert "pokračuje" in " ".join(texts)
    assert " ".join(texts).count("první kus a pak") == 1
    assert removed >= 3
    assert segs[-1]["start"] == 35 and segs[-1]["speaker"] == "B"
    assert [s["i"] for s in segs] == list(range(len(segs)))


def test_align_to_vtt_sets_aligned_time_and_keeps_llm_time():
    _, words = vtt.parse(YT_VTT)
    segs = [{"i": 0, "start": 1.5, "end": 2.5, "speaker": "A", "text": "Dobrý den, vítejte u dalšího", "chunk": 0},
            {"i": 1, "start": 6.0, "end": 8.0, "speaker": "A", "text": "dílu podcastu AI v kostce.", "chunk": 0},
            {"i": 2, "start": 9.0, "end": 9.5, "speaker": "B", "text": "úplně jiný text tady", "chunk": 0}]
    align_to_vtt(segs, words, {0: (0, 10)})
    assert segs[0]["time_source"] == "aligned_vtt" and segs[0]["start"] == 0.16
    assert segs[0]["llm_start"] == 1.5                       # původní čas zachován
    assert segs[1]["start"] == 3.0 and segs[1]["alignment"]["confidence"] == 1.0
    # nespárovaný segment na konci: čas LLM posunutý o odchylku poslední kotvy (6,0 → 3,0 ⇒ −3 s)
    assert segs[2]["time_source"] == "interpolated" and segs[2]["start"] == 6.0 and segs[2]["llm_start"] == 9.0


def test_align_without_vtt_marks_llm():
    segs = [{"i": 0, "start": 1.0, "end": 2.0, "speaker": "A", "text": "x", "chunk": 0}]
    align_to_vtt(segs, [], {0: (0, 10)})
    assert segs[0]["time_source"] == "llm" and segs[0]["alignment"] is None


def test_unaligned_llm_time_does_not_block_following_segments_and_is_interpolated():
    # reálný případ z pilotu: segment s nesmyslným LLM časem 213 s mezi správně zarovnanými
    from pipeline.align import interpolate_unaligned
    segs = [
        {"i": 0, "start": 10.0, "end": 20.0, "llm_start": 50, "llm_end": 60, "time_source": "aligned_vtt", "text": "a b"},
        {"i": 1, "start": 213.0, "end": 214.0, "llm_start": 213, "llm_end": 214, "time_source": "llm", "text": "c d e f"},
        {"i": 2, "start": 213.5, "end": 222.0, "llm_start": 213.5, "llm_end": 222, "time_source": "llm", "text": "g h"},
        {"i": 3, "start": 40.0, "end": 45.0, "llm_start": 222, "llm_end": 222, "time_source": "aligned_vtt", "text": "i j"},
    ]
    interpolate_unaligned(segs)
    assert [s["time_source"] for s in segs] == ["aligned_vtt", "interpolated", "interpolated", "aligned_vtt"]
    assert segs[1]["start"] == 20.0 and segs[2]["end"] == 40.0
    assert segs[1]["end"] > segs[1]["start"] and segs[1]["end"] - 20 > segs[2]["end"] - segs[2]["start"]  # 4 slova vs 2
    assert segs[1]["llm_start"] == 213                                                                     # původní čas zachován


def test_prompt_uses_mmss_and_chunk_offset():
    from pipeline.providers.base import STTContext
    from pipeline.providers.gemini_stt import GeminiSTT
    p = GeminiSTT(client=object()).build_prompt(STTContext(chunk_index=1, chunk_count=3, chunk_duration=300, chunk_start=295))
    assert "00:00 až 05:00" in p and "04:55" in p
