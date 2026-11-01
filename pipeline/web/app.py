"""Webová aplikace Knowledge OS (FastAPI) — API + PWA.

Běží na VPS na 127.0.0.1:8080 a ven je publikovaná jen přes `tailscale serve` (HTTPS v soukromé síti),
proto bez přihlašování. Návrh API: 99_System/docs/MAPA_SYSTEMU.html §5.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import catalog, documents, ids, index, meetings, queue, state, transcript, vectors, voice, worker
from pipeline.docparse import Unsupported
from pipeline.config import Vault
from pipeline.entities import TYPES, Registry

STATIC = Path(__file__).parent / "static"


class AddSource(BaseModel):
    url: str
    force: bool = False


class EntityPatch(BaseModel):
    name: str | None = None
    type: str | None = None
    aliases: list[str] | None = None
    parent: str | None = None
    status: str | None = None


class Merge(BaseModel):
    into: str


class VoiceFinish(BaseModel):
    chunks: int
    title: str | None = None


def create_app(vault: Vault | None = None) -> FastAPI:
    vault = vault or Vault.default()
    app = FastAPI(title="Knowledge OS", docs_url="/api/docs", openapi_url="/api/openapi.json")
    lock = threading.Lock()
    holder: dict = {"con": None, "mtime": 0.0}

    def con():
        p = index.path(vault)
        if not p.exists():
            index.build(vault)
        m = p.stat().st_mtime
        with lock:
            if holder["con"] is None or m != holder["mtime"]:
                if holder["con"] is not None:
                    holder["con"].close()
                holder["con"], holder["mtime"] = index.connect(vault), m
            return holder["con"]

    # hledání podle významu: model a dopočítávání otisků běží jen v tomto procesu (web)
    vec_bg = vectors.Background(vault) if not os.getenv("PYTEST_CURRENT_TEST") and os.getenv("KOS_NO_VECTORS") != "1" else None

    def semantic(question, k, since, until, source_id):
        return vectors.searcher(vault).search(con(), question, k, since, until, source_id)

    pend_dir = vault.system / "logs" / "doc_pending"     # soubory čekající na rozhodnutí „nová verze?“

    def stage_pending(filename: str, data: bytes, visibility: str, result: dict, **extra) -> dict:
        import secrets
        token = secrets.token_hex(8)
        pend_dir.mkdir(parents=True, exist_ok=True)
        (pend_dir / f"{token}.bin").write_bytes(data)
        (pend_dir / f"{token}.json").write_text(json.dumps({"filename": filename, "visibility": visibility, "result": result,
                                                            **extra}, ensure_ascii=False), encoding="utf-8")
        return {**result, "token": token, "filename": filename}

    def reindex_async():
        """Po přidání dokumentu: index hned (je rychlý), otisky na pozadí."""
        index.build(vault)
        if vec_bg:
            vec_bg.kick.set()

    # ---------- zdroje a fronta ----------
    @app.post("/api/sources")
    def add_source(body: AddSource):
        msg = queue.add(vault, body.url.strip(), force=body.force)
        vid = ids.youtube_id(body.url)
        ok = msg.startswith("Přidáno") or "už ve frontě" in msg
        return {"ok": ok, "message": msg, "source_id": f"yt:{vid}" if vid else None,
                "was_removed": "dříve odstranil" in msg}

    @app.get("/api/sources")
    def list_sources(limit: int = 100):
        c = con()
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM sources ORDER BY published_at DESC LIMIT ?", (limit,))]
        st = worker.read_status(vault)
        return {"queue": queue.read(vault), "queue_local": queue.read(vault, queue.local_file(vault)),
                "pause": worker.quota_pause(vault),
                "failed": worker.failed_list(vault), "worker": st, "items": rows}

    @app.get("/api/sources/{source_id}")
    def source_detail(source_id: str):
        row = con().execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Zdroj nenalezen")
        return {**dict(row), "state": state.load(vault, source_id)}

    @app.delete("/api/sources/{source_id}")
    def delete_source(source_id: str, reason: str = ""):
        from pipeline import remove
        try:
            r = remove.remove(vault, source_id, reason)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        return r

    @app.post("/api/sources/retry")
    def retry(body: AddSource):
        return {"message": worker.retry_failed(vault, body.url)}

    @app.post("/share")
    async def share_target(request: Request):
        form = await request.form()
        files = [f for f in form.getlist("file") if hasattr(f, "filename") and f.filename]
        from urllib.parse import quote
        if files:                                   # sdílený soubor (PDF, Word, text) → dokument
            msgs = []
            for f in files:
                try:
                    data = await f.read()
                    r = documents.ingest(vault, f.filename, data, visibility="private", origin="sdílení")
                    if r["status"] == "maybe_version":
                        stage_pending(f.filename, data, "private", r)
                        msgs.append(f"„{f.filename}“: vypadá jako nová verze — rozhodněte v Dokumentech")
                        continue
                    msgs.append(f"„{r.get('title')}“: " + ("už v systému je" if r["status"] == "duplicate" else "přidáno"))
                except Unsupported as e:
                    msgs.append(f"„{f.filename}“: {e}")
            reindex_async()
            return RedirectResponse(f"/?shared={quote(' · '.join(msgs))}#dokumenty", status_code=303)
        text = " ".join(str(form.get(k) or "") for k in ("url", "text", "title"))
        vid = ids.youtube_id(text)
        if not vid and re.search(r"https?://\S+", text):          # jiný odkaz → webová stránka jako dokument
            u = re.search(r"https?://\S+", text).group(0)
            try:
                r = documents.ingest_url(vault, u, visibility="public", watch=True)
                msg = f"„{r.get('title')}“: " + {"ok": "stránka přidána do dokumentů", "duplicate": "už v systému je",
                                                   "maybe_version": "podobná stránka už v systému je"}.get(r["status"], r["status"])
                if r["status"] == "ok":
                    reindex_async()
            except Unsupported as e:
                msg = str(e)
            return RedirectResponse(f"/?shared={quote(msg)}#dokumenty", status_code=303)
        msg = queue.add(vault, ids.youtube_url(vid)) if vid else "V ničem sdíleném není odkaz na YouTube."
        from urllib.parse import quote
        return RedirectResponse(f"/?shared={quote(msg)}#pridat", status_code=303)

    # ---------- hledání a přepisy ----------
    @app.post("/api/ask")
    @app.post("/api/chat")
    def chat_endpoint(body: dict):
        from pipeline import analyze, ask
        q = str(body.get("question") or "").strip()
        if not q:
            raise HTTPException(400, "Zadejte otázku.")
        level = body.get("level") if body.get("level") in ask.LEVELS else "standard"
        try:
            def provider_for(private: bool):
                p = analyze.default_provider(vault, "private" if private else "chat")
                if hasattr(p, "max_retries"):        # chat čeká na odpověď člověk → krátké pokusy, ne minuty
                    p.max_retries, p.backoff, p.timeout_ms = 2, 3, 40000
                    p.fallbacks = list(getattr(p, "fallbacks", []))[:1]
                return p
            return ask.chat(con(), Registry.load(vault), q, provider_for,
                            level, int(body.get("page") or 0), body.get("since"), body.get("until"),
                            semantic=semantic, source_id=body.get("source_id") or None)
        except Exception as e:  # noqa: BLE001
            from pipeline.providers.gemini_stt import BillingError, QuotaExhausted, classify
            if isinstance(e, QuotaExhausted):
                raise HTTPException(503, "Denní bezplatný limit Gemini je vyčerpaný (otázka se týká vašich poznámek "
                                         "nebo schůzek, ty jdou jen přes Gemini). Zkuste to zítra.")
            if isinstance(e, BillingError):
                raise HTTPException(402, "Došel kredit na OpenRouteru (openrouter.ai → Credits).")
            if isinstance(e, ValueError) and "API_KEY" in str(e):
                raise HTTPException(400, "Chybí klíč k AI modelům. Doplňte OPENROUTER_API_KEY do .env "
                                         "(python -m pipeline setup) a restartujte aplikaci. Hledání funguje i bez klíče.")
            if classify(e) in ("overloaded", "transient", "rate"):
                raise HTTPException(503, "Model je teď přetížený, zkuste to za chvíli.")
            raise

    @app.get("/api/usage")
    def usage_endpoint(month: str | None = None):
        from pipeline import usage
        return usage.summary(month)

    @app.delete("/api/queue")
    def dequeue(url: str):
        queue.remove(vault, url)
        queue.remove(vault, url, queue.local_file(vault))
        return {"ok": True}

    @app.post("/api/queue/retry")
    def queue_retry(url: str):
        """Spustit přepis znovu: zrušit pauzu a dát video na začátek fronty (hotové kusy se neopakují)."""
        if not queue.move_to_front(vault, url):
            raise HTTPException(404, "Video ve frontě není.")
        (vault.system / "logs" / "quota_pause.json").unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/jobs")
    def jobs_overview(q: str = ""):
        from pipeline import jobs
        return jobs.overview(vault, con(), q)

    @app.delete("/api/queue/failed")
    def drop_failed(url: str):
        from pipeline import jobs
        return {"ok": jobs.drop_failed(vault, url)}

    @app.post("/api/worker/resume")
    def worker_resume():
        (vault.system / "logs" / "quota_pause.json").unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/search")
    def search(q: str = "", speaker: str | None = None, since: str | None = None, until: str | None = None,
               source_type: str | None = None, entity: str | None = None, limit: int = 50, offset: int = 0):
        return index.search(con(), q, speaker, since, until, source_type, entity, min(limit, 200), offset)

    @app.get("/api/segments/{source_id}/{seg}")
    def segment_context(source_id: str, seg: int, context: int = 3):
        rows = con().execute("SELECT seg, t, speaker, text, time_source FROM segments WHERE source_id=? "
                             "AND seg BETWEEN ? AND ? ORDER BY seg", (source_id, seg - context, seg + context))
        return {"source_id": source_id, "seg": seg, "items": [dict(r) for r in rows]}

    @app.get("/api/transcripts/{source_id}")
    def get_transcript(source_id: str):
        p = vault.transcripts / f"{ids.safe_filename(source_id)}.json"
        if not p.exists():
            raise HTTPException(404, "Přepis nenalezen")
        return transcript.load(p)

    # ---------- slovník názvů pro přepis ----------
    @app.get("/api/glossary")
    def glossary_get():
        from pipeline import glossary
        return {"text": glossary.read_text(vault), "entries": glossary.entries(vault), "auto": glossary.auto_terms(vault),
                "path": vault.rel(glossary.path(vault))}

    @app.put("/api/glossary")
    def glossary_put(body: dict):
        from pipeline import glossary
        text = str(body.get("text") or "")
        if len(text) > 50000:
            raise HTTPException(400, "Slovník je příliš dlouhý.")
        return {"entries": glossary.write_text(vault, text)}

    @app.post("/api/glossary/apply")
    def glossary_apply():
        from pipeline import glossary
        return glossary.apply_to_existing(vault)

    # ---------- dokumenty ----------
    @app.get("/api/documents")
    def docs_list(archived: bool = False):
        return {"items": documents.listing(vault, include_archived=archived),
                "pending": [json.loads(p.read_text(encoding="utf-8")) | {"token": p.stem}
                            for p in sorted(pend_dir.glob("*.json"))] if pend_dir.exists() else [],
                "vectors": vectors.stats(vault, con())}

    @app.post("/api/documents")
    async def docs_upload(request: Request):
        """multipart: file (i více), visibility (private|public), replace (source_id), force_new (1)."""
        form = await request.form()
        vis = "public" if form.get("visibility") == "public" else "private"
        replace = str(form.get("replace") or "") or None
        force_new = str(form.get("force_new") or "") in ("1", "true")
        out, changed = [], False
        for f in form.getlist("file"):
            if not getattr(f, "filename", None):
                continue
            data = await f.read()
            try:
                r = documents.ingest(vault, f.filename, data, visibility=vis, replace=replace, force_new=force_new)
                if r["status"] == "maybe_version":
                    r = stage_pending(f.filename, data, vis, r)
                changed |= r["status"] == "ok"
                out.append({"filename": f.filename, **r})
            except Unsupported as e:
                out.append({"filename": f.filename, "status": "error", "error": str(e)})
        if changed:
            reindex_async()
        return {"results": out}

    @app.post("/api/documents/url")
    def docs_url(body: dict):
        url = str(body.get("url") or "").strip()
        vis = "private" if body.get("visibility") == "private" else "public"
        try:
            r = documents.ingest_url(vault, url, visibility=vis, watch=bool(body.get("watch", True)),
                                     replace=body.get("replace") or None, force_new=bool(body.get("force_new")))
        except Unsupported as e:
            raise HTTPException(400, str(e))
        if r["status"] == "maybe_version":        # u adres se stejnou URL rozhodne stroj; jinak se zeptáme
            name, data = documents.fetch_url(url)
            r = stage_pending(name, data, vis, r, url=url, watch=bool(body.get("watch", True)))
        if r["status"] == "ok":
            reindex_async()
        return r

    @app.post("/api/documents/decide")
    def docs_decide(body: dict):
        """Rozhodnutí u „vypadá jako nová verze“: action = replace (+ replace=source_id) | new | cancel."""
        token = re.sub(r"[^0-9a-f]", "", str(body.get("token") or ""))
        jf, bf = pend_dir / f"{token}.json", pend_dir / f"{token}.bin"
        if not token or not jf.exists():
            raise HTTPException(404, "Rozhodnutí už neplatí (soubor byl zpracován nebo zrušen).")
        info = json.loads(jf.read_text(encoding="utf-8"))
        action = body.get("action")
        r = {"status": "cancelled"}
        if action in ("replace", "new"):
            try:
                kw = dict(visibility=info.get("visibility", "private"),
                          replace=body.get("replace") if action == "replace" else None, force_new=action == "new")
                if info.get("url"):
                    r = documents.ingest(vault, info["filename"], bf.read_bytes(), url=info["url"],
                                         watch=info.get("watch", True), origin="web-url", **kw)
                else:
                    r = documents.ingest(vault, info["filename"], bf.read_bytes(), **kw)
            except Unsupported as e:
                raise HTTPException(400, str(e))
            if r["status"] == "ok":
                reindex_async()
        jf.unlink(missing_ok=True)
        bf.unlink(missing_ok=True)
        return r

    @app.post("/api/documents/{source_id}/supersede")
    def docs_supersede(source_id: str, body: dict):
        """Tento dokument nahrazuje starší (body.old) — pro soubory ze složky, kde se stroj neptal."""
        old = str(body.get("old") or "")
        if not documents.tpath(vault, old).exists() or not documents.tpath(vault, source_id).exists():
            raise HTTPException(404, "Dokument nenalezen")
        documents.supersede(vault, old, source_id)
        t = documents.load(vault, source_id)
        t["meta"].pop("possible_version_of", None)
        documents._save(vault, t)
        reindex_async()
        return {"ok": True}

    @app.post("/api/documents/{source_id}/visibility")
    def docs_visibility(source_id: str, body: dict):
        if not documents.tpath(vault, source_id).exists():
            raise HTTPException(404, "Dokument nenalezen")
        m = documents.set_visibility(vault, source_id, body.get("visibility", "private"))
        try:
            documents.render_card(vault, source_id)
        except Exception:  # noqa: BLE001
            pass
        reindex_async()
        return m

    @app.post("/api/documents/{source_id}/refresh")
    def docs_refresh(source_id: str):
        if not documents.tpath(vault, source_id).exists():
            raise HTTPException(404, "Dokument nenalezen")
        r = documents.refresh(vault, source_id)
        if r.get("status") == "ok":
            reindex_async()
        return r

    @app.delete("/api/documents/failed/{n}")
    def docs_drop_failed(n: int):
        return {"ok": documents.drop_failed(vault, n)}

    @app.get("/api/documents/{source_id}/file")
    def docs_file(source_id: str):
        if not documents.tpath(vault, source_id).exists():
            raise HTTPException(404, "Dokument nenalezen")
        m = documents.load(vault, source_id)["meta"]
        if m.get("url"):                        # webová stránka: uložené HTML bez stylů/skriptů je prázdné → na originál
            return RedirectResponse(m["url"], status_code=302)
        p = vault.root / (m.get("asset") or "")
        if not m.get("asset") or not p.exists():
            raise HTTPException(404, "Originál není k dispozici")
        media = {".pdf": "application/pdf", ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
                 ".html": "text/html; charset=utf-8",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(p.suffix.lower())
        return FileResponse(p, media_type=media, filename=m.get("filename") or p.name,
                            content_disposition_type="inline" if p.suffix.lower() in (".pdf", ".txt", ".md") else "attachment")

    @app.get("/api/preview/{source_id}")
    def preview(source_id: str):
        """Náhled zdroje: shrnutí, text přepisu, u hlasové poznámky i nahrávka."""
        base = ids.safe_filename(source_id)
        p = vault.transcripts / f"{base}.json"
        if not p.exists():
            raise HTTPException(404, "Přepis nenalezen")
        t = transcript.load(p)
        an = vault.transcripts / f"{base}.analysis.json"
        a = json.loads(an.read_text(encoding="utf-8")) if an.exists() else {}
        is_doc = t.get("meta", {}).get("kind") == "document"
        text = documents.plain_text(t) if is_doc else voice.plain_text(t)
        ns, ident = source_id.split(":", 1)
        audio = ns == "voice" and any((vault.assets / "voice").glob(f"{ident}.*"))
        m = t.get("meta", {})
        return {"source_id": source_id, "title": m.get("title"), "duration": t.get("duration_s"),
                "summary": a.get("tldr", ""), "text": text[:30000], "truncated": len(text) > 30000,
                "audio": f"/api/voice/audio/{source_id}" if audio else None,
                "file": f"/api/documents/{source_id}/file" if is_doc and m.get("asset") else None,
                "url": m.get("url"), "doc_type": a.get("doc_type"), "applies_to": a.get("applies_to"),
                "validity": a.get("validity"), "key_points": a.get("key_points") if is_doc else None}

    @app.get("/api/voice/audio/{source_id}")
    def voice_audio(source_id: str):
        ns, ident = source_id.split(":", 1)
        files = sorted((vault.assets / "voice").glob(f"{ident}.*")) if ns == "voice" and "/" not in ident else []
        if not files:
            raise HTTPException(404, "nahrávka není")
        f = files[0]
        return FileResponse(f, media_type="audio/ogg" if f.suffix in (".opus", ".ogg") else None)

    @app.get("/api/speakers")
    def speakers():
        return [dict(r) for r in con().execute(
            "SELECT speaker, COUNT(DISTINCT source_id) sources, COUNT(*) segments FROM segments "
            "WHERE speaker NOT LIKE 'Mluvčí %' GROUP BY speaker ORDER BY sources DESC, segments DESC")]

    # ---------- entity ----------
    @app.get("/api/entity-types")
    def entity_types():
        return TYPES

    @app.get("/api/entities")
    def entities(q: str = "", type: str | None = None, status: str | None = None, limit: int = 200,
                 group: str | None = None):
        return index.entity_list(con(), q, type, status, min(limit, 1000), group)

    @app.post("/api/entities/cleanup/suggest")
    def entities_cleanup_suggest():
        """Spustí návrh úklidu na pozadí; stav a výsledek → GET /api/entities/cleanup."""
        import os
        from pipeline import analyze, entity_cleanup
        if os.getenv("OPENROUTER_API_KEY"):
            # jen názvy entit (nic citlivého); Gemini Flash to zvládne za pár sekund, DeepSeek tu bývá pomalý
            from pipeline.providers.openrouter import OpenRouterAnalysis
            p = OpenRouterAnalysis(model="google/gemini-2.5-flash", fallbacks=["deepseek/deepseek-v4-flash"],
                                   reasoning=True)
            p.purpose = "úklid entit"
        else:
            p = analyze.default_provider(vault, "chat")
        if hasattr(p, "max_retries"):
            p.max_retries, p.backoff, p.timeout_s = 2, 3, 120
            p.fallbacks = list(getattr(p, "fallbacks", []))[:1]
        return entity_cleanup.start(vault, p)

    @app.get("/api/entities/cleanup")
    def entities_cleanup_state():
        from pipeline import entity_cleanup
        return entity_cleanup.state()

    @app.post("/api/entities/cleanup/apply")
    def entities_cleanup_apply(body: dict):
        from pipeline import entity_cleanup
        r = entity_cleanup.apply(vault, list(body.get("items") or []))
        threading.Thread(target=index.build, args=(vault,), daemon=True).start()
        return r

    @app.get("/api/entities/{eid}/mentions")
    def mentions(eid: str, since: str | None = None, until: str | None = None, speaker: str | None = None,
                 attitude: str | None = None, include_children: bool = False):
        r = index.entity_mentions(con(), eid, since, until, speaker, attitude, include_children)
        if not r["entity"]:
            raise HTTPException(404, "Entita nenalezena")
        return r

    @app.get("/api/entities/{eid}/timeline")
    def timeline(eid: str, bucket: str = "month"):
        fmt = "%Y-%m" if bucket == "month" else "%Y-%W"
        rows = con().execute(f"SELECT strftime('{fmt}', published_at) b, COALESCE(attitude,'—') a, COUNT(*) n "
                             "FROM mentions WHERE entity=? AND published_at != '' GROUP BY b, a ORDER BY b", (eid,))
        return [dict(r) for r in rows]

    def _save_registry(reg: Registry):
        reg.save()
        threading.Thread(target=index.build, args=(vault,), daemon=True).start()

    @app.patch("/api/entities/{eid}")
    def patch_entity(eid: str, body: EntityPatch):
        reg = Registry.load(vault)
        if eid not in reg.data:
            raise HTTPException(404, "Entita nenalezena")
        e = reg.data[eid]
        for k, v in body.model_dump(exclude_none=True).items():
            if k == "type" and v not in TYPES:
                raise HTTPException(400, f"Neznámý typ {v}")
            if k == "status" and v not in ("navrženo", "potvrzeno", "zamítnuto"):
                raise HTTPException(400, f"Neznámý stav {v}")
            e[k] = v
        _save_registry(reg)
        return {"id": eid, **e}

    @app.post("/api/entities/{eid}/merge")
    def merge_entity(eid: str, body: Merge):
        reg = Registry.load(vault)
        try:
            reg.merge(vault, eid, body.into)        # přepíše i zmínky ve všech souborech (zdroj pravdy je v Gitu)
        except ValueError as e:
            raise HTTPException(400, str(e))
        _save_registry(reg)
        return {"merged": eid, "into": body.into}

    # ---------- hlas ----------
    @app.post("/api/voice/sessions")
    def voice_start(request: Request):
        meeting = request.headers.get("x-meeting") or None
        if meeting:
            try:
                meetings.load(vault, meeting)
            except (FileNotFoundError, ValueError):
                raise HTTPException(404, "zápis neexistuje")
        s = voice.new_session(vault, request.headers.get("x-mime", "audio/webm"), meeting=meeting)
        if meeting:
            try:
                meetings.attach_session(vault, meeting, s)
            except ValueError as e:
                voice.discard(vault, s)
                raise HTTPException(409, str(e))
        return {"session": s}

    @app.put("/api/voice/sessions/{session}/chunks/{n}")
    async def voice_chunk(session: str, n: int, request: Request):
        try:
            have = voice.put_chunk(vault, session, n, await request.body())
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "chunks": have}

    @app.post("/api/voice/sessions/{session}/finish")
    def voice_finish(session: str, body: VoiceFinish):
        try:
            r = voice.finish(vault, session, body.chunks, body.title)
            if r.get("meeting") and r.get("finished"):
                meetings.mark_processing(vault, r["meeting"])
            return r
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/voice/sessions/{session}")
    def voice_discard(session: str):
        try:
            meeting = voice.session_meta(vault, session).get("meeting")
            voice.discard(vault, session)
            if meeting:
                m = meetings.load(vault, meeting)
                if m.get("session") == session and m["status"] in ("recording", "error"):
                    m.update(session=None, status="draft")
                    meetings.save(vault, m)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError:
            raise HTTPException(404, "relace neexistuje")
        return {"ok": True}

    @app.get("/api/voice/notes")
    def voice_notes(limit: int = 20):
        return voice.recent_notes(vault, limit)

    @app.get("/api/voice/pending")
    def voice_pending():
        return voice.pending(vault)

    @app.post("/api/voice/retry")
    def voice_retry():
        return {"retried": voice.retry(vault)}

    # ---------- schůzky ----------
    def _m(fn, *a, **k):
        try:
            return fn(*a, **k)
        except FileNotFoundError:
            raise HTTPException(404, "nenalezeno")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/meeting-templates")
    def m_templates():
        return {"templates": meetings.list_templates(vault), "types": meetings.FIELD_TYPES}

    @app.put("/api/meeting-templates/{tid}")
    def m_template_save(tid: str, body: dict):
        body["id"] = body.get("id") or tid
        return _m(meetings.save_template, vault, body)

    @app.post("/api/meeting-templates")
    def m_template_new(body: dict):
        body.pop("id", None)
        return _m(meetings.save_template, vault, body)

    @app.delete("/api/meeting-templates/{tid}")
    def m_template_delete(tid: str):
        _m(meetings.delete_template, vault, tid)
        return {"ok": True}

    @app.get("/api/meetings")
    def m_list():
        return meetings.list_meetings(vault)

    @app.post("/api/meetings")
    def m_new(body: dict):
        return _m(meetings.new_meeting, vault, body.get("template"))

    @app.get("/api/meetings/{mid}")
    def m_get(mid: str):
        m = _m(meetings.load, vault, mid)
        if m.get("source_id"):
            p = vault.transcripts / (ids.safe_filename(m["source_id"]) + ".json")
            if p.exists():
                t = json.loads(p.read_text(encoding="utf-8"))
                m["transcript"] = [{"i": x["i"], "t": x["start"], "speaker": x["speaker"], "text": x["text"]}
                                   for x in t["segments"]]
        m["has_audio"] = (vault.assets / "meetings" / f"{mid}.opus").exists()
        return m

    @app.put("/api/meetings/{mid}")
    def m_update(mid: str, body: dict):
        return _m(meetings.update, vault, mid, body.get("values"), body.get("speakers"), body.get("suggestions"),
                  body.get("base_status"))

    @app.post("/api/meetings/{mid}/approve")
    def m_approve(mid: str, body: dict | None = None):
        if body and (body.get("values") is not None or body.get("speakers") is not None):
            _m(meetings.update, vault, mid, body.get("values"), body.get("speakers"), body.get("suggestions"),
                  body.get("base_status"))
        return _m(meetings.approve, vault, mid)

    @app.delete("/api/meetings/{mid}")
    def m_delete(mid: str):
        _m(meetings.delete_meeting, vault, mid)
        return {"ok": True}

    @app.get("/api/meetings/{mid}/audio")
    def m_audio(mid: str):
        _m(meetings.load, vault, mid)
        p = vault.assets / "meetings" / f"{mid}.opus"
        if not p.exists():
            raise HTTPException(404, "nahrávka není")
        return FileResponse(p, media_type="audio/ogg")

    @app.get("/api/meetings/{mid}/doc")
    def m_doc(mid: str):
        return {"html": meetings.doc_html(_m(meetings.load, vault, mid))}

    @app.get("/api/meetings/{mid}/pdf")
    def m_pdf(mid: str):
        m = _m(meetings.load, vault, mid)
        if m["status"] != "approved":
            raise HTTPException(400, "PDF je k dispozici po schválení zápisu.")
        from urllib.parse import quote
        name = meetings.pdf_filename(m)
        return Response(meetings.pdf_bytes(m), media_type="application/pdf",
                        headers={"Content-Disposition": f"inline; filename={name}; filename*=UTF-8''{quote(name)}"})

    # ---------- provoz ----------
    @app.get("/api/status")
    def status():
        return {"worker": worker.read_status(vault), "index": index.stats(con()),
                "queue": len(queue.read(vault)), "failed": len(worker.failed_list(vault)),
                "queue_local": len(queue.read(vault, queue.local_file(vault))),
                "voice_pending": len(voice.ready_sessions(vault)),
                "voice_errors": sum(1 for r in voice.pending(vault) if r.get("error")),
                "quota_pause": worker.quota_pause(vault)}

    @app.post("/api/reindex")
    def reindex():
        return index.build(vault)

    # ---------- PWA ----------
    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    def sw():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

    @app.get("/")
    def home():
        # Verze souborů v odkazech (?v=...), aby telefon po nasazení nenačetl starý styl z mezipaměti.
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for name in ("app.css", "app.js", "meetings.js", "jkdoc.css", "fonts.css"):
            v = int((STATIC / name).stat().st_mtime)
            html = html.replace(f'/static/{name}"', f'/static/{name}?v={v}"')
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
