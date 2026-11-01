import json
import shutil
import subprocess

import pytest

from pipeline import usage
from pipeline.providers import openrouter
from pipeline.providers.base import STTContext


def _out(content, cost=0.001):
    return {"model": "m", "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": cost}}


def test_analysis_parses_json_and_records_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("KOS_ROOT", str(tmp_path))
    calls = []

    def fake_chat(model, messages, **kw):
        calls.append((model, messages, kw))
        assert "json" in messages[0]["content"].lower() and '"type": "object"' in messages[0]["content"]
        return _out('```json\n{"answer": "Ahoj [1]", "enough": true}\n```')
    monkeypatch.setattr(openrouter, "chat", fake_chat)
    p = openrouter.OpenRouterAnalysis(model="deepseek/x", sleep=lambda s: None)
    p.purpose, p.tracker = "chat", usage.Tracker()
    r = p.generate("otázka", {"type": "OBJECT", "properties": {"answer": {"type": "STRING"}}})
    assert r["answer"] == "Ahoj [1]" and calls[0][2]["reasoning"] == {"enabled": False}
    assert p.tracker.total()["cost_usd"] == 0.001
    s = usage.summary()
    assert s["total"]["calls"] == 1 and s["by"][0]["purpose"] == "chat"


def test_openrouter_429_uses_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("KOS_ROOT", str(tmp_path))
    seen = []

    def fake_chat(model, messages, **kw):
        seen.append(model)
        if model == "a":
            raise openrouter.OpenRouterError(503, "UNAVAILABLE high demand")
        return _out('{"x": 1}')
    monkeypatch.setattr(openrouter, "chat", fake_chat)
    p = openrouter.OpenRouterAnalysis(model="a", fallbacks=["b"], backoff=0, sleep=lambda s: None)
    assert p.generate("q", {"type": "object"}) == {"x": 1} and seen[-1] == "b"


def test_402_is_billing(monkeypatch, tmp_path):
    from pipeline.providers.gemini_stt import BillingError
    monkeypatch.setattr(openrouter, "chat", lambda *a, **k: (_ for _ in ()).throw(
        openrouter.OpenRouterError(402, "Insufficient credits")))
    with pytest.raises(BillingError):
        openrouter.OpenRouterAnalysis(model="a", sleep=lambda s: None).generate("q", {"type": "object"})


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_stt_segments(tmp_path, monkeypatch):
    monkeypatch.setenv("KOS_ROOT", str(tmp_path))
    ch = tmp_path / "c.ogg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=3", "-c:a", "libopus", str(ch)],
                   check=True)

    def fake_chat(model, messages, **kw):
        parts = messages[0]["content"]
        assert parts[1]["input_audio"]["format"] == "mp3" and "JSON" in parts[0]["text"]
        return _out(json.dumps([{"start": "00:00", "end": "00:02", "speaker": "Mluvčí 1", "text": "Dobrý den."}]))
    monkeypatch.setattr(openrouter, "chat", fake_chat)
    segs = openrouter.OpenRouterSTT(sleep=lambda s: None).transcribe(ch, STTContext(chunk_duration=3))
    assert segs[0].text == "Dobrý den." and usage.summary()["total"]["in"] == 100
    # dlouhý úsek, ale jen 100 vstupních tokenů = audio nepřijato → záložní model
    seen = []

    def drop_chat(model, messages, **kw):
        seen.append(model)
        return _out(json.dumps([{"start": "00:00", "end": "00:02", "speaker": "A", "text": "x"}]))
    monkeypatch.setattr(openrouter, "chat", drop_chat)
    p = openrouter.OpenRouterSTT(model="q", fallbacks=[], backoff=0, max_retries=2, sleep=lambda s: None)
    with pytest.raises(openrouter.OpenRouterError):
        p.transcribe(ch, STTContext(chunk_duration=300))
