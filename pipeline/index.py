"""Vyhledávací index: 99_System/index/kos.sqlite (odvozený, kdykoli přestavitelný; v .gitignore).

Staví se z vaultu: přepisy (segmenty), zmínky, analýzy (tvrzení), registr entit a frontmatter karet.
Fulltext = SQLite FTS5 (bez diakritiky) nad textem segmentu + jeho stemmovanou verzí, takže
„Pandasu“ najde „pandas“. Formát výsledků odpovídá `pipeline.search.Hit` (CLI i webová aplikace).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pipeline import catalog, frontmatter, vectors
from pipeline.config import Vault
from pipeline.czech import stems
from pipeline.entities import Registry

SCHEMA = """
CREATE TABLE sources(source_id TEXT PRIMARY KEY, title TEXT, published_at TEXT, source_type TEXT,
                     channel TEXT, card TEXT, url TEXT, duration TEXT, stt_status TEXT, n_claims INT, n_mentions INT,
                     visibility TEXT, archived INT DEFAULT 0);
CREATE TABLE segments(rowid INTEGER PRIMARY KEY, source_id TEXT, seg INT, t REAL, speaker TEXT, text TEXT,
                      time_source TEXT);
CREATE INDEX seg_src ON segments(source_id, seg);
CREATE VIRTUAL TABLE seg_fts USING fts5(text, stemmed, content='', tokenize='unicode61 remove_diacritics 2');
CREATE TABLE entities(id TEXT PRIMARY KEY, name TEXT, type TEXT, status TEXT, parent TEXT, aliases TEXT,
                      n_mentions INT DEFAULT 0, n_sources INT DEFAULT 0, last_seen TEXT);
CREATE TABLE mentions(entity TEXT, source_id TEXT, seg INT, t REAL, speaker TEXT, surface TEXT, attitude TEXT,
                      published_at TEXT);
CREATE INDEX men_ent ON mentions(entity, published_at);
CREATE TABLE claims(source_id TEXT, id TEXT, speaker TEXT, claim TEXT, quote TEXT, type TEXT, t REAL, seg INT,
                    grounding TEXT, published_at TEXT);
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE chunks(id INTEGER PRIMARY KEY, source_id TEXT, seg_from INT, seg_to INT, h TEXT, embed_text TEXT);
CREATE INDEX chunk_src ON chunks(source_id);
"""


def path(vault: Vault) -> Path:
    return vault.index_dir / "kos.sqlite"


def connect(vault: Vault) -> sqlite3.Connection:
    con = sqlite3.connect(path(vault), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


_BUILD_LOCK = __import__("threading").Lock()


def build(vault: Vault) -> dict:
    """Postaví index znovu do dočasného souboru a atomicky ho vymění (web může mezitím číst)."""
    with _BUILD_LOCK:
        return _build(vault)


def _build(vault: Vault) -> dict:
    import os
    vault.index_dir.mkdir(parents=True, exist_ok=True)
    tmp = path(vault).with_suffix(f".{os.getpid()}.tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    reg = Registry.load(vault)

    cards = {}
    for e in catalog.scan(vault):
        if e.source_id:
            fm = frontmatter.parse(e.path.read_text(encoding="utf-8")[:8192])
            cards[e.source_id] = (e, fm)

    n_seg = n_men = 0
    for tf in sorted(vault.transcripts.glob("*.json")):
        if tf.name.endswith((".analysis.json", ".mentions.json")):
            continue
        t = json.loads(tf.read_text(encoding="utf-8"))
        sid = t.get("source_id")
        if not sid:
            continue
        e, fm = cards.get(sid, (None, {}))
        meta = t.get("meta", {})
        is_doc = meta.get("kind") == "document"
        archived = bool(meta.get("archived"))
        date = str(fm.get("published_at") or (meta.get("added") or "")[:10] if is_doc else fm.get("published_at") or "")
        visibility = fm.get("visibility") or meta.get("visibility") or ("public" if sid.startswith("yt:") else "private")
        an_f = tf.with_name(tf.name[:-5] + ".analysis.json")
        mn_f = tf.with_name(tf.name[:-5] + ".mentions.json")
        an = json.loads(an_f.read_text(encoding="utf-8")) if an_f.exists() else {}
        mn = json.loads(mn_f.read_text(encoding="utf-8")).get("mentions", []) if mn_f.exists() else []
        title = fm.get("title") or meta.get("title") or (e.path.stem if e else sid)
        con.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            sid, title, date,
            fm.get("source_type") or ("document" if is_doc else "youtube" if sid.startswith("yt:") else sid.split(":")[0]),
            meta.get("channel") or meta.get("uploader") or "", vault.rel(e.path) if e else None,
            fm.get("url") or meta.get("url") or "", fm.get("duration") or "", t["stt_quality"]["status"],
            len(an.get("claims", [])), len(mn), visibility, int(archived)))
        if archived:          # starší verze dokumentu: v přehledu ano, v hledání a chatu ne
            continue
        # úseky pro hledání podle významu (vektory): hlavička „název › kapitola / mluvčí“ + text
        if is_doc:
            groups = [[s] for s in t["segments"]]
        else:
            groups = vectors.windows(t["segments"])
        for g in groups:
            head = t["speakers"].get(g[0]["speaker"], "") if is_doc else ""
            txt = (f"{title}" + (f" › {head}" if head else "") + "\n" + " ".join(x["text"] for x in g))[:2000]
            con.execute("INSERT INTO chunks(source_id, seg_from, seg_to, h, embed_text) VALUES (?,?,?,?,?)",
                        (sid, g[0]["i"], g[-1]["i"], vectors.chunk_hash(txt), txt))
        for s in t["segments"]:
            spk = t["speakers"].get(s["speaker"], s["speaker"])
            cur = con.execute("INSERT INTO segments(source_id, seg, t, speaker, text, time_source) VALUES (?,?,?,?,?,?)",
                              (sid, s["i"], s["start"], spk, s["text"], s.get("time_source")))
            con.execute("INSERT INTO seg_fts(rowid, text, stemmed) VALUES (?,?,?)",
                        (cur.lastrowid, s["text"], " ".join(stems(s["text"]))))
            n_seg += 1
        for m in mn:
            if reg.rejected(m["entity"]):
                continue
            con.execute("INSERT INTO mentions VALUES (?,?,?,?,?,?,?,?)", (
                m["entity"], sid, m["segment"], m["t"], m["speaker"], m.get("surface"), m.get("attitude"), date))
            n_men += 1
        for c in an.get("claims", []):
            con.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?)", (
                sid, c["id"], c.get("speaker"), c["claim"], c["quote"], c["type"], c.get("t"),
                (c.get("segment_ids") or [None])[0], c["grounding"]["method"], date))

    for eid, e in reg.data.items():
        con.execute("INSERT INTO entities(id, name, type, status, parent, aliases) VALUES (?,?,?,?,?,?)",
                    (eid, e["name"], e["type"], e.get("status"), e.get("parent"), json.dumps(e.get("aliases") or [],
                                                                                              ensure_ascii=False)))
    con.execute("""UPDATE entities SET
        n_mentions=(SELECT COUNT(*) FROM mentions m WHERE m.entity=entities.id),
        n_sources=(SELECT COUNT(DISTINCT source_id) FROM mentions m WHERE m.entity=entities.id),
        last_seen=(SELECT MAX(published_at) FROM mentions m WHERE m.entity=entities.id)""")
    from datetime import datetime
    stats = {"sources": len(cards), "segments": n_seg, "mentions": n_men, "entities": len(reg.data),
             "built_at": datetime.now().isoformat(timespec="seconds")}
    con.execute("INSERT INTO meta VALUES ('stats', ?)", (json.dumps(stats),))
    con.commit()
    con.close()
    tmp.replace(path(vault))
    return stats


def _fts_query(q: str) -> str:
    """Uživatelský dotaz → FTS5: každé slovo musí sedět v textu nebo ve stemmované podobě (prefix)."""
    parts = []
    for w, s in zip(q.split(), stems(q) + [""] * len(q.split())):
        w = "".join(ch for ch in w if ch.isalnum())
        if not w:
            continue
        s = s or w
        parts.append(f'(text:"{w}"* OR stemmed:"{s}"*)')
    return " AND ".join(parts) or '""'


def search(con: sqlite3.Connection, q: str = "", speaker: str | None = None, since: str | None = None,
           until: str | None = None, source_type: str | None = None, entity: str | None = None,
           limit: int = 50, offset: int = 0) -> dict:
    where, args = [], []
    if q.strip():
        where.append("s.rowid IN (SELECT rowid FROM seg_fts WHERE seg_fts MATCH ?)")
        args.append(_fts_query(q))
    if speaker:
        where.append("s.speaker LIKE ?"); args.append(f"%{speaker}%")
    if since:
        where.append("src.published_at >= ?"); args.append(since)
    if until:
        where.append("src.published_at <= ?"); args.append(until)
    if source_type:
        where.append("src.source_type = ?"); args.append(source_type)
    if entity:
        where.append("EXISTS (SELECT 1 FROM mentions m WHERE m.source_id=s.source_id AND m.seg=s.seg AND m.entity=?)")
        args.append(entity)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    base = f"FROM segments s JOIN sources src ON src.source_id=s.source_id {w}"
    total = con.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    rows = con.execute(f"""SELECT s.source_id, s.seg, s.t, s.speaker, s.text, s.time_source, src.title,
                           src.published_at, src.card, src.source_type, src.url AS src_url {base}
                           ORDER BY src.published_at DESC, s.source_id, s.t LIMIT ? OFFSET ?""",
                       [*args, limit, offset]).fetchall()
    facets = {
        "sources": [dict(r) for r in con.execute(
            f"SELECT s.source_id, src.title, COUNT(*) n {base} GROUP BY s.source_id ORDER BY n DESC LIMIT 20", args)],
        "speakers": [dict(r) for r in con.execute(
            f"SELECT s.speaker, COUNT(*) n {base} GROUP BY s.speaker ORDER BY n DESC LIMIT 20", args)],
    }
    return {"total": total, "items": [_hit(r) for r in rows], "facets": facets}


def _hit(r) -> dict:
    d = dict(r)
    sid = d["source_id"]
    src_url = d.pop("src_url", None)
    d["url"] = f"https://youtu.be/{sid.split(':', 1)[1]}?t={int(d['t'])}" if sid.startswith("yt:") else (
        src_url or None if d.get("source_type") == "document" else None)
    d["segment"] = f"{sid}#s{d['seg']}"
    return d


MINOR_TYPES = ("tema", "misto")      # obecná témata a místa: ve výchozím seznamu skrytá


def entity_list(con, q: str = "", etype: str | None = None, status: str | None = None, limit: int = 200,
                group: str | None = None) -> list:
    """group="main" = bez témat a míst. Zamítnuté entity jen při status="zamítnuto"."""
    where, args = ["1=1"], []
    if status != "zamítnuto":
        where.append("COALESCE(status,'') != 'zamítnuto'")
    if group == "main" and not etype:
        where.append(f"type NOT IN ({','.join('?' * len(MINOR_TYPES))})"); args += list(MINOR_TYPES)
    if q:
        where.append("(name LIKE ? OR aliases LIKE ?)"); args += [f"%{q}%", f"%{q}%"]
    if etype:
        where.append("type = ?"); args.append(etype)
    if status:
        where.append("status = ?"); args.append(status)
    rows = con.execute(f"SELECT * FROM entities WHERE {' AND '.join(where)} ORDER BY n_mentions DESC, name LIMIT ?",
                       [*args, limit]).fetchall()
    return [dict(r, aliases=json.loads(r["aliases"] or "[]")) for r in rows]


def entity_mentions(con, eid: str, since=None, until=None, speaker=None, attitude=None, include_children=False) -> dict:
    ids = [eid]
    if include_children:
        frontier = [eid]
        while frontier:
            kids = [r[0] for r in con.execute(
                f"SELECT id FROM entities WHERE parent IN ({','.join('?' * len(frontier))})", frontier)]
            frontier = [k for k in kids if k not in ids]
            ids += frontier
    where, args = [f"m.entity IN ({','.join('?' * len(ids))})"], list(ids)
    if since:
        where.append("m.published_at >= ?"); args.append(since)
    if until:
        where.append("m.published_at <= ?"); args.append(until)
    if speaker:
        where.append("m.speaker LIKE ?"); args.append(f"%{speaker}%")
    if attitude:
        where.append("m.attitude = ?"); args.append(attitude)
    rows = con.execute(f"""SELECT m.*, s.text, s.time_source, src.title, src.card FROM mentions m
        JOIN segments s ON s.source_id=m.source_id AND s.seg=m.seg JOIN sources src ON src.source_id=m.source_id
        WHERE {' AND '.join(where)} ORDER BY m.published_at DESC, m.source_id, m.t""", args).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["seg"] = d.pop("seg")
        d["url"] = f"https://youtu.be/{d['source_id'].split(':', 1)[1]}?t={int(d['t'])}" if d["source_id"].startswith("yt:") else None
        items.append(d)
    ent = con.execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()
    by_att = {}
    for i in items:
        by_att[i.get("attitude") or "—"] = by_att.get(i.get("attitude") or "—", 0) + 1
    return {"entity": dict(ent, aliases=json.loads(ent["aliases"] or "[]")) if ent else None,
            "total": len(items), "sources": len({i["source_id"] for i in items}), "by_attitude": by_att,
            "items": items}


def stats(con) -> dict:
    r = con.execute("SELECT v FROM meta WHERE k='stats'").fetchone()
    return json.loads(r[0]) if r else {}
