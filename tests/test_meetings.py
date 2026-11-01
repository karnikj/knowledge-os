import shutil
import subprocess

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pipeline import meetings, voice, worker  # noqa: E402
from pipeline.providers.base import RawSegment  # noqa: E402
from pipeline.web.app import create_app  # noqa: E402
from tests.test_f2 import FakeAnalysis, analysed  # noqa: E402,F401


@pytest.fixture
def mc(analysed):
    vault, _, _ = analysed
    return vault, TestClient(create_app(vault))


class MeetSTT:
    engine, prompt_version = "fake-stt", 1

    def transcribe(self, chunk, ctx):
        return [RawSegment(0.5, 4, "Mluvčí 1", "Dobrý den, Petře, začneme rozpočtem na příští rok."),
                RawSegment(4.5, 9, "Mluvčí 2", "Rozpočet schvalujeme ve výši dva miliony."),
                RawSegment(9.5, 14, "Mluvčí 1", "Petr připraví podklady do pátku.")]


class MeetFill:
    engine, prompt_version = "fake-fill", 1

    def __init__(self):
        self.prompt = None

    def generate(self, prompt, schema):
        self.prompt = prompt
        assert "ukoly" in schema["properties"]["fields"]["properties"]
        return {"speakers": [{"label": "Mluvčí 2", "name": "Petr"}, {"label": "Mluvčí 1", "name": ""}],
                "fields": {"nazev": {"text": "Rozpočet 2027", "segs": [0]},
                           "shrnuti": {"text": "Schválen rozpočet.", "segs": [1, 99]},
                           "rozhodnuti": [{"text": "Rozpočet 2 mil. Kč schválen.", "segs": [1]}],
                           "ukoly": [{"co": "Připravit podklady", "kdo": "Petr", "termin": "pátek", "segs": [2]}],
                           "ucastnici": [{"text": "Petr"}, {"text": "Jana"}]}}


def test_templates_fixed(mc):
    vault, c = mc
    r = c.get("/api/meeting-templates").json()
    assert [t["name"] for t in r["templates"]] == ["Zápis ze schůzky", "Zápis z porady"]
    assert c.post("/api/meeting-templates", json={"name": "X", "fields": [{"label": "A"}]}).status_code == 400
    assert c.delete("/api/meeting-templates/schuzka").status_code == 400
    m = c.post("/api/meetings", json={"template": "porada"}).json()
    assert m["template"]["name"] == "Zápis z porady" and "kontrola" in m["values"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_meeting_end_to_end(mc, tmp_path):
    vault, c = mc
    m = c.post("/api/meetings", json={}).json()
    mid = m["id"]
    assert m["status"] == "draft" and m["values"]["datum"]["text"]
    # uživatel předvyplní účastníka a vlastní shrnutí
    c.put(f"/api/meetings/{mid}", json={"values": {"ucastnici": [{"text": "Jana"}],
                                                    "shrnuti": {"text": "Moje shrnutí."}}})
    src = tmp_path / "rec.webm"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=14",
                    "-c:a", "libopus", "-f", "webm", str(src)], check=True)
    sess = c.post("/api/voice/sessions", headers={"x-meeting": mid}).json()["session"]
    assert c.get(f"/api/meetings/{mid}").json()["status"] == "recording"
    c.put(f"/api/voice/sessions/{sess}/chunks/0", content=src.read_bytes())
    c.post(f"/api/voice/sessions/{sess}/finish", json={"chunks": 1})
    assert c.get(f"/api/meetings/{mid}").json()["status"] == "processing"

    fill = MeetFill()
    meetings.process(vault, mid, sess, stt_provider=MeetSTT(), fill_provider=fill)
    m = c.get(f"/api/meetings/{mid}").json()
    assert "Jana" in fill.prompt and "[2]" in fill.prompt
    assert m["status"] == "review" and m["review_until"] and len(m["transcript"]) == 3 and m["has_audio"]
    assert m["values"]["nazev"]["text"] == "Rozpočet 2027" and m["values"]["nazev"]["ai"]
    assert m["values"]["shrnuti"]["text"] == "Moje shrnutí."                      # uživatelův text zůstal
    assert m["suggestions"]["shrnuti"]["segs"] == [1]                              # neplatný seg 99 zahozen
    assert [p["text"] for p in m["values"]["ucastnici"]] == ["Jana", "Petr"]        # doplněno bez duplicity
    assert m["values"]["ukoly"][0]["kdo"] == "Petr" and m["values"]["ukoly"][0]["ai"]
    lab = {v: k for k, v in m["speaker_labels"].items()}
    assert m["speakers"] == {lab["Mluvčí 1"]: "", lab["Mluvčí 2"]: "Petr"}
    assert not (vault.root / "18_Inbox_Hlas" / sess).exists()
    assert c.get(f"/api/meetings/{mid}/pdf").status_code == 400                    # PDF až po schválení

    # předpokoj: upřesnit mluvčího a termín, pak schválit
    m["values"]["ukoly"][0]["termin"] = "3. 10. 2026"
    r = c.post(f"/api/meetings/{mid}/approve", json={"values": {"ukoly": m["values"]["ukoly"]},
                                                     "speakers": {lab["Mluvčí 1"]: "Jana", lab["Mluvčí 2"]: "Petr"}}).json()
    assert r["status"] == "approved" and not r["auto_approved"]
    card = (vault.root / r["card"]).read_text(encoding="utf-8")
    assert r["card"].startswith("14_Internal/Schuzky/") and "- [ ] Připravit podklady — Petr — do: 3. 10. 2026" in card
    assert c.put(f"/api/meetings/{mid}", json={"values": {}}).status_code == 400    # schválené už ne
    hits = c.get("/api/search", params={"q": "podklady"}).json()
    assert hits["total"] == 1 and hits["items"][0]["speaker"] == "Jana"             # mluvčí přejmenován
    pytest.importorskip("weasyprint")
    pdf = c.get(f"/api/meetings/{mid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_auto_approve_after_deadline(mc):
    vault, c = mc
    m = meetings.new_meeting(vault)
    m.update(status="review", review_until="2000-01-01T00:00:00")
    m["values"]["nazev"] = {"text": "Stará schůzka", "segs": [], "ai": True}
    meetings.save(vault, m)
    done = worker.step(vault)
    assert any("automaticky" in d for d in done)
    m = meetings.load(vault, m["id"])
    assert m["status"] == "approved" and m["auto_approved"]
    assert "nebyl zkontrolován" in (vault.root / m["card"]).read_text(encoding="utf-8")


def test_discard_meeting_recording_returns_to_draft(mc):
    vault, c = mc
    mid = c.post("/api/meetings", json={}).json()["id"]
    sess = c.post("/api/voice/sessions", headers={"x-meeting": mid}).json()["session"]
    assert c.delete(f"/api/voice/sessions/{sess}").json()["ok"]
    assert c.get(f"/api/meetings/{mid}").json()["status"] == "draft"
    assert c.post("/api/voice/sessions", headers={"x-meeting": "20990101-000000-zzzz"}).status_code == 404


def test_edit_during_processing_keeps_ai_and_user(mc):
    """Uživatel psal během zpracování (klient zná stav processing), AI mezitím zápis vyplnila."""
    vault, c = mc
    m = c.post("/api/meetings", json={}).json()
    mid = m["id"]
    full = meetings.load(vault, mid)
    full["status"] = "review"
    full["values"]["shrnuti"] = {"text": "Shrnutí od AI.", "segs": [1], "ai": True}
    full["values"]["ukoly"] = [{"co": "Úkol od AI", "kdo": "", "termin": "", "segs": [1], "ai": True, "hotovo": False}]
    meetings.save(vault, full)
    r = c.put(f"/api/meetings/{mid}", json={"base_status": "processing",
              "values": {"shrnuti": {"text": "Moje shrnutí.", "segs": [], "ai": False},
                         "ukoly": [{"co": "Můj úkol", "kdo": "Já", "termin": "", "segs": [], "ai": False}]},
              "suggestions": {}}).json()
    assert r["values"]["shrnuti"]["text"] == "Moje shrnutí."
    assert r["suggestions"]["shrnuti"]["text"] == "Shrnutí od AI."
    assert [u["co"] for u in r["values"]["ukoly"]] == ["Můj úkol", "Úkol od AI"]
    # běžná úprava v předpokoji (klient zná review) přepisuje normálně
    r = c.put(f"/api/meetings/{mid}", json={"base_status": "review", "values": {"ukoly": []}}).json()
    assert r["values"]["ukoly"] == []


def test_rename_speakers_in_values_and_search_text():
    m = {"template": meetings.DEFAULT_TEMPLATE, "speaker_labels": {"S0": "Mluvčí 1", "S1": "Mluvčí 10"},
         "speakers": {"S0": "Jana Nováková", "S1": ""},
         "values": {"shrnuti": {"text": "Mluvčí 1 navrhla, Mluvčí 10 souhlasil.", "segs": [], "ai": True},
                    "ukoly": [{"co": "Poslat nabídku", "kdo": "Mluvčí 1", "termin": "", "segs": [], "ai": True}],
                    "ucastnici": [{"text": "Dana Skalová, Moneta", "segs": [], "ai": False}]},
         "suggestions": {}}
    assert meetings.rename_speakers_in_values(m) == 2
    assert m["values"]["shrnuti"]["text"] == "Jana Nováková navrhla, Mluvčí 10 souhlasil."
    assert m["values"]["ukoly"][0]["kdo"] == "Jana Nováková"
    assert "Moneta" in meetings._search_text(m) and "Poslat nabídku" in meetings._search_text(m)
