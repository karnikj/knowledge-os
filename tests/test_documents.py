"""Dokumenty (MD, TXT, DOCX, PDF, web) a hybridní hledání (slova + význam)."""
import io
import json
import zipfile

import pytest

from pipeline import docparse, documents, index, vectors
from pipeline.docparse import Unsupported


@pytest.fixture(autouse=True)
def _hash_vectors(monkeypatch):
    monkeypatch.setenv("KOS_VECTOR_MODEL", "hash-test")     # bez stahování modelu


def _pdf(pages: list[list[tuple[str, float]]]) -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for lines in pages:
        p = doc.new_page()
        y = 72
        for text, size in lines:
            p.insert_text((72, y), text, fontsize=size)
            y += size * 1.8
    return doc.tobytes()


def _docx(paras: list[tuple[str, str]]) -> bytes:
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    ppr = lambda st: ('<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % st) if st else ""      # noqa: E731
    body = "".join(f"<w:p>{ppr(st)}<w:r><w:t>{t}</w:t></w:r></w:p>" for st, t in paras)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document {W}><w:body>{body}</w:body></w:document>')
        z.writestr("word/styles.xml", f'<w:styles {W}><w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
                                      f'<w:style w:styleId="Heading2"><w:name w:val="heading 2"/></w:style></w:styles>')
    return buf.getvalue()


MD = """# Směrnice o cestovních náhradách

## Pracovní cesta
Pracovní cestu schvaluje vedoucí zaměstnance předem. Zaměstnanec vyplní cestovní příkaz v systému.

## Použití soukromého vozidla
Při použití soukromého auta náleží zaměstnanci sazba základní náhrady za každý 1 km jízdy a náhrada za pohonné hmoty.
"""


# ------------------------------------------------------------------ převod
def test_parse_markdown_sections_and_title():
    r = docparse.parse("smernice.md", MD.encode())
    assert r["title"] == "Směrnice o cestovních náhradách" and r["kind"] == "md" and r["language"] == "cs"
    secs = [c["section"] for c in r["chunks"]]
    assert "Směrnice o cestovních náhradách › Použití soukromého vozidla" in secs


def test_parse_docx_headings_and_tables():
    data = _docx([("Heading1", "Instalace"), ("", "Spusťte instalátor a potvrďte licenci."),
                  ("Heading2", "Požadavky"), ("", "Windows 11 a 8 GB RAM.")])
    r = docparse.parse("navod.docx", data)
    assert r["title"] == "Instalace"
    assert any(c["section"] == "Instalace › Požadavky" and "8 GB" in c["text"] for c in r["chunks"])


def test_parse_pdf_pages_headings_and_scan_rejected():
    data = _pdf([[("Conditional Access", 20), ("Require multifactor authentication for all users in the tenant.", 10),
                  ("Create a policy in Microsoft Entra admin center and select Grant.", 10)],
                 [("Device compliance", 20), ("Intune compliance policies define the rules devices must meet.", 10)]])
    r = docparse.parse("entra.pdf", data)
    assert r["pages"] == 2 and r["language"] == "en"
    by = {c["section"]: c for c in r["chunks"]}
    assert by["Device compliance"]["page"] == 2 and "Intune" in by["Device compliance"]["text"]
    with pytest.raises(Unsupported, match="sken"):
        docparse.parse("sken.pdf", _pdf([[("x", 10)]] * 3))


def test_unsupported_formats():
    with pytest.raises(Unsupported, match="není podporovaný"):
        docparse.parse("obrazek.jpg", b"\xff\xd8")
    with pytest.raises(Unsupported, match="neobsahuje"):
        docparse.parse("prazdny.txt", b"   \n  ")


def test_parse_html_main_content():
    html = ("<html><head><title>Configure MFA | Microsoft Learn</title></head><body><nav>Menu Home Docs</nav>"
            "<main><h1>Configure MFA</h1>" + "<p>Sign in to the Entra admin center and open Protection.</p>" * 12 +
            "<h2>Next steps</h2><p>Review the sign-in logs.</p></main><footer>© Microsoft</footer></body></html>")
    r = docparse.parse("mfa.html", html.encode())
    assert r["title"] == "Configure MFA"
    assert all("Menu" not in c["text"] and "©" not in c["text"] for c in r["chunks"])


# ------------------------------------------------------------------ příjem, duplicity, verze
def test_ingest_duplicate_version_and_index(vault):
    r = documents.ingest(vault, "smernice_cestovne_v1.md", MD.encode())
    assert r["status"] == "ok" and r["source_id"].startswith("file:")
    sid1 = r["source_id"]
    assert documents.ingest(vault, "jiny_nazev.md", MD.encode())["status"] == "duplicate"   # stejný obsah
    v2 = MD.replace("sazba základní náhrady", "sazba 5,80 Kč základní náhrady")
    r2 = documents.ingest(vault, "smernice_cestovne_v2.md", v2.encode())
    assert r2["status"] == "maybe_version" and r2["candidates"][0]["source_id"] == sid1
    r3 = documents.ingest(vault, "smernice_cestovne_v2.md", v2.encode(), replace=sid1)
    assert r3["status"] == "ok"
    old = documents.load(vault, sid1)["meta"]
    assert old["archived"] and old["superseded_by"] == r3["source_id"]
    index.build(vault)
    con = index.connect(vault)
    assert con.execute("SELECT archived FROM sources WHERE source_id=?", (sid1,)).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM segments WHERE source_id=?", (sid1,)).fetchone()[0] == 0   # mimo hledání
    assert con.execute("SELECT COUNT(*) FROM chunks WHERE source_id=?", (r3["source_id"],)).fetchone()[0] >= 2
    row = con.execute("SELECT source_type, visibility FROM sources WHERE source_id=?", (r3["source_id"],)).fetchone()
    assert tuple(row) == ("document", "private")
    # smazání nové verze vrátí předchozí z archivu
    from pipeline import remove
    assert remove.remove(vault, r3["source_id"])["restored_version"] == sid1
    assert not documents.load(vault, sid1)["meta"].get("archived")


def test_inbox_intake_and_rejects(vault):
    box = vault.root / documents.INBOX
    box.mkdir(exist_ok=True)
    (box / "postup.md").write_text(MD, encoding="utf-8")
    (box / "foto.png").write_bytes(b"\x89PNG....")
    done = documents.intake_inbox(vault)
    assert "dokument postup.md" in done and "odmítnut foto.png" in done
    assert not (box / "postup.md").exists() and (box / "_nepodporovane" / "foto.png").exists()
    assert documents.failed_list(vault)[0]["name"] == "foto.png"
    assert len(documents.pending_cards(vault)) == 1


# ------------------------------------------------------------------ AI karta
class FakeCard:
    engine = "fake-card"

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, schema):
        self.prompts.append(prompt)
        return {"doc_type": "interní norma / směrnice", "summary": "Směrnice upravuje pracovní cesty a náhrady.",
                "key_points": ["Cestu schvaluje vedoucí předem."], "applies_to": "zaměstnanci firmy",
                "validity": "", "terms": ["cestovní příkaz"], "topics": ["cestovní náhrady"],
                "entities": [{"name": "Neexistující s.r.o.", "type": "organizace", "surface": "Neexistující"}]}


def test_make_card_renders_obsidian_card(vault):
    sid = documents.ingest(vault, "smernice.md", MD.encode())["source_id"]
    a = documents.make_card(vault, sid, FakeCard())
    assert a["doc_type"].startswith("interní") and a["chapters"] and documents.pending_cards(vault) == []
    card = next((vault.internal / "Dokumenty").glob("*.md")).read_text(encoding="utf-8")
    assert "typ: dokument" in card and "## 📌 Shrnutí" in card and "source_type: document" in card
    # entita, která v textu není, se nezapíše (zmínky jsou doložené)
    m = json.loads((vault.transcripts / "file__{}.mentions.json".format(sid.split(":")[1])).read_text(encoding="utf-8"))
    assert m["mentions"] == []


# ------------------------------------------------------------------ hybridní hledání + chat
EN = """# Conditional Access
Require multifactor authentication for all users. In the Entra admin center create a policy and choose Grant.

# Intune compliance
Compliance policies define the rules and settings that devices must meet to be considered compliant.
"""


class FakeChat:
    engine = "fake-chat"

    def __init__(self):
        self.prompt = ""

    def generate(self, prompt, schema):
        self.prompt = prompt
        return {"answer": "Politiku vytvoříte v Entra [1].", "enough": True, "mentions": []}


def test_vectors_and_semantic_retrieval(vault):
    from pipeline import ask
    from pipeline.entities import Registry
    documents.ingest(vault, "entra.md", EN.encode(), visibility="public")
    sid = documents.ingest(vault, "smernice.md", MD.encode())["source_id"]
    index.build(vault)
    assert vectors.ensure(vault) >= 3
    assert vectors.ensure(vault) == 0                           # podruhé nic (cache podle hashe)
    con = index.connect(vault)
    s = vectors.Searcher(vault)
    hits = s.search(con, "compliance policies devices rules", k=3)
    assert hits and con.execute("SELECT title FROM sources WHERE source_id=?", (hits[0]["source_id"],)).fetchone()[0] == "Conditional Access"
    sem = lambda q, k, a, b, src: s.search(con, q, k, a, b, src)              # noqa: E731
    r = ask.retrieve(con, Registry.load(vault), "compliance devices", semantic=sem)
    assert r
    # zeptat se na jeden dokument: malý dokument dostane model celý, citace mají kapitolu
    fake = FakeChat()
    out = ask.chat(con, Registry.load(vault), "Kdo schvaluje cestu?", lambda private: fake, source_id=sid, semantic=sem)
    assert out["whole_document"] and "Dokument „Směrnice o cestovních náhradách“" in fake.prompt
    assert out["citations"][0]["source_type"] == "document" and out["private"]


def test_web_documents_api(vault):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from pipeline.web.app import create_app
    c = TestClient(create_app(vault))
    r = c.post("/api/documents", files=[("file", ("smernice.md", MD.encode(), "text/markdown")),
                                         ("file", ("fotka.jpg", b"xx", "image/jpeg"))], data={"visibility": "private"}).json()
    ok, bad = r["results"]
    assert ok["status"] == "ok" and bad["status"] == "error" and "není podporovaný" in bad["error"]
    sid = ok["source_id"]
    v2 = MD.replace("předem", "předem písemně")
    r = c.post("/api/documents", files=[("file", ("smernice.md", v2.encode(), "text/markdown"))]).json()["results"][0]
    assert r["status"] == "maybe_version" and r["token"]
    assert len(c.get("/api/documents").json()["pending"]) == 1
    d = c.post("/api/documents/decide", json={"token": r["token"], "action": "replace", "replace": sid}).json()
    assert d["status"] == "ok" and d["replaced"] == sid
    lst = c.get("/api/documents").json()
    assert [x["source_id"] for x in lst["items"]] == [d["source_id"]] and lst["pending"] == []
    assert len(c.get("/api/documents", params={"archived": True}).json()["items"]) == 2
    assert c.get(f"/api/documents/{d['source_id']}/file").status_code == 200
    pv = c.get(f"/api/preview/{d['source_id']}").json()
    assert "písemně" in pv["text"] and pv["file"]
    jobs = c.get("/api/jobs").json()["items"]
    kinds = {(i["kind"], i["state"]) for i in jobs}
    assert ("document", "čeká na AI kartu") in kinds and ("document", "starší verze (archiv)") in kinds
    assert c.post(f"/api/documents/{d['source_id']}/visibility", json={"visibility": "public"}).json()["visibility"] == "public"


def test_share_target_file_and_link(vault, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from pipeline.web.app import create_app
    c = TestClient(create_app(vault))
    r = c.post("/share", files=[("file", ("smernice.md", MD.encode(), "text/markdown"))], data={"title": "x"},
               follow_redirects=False)
    assert r.status_code == 303 and "#dokumenty" in r.headers["location"] and "p%C5%99id%C3%A1no" in r.headers["location"]
    assert len(documents.metas(vault)) == 1
    html = ("<html><head><title>Návod</title></head><body><main><h1>Návod</h1>" + "<p>Postup nastavení krok za krokem.</p>" * 20
            + "</main></body></html>").encode()

    class R:
        status_code, content = 200, html
        headers = {"content-type": "text/html; charset=utf-8"}
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    r = c.post("/share", data={"text": "Mrkni https://example.com/navod"}, follow_redirects=False)
    assert "#dokumenty" in r.headers["location"]
    assert any(d["meta"].get("url") == "https://example.com/navod" for d in documents.metas(vault))


def test_glossary_fix_and_api(vault):
    from pipeline import glossary
    assert glossary.fix_text(vault, "Používám kloud kód, s kloudem v telefonu.")[0] == "Používám Claude Code, s Claudem v telefonu."
    assert glossary.fix_text(vault, "kloudovka zůstává")[0] == "kloudovka zůstává"
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from pipeline.web.app import create_app
    c = TestClient(create_app(vault))
    r = c.put("/api/glossary", json={"text": "# S\n- Mistral ← mistrál\n- Qwen ← kvén, kven\n"}).json()
    assert [e["term"] for e in r["entries"]] == ["Mistral", "Qwen"]
    t = {"source_id": "voice:x", "segments": [{"i": 0, "text": "Zkoušel jsem kvén a mistrál."}]}
    (vault.transcripts / "voice__x.json").write_text(json.dumps(t), encoding="utf-8")
    assert c.post("/api/glossary/apply").json()["fixes"] == 2
    assert "Qwen a Mistral" in (vault.transcripts / "voice__x.json").read_text(encoding="utf-8")
    assert glossary.prompt_terms(vault)[:2] == ["Mistral", "Qwen"]


def test_full_triage_keeps_only_relevant_and_caches():
    from pipeline import ask, usage
    calls = []

    class P:
        def generate(self, prompt, schema):
            calls.append(prompt)
            return {"relevant": [2, 4]}
    cands = [{"n": i, "title": f"T{i}", "speaker": "", "text": f"text {i}", "private": False,
              "block": f"[{i}] T{i}\ntext {i}\n"} for i in range(1, 6)]
    kept, checked = ask.triage("otázka", cands, lambda priv: P(), usage.Tracker(), ("q-test",))
    assert checked == 5 and [c["title"] for c in kept] == ["T2", "T4"]
    assert [c["n"] for c in kept] == [1, 2] and kept[1]["block"].startswith("[2] T4")
    kept2, _ = ask.triage("otázka", cands, lambda priv: P(), usage.Tracker(), ("q-test",))
    assert kept2 == kept and len(calls) == 1            # další stránka z paměti, bez nového volání
