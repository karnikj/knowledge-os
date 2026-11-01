from pipeline import settings, transcript

Q = settings.DEFAULTS["quality"]


def _seg(i, a, b, text="slovo " * 5, spk="A", aligned=True):
    return {"i": i, "start": a, "end": b, "llm_start": a, "llm_end": b, "speaker": spk, "text": text.strip(),
            "chunk": 0, "time_source": "aligned_vtt" if aligned else "llm",
            "alignment": {"source": "vtt", "confidence": 0.9} if aligned else None}


def test_quality_ok_with_silence_not_counted_as_gap():
    segs = [_seg(0, 0, 50), _seg(1, 110, 170)]      # mezi 50–110 je ticho/hudba
    q = transcript.quality(segs, 170, [(50, 110)], vtt_word_count=10, seam_duplicates=0, q=Q)
    assert q["status"] == "STT_OK", q
    assert q["speech_coverage"] == 1.0 and q["audio_coverage"] < 0.7


def test_quality_incomplete_on_unexplained_gap():
    segs = [_seg(0, 0, 50), _seg(1, 150, 170)]      # 100 s řeči bez přepisu
    q = transcript.quality(segs, 170, [], vtt_word_count=10, seam_duplicates=0, q=Q)
    assert q["status"] == "STT_INCOMPLETE"
    assert any("díra" in r for r in q["reasons"])


def test_quality_incomplete_when_fewer_words_than_captions():
    segs = [_seg(0, 0, 100)]
    q = transcript.quality(segs, 100, [], vtt_word_count=20, seam_duplicates=0, q=Q)
    assert q["vtt_word_ratio"] == 0.25 and q["status"] == "STT_INCOMPLETE"


def test_build_speakers_and_render_markdown():
    segs = [_seg(0, 0, 10, "Ahoj.", "Jan Novák"), _seg(1, 10, 20, "Dobrý den.", "Host", aligned=False),
            _seg(2, 20, 30, "Pokračuji.", "Host", aligned=False)]
    q = transcript.quality(segs, 30, [], 0, 0, Q)
    t = transcript.build("yt:AAAAAAAAAAA", engine="fake", prompt_version=1, duration=30, segments=segs,
                         quality_info=q)
    assert t["speakers"] == {"S0": "Jan Novák", "S1": "Host"}
    assert t["segments"][1]["speaker"] == "S1"
    assert t["content_hash"].startswith("sha256:")
    md = transcript.render_markdown(t, "AAAAAAAAAAA")
    assert "**[00:00](https://youtu.be/AAAAAAAAAAA?t=0) Jan Novák:** Ahoj. ^s0" in md
    assert "**[~00:10](https://youtu.be/AAAAAAAAAAA?t=10) Host:** Dobrý den. Pokračuji. ^s1" in md
