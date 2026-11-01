"""Úklid registru entit: AI projde navržené (nepotvrzené) entity a navrhne, co je šum (zamítnout),
co má špatný typ (změnit) a co je duplicita (sloučit). Uživatel návrhy odklikne, teprve pak se provedou."""
from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pipeline import index
from pipeline.config import Vault
from pipeline.entities import REJECTED, TYPES, Registry

PROMPT = """Kontroluješ rejstřík entit osobní znalostní báze (přepisy českých podcastů o AI, schůzky, dokumenty).
Entita má být KONKRÉTNÍ pojmenovaná věc: osoba, firma, produkt/nástroj, AI model, služba, hardware, projekt,
publikace, místo, událost. Typ „tema“ = obecné téma (to je v pořádku, pokud je rozumně pojmenované).

Pro každou PROBLÉMOVOU entitu navrhni jednu akci (entity v pořádku vynech):
- "reject": není to entita, jen obecné slovo nebo šum (např. „AI“ jako AI model, „Software“, „jazykový model“,
  „uživatelé“, „Amerika“ jen jako obecné místo zmíněné mimochodem je OK ponechat — zamítej jen skutečný šum),
- "retype": entita je správná, ale má špatný typ (uveď new_type z: {types}); např. Claude Code = software,
  obecný pojem jako „Umělá inteligence“ označený jako model = tema,
- "merge": JISTĚ táž věc jako jiná entita (jiný zápis, zkratka, překlep přepisu, „Kurzor“ = Cursor, „Claude AI“ = Claude)
  — uveď into = id entity, do které se má sloučit. NE u různých verzí/modelů (Claude 4.7 ≠ Claude Opus 4.8 ≠ Claude),
  NE jen podle křestního jména, NE obecný pojem do konkrétního produktu.
- Skutečné osoby (i historické), firmy a produkty nezamítej, i když jsou zmíněné jen jednou.
U každého návrhu krátce česky „why“ (max 8 slov). Buď konzervativní: když si nejsi jistý, entitu vynech.

Pro „merge“ smíš použít jako `into` i id z tohoto referenčního seznamu všech entit (id | název):
{ref}

Entity ke kontrole (id | název | typ | počet zmínek | aliasy):
{lines}
"""
BATCH = 100
SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "string"}, "action": {"type": "string", "enum": ["reject", "retype", "merge"]},
    "new_type": {"type": "string"}, "into": {"type": "string"}, "why": {"type": "string"}},
    "required": ["id", "action"]}}}, "required": ["items"]}


def _counts(vault: Vault) -> dict[str, int]:
    try:
        con = sqlite3.connect(index.path(vault))
        out = dict(con.execute("SELECT id, n_mentions FROM entities"))
        con.close()
        return out
    except sqlite3.Error:
        return {}


def suggest(vault: Vault, provider, progress=None) -> tuple[list[dict], int]:
    """Návrhy po dávkách (~100 entit, 4 souběžně), aby žádné volání netrvalo dlouho. Vrací (návrhy, počet chyb)."""
    reg = Registry.load(vault)
    counts = _counts(vault)
    # témata ne: jsou obecná z podstaty (ve výchozím seznamu skrytá) a AI by je hromadně „zamítala“
    cand = [(eid, e) for eid, e in reg.data.items()
            if e.get("status") not in ("potvrzeno", REJECTED) and e.get("type") != "tema"]
    cand.sort(key=lambda x: x[1]["name"].lower())
    batches = [cand[i:i + BATCH] for i in range(0, len(cand), BATCH)]
    ref = "\n".join(f"{eid} | {e['name']}" for eid, e in reg.data.items()
                    if e.get("status") != REJECTED and e.get("type") != "tema")
    done, failed = [0], [0]

    def ask(group):
        lines = "\n".join(f"{eid} | {e['name']} | {e.get('type')} | {counts.get(eid, 0)} | "
                          f"{', '.join((e.get('aliases') or [])[:5])}" for eid, e in group)
        try:
            out = provider.generate(PROMPT.format(types=", ".join(TYPES), ref=ref, lines=lines), SCHEMA)
            items = out.get("items") or []
        except Exception as e:  # noqa: BLE001 — jedna dávka selže, ostatní platí
            print(f"úklid entit: dávka selhala: {e}")
            failed[0] += 1
            items = []
        done[0] += 1
        if progress:
            progress(done[0], len(batches))
        return items
    with ThreadPoolExecutor(4) as ex:
        raw = [x for part in ex.map(ask, batches) for x in part]
    res, seen = [], set()
    for s in raw:
        eid, act = s.get("id"), s.get("action")
        if eid not in reg.data or eid in seen or reg.data[eid].get("status") in ("potvrzeno", REJECTED):
            continue
        e = reg.data[eid]
        item = {"id": eid, "name": e["name"], "type": e.get("type"), "mentions": counts.get(eid, 0),
                "action": act, "why": (s.get("why") or "")[:80]}
        if act == "retype":
            if s.get("new_type") not in TYPES or s.get("new_type") == e.get("type"):
                continue
            item["new_type"] = s["new_type"]
        elif act == "merge":
            into = s.get("into")
            if into not in reg.data or into == eid or reg.data[into].get("status") == REJECTED:
                continue
            item["into"], item["into_name"] = into, reg.data[into]["name"]
        elif act != "reject":
            continue
        seen.add(eid)
        res.append(item)
    # vzájemná sloučení (A→B i B→A): nechat směr k entitě s více zmínkami; řetězy A→B→C zkrátit na A→C
    mg = {x["id"]: x for x in res if x["action"] == "merge"}
    for x in list(mg.values()):
        if x["id"] not in mg:
            continue
        y = mg.get(x["into"])
        if y and y["into"] == x["id"]:
            loser = x if counts.get(x["into"], 0) < counts.get(x["id"], 0) else y
            if loser in res:
                res.remove(loser)
            mg.pop(loser["id"], None)
    for x in mg.values():
        hops = 0
        while x["into"] in mg and hops < 5:
            x["into"] = mg[x["into"]]["into"]
            x["into_name"] = reg.data[x["into"]]["name"]
            hops += 1
    res = [x for x in res if not (x["action"] == "merge" and x["into"] == x["id"])]
    order = {"reject": 0, "merge": 1, "retype": 2}
    return sorted(res, key=lambda x: (order[x["action"]], -x["mentions"])), failed[0]


# běh na pozadí: web se jen ptá na stav (návrh trvá desítky sekund)
JOB: dict = {"status": "idle"}
_LOCK = threading.Lock()


def start(vault: Vault, provider) -> dict:
    with _LOCK:
        if JOB.get("status") == "running" and time.time() - JOB.get("started", 0) < 600:
            return dict(JOB)
        JOB.clear()
        JOB.update(status="running", started=time.time(), done=0, total=0, items=[], failed=0)

    def prog(d, t):
        JOB.update(done=d, total=t)

    def run():
        try:
            items, failed = suggest(vault, provider, prog)
            JOB.update(status="done", items=items, failed=failed, finished=time.time())
        except Exception as e:  # noqa: BLE001
            JOB.update(status="error", error=str(e)[:300], finished=time.time())
    threading.Thread(target=run, daemon=True).start()
    return dict(JOB)


def state() -> dict:
    out = dict(JOB)
    if out.get("status") == "running":
        out["elapsed"] = int(time.time() - out.get("started", time.time()))
    return out


def apply(vault: Vault, items: list[dict]) -> dict:
    reg = Registry.load(vault)
    done = {"reject": 0, "retype": 0, "merge": 0, "skipped": 0}
    for s in items:
        eid, act = s.get("id"), s.get("action")
        if eid not in reg.data:
            done["skipped"] += 1
            continue
        if act == "reject":
            reg.data[eid]["status"] = REJECTED
        elif act == "retype" and s.get("new_type") in TYPES:
            reg.data[eid]["type"] = s["new_type"]
        elif act == "merge" and s.get("into") in reg.data and s["into"] != eid:
            reg.merge(vault, eid, s["into"])
        else:
            done["skipped"] += 1
            continue
        done[act] += 1
    reg._rebuild()
    reg.save()
    return done
