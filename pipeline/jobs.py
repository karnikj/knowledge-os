"""Přehled úloh pro stránku „Úlohy": co běží / čeká, co selhalo, co je hotové — v jednom seznamu.

Každá položka má `group` (running | failed | done), `kind` (youtube | voice | meeting | …), název, čas,
případnou chybu a seznam akcí, které na ni web nabídne (spustit znovu, odebrat, smazat…).
Hledání `q` prochází název, odkaz, chybu i obsah přepisů (fulltext), bez ohledu na diakritiku.
"""
from __future__ import annotations

import sqlite3
import unicodedata

from pipeline import ids, index, meetings, queue, voice, worker
from pipeline.config import Vault

KIND_LABEL = {"job": "zpracování", "youtube": "video", "voice_note": "hlasová poznámka", "voice": "hlasová poznámka",
              "meeting": "schůzka", "file": "dokument", "document": "dokument"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _kind_of_source(row: dict) -> str:
    st = row.get("source_type") or ""
    if st in ("voice_note", "voice"):
        return "voice"
    return st or row["source_id"].split(":", 1)[0]


def _session_time(session: str) -> str:
    """20260925-101500-xxxx → 2026-09-25T10:15:00 (když relace čas v názvu nemá, vrátí název)."""
    s = session.replace("-", "")
    if len(s) >= 14 and s[:14].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:{s[10:12]}:{s[12:14]}"
    return session


def _quality_reasons(vault: Vault, source_id: str) -> str:
    """Proč kontrola kvality přepisu nedala plné OK (např. „pokrytí řeči 89%“)."""
    import json
    p = vault.transcripts / (ids.safe_filename(source_id) + ".json")
    try:
        q = json.loads(p.read_text(encoding="utf-8")).get("stt_quality") or {}
    except (OSError, ValueError):
        return ""
    return ", ".join(q.get("reasons") or [])


def _yt_title(url: str) -> str:
    vid = ids.youtube_id(url)
    return f"YouTube {vid}" if vid else url


def _content_hits(con: sqlite3.Connection | None, q: str) -> dict[str, str]:
    """source_id → první odpovídající věta z přepisu (fulltext)."""
    if not con or not q.strip():
        return {}
    try:
        r = index.search(con, q, limit=200)
    except sqlite3.Error:
        return {}
    out: dict[str, str] = {}
    for h in r["items"]:
        out.setdefault(h["source_id"], h["text"])
    return out


def overview(vault: Vault, con: sqlite3.Connection | None, q: str = "") -> dict:
    items: list[dict] = []
    st = worker.read_status(vault)
    job = st.get("job")
    pause = worker.quota_pause(vault)

    # --- běží / čeká ---
    log = st.get("log") or []
    pend = voice.pending(vault)
    sessions = {p["session"]: p for p in pend}
    jsess = next((x for x in str(job or "").split() if x in sessions), None)  # job bývá „schůzka <relace>“
    if jsess:
        sp = sessions[jsess]
        if sp.get("meeting"):
            job = None  # zpracování schůzky ukazuje položka schůzky
        else:
            items.append({"key": f"job:{job}", "group": "running", "state": "přepisuje se", "kind": "voice",
                          "title": sp.get("title") or "Hlasová poznámka", "at": _session_time(jsess),
                          "session": jsess, "log": log, "actions": []})
            job = None
    if job:
        items.append({"key": f"job:{job}", "group": "running", "state": "zpracovává se", "log": log,
                      "kind": "youtube" if ids.youtube_id(str(job)) else "job",
                      "title": _yt_title(job) if ids.youtube_id(str(job)) else str(job),
                      "at": st.get("since") or st.get("at"), "url": job if str(job).startswith("http") else None,
                      "actions": []})
    for i, u in enumerate(queue.read(vault)):
        if u == job:
            continue
        items.append({"key": f"q:{u}", "group": "running", "state": "ve frontě", "kind": "youtube",
                      "title": _yt_title(u), "url": u, "order": i,
                      "detail": (f"Pozastaveno do {pause['until'][11:16]}" if pause else None),
                      "actions": ["requeue", "dequeue"]})
    for u in queue.read(vault, queue.local_file(vault)):
        items.append({"key": f"ql:{u}", "group": "running", "state": "čeká na PC", "kind": "youtube",
                      "title": _yt_title(u), "url": u,
                      "detail": "YouTube blokuje stahování ze serveru, zpracuje se na PC.", "actions": ["dequeue"]})

    for p in pend:
        if p["session"] == jsess:
            continue
        if p.get("meeting") and (p.get("finished") or p.get("error")):
            continue  # odeslanou nahrávku schůzky ukazuje položka schůzky (zpracovává se / chyba)
        kind = "meeting" if p.get("meeting") else "voice"
        base = {"kind": kind, "session": p["session"], "meeting": p.get("meeting"),
                "title": p.get("title") or ("Nahrávka schůzky" if p.get("meeting") else "Hlasová poznámka"),
                "at": p.get("created") or _session_time(p["session"]), "detail": f"{p.get('chunks', 0)} kusů nahrávky"}
        if p.get("error"):
            items.append({**base, "key": f"v:{p['session']}", "group": "failed", "state": "chyba",
                          "error": p["error"], "actions": ["voice_retry", "discard"]})
        elif p.get("finished"):
            items.append({**base, "key": f"v:{p['session']}", "group": "running", "state": "přepisuje se",
                          "detail": (f"Čeká na limit Gemini do {pause['until'][11:16]}" if pause else base["detail"]),
                          "actions": ["discard"]})
        else:
            items.append({**base, "key": f"v:{p['session']}", "group": "running", "state": "neodesláno",
                          "detail": "Nahrávka nebyla odeslána k přepisu.", "chunks": p.get("chunks", 0),
                          "actions": ["send", "discard"]})

    # --- selhalo (YouTube) ---
    for f in worker.failed_list(vault):
        items.append({"key": f"f:{f['url']}", "group": "failed", "state": "chyba", "kind": "youtube",
                      "title": _yt_title(f["url"]), "url": f["url"], "at": f["at"], "error": f["error"],
                      "actions": ["retry_failed", "drop_failed"]})

    # --- schůzky ---
    meeting_sids = set()
    for m in meetings.list_meetings(vault):
        full = meetings.load(vault, m["id"])
        sid = full.get("source_id")
        if sid:
            meeting_sids.add(sid)
        base = {"key": f"m:{m['id']}", "kind": "meeting", "title": m["title"], "at": m["created"],
                "meeting": m["id"], "source_id": sid}
        if m["status"] == "processing":
            items.append({**base, "group": "running", "state": "zpracovává se", "actions": ["open_meeting"],
                          "log": log if jsess and sessions[jsess].get("meeting") == m["id"] else None})
        elif m["status"] == "error":
            items.append({**base, "group": "failed", "state": "chyba", "error": m.get("error") or "",
                          "actions": ["open_meeting", "delete_source" if sid else "delete_meeting"]})
        elif m["status"] == "review":
            items.append({**base, "group": "done", "state": "ke schválení",
                          "detail": f"Automaticky se schválí {str(m.get('review_until') or '')[:10]}",
                          "actions": ["open_meeting", "delete_source" if sid else "delete_meeting"]})
        elif m["status"] == "approved":
            items.append({**base, "group": "done", "state": "schváleno", "at": m.get("approved_at") or m["created"],
                          "actions": ["open_meeting", "delete_source" if sid else "delete_meeting"]})

    # --- dokumenty: nepřijaté soubory, čekající AI karty, možné nové verze
    from pipeline import documents
    for f in documents.failed_list(vault):
        items.append({"key": f"df:{f['n']}", "group": "failed", "state": "odmítnuto", "kind": "document",
                      "title": f["name"], "at": f["at"], "error": f["error"], "n": f["n"],
                      "detail": f"z {f.get('origin') or 'složky'}", "actions": ["drop_doc_failed"]})
    pending_cards = set(documents.pending_cards(vault))
    doc_meta = {d["source_id"]: d["meta"] for d in documents.metas(vault)}

    # --- hotové zdroje ---
    if con is not None:
        for r in con.execute("SELECT * FROM sources ORDER BY published_at DESC"):
            r = dict(r)
            if r["source_id"] in meeting_sids or r["source_id"].startswith("meeting:"):
                continue
            kind = _kind_of_source(r)
            detail = " · ".join(x for x in [r.get("channel") if kind == "youtube" else None, r.get("duration"),
                                            f"{r['n_claims']} tvrzení" if r.get("n_claims") else None] if x)
            if kind == "document":
                m = doc_meta.get(r["source_id"], {})
                sid = r["source_id"]
                det = " · ".join(x for x in [
                    {"pdf": "PDF", "docx": "Word", "md": "Markdown", "txt": "text", "html": "web"}.get(m.get("format"), m.get("format")),
                    f"{m['pages']} str." if m.get("pages") else None,
                    "veřejný" if r.get("visibility") == "public" else "soukromý",
                    "hlídá změny" if m.get("watch") else None] if x)
                base = {"key": f"s:{sid}", "kind": "document", "title": r.get("title") or sid, "at": r.get("published_at"),
                        "url": r.get("url") or None, "source_id": sid}
                if m.get("archived"):
                    items.append({**base, "group": "done", "state": "starší verze (archiv)", "detail": det,
                                  "actions": ["preview", "delete_source"]})
                elif sid in pending_cards:
                    items.append({**base, "group": "running", "state": "čeká na AI kartu",
                                  "detail": det + " · už teď dohledatelný v Hledat i Chatu", "actions": ["preview", "delete_source"]})
                else:
                    extra = ["supersede"] if m.get("possible_version_of") else []
                    items.append({**base, "group": "done", "state": "hotovo", "detail": det,
                                  "possible_version_of": m.get("possible_version_of") or [],
                                  "actions": ["preview", *extra, "delete_source"]})
                continue
            ok = r.get("stt_status") in (None, "", "STT_OK")
            if not ok:
                why = _quality_reasons(vault, r["source_id"])
                detail = " · ".join(x for x in [detail, "kontrola přepisu: " + why if why else None] if x)
            items.append({"key": f"s:{r['source_id']}", "group": "done",
                          "state": "hotovo" if ok else ("přepis neúplný" if r.get("stt_status") == "STT_INCOMPLETE"
                                                        else "hotovo, přepis ke kontrole"),
                          "kind": kind, "title": r.get("title") or r["source_id"], "at": r.get("published_at"),
                          "url": r.get("url"), "source_id": r["source_id"], "detail": detail,
                          "actions": ["preview", "delete_source"]})

    for it in items:
        it["kind_label"] = KIND_LABEL.get(it["kind"], it["kind"])

    counts = {g: sum(1 for i in items if i["group"] == g) for g in ("running", "failed", "done")}
    if q.strip():
        nq = _norm(q)
        hits = _content_hits(con, q)
        out = []
        for it in items:
            hay = _norm(" ".join(str(it.get(k) or "") for k in
                                 ("title", "url", "error", "detail", "state", "kind_label", "at", "source_id")))
            if nq in hay:
                out.append(it)
            elif it.get("source_id") in hits:
                out.append({**it, "hit": hits[it["source_id"]]})
        items = out
    return {"items": items, "counts": counts, "pause": pause}


def drop_failed(vault: Vault, url: str) -> bool:
    p = vault.system / "queue_failed.txt"
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines()
    keep = [l for l in lines if not l.startswith(url + "\t")]
    p.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return len(keep) != len(lines)
