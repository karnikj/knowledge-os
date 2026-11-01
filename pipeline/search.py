"""Deterministické vyhledávání nad soubory ve vaultu (pro Claude Code a CLI).

- `mentions(entity)` – úplný výpis zmínek entity (vč. aliasů a volitelně podřízených entit),
- `search(text)` – fulltext v přepisech s tolerancí ke skloňování,
obojí s filtry mluvčí / období / postoj. Výsledky jsou úplné, žádný vzorek.
Ve F3 stejné dotazy obslouží SQLite index na VPS; formát výsledků zůstane stejný.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from pipeline import catalog, frontmatter
from pipeline.config import Vault
from pipeline.czech import phrase_in
from pipeline.entities import Registry
from pipeline.textnorm import norm_word


@dataclass
class Hit:
    source_id: str
    title: str
    published_at: str
    segment: int
    t: float
    url: str
    speaker: str
    text: str
    entity: str | None = None
    surface: str | None = None
    attitude: str | None = None
    card: str | None = None


def _sources(vault: Vault) -> dict[str, dict]:
    out = {}
    for e in catalog.scan(vault):
        if not e.source_id:
            continue
        fm = frontmatter.parse(e.path.read_text(encoding="utf-8")[:8192])
        out[e.source_id] = {"title": fm.get("title") or e.path.stem, "published_at": str(fm.get("published_at") or ""),
                            "card": vault.rel(e.path)}
    return out


def _url(sid: str, t: float) -> str:
    return f"https://youtu.be/{sid.split(':', 1)[1]}?t={int(t)}" if sid.startswith("yt:") else ""


def _speaker_ok(want: str | None, have: str) -> bool:
    return not want or norm_word(want.replace(" ", "")) in norm_word(have.replace(" ", ""))


def _in_period(date: str, since: str | None, until: str | None) -> bool:
    return (not since or (date and date >= since)) and (not until or (date and date <= until))


def resolve_entity(reg: Registry, query: str) -> list[str]:
    """ID entit odpovídající dotazu: přesně přes název/alias, jinak podřetězcem v názvu."""
    eid = reg.resolve(query)
    if eid:
        return [eid]
    q = norm_word(query.replace(" ", ""))
    return [k for k, e in reg.data.items()
            if q and (q in norm_word(e["name"].replace(" ", "")) or any(q in norm_word(x.replace(" ", "")) for x in e.get("aliases", [])))]


def mentions(vault: Vault, query: str, since: str | None = None, until: str | None = None,
             speaker: str | None = None, attitude: str | None = None, include_children: bool = False) -> tuple[list[str], list[Hit]]:
    reg = Registry.load(vault)
    eids = set()
    for eid in resolve_entity(reg, query):
        eids |= reg.descendants(eid) if include_children else {eid}
    srcs = _sources(vault)
    hits: list[Hit] = []
    for f in sorted(vault.transcripts.glob("*.mentions.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        sid = data["source_id"]
        info = srcs.get(sid, {"title": sid, "published_at": "", "card": None})
        if not _in_period(info["published_at"], since, until):
            continue
        tfile = f.with_name(f.name.replace(".mentions.json", ".json"))
        segs = {s["i"]: s for s in json.loads(tfile.read_text(encoding="utf-8"))["segments"]} if tfile.exists() else {}
        for m in data["mentions"]:
            if m["entity"] not in eids or not _speaker_ok(speaker, m["speaker"]):
                continue
            if attitude and m.get("attitude") != attitude:
                continue
            seg = segs.get(m["segment"], {})
            hits.append(Hit(sid, info["title"], info["published_at"], m["segment"], m["t"], _url(sid, m["t"]),
                            m["speaker"], seg.get("text", ""), reg.data.get(m["entity"], {}).get("name"),
                            m.get("surface"), m.get("attitude"), info["card"]))
    hits.sort(key=lambda h: (h.published_at, h.source_id, h.t))
    return sorted(eids), hits


def search(vault: Vault, text: str, since: str | None = None, until: str | None = None,
           speaker: str | None = None) -> list[Hit]:
    srcs = _sources(vault)
    hits: list[Hit] = []
    for f in sorted(vault.transcripts.glob("*.json")):
        if f.name.endswith((".analysis.json", ".mentions.json")):
            continue
        t = json.loads(f.read_text(encoding="utf-8"))
        sid = t.get("source_id")
        if not sid:
            continue
        info = srcs.get(sid, {"title": t.get("meta", {}).get("title", sid), "published_at": "", "card": None})
        if not _in_period(info["published_at"], since, until):
            continue
        for s in t["segments"]:
            name = t["speakers"].get(s["speaker"], s["speaker"])
            if _speaker_ok(speaker, name) and phrase_in(text, s["text"]):
                hits.append(Hit(sid, info["title"], info["published_at"], s["i"], s["start"],
                                _url(sid, s["start"]), name, s["text"], card=info["card"]))
    hits.sort(key=lambda h: (h.published_at, h.source_id, h.t))
    return hits


def print_hits(hits: list[Hit], as_json: bool = False, header: str = "") -> None:
    if as_json:
        print(json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=1))
        return
    n_src = len({h.source_id for h in hits})
    print(f"{header}{len(hits)} zásahů v {n_src} zdrojích")
    cur = None
    for h in hits:
        if h.source_id != cur:
            cur = h.source_id
            print(f"\n## {h.published_at}  {h.title}  ({h.source_id})" + (f"  [[{h.card}]]" if h.card else ""))
        att = f" [{h.attitude}]" if h.attitude else ""
        sur = f" «{h.surface}»" if h.surface else ""
        from pipeline.timefmt import mmss
        print(f"- {mmss(h.t)} {h.speaker}{sur}{att}: {h.text[:300]}  {h.url}  (^s{h.segment})")
