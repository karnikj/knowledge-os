"""„Zeptat se“: odpověď v češtině POUZE ze zdrojů ve znalostní bázi, s citacemi (video + čas, dokument + strana).

Postup (hybridní RAG):
  1. vyhledání: podle slov (FTS5, stemmované, bm25) + podle významu (vektory, pipeline.vectors — i česká
     otázka nad anglickým textem) + entity z otázky (registr → zmínky); pořadí se sečte (reciprocal rank),
  2. kontext: nalezené segmenty s ±1 sousedním segmentem, seskupené do očíslovaných úryvků [n],
  3. Gemini odpoví jen z úryvků a každé tvrzení cituje [n]; když úryvky nestačí, řekne to,
  4. výstup: odpověď (Markdown) + seznam citací (zdroj, mluvčí, čas, odkaz, text úryvku).
"""
from __future__ import annotations

import math
import re
import sqlite3
import time

from pipeline.czech import stem
from pipeline.entities import Registry
from pipeline.textnorm import norm_word

STOP = set("""a aby ale ani ano asi az bez bude budou by byl byla byli bylo byt co coz cemu ci do jak jaka jake jaky
jako je jeho jej jeji jen jestli jsem jsi jsme jsou jste k kam kde kdo kdy kdyz ke ktera ktere ktery kteri
ma mate me mi mit mne mnou moc muj muze my na nad nam nas ne nebo nej nejak neni nez nic o od on ona oni ono
pak po pod podle pokud pro proc proto protoze pri s se si sve svuj ta tak take takze tam te tech tedy ten tento
teto tim to tohle toho tom tomto tu tuto ty tyto u uz v vam vas ve vice vsak vse vy z za ze zda ze zde
rekni reknete popis popiste vysvetli vysvetlete prosim chci chtel vime vis vite mluvil mluvila mluvili rikal rikala
rikali hodnotil hodnotila hodnotili ho mu ji jim jich zminil zminila zminili vyuziva vyuzivaji pouziva pouzivaji""".split())

ASK_PROMPT = """Jsi asistent osobní znalostní báze. Odpověz na otázku česky, POUZE na základě očíslovaných úryvků
z přepisů níže (videa, hlasové poznámky, schůzky). Nepoužívej vlastní znalosti.

Pravidla:
- Každé tvrzení v odpovědi zakonči citací úryvku ve tvaru [n] (i více: [2][5]).
- Rozlišuj, kdo co řekl (mluvčí, zdroj) — jde o názory a informace z pořadů, ne o ověřená fakta.
- Když úryvky na otázku neodpovídají nebo jen částečně, řekni to otevřeně (enough=false) a nic nedomýšlej.
- Struktura: krátká přímá odpověď (1–3 věty), pak podle potřeby odrážky s podrobnostmi. Markdown, bez nadpisů.

Otázka: {question}

Úryvky:
{context}
"""

SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"},
    "enough": {"type": "boolean"},
    "used": {"type": "array", "items": {"type": "integer"}}}, "required": ["answer", "enough"]}


def keywords(question: str) -> list[str]:
    out = []
    for w in re.findall(r"[\w\-\.]+", question):
        n = norm_word(w).strip(".-")
        if len(n) >= 2 and n not in STOP and n not in out:
            out.append(n)
    return out


def _fts(words: list[str]) -> str:
    parts = []
    for w in words:
        w2 = re.sub(r"[^0-9a-z]", "", w)
        if not w2:
            continue
        s = stem(w2) or w2
        parts.append(f'text:"{w2}"* OR stemmed:"{s}"*')
    return " OR ".join(f"({p})" for p in parts)


def find_entities(question: str, reg: Registry) -> list[str]:
    words = re.findall(r"[\w\-\.]+", question)
    found = []
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            if n == 1 and (norm_word(phrase) in STOP or len(phrase) < 2):
                continue
            eid = reg.resolve(phrase)
            if eid and eid not in found and not reg.rejected(eid) and (reg.data[eid].get("type") != "tema" or n > 1):
                found.append(eid)
    return found[:5]


def retrieve(con: sqlite3.Connection, reg: Registry, question: str, since: str | None = None,
             until: str | None = None, max_hits: int = 40, semantic=None, source_id: str | None = None) -> list[dict]:
    """Vrací seznam (source_id, seg) s pořadím relevance.

    semantic(question, k, since, until, source_id) → [{source_id, seg_from, seg_to}] = hledání podle významu;
    source_id = hledat jen v jednom zdroji („zeptat se na tento dokument“)."""
    score: dict[tuple[str, int], float] = {}
    where, args = ["src.archived = 0"], []
    if source_id:
        where.append("src.source_id = ?"); args.append(source_id)
    if since:
        where.append("src.published_at >= ?"); args.append(since)
    if until:
        where.append("src.published_at <= ?"); args.append(until)
    w = (" AND " + " AND ".join(where)) if where else ""
    kws = keywords(question)
    q = _fts(kws)
    weights = _idf(con, kws) if q else []
    if q:
        rows = con.execute(f"""SELECT s.source_id, s.seg, bm25(seg_fts) AS r, s.text FROM seg_fts
                              JOIN segments s ON s.rowid = seg_fts.rowid JOIN sources src ON src.source_id = s.source_id
                              WHERE seg_fts MATCH ? {w} ORDER BY r LIMIT 80""", [q, *args]).fetchall()
        stems_q = [(stem(re.sub(r"[^0-9a-z]", "", k)) or k)[:6] for k in kws]
        wsum = sum(weights) or 1
        for i, r in enumerate(rows):
            # shoda jen v obecném slově („nastavit“, „slouží“) váží málo, ve vzácném („MFA“, „Entra“) hodně
            txt = " ".join(norm_word(x) for x in re.findall(r"[\w\-\.]+", r[3]))
            frac = sum(w for st, w in zip(stems_q, weights) if st and st in txt) / wsum
            score[(r[0], r[1])] = score.get((r[0], r[1]), 0) + (0.15 + 0.85 * frac) / (i + 5)
    if semantic is not None:
        try:
            vhits = semantic(question, 60, since, until, source_id)
        except Exception as e:  # noqa: BLE001 — bez vektorů zůstane hledání podle slov
            print(f"vektory: {e}")
            vhits = []
        top = vhits[0]["score"] if vhits else 0
        # podobnost významu je u tohoto modelu „zašuměná“ (i nesouvisející úsek má ~0,3) → jen blízko nejlepšího
        vhits = [h for h in vhits if h.get("score", 1) >= max(0.28, top * 0.8)]
        for i, h in enumerate(vhits):
            for seg in range(h["seg_from"], h["seg_to"] + 1):
                k = (h["source_id"], seg)
                score[k] = score.get(k, 0) + 1.0 / (i + 5)
    for eid in find_entities(question, reg):
        ids = reg.descendants(eid)
        rows = con.execute(f"""SELECT m.source_id, m.seg FROM mentions m JOIN sources src ON src.source_id = m.source_id
                              WHERE m.entity IN ({','.join('?' * len(ids))}) {w}
                              ORDER BY m.published_at DESC LIMIT 60""", [*ids, *args]).fetchall()
        for i, r in enumerate(rows):
            score[(r[0], r[1])] = score.get((r[0], r[1]), 0) + 1.5 / (i + 5)
    best = sorted(score.items(), key=lambda kv: -kv[1])[:max_hits]
    return [{"source_id": k[0], "seg": k[1], "score": v} for k, v in best]


def _idf(con: sqlite3.Connection, kws: list[str]) -> list[float]:
    """Váha slova otázky podle vzácnosti v bázi: log(N / počet segmentů se slovem)."""
    n = con.execute("SELECT COUNT(*) FROM segments").fetchone()[0] or 1
    out = []
    for k in kws:
        q = _fts([k])
        try:
            df = con.execute("SELECT COUNT(*) FROM seg_fts WHERE seg_fts MATCH ?", (q,)).fetchone()[0] if q else 0
        except sqlite3.Error:
            df = 0
        out.append(max(0.2, math.log((n + 1) / (df + 1))))
    return out


def _cite(n: int, sid: str, src, r: list, speakers: list[str]) -> dict:
    """Jeden očíslovaný úryvek [n] pro model i pro web (video: čas, dokument: kapitola + strana)."""
    t = r[0][1]
    text = " ".join(x[3] for x in r) if src[4] != "document" else "\n\n".join(x[3] for x in r)
    vis = src[5] if len(src) > 5 else None
    c = {"n": n, "source_id": sid, "title": src[0], "date": src[1], "channel": src[2],
         "source_type": src[4], "seg": r[0][0], "t": t, "speaker": ", ".join(s for s in speakers if s),
         "text": text, "url": None, "private": (vis or ("public" if sid.startswith("yt:") else "private")) != "public"}
    if src[4] == "document":
        c["page"] = int(t) if t else None
        c["section"] = c["speaker"]
        c["url"] = src[3] or None
        where = ", ".join(x for x in (c["section"], f"str. {c['page']}" if c["page"] else "") if x)
        c["block"] = f"[{n}] Dokument „{src[0]}“" + (f" — {where}" if where else "") + f"\n{text}\n"
    else:
        vid = sid.split(":", 1)[1] if sid.startswith("yt:") else None
        c["url"] = f"https://youtu.be/{vid}?t={int(t)}" if vid else None
        c["block"] = (f"[{n}] {src[0]} ({src[1] or 'bez data'}; {src[2] or src[4]}) — {c['speaker']}, "
                      f"{int(t) // 60}:{int(t) % 60:02d}\n{text}\n")
    return c


def doc_snippets(con: sqlite3.Connection, sid: str, max_chars: int = 1500) -> list[dict]:
    """Celý dokument jako úryvky v pořadí (pro „zeptat se na tento dokument“, když se vejde modelu)."""
    src = con.execute("SELECT title, published_at, channel, url, source_type, visibility FROM sources "
                      "WHERE source_id=?", (sid,)).fetchone()
    if not src:
        return []
    rows = con.execute("SELECT seg, t, speaker, text FROM segments WHERE source_id=? ORDER BY seg", (sid,)).fetchall()
    cites, run, size = [], [], 0
    for r in rows:
        if run and (size + len(r[3]) > max_chars or r[2] != run[-1][2]):
            cites.append(_cite(len(cites) + 1, sid, src, run, [run[0][2]])); run, size = [], 0
        run.append(r); size += len(r[3])
    if run:
        cites.append(_cite(len(cites) + 1, sid, src, run, [run[0][2]]))
    return cites


def collect(con: sqlite3.Connection, hits: list[dict]) -> list[dict]:
    """Zásahy → očíslované úryvky [n] seřazené podle relevance napříč zdroji (sousední segmenty se slijí).

    Přepis: zásah ±1 segment kontextu. Dokument: úsek (~1000 znaků) stačí sám, slévají se nejvýš 2 úseky.
    Pořadí = nejlepší skóre úryvku, takže jeden zdroj s mnoha zásahy nevytlačí ostatní."""
    by_src: dict[str, set[int]] = {}
    sc: dict[tuple[str, int], float] = {}
    sids = list({h["source_id"] for h in hits})
    docs = {r[0] for r in con.execute(
        f"SELECT source_id FROM sources WHERE source_type='document' AND source_id IN ({','.join('?' * len(sids))})",
        sids)} if sids else set()
    for rank, h in enumerate(hits):
        sc[(h["source_id"], h["seg"])] = h.get("score", 1.0 / (rank + 5))
        by_src.setdefault(h["source_id"], set()).update(
            {h["seg"]} if h["source_id"] in docs else {h["seg"] - 1, h["seg"], h["seg"] + 1})
    runs_all = []
    for sid in by_src:
        src = con.execute("SELECT title, published_at, channel, url, source_type, visibility FROM sources "
                          "WHERE source_id=?", (sid,)).fetchone()
        if not src:
            continue
        segs = sorted(x for x in by_src[sid] if x >= 0)
        rows = {r[0]: r for r in con.execute(
            f"SELECT seg, t, speaker, text FROM segments WHERE source_id=? AND seg IN ({','.join('?' * len(segs))})",
            [sid, *segs])}
        run, runs = [], []
        is_doc = sid in docs
        for x in segs:
            if x not in rows:
                continue
            if run and (x != run[-1][0] + 1 or (is_doc and (len(run) >= 2 or rows[x][2] != run[-1][2]))):
                runs.append(run); run = []
            run.append(rows[x])
        if run:
            runs.append(run)
        for r in runs:
            best = max((sc.get((sid, x[0]), 0) for x in r), default=0)
            runs_all.append((best, sid, src, r))
    runs_all.sort(key=lambda z: -z[0])
    cites = []
    for _, sid, src, r in runs_all:
        speakers = []
        for x in r:
            if x[2] not in speakers:
                speakers.append(x[2])
        cites.append(_cite(len(cites) + 1, sid, src, r, speakers))
    return cites


def build_context(con: sqlite3.Connection, hits: list[dict], max_chars: int = 45000) -> tuple[str, list[dict]]:
    cites, out, total = [], [], 0
    for c in collect(con, hits):
        if total + len(c["block"]) > max_chars:
            break
        cites.append(c); out.append(c["block"]); total += len(c["block"])
    return "\n".join(out), cites


LEVELS = {
    "light": {"n": 8, "chars": 12000, "mentions": 0,
              "style": "Do `answer` napiš STRUČNOU odpověď: 2–4 věty s citacemi [n]. Pole `mentions` nech prázdné."},
    "standard": {"n": 20, "chars": 30000, "mentions": 5,
                 "style": ("Do `answer` napiš 2–3 věty přímé odpovědi s citacemi [n]. Do `mentions` dej 5 nejrelevantnějších "
                           "RŮZNÝCH zmínek (méně jen tehdy, když jich úryvky víc neobsahují). " + "{mention}")},
    "full": {"n": 15, "chars": 40000, "mentions": 15,
             "style": ("ÚPLNÝ VÝČET: do `mentions` dej JEDNU položku za KAŽDÝ úryvek, který se k tématu otázky vztahuje "
                       "(nesluč je, i když jde o stejného mluvčího), v pořadí úryvků. Úryvky mimo téma vynech. "
                       "`answer` = jedna věta shrnutí. " + "{mention}")},
}

MENTION = ("Každá zmínka = pole `point`: 1–2 plynulé věty česky vlastními slovy (anglický text přelož), co konkrétně "
           "úryvek říká. Název zdroje, typ („dokument“, „video“) ani datum do textu NEPIŠ — uživatel je uvidí v odkazu [n]. "
           "Jen u přepisů videí, poznámek a schůzek, kde záleží na tom, KDO to řekl, uveď mluvčího přirozeně ve větě "
           "(např. „Martin Jirsák používá…“).")
for _l in LEVELS.values():
    _l["style"] = _l["style"].replace("{mention}", MENTION)

SCHEMA_CHAT = {"type": "object", "properties": {
    "answer": {"type": "string"},
    "enough": {"type": "boolean"},
    "mentions": {"type": "array", "items": {"type": "object", "properties": {
        "who": {"type": "string"}, "source": {"type": "string"}, "date": {"type": "string"},
        "point": {"type": "string"}, "n": {"type": "array", "items": {"type": "integer"}}},
        "required": ["point", "n"]}}},
    "required": ["answer", "enough"]}


def _render(out: dict, limit: int) -> str:
    text = (out.get("answer") or "").strip()
    items = (out.get("mentions") or [])[:limit] if limit else []
    lines = []
    for m in items:
        cites = "".join(f"[{int(n)}]" for n in (m.get("n") or [])[:4] if str(n).lstrip("-").isdigit())
        point = (m.get("point") or "").strip()
        if point:                                # jen obsah; zdroj a typ ukazuje barevné číslo citace
            if re.search(r"\[\d+\]", point):         # model citoval už v textu → nezdvojovat
                cites = ""
            lines.append(f"- {point} {cites}".rstrip())
    return (text + ("\n\n" + "\n".join(lines) if lines else "")).strip()


CHAT_PROMPT = """Jsi asistent osobní znalostní báze. Odpověz česky POUZE na základě očíslovaných úryvků ze zdrojů
(přepisy videí, hlasové poznámky, schůzky a dokumenty — manuály, postupy, předpisy; dokumenty mohou být anglicky).
Nepoužívej vlastní znalosti.

Pravidla:
- Každé tvrzení zakonči citací úryvku ve tvaru [n] (i více: [2][5]). Čísla používej přesně podle úryvků.
- Piš plynulou odpověď; názvy zdrojů a slova jako „dokument“ či „video“ do textu nevkládej — zdroj ukazuje citace [n].
- U přepisů rozlišuj, kdo co řekl — jde o názory a informace z pořadů, ne o ověřená fakta.
  U dokumentů jde o jejich obsah: postupy a pravidla popiš přesně (kroky, názvy nastavení, paragrafy).
- Když úryvky na otázku neodpovídají nebo jen částečně, řekni to (enough=false) a nic nedomýšlej.
- Výstup je JSON: {style}

Otázka: {question}

Úryvky:
{context}
"""


WHOLE_DOC_CHARS = 120000

TRIAGE_PROMPT = """Rozhoduješ, které úryvky ze znalostní báze se týkají otázky. Úryvky jsou zkrácené.
Relevantní = úryvek obsahuje informaci, která k odpovědi na otázku přispívá (i částečně). Nerelevantní = jen
sdílí obecné slovo nebo téma, ale k otázce nic neříká. Buď přísný. Vrať JSON {{"relevant": [čísla úryvků]}}.

Otázka: {question}

Úryvky:
{context}
"""
SCHEMA_TRIAGE = {"type": "object", "properties": {"relevant": {"type": "array", "items": {"type": "integer"}}},
                 "required": ["relevant"]}
TRIAGE_MAX = 80
_TRIAGE: dict = {}


def _renumber(cs: list[dict]) -> list[dict]:
    out = []
    for i, c in enumerate(cs, 1):
        old = f"[{c['n']}]"
        out.append({**c, "n": i, "block": f"[{i}]" + c["block"][len(old):]})
    return out


def triage(question: str, cands: list[dict], provider_for, tracker, key: tuple) -> tuple[list[dict], int]:
    """Úplný výčet: AI projde všechny nalezené úryvky (zkrácené) a nechá jen ty, které se otázky týkají.
    Výsledek se pamatuje 30 min, aby další stránky („Dalších 15 zmínek“) nestály nic a byly stejné."""
    now = time.time()
    for k in [k for k, v in _TRIAGE.items() if now - v[0] > 1800]:
        _TRIAGE.pop(k, None)
    if key in _TRIAGE:
        return _TRIAGE[key][1], _TRIAGE[key][2]
    cands = cands[:TRIAGE_MAX]
    ctx = "\n".join(f"[{c['n']}] {c['title'][:80]}" + (f" — {c['speaker'][:40]}" if c.get("speaker") else "")
                    + "\n" + re.sub(r"\s+", " ", c["text"])[:450] + "\n" for c in cands)
    try:
        provider = provider_for(any(c["private"] for c in cands))
        provider.tracker = tracker
        out = provider.generate(TRIAGE_PROMPT.format(question=question, context=ctx), SCHEMA_TRIAGE)
        rel = out.get("relevant") if isinstance(out, dict) else None
    except Exception as e:  # noqa: BLE001 — bez prověření zůstane původní seznam
        print(f"triage: {e}")
        rel = None
    if rel is None:
        kept = cands
    else:
        keep = {int(x) for x in rel if str(x).lstrip("-").isdigit()}
        kept = [c for c in cands if c["n"] in keep]
    kept = _renumber(kept)
    _TRIAGE[key] = (now, kept, len(cands))
    return kept, len(cands)


def chat(con: sqlite3.Connection, reg: Registry, question: str, provider_for, level: str = "standard",
         page: int = 0, since: str | None = None, until: str | None = None, semantic=None,
         source_id: str | None = None) -> dict:
    """provider_for(private: bool) → poskytovatel. Soukromé zdroje (poznámky, schůzky) → Gemini, veřejné → levný model.

    source_id = otázka jen nad jedním zdrojem; menší dokument dostane model celý (bez vyhledávání)."""
    from pipeline import usage
    cfg = LEVELS.get(level, LEVELS["standard"])
    question = question.strip()[:1000]
    whole = False
    if source_id:
        tot = con.execute("SELECT COALESCE(SUM(LENGTH(text)),0) FROM segments WHERE source_id=?", (source_id,)).fetchone()[0]
        if 0 < tot <= WHOLE_DOC_CHARS:
            whole = True
    if whole:
        allc = doc_snippets(con, source_id)
        cfg = {**cfg, "n": len(allc), "chars": WHOLE_DOC_CHARS * 2}
        level = "standard" if level == "full" else level
    else:
        hits = retrieve(con, reg, question, since, until, max_hits=200 if level == "full" else 60,
                        semantic=semantic, source_id=source_id)
        allc = collect(con, hits)
    tracker = usage.Tracker()
    checked = len(allc)
    if level == "full" and allc:
        allc, checked = triage(question, allc, provider_for, tracker,
                               (question.lower(), since, until, source_id))
    if level == "full":
        chosen = allc[page * cfg["n"]:(page + 1) * cfg["n"]]
    else:
        chosen, total = [], 0
        for c in allc:
            if len(chosen) >= cfg["n"]:
                break
            if total + len(c["block"]) > cfg["chars"]:
                continue                        # příliš dlouhý úryvek přeskočit, další se může vejít
            chosen.append(c); total += len(c["block"])
    base = {"level": level, "page": page, "total_snippets": len(allc), "checked": checked, "whole_document": whole,
            "has_more": level == "full" and (page + 1) * cfg["n"] < len(allc)}
    if not chosen:
        msg = ("Další zmínky už nejsou." if page else
               f"Prověřil jsem {checked} nalezených úryvků, ale žádný se k otázce opravdu nevztahuje." if checked else
               "Ve znalostní bázi jsem k této otázce nic nenašel.")
        return {**base, "answer": msg, "enough": False, "citations": [], "usage": tracker.total()}
    from pipeline.providers.gemini_stt import QuotaExhausted
    private = any(c["private"] for c in chosen)
    note = ""

    def run(snips, priv):
        provider = provider_for(priv)
        provider.tracker = tracker
        context = "\n".join(c["block"] for c in snips)
        return provider.generate(CHAT_PROMPT.format(style=cfg["style"], question=question, context=context), SCHEMA_CHAT)
    try:
        out = run(chosen, private)
    except Exception as e:  # noqa: BLE001
        from pipeline.providers.gemini_stt import classify
        busy = not isinstance(e, QuotaExhausted) and classify(e) in ("overloaded", "transient", "rate")
        if not (private and (isinstance(e, QuotaExhausted) or busy)):
            raise
        public = [c for c in chosen if not c["private"]]
        if not public:
            raise
        # soukromé zdroje jdou jen přes Gemini; když má vyčerpaný limit nebo je přetížené, odpovíme z veřejných
        why = "je teď přetížené" if busy else "má vyčerpaný denní limit"
        note = (f"Vaše soukromé zdroje ({len(chosen) - len(public)} úryvků) tentokrát nebyly zahrnuty — "
                f"Gemini {why} a soukromá data jinam neposíláme. Zkuste otázku za chvíli znovu.")
        chosen, private = public, False
        out = run(chosen, False)
    ans = _render(out, cfg["mentions"])
    used = set(int(x) for x in re.findall(r"\[(\d+)\]", ans)) | set(out.get("used") or [])
    cites = [{k: v for k, v in c.items() if k != "block"} for c in chosen if c["n"] in used]
    return {**base, "answer": ans, "enough": bool(out.get("enough", True)), "citations": cites,
            "searched": len(chosen), "private": private, "usage": tracker.total(), "note": note,
            "range": [chosen[0]["n"], chosen[-1]["n"]]}


def ask(con: sqlite3.Connection, reg: Registry, question: str, provider, since: str | None = None,
        until: str | None = None) -> dict:
    """Zpětná kompatibilita (jeden poskytovatel pro vše)."""
    return chat(con, reg, question, lambda private: provider, "standard", 0, since, until)
