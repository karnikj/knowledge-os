"""F3: index, webové API, hlasové poznámky (bez sítě; STT a analýza napodobené)."""
import json
import shutil
import subprocess

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pipeline import index, voice  # noqa: E402
from pipeline.providers.base import RawSegment  # noqa: E402
from pipeline.web.app import create_app  # noqa: E402
from tests.test_f2 import FakeAnalysis, analysed  # noqa: E402,F401


@pytest.fixture
def client(analysed):
    vault, _, _ = analysed
    from pipeline import render
    render.run(vault, "yt:demoVideo01")
    index.build(vault)
    return vault, TestClient(create_app(vault))


def test_search_and_facets(client):
    vault, c = client
    r = c.get("/api/search", params={"q": "Pandasu"}).json()
    assert r["total"] == 1 and r["items"][0]["url"].endswith("?t=5") and r["items"][0]["speaker"] == "Martin Jirsák"
    assert c.get("/api/search", params={"q": "pandas", "speaker": "Moderátor"}).json()["total"] == 0
    assert c.get("/api/search", params={"q": "reporty", "since": "2026-08-01"}).json()["total"] == 0


def test_entities_mentions_patch_merge(client):
    vault, c = client
    ents = {e["id"]: e for e in c.get("/api/entities").json()}
    assert ents["pandas"]["n_mentions"] == 1
    m = c.get("/api/entities/power-bi/mentions").json()
    assert m["total"] == 1 and m["by_attitude"] == {"negative": 1}
    assert c.patch("/api/entities/pandas", json={"status": "potvrzeno"}).json()["status"] == "potvrzeno"
    assert c.post("/api/entities/claude/merge", json={"into": "anthropic"}).json()["into"] == "anthropic"
    reg = json.loads(json.dumps(__import__("yaml").safe_load((vault.config_dir / "entities.yaml").read_text(encoding="utf-8"))))
    assert "claude" not in reg and "Claude" in reg["anthropic"]["aliases"]


def test_add_source_and_share(client):
    vault, c = client
    r = c.post("/api/sources", json={"url": "https://youtu.be/BBBBBBBBBBB"}).json()
    assert r["ok"] and r["source_id"] == "yt:BBBBBBBBBBB"
    assert "už ve vaultu" in c.post("/api/sources", json={"url": "https://youtu.be/demoVideo01"}).json()["message"]
    r = c.post("/share", data={"text": "Koukni https://www.youtube.com/watch?v=CCCCCCCCCCC"}, follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/api/sources").json()["queue"] == ["https://www.youtube.com/watch?v=BBBBBBBBBBB",
                                                    "https://www.youtube.com/watch?v=CCCCCCCCCCC"]
    assert c.get("/").status_code == 200 and c.get("/manifest.webmanifest").json()["share_target"]["action"] == "/share"


class FakeSTT:
    engine, prompt_version = "fake-stt", 1

    def transcribe(self, chunk, ctx):
        return [RawSegment(0.5, 4, "Mluvčí 1", "Zítra zavolat Petrovi kvůli projektu Knowledge OS."),
                RawSegment(4.5, 9, "Mluvčí 1", "Nápad: přidat do aplikace export do Power BI.")]


class VoiceAnalysis:
    engine, prompt_version = "fake-analysis", 1

    def analyze_window(self, lines, ctx):
        assert ctx["kind"] == "osobní hlasová poznámka"
        return {"summary": "Úkol a nápad.", "chapters": [{"segment": 0, "title": "Úkoly"}],
                "claims": [{"speaker": "Já", "segment_ids": [1], "quote": "přidat do aplikace export do Power BI",
                            "claim": "Nápad na export do Power BI.", "type": "recommendation", "volatility": "low",
                            "attitudes": []}],
                "mentions": [{"segment": 1, "surface": "Power BI", "name": "Power BI", "type": "software"}]}

    def summarize(self, ctx, sums, chapters):
        return {"tldr": "Úkol zavolat Petrovi a nápad na export.", "key_points": [], "topics": ["Plánování"],
                "dominant_volatility": "low"}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_voice_session_end_to_end(client, tmp_path):
    vault, c = client
    src = tmp_path / "rec.webm"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                    "-c:a", "libopus", "-f", "webm", str(src)], check=True)
    data = src.read_bytes()
    sess = c.post("/api/voice/sessions").json()["session"]
    parts = [data[:len(data) // 2], data[len(data) // 2:]]
    for i, p in enumerate(parts):
        assert c.put(f"/api/voice/sessions/{sess}/chunks/{i}", content=p).json()["ok"]
    assert c.put(f"/api/voice/sessions/{sess}/chunks/{i}", content=parts[1]).json()["chunks"] == 2   # opakování OK
    assert c.post(f"/api/voice/sessions/{sess}/finish", json={"chunks": 3}).json()["missing"] == [2]
    assert voice.ready_sessions(vault) == []                       # chybí kus → nezpracovávat
    assert c.post(f"/api/voice/sessions/{sess}/finish", json={"chunks": 2, "title": "Test"}).json()["finished"]
    assert c.get("/api/voice/notes").json()[0]["status"] == "zpracovává se"

    r = voice.process(vault, sess, stt_provider=FakeSTT(), analysis_provider=VoiceAnalysis())
    assert r["source_id"] == f"voice:{sess}" and "Power BI" in r["text"]
    assert (vault.assets / "voice" / f"{sess}.opus").exists() and not (vault.root / "18_Inbox_Hlas" / sess).exists()
    card = vault.root / r["card"]
    assert card.parent.name == "VoiceNotes" and "visibility: private" in card.read_text(encoding="utf-8")
    index.build(vault)
    notes = c.get("/api/voice/notes").json()
    assert notes[0]["source_id"] == f"voice:{sess}" and notes[0]["title"] == "Test"
    assert notes[0]["summary"].startswith("Úkol zavolat")
    assert c.get("/api/search", params={"q": "zavolat", "source_type": "voice_note"}).json()["total"] == 1


def test_bad_session_id_rejected(client):
    _, c = client
    assert c.put("/api/voice/sessions/..%2F..%2Fetc/chunks/0", content=b"x").status_code in (400, 404)


def test_blocked_youtube_goes_to_local_queue(client, monkeypatch):
    from pipeline import queue, worker
    vault, c = client
    queue.add(vault, "https://www.youtube.com/watch?v=AAAAAAAAAAA")

    def boom(v, url):
        raise RuntimeError("ERROR: [youtube] AAAAAAAAAAA: Sign in to confirm you're not a bot")
    monkeypatch.setattr(worker, "process_youtube", boom)
    done = worker.step(vault)
    assert queue.read(vault) == []
    assert queue.read(vault, queue.local_file(vault)) == ["https://www.youtube.com/watch?v=AAAAAAAAAAA"]
    assert worker.failed_list(vault) == []
    assert "PC" in done[0]
    assert c.get("/api/sources").json()["queue_local"]
    # PC zpracuje lokální frontu
    monkeypatch.setattr(worker, "process_youtube", lambda v, u: None)
    assert worker.step(vault, local=True) == ["video AAAAAAAAAAA"]
    assert queue.read(vault, queue.local_file(vault)) == []


def test_voice_retry(client):
    vault, c = client
    s = voice.new_session(vault)
    voice.put_chunk(vault, s, 0, b"x")
    voice.finish(vault, s, 1)
    m = voice.inbox(vault) / s / "meta.json"
    meta = json.loads(m.read_text()); meta.update(finished=False, error="503"); m.write_text(json.dumps(meta))
    assert voice.ready_sessions(vault) == []
    assert c.get("/api/status").json()["voice_errors"] == 1
    assert c.post("/api/voice/retry").json()["retried"] == [s]
    assert voice.ready_sessions(vault) == [s]


def test_remove_source_everywhere(client):
    from pipeline import catalog, queue, remove
    vault, c = client
    sid = "yt:demoVideo01"
    assert c.get("/api/search", params={"q": "Pandasu"}).json()["total"] > 0
    before = remove.files_for(vault, sid)
    assert any(p.suffix == ".md" for p in before)
    r = c.delete(f"/api/sources/{sid}").json()
    assert r["files"] and not remove.files_for(vault, sid)
    assert catalog.find(vault, sid) is None
    assert c.get("/api/search", params={"q": "Pandasu"}).json()["total"] == 0
    # nepotvrzené entity bez zmínek zmizely z registru
    from pipeline.entities import Registry
    reg = Registry.load(vault)
    assert set(r["entities_removed"]).isdisjoint(reg.data)
    # znovu přidat nejde bez force
    msg = c.post("/api/sources", json={"url": "https://youtu.be/demoVideo01"}).json()
    assert msg["was_removed"] and not msg["ok"]
    msg = c.post("/api/sources", json={"url": "https://youtu.be/demoVideo01", "force": True}).json()
    assert msg["ok"] and queue.read(vault)
    assert c.delete(f"/api/sources/{sid}").status_code == 404


def test_voice_discard(client):
    vault, c = client
    s = c.post("/api/voice/sessions").json()["session"]
    c.put(f"/api/voice/sessions/{s}/chunks/0", content=b"x")
    assert c.delete(f"/api/voice/sessions/{s}").json()["ok"]
    assert not (voice.inbox(vault) / s).exists()
    assert c.delete(f"/api/voice/sessions/{s}").status_code == 404
    assert c.delete("/api/voice/sessions/..%2Fetc").status_code in (400, 404)


def test_quota_exhausted_pauses_worker_and_keeps_work(client, monkeypatch):
    from pipeline import queue, worker
    from pipeline.providers.gemini_stt import QuotaExhausted
    vault, c = client
    s = voice.new_session(vault); voice.put_chunk(vault, s, 0, b"x"); voice.finish(vault, s, 1)

    def boom(*a, **k):
        raise QuotaExhausted("limit")
    monkeypatch.setattr(voice, "process", boom)
    assert worker.step(vault) == []
    assert worker.quota_pause(vault) and voice.ready_sessions(vault) == [s]     # nic se neztratilo
    assert c.get("/api/status").json()["quota_pause"]["until"]
    called = []
    monkeypatch.setattr(voice, "process", lambda *a, **k: called.append(1))
    worker.step(vault)
    assert called == []                                                          # během pauzy se nevolá


def test_overloaded_gemini_keeps_video_in_queue(client, monkeypatch):
    from pipeline import queue, worker
    vault, c = client
    queue.add(vault, "https://www.youtube.com/watch?v=BBBBBBBBBBB")

    def boom(v, url):
        raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
    monkeypatch.setattr(worker, "process_youtube", boom)
    worker.step(vault)
    assert queue.read(vault) == ["https://www.youtube.com/watch?v=BBBBBBBBBBB"]
    assert worker.failed_list(vault) == [] and worker.quota_pause(vault)


def test_ask_answers_only_from_sources(client, monkeypatch):
    from pipeline import analyze
    vault, c = client

    class FakeAsk:
        def generate(self, prompt, schema):
            assert "Pandas" in prompt and "[1]" in prompt and "POUZE" in prompt
            return {"answer": "Martin Jirsák používá Pandas na reporty [1].", "enough": True, "used": [1]}
    monkeypatch.setattr(analyze, "default_provider", lambda *a, **k: FakeAsk())
    r = c.post("/api/ask", json={"question": "K čemu se používá Pandas?"}).json()
    assert r["enough"] and r["citations"][0]["n"] == 1 and r["citations"][0]["url"].startswith("https://youtu.be/")
    r = c.post("/api/ask", json={"question": "Kdo mluvil o sopkách na Islandu?"}).json()
    assert r["citations"] == [] and not r["enough"]


def test_dequeue_and_resume(client):
    from pipeline import queue, worker
    vault, c = client
    queue.add(vault, "https://www.youtube.com/watch?v=CCCCCCCCCCC")
    worker._set_quota_pause(vault, RuntimeError("503"), minutes=30)
    assert c.get("/api/sources").json()["pause"]
    assert c.post("/api/worker/resume").json()["ok"] and not worker.quota_pause(vault)
    assert c.delete("/api/queue", params={"url": "https://www.youtube.com/watch?v=CCCCCCCCCCC"}).json()["ok"]
    assert queue.read(vault) == []


def test_queue_retry_moves_to_front(client):
    from pipeline import queue, worker
    vault, c = client
    for v in ("DDDDDDDDDDD", "EEEEEEEEEEE"):
        queue.add(vault, f"https://www.youtube.com/watch?v={v}")
    worker._set_quota_pause(vault, RuntimeError("503"), minutes=30)
    assert c.post("/api/queue/retry", params={"url": "https://www.youtube.com/watch?v=EEEEEEEEEEE"}).json()["ok"]
    assert queue.read(vault)[0].endswith("EEEEEEEEEEE") and not worker.quota_pause(vault)
    assert c.post("/api/queue/retry", params={"url": "https://youtu.be/FFFFFFFFFFF"}).status_code == 404


def test_chat_levels_and_paging(client, monkeypatch):
    from pipeline import analyze, ask
    vault, c = client
    kinds = []

    class Fake:
        def generate(self, prompt, schema):
            import re
            nums = sorted(set(int(x) for x in re.findall(r"^\[(\d+)\]", prompt, re.M)))
            return {"answer": " ".join(f"bod [{n}]" for n in nums), "enough": True}
    monkeypatch.setattr(analyze, "default_provider", lambda v, kind="private": kinds.append(kind) or Fake())
    monkeypatch.setitem(ask.LEVELS["full"], "n", 1)
    r = c.post("/api/chat", json={"question": "Pandas reporty", "level": "full"}).json()
    assert r["level"] == "full" and r["range"] == [1, 1] and r["usage"]["cost_usd"] == 0
    if r["total_snippets"] > 1:
        assert r["has_more"]
        r2 = c.post("/api/chat", json={"question": "Pandas reporty", "level": "full", "page": 1}).json()
        assert r2["range"] == [2, 2]
    assert kinds[0] == "chat"                     # veřejné video → levný model, ne Gemini
    r = c.post("/api/chat", json={"question": "Pandas", "level": "light"}).json()
    assert r["citations"] and "usage" in r


def test_jobs_overview_search_and_failed(client):
    vault, c = client
    (vault.system / "queue_failed.txt").write_text(
        "https://www.youtube.com/watch?v=AAAAAAAAAAA\t2026-09-25T10:00:00\tHTTP 403 Forbidden\n", encoding="utf-8")
    r = c.get("/api/jobs").json()
    assert r["counts"]["failed"] == 1 and r["counts"]["done"] >= 1
    done = [i for i in r["items"] if i["group"] == "done"]
    assert done[0]["source_id"] == "yt:demoVideo01" and "delete_source" in done[0]["actions"]
    # hledání v názvu/chybě bez diakritiky i v obsahu přepisu
    assert [i["group"] for i in c.get("/api/jobs", params={"q": "forbidden"}).json()["items"]] == ["failed"]
    hit = c.get("/api/jobs", params={"q": "Pandasu"}).json()["items"]
    assert hit and hit[0]["source_id"] == "yt:demoVideo01" and "hit" in hit[0]
    assert c.delete("/api/queue/failed", params={"url": "https://www.youtube.com/watch?v=AAAAAAAAAAA"}).json()["ok"]
    assert c.get("/api/jobs").json()["counts"]["failed"] == 0


def test_preview_source(client):
    vault, c = client
    r = c.get("/api/preview/yt:demoVideo01").json()
    assert r["text"] and r["audio"] is None
    assert c.get("/api/preview/yt:neexistuje").status_code == 404
    assert c.get("/api/voice/audio/voice:neexistuje").status_code == 404


def test_entity_reject_group_and_cleanup(client, monkeypatch):
    import time
    from pipeline import analyze, index
    vault, c = client
    assert c.patch("/api/entities/pandas", json={"status": "zamítnuto"}).json()["status"] == "zamítnuto"
    assert c.patch("/api/entities/pandas", json={"status": "blbost"}).status_code == 400
    index.build(vault)
    ids = {e["id"] for e in c.get("/api/entities").json()}
    assert "pandas" not in ids
    assert [e["id"] for e in c.get("/api/entities", params={"status": "zamítnuto"}).json()] == ["pandas"]
    assert all(e["type"] not in ("tema", "misto") for e in c.get("/api/entities", params={"group": "main"}).json())
    assert c.get("/api/entities/pandas/mentions").json()["total"] == 0        # zmínky zamítnuté se neindexují

    class Fake:
        def generate(self, prompt, schema):
            if "power-bi" not in prompt:
                return {"items": []}
            return {"items": [{"id": "power-bi", "action": "retype", "new_type": "sluzba", "why": "je to služba"},
                              {"id": "claude", "action": "merge", "into": "anthropic", "why": "x"},
                              {"id": "anthropic", "action": "merge", "into": "claude", "why": "opačně"},
                              {"id": "neexistuje", "action": "reject"},
                              {"id": "pandas", "action": "reject"}]}           # už zamítnutá → vynechat
    monkeypatch.setattr(analyze, "default_provider", lambda *a, **k: Fake())
    c.post("/api/entities/cleanup/suggest")
    for _ in range(100):
        st = c.get("/api/entities/cleanup").json()
        if st["status"] != "running":
            break
        time.sleep(0.05)
    assert st["status"] == "done", st
    items = st["items"]
    merges = [i for i in items if i["action"] == "merge"]
    assert len(merges) == 1 and {merges[0]["id"], merges[0]["into"]} == {"claude", "anthropic"}   # jen jeden směr
    assert ("power-bi", "retype") in {(i["id"], i["action"]) for i in items} and len(items) == 2
    r = c.post("/api/entities/cleanup/apply", json={"items": items}).json()
    assert r["retype"] == 1 and r["merge"] == 1
    reg = __import__("yaml").safe_load((vault.config_dir / "entities.yaml").read_text(encoding="utf-8"))
    assert reg["power-bi"]["type"] == "sluzba" and merges[0]["id"] not in reg and merges[0]["into"] in reg
