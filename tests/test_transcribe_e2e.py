"""End-to-end F1 s napodobeným STT a syntetickým audiem (ffmpeg). Bez sítě."""
import json
import shutil
import subprocess

import pytest

from pipeline import state, transcribe
from pipeline.providers.base import RawSegment

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg není k dispozici")

VID = "TESTVIDEO01"


class FakeSTT:
    engine = "fake-stt"
    prompt_version = 1

    def __init__(self):
        self.calls = []

    def transcribe(self, chunk, ctx):
        self.calls.append((chunk.name, ctx.chunk_index, ctx.known_speakers))
        n = ctx.chunk_index
        w = ["alfa", "beta", "gama", "delta", "epsilon", "zeta"][n * 2:n * 2 + 2]
        return [RawSegment(0.5, 8, "Moderátor", f"otázka {w[0]} {w[0]}x {w[0]}y"),
                RawSegment(9, 18, "Host", f"odpověď {w[1]} {w[1]}x {w[1]}y")]


@pytest.fixture
def media_vault(vault):
    (vault.config_dir / "pipeline.yaml").write_text(
        "stt: {chunk_seconds: 20, overlap_seconds: 2, min_interval_seconds: 0}\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=50",
                    "-c:a", "aac", str(vault.audio / f"{VID}.m4a")], check=True)
    (vault.transcripts / "raw" / f"yt__{VID}").mkdir(parents=True)
    (vault.transcripts / "raw" / f"yt__{VID}" / "info.json").write_text(
        json.dumps({"title": "Testovací díl", "channel": "Test"}), encoding="utf-8")
    return vault


def test_transcribe_end_to_end_and_cache(media_vault):
    stt = FakeSTT()
    t = transcribe.transcribe_youtube(VID, media_vault, provider=stt, offline=True)
    assert len(stt.calls) == 3                                   # 50 s / 20 s kusy
    assert stt.calls[1][2] == ["Moderátor", "Host"]              # mluvčí předáni dalšímu kusu
    assert t["source_id"] == f"yt:{VID}" and t["engine"] == "fake-stt"
    assert t["speakers"] == {"S0": "Moderátor", "S1": "Host"}
    starts = [s["start"] for s in t["segments"]]
    assert starts == sorted(starts) and starts[2] >= 18        # posun o offset kusu
    assert all(s["time_source"] == "llm" for s in t["segments"])  # bez titulků
    js = media_vault.transcripts / f"yt__{VID}.json"
    md = media_vault.transcripts / f"yt__{VID}.md"
    assert js.exists() and md.exists()
    assert f"https://youtu.be/{VID}?t=" in md.read_text(encoding="utf-8")
    st = state.load(media_vault, f"yt:{VID}")
    assert st["steps"]["stt"]["engine"] == "fake-stt" and st["steps"]["align"]["status"] in ("done", "requires_local_whisper")
    assert not (media_vault.assets / "chunks_tmp" / f"yt__{VID}").exists()   # dočasné kusy uklizeny

    # druhé spuštění: vše z cache, žádné volání STT
    stt2 = FakeSTT()
    t2 = transcribe.transcribe_youtube(VID, media_vault, provider=stt2, offline=True)
    assert stt2.calls == [] and t2["content_hash"] == t["content_hash"]

    # změna verze promptu → cache neplatí
    stt3 = FakeSTT(); stt3.prompt_version = 2
    transcribe.transcribe_youtube(VID, media_vault, provider=stt3, offline=True)
    assert len(stt3.calls) == 3


def test_truncated_chunk_is_retried_then_split(vault):
    from pathlib import Path
    from pipeline import settings
    from pipeline.transcribe import _transcribe_checked, chunk_check
    from pipeline.providers.base import STTContext
    from pipeline.vtt import Word
    cfg = settings.load(vault)
    vtt_words = [Word("slovo", float(t), float(t) + 0.5, 0) for t in range(0, 100)]   # 100 slov za 100 s
    good = [RawSegment(0, 99, "A", " ".join(["slovo"] * 98))]
    short = [RawSegment(0, 20, "A", " ".join(["slovo"] * 20))]
    assert chunk_check(good, 0, 100, vtt_words, cfg) is None
    assert "20%" in chunk_check(short, 0, 100, vtt_words, cfg)

    class Flaky:
        engine, prompt_version = "fake", 1
        def __init__(self, outs): self.outs, self.n = list(outs), 0
        def transcribe(self, path, ctx):
            self.n += 1
            return self.outs.pop(0)

    # useknutá → opakování uspěje
    f = Flaky([short, good])
    segs, note = _transcribe_checked(f, Path("x.ogg"), STTContext(chunk_duration=100), 0, 100, vtt_words, cfg, vault.root)
    assert f.n == 2 and note["attempts"] == 2 and len(segs[0].text.split()) == 98

    # useknutá dvakrát → rozdělení na poloviny (audio.cut napodobíme)
    import pipeline.audio as au
    orig = au.cut
    au.cut = lambda src, dst, a, l, bitrate="32k": dst
    try:
        half = [RawSegment(0, 50, "A", " ".join(["slovo"] * 50))]
        f = Flaky([short, short, half, half])
        segs, note = _transcribe_checked(f, Path("x.ogg"), STTContext(chunk_duration=100), 0, 100, vtt_words, cfg, vault.root)
        assert note.get("split") and f.n == 4
    finally:
        au.cut = orig
