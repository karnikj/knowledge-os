import json

import pytest

from pipeline.providers.base import STTContext
from pipeline.providers.gemini_stt import GeminiSTT, RateLimiter, parse_response


def test_parse_response_clamps_sorts_and_skips_empty():
    raw = json.dumps([{"start": 12, "end": 15, "speaker": "B", "text": "druhý"},
                      {"start": "00:01", "end": "00:04.5", "speaker": "A", "text": "první"},
                      {"start": 20, "end": 999, "speaker": "", "text": "konec"},
                      {"start": 5, "end": 6, "speaker": "A", "text": "  "}])
    segs = parse_response(raw, duration=30)
    assert [s.text for s in segs] == ["první", "druhý", "konec"]
    assert segs[0].start == 1.0 and segs[0].end == 4.5
    assert segs[2].end == 30 and segs[2].speaker == "Mluvčí 1"


def test_rate_limiter_waits_min_interval():
    now = [0.0]
    slept = []
    rl = RateLimiter(7, clock=lambda: now[0], sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    rl.wait(); now[0] += 2; rl.wait()
    assert slept == [5]


class _Resp:
    def __init__(self, text): self.text = text


class _FakeModels:
    def __init__(self, outcomes): self.outcomes, self.calls = list(outcomes), 0

    def generate_content(self, model, contents, config):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return _Resp(o)


class _FakeClient:
    def __init__(self, outcomes): self.models = _FakeModels(outcomes)


def test_transcribe_retries_on_429_then_succeeds(tmp_path):
    chunk = tmp_path / "c.ogg"; chunk.write_bytes(b"x")
    client = _FakeClient([RuntimeError("429 RESOURCE_EXHAUSTED"),
                          json.dumps([{"start": 0, "end": 2, "speaker": "A", "text": "ahoj"}])])
    stt = GeminiSTT(client=client, min_interval=0, backoff=0, sleep=lambda s: None)
    segs = stt.transcribe(chunk, STTContext(chunk_duration=10))
    assert client.models.calls == 2 and segs[0].text == "ahoj"


def test_transcribe_does_not_retry_non_retryable(tmp_path):
    chunk = tmp_path / "c.ogg"; chunk.write_bytes(b"x")
    client = _FakeClient([ValueError("400 INVALID_ARGUMENT")])
    stt = GeminiSTT(client=client, min_interval=0, backoff=0, sleep=lambda s: None)
    with pytest.raises(ValueError):
        stt.transcribe(chunk, STTContext(chunk_duration=10))
    assert client.models.calls == 1


def test_prompt_contains_context():
    p = GeminiSTT(client=object()).build_prompt(STTContext(title="Díl 68", known_speakers=["Robert Novotný"],
                                                           chunk_index=2, chunk_count=5, chunk_duration=300))
    assert "Díl 68" in p and "Robert Novotný" in p and "úsek 3 z 5" in p and "DOSLOVNĚ" in p


DAILY = ("429 RESOURCE_EXHAUSTED. You exceeded your current quota, please check your plan and billing details. "
         "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier. Please retry in 30.1s.")


def test_classify_errors():
    from pipeline.providers.gemini_stt import classify
    assert classify(RuntimeError(DAILY)) == "daily_quota"          # „billing“ v textu 429 není platební chyba
    assert classify(RuntimeError("429 RESOURCE_EXHAUSTED per minute")) == "rate"
    assert classify(RuntimeError("402 Your prepayment credits are depleted")) == "billing"
    assert classify(RuntimeError("503 UNAVAILABLE high demand")) == "overloaded"
    assert classify(RuntimeError("404 NOT_FOUND model x")) == "missing"
    assert classify(ValueError("400 INVALID_ARGUMENT")) is None


def test_daily_quota_switches_model_then_gives_up(tmp_path):
    from pipeline.providers.gemini_stt import QuotaExhausted
    chunk = tmp_path / "c.ogg"; chunk.write_bytes(b"x")
    ok = json.dumps([{"start": 0, "end": 2, "speaker": "A", "text": "ahoj"}])
    client = _FakeClient([RuntimeError(DAILY), ok])
    stt = GeminiSTT(model="m1", fallbacks=["m2"], client=client, min_interval=0, backoff=0, sleep=lambda s: None)
    assert stt.transcribe(chunk, STTContext(chunk_duration=10))[0].text == "ahoj" and stt.engine == "m2"
    client = _FakeClient([RuntimeError(DAILY), RuntimeError(DAILY)])
    stt = GeminiSTT(model="m1", fallbacks=["m2"], client=client, min_interval=0, backoff=0, sleep=lambda s: None)
    with pytest.raises(QuotaExhausted):
        stt.transcribe(chunk, STTContext(chunk_duration=10))


def test_backoff_doubles(tmp_path):
    chunk = tmp_path / "c.ogg"; chunk.write_bytes(b"x")
    waits = []
    client = _FakeClient([RuntimeError("500 INTERNAL")] * 3 + [json.dumps([{"start": 0, "end": 1, "text": "x"}])])
    stt = GeminiSTT(client=client, min_interval=0, backoff=8, sleep=waits.append)
    stt.transcribe(chunk, STTContext(chunk_duration=10))
    assert [round(w) // 8 for w in waits] == [1, 2, 4]
