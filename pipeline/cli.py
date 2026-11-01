"""Příkazová řádka Knowledge OS:  python -m pipeline <příkaz>"""
from __future__ import annotations

import argparse
import sys

from pipeline import catalog, doctor, migrate_f0, queue, state
from pipeline.config import Vault


def _utf8_stdout() -> None:
    # Windows konzole: aby šla čeština a symboly ✓ ✗
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser(prog="python -m pipeline", description="Knowledge OS pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="kontrola prostředí a vaultu")
    p.add_argument("--online", action="store_true", help="ověřit i spojení s Gemini API")
    p.add_argument("--probe", action="store_true", help="s --online: poslat každému modelu krátký testovací dotaz")

    p = sub.add_parser("add", help="přidat YouTube URL do fronty (99_System/queue.txt)")
    p.add_argument("url")
    p.add_argument("--force", action="store_true", help="přidat i dříve odstraněné video")

    p = sub.add_parser("remove", help="odstranit zdroj ze znalostní báze (bez --yes jen vypíše, co by smazal)")
    p.add_argument("source_id", help="např. yt:dQw4w9WgXcQ nebo voice:20260924-101500-ab12")
    p.add_argument("--reason", default="")
    p.add_argument("--yes", action="store_true")

    sub.add_parser("queue", help="vypsat frontu")
    sub.add_parser("catalog", help="vypsat zdroje ve vaultu (z frontmatteru)")

    p = sub.add_parser("state", help="zobrazit stav kroků pro zdroj")
    p.add_argument("source_id")

    p = sub.add_parser("migrate-f0", help="doplnit identitu zdroje do existujících karet")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("transcribe", help="F1: doslovný přepis YouTube videa s časy a mluvčími")
    p.add_argument("urls", nargs="+", help="URL nebo YouTube ID (lze více)")
    p.add_argument("--force", action="store_true", help="ignorovat cache kusů a přepsat znovu")
    p.add_argument("--offline", action="store_true", help="nestahovat nic, použít jen lokální audio/titulky")

    p = sub.add_parser("pilot", help="F1a: přepsat pilotní sadu a vytvořit report kvality")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("analyze", help="F2: analýza přepisu (tvrzení, zmínky, shrnutí)")
    p.add_argument("ids", nargs="+", help="YouTube ID / URL / source_id, nebo 'all'")
    p.add_argument("--force", action="store_true", help="uložit i výrazně chudší analýzu než stávající")
    p.add_argument("--reset-entities", action="store_true",
                   help="postavit registr entit znovu (ponechá jen potvrzené); odpovědi modelu z cache")

    p = sub.add_parser("render", help="F2: vykreslit kartu do 01_Zdroje z přepisu a analýzy")
    p.add_argument("ids", nargs="+")

    p = sub.add_parser("process", help="celý řetězec: přepis → analýza → karta")
    p.add_argument("ids", nargs="+")
    p.add_argument("--force", action="store_true", help="přepsat audio znovu (ignorovat cache kusů)")

    p = sub.add_parser("mentions", help="všechny zmínky entity (osoba, firma, software, model…)")
    p.add_argument("entity")
    p.add_argument("--since"); p.add_argument("--until"); p.add_argument("--speaker")
    p.add_argument("--attitude", choices=["positive", "neutral", "negative", "mixed"])
    p.add_argument("--children", action="store_true", help="zahrnout podřízené entity (Microsoft → Power BI)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("search", help="fulltext v přepisech (tolerantní ke skloňování)")
    p.add_argument("text")
    p.add_argument("--since"); p.add_argument("--until"); p.add_argument("--speaker")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("entities", help="vypsat entity (filtr typu / stavu)")
    p.add_argument("--type"); p.add_argument("--status", choices=["navrženo", "potvrzeno"])

    p = sub.add_parser("worker", help="zpracování na pozadí: hlas, fronta, index, git (VPS)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=int, default=10)
    p.add_argument("--local", action="store_true", help="PC: zpracovat videa, která VPS nestáhl (queue_local.txt)")

    p = sub.add_parser("web", help="webová aplikace (PWA) a API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)

    sub.add_parser("voice-retry", help="znovu zpracovat hlasové poznámky, jejichž zpracování selhalo")

    sub.add_parser("reindex", help="přestavět vyhledávací index (99_System/index/kos.sqlite)")

    p = sub.add_parser("doc-add", help="přidat dokument (MD, TXT, DOCX, PDF) nebo webovou stránku")
    p.add_argument("path", help="cesta k souboru nebo https:// adresa")
    p.add_argument("--public", action="store_true", help="veřejný dokument (manuál, zákon…) → levnější model")
    p.add_argument("--new", action="store_true", help="uložit jako samostatný i při podobnosti s existujícím")

    p = sub.add_parser("vectors", help="dopočítat otisky pro hledání podle významu (hybridní hledání)")
    p.add_argument("--rebuild", action="store_true", help="smazat uložené otisky a spočítat vše znovu")

    sub.add_parser("models", help="vypsat modely Gemini dostupné pro API klíč v .env")

    sub.add_parser("runner", help="spouštět úlohy zadané asistentem (okno nechat otevřené)")

    p = sub.add_parser("legacy-add", help="[dočasné, V1] zpracovat video starým tokem Gemini-z-audia")
    p.add_argument("url")

    args = ap.parse_args(argv)
    vault = Vault.default()

    if args.cmd == "doctor":
        if args.probe:
            import os
            os.environ["KOS_PROBE"] = "1"
        return doctor.print_report(doctor.run(vault, online=args.online))
    if args.cmd == "add":
        print(queue.add(vault, args.url, force=args.force))
        return 0
    if args.cmd == "remove":
        from pipeline import remove
        r = remove.remove(vault, args.source_id, args.reason, dry_run=not args.yes)
        print(("Odstraněno" if args.yes else "Odstranilo by se (potvrďte --yes)") + f": {r['source_id']} {r['title']}")
        print("  " + "\n  ".join(r["files"]))
        if r.get("entities_removed"):
            print(f"  entity bez dalších zmínek odebrány: {', '.join(r['entities_removed'])}")
        return 0
    if args.cmd == "queue":
        items = queue.read(vault)
        print("\n".join(items) if items else "Fronta je prázdná.")
        return 0
    if args.cmd == "catalog":
        for e in catalog.scan(vault):
            print(f"{e.source_id or '-':16} {e.published_at or '':10}  {vault.rel(e.path)}")
        return 0
    if args.cmd == "state":
        import json
        print(json.dumps(state.load(vault, args.source_id), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "migrate-f0":
        changes = migrate_f0.run(vault, dry_run=args.dry_run)
        for ch in changes:
            mark = "změna" if ch.changed else "beze změny"
            print(f"{mark:11} {ch.source_id or '-':16} {ch.path.name}  {ch.note}")
        print(f"\n{sum(c.changed for c in changes)} z {len(changes)} karet "
              f"{'by se změnilo' if args.dry_run else 'změněno'}.")
        return 0
    if args.cmd == "transcribe":
        from pipeline import transcribe
        rc = 0
        for u in args.urls:
            try:
                transcribe.transcribe_youtube(u, vault, force=args.force, offline=args.offline)
            except Exception as e:  # noqa: BLE001
                rc = 1
                print(f"CHYBA {u}: {e}")
        return rc
    if args.cmd == "pilot":
        from pipeline import pilot
        return pilot.run(vault, force=args.force)
    if args.cmd in ("analyze", "render", "process"):
        from pipeline import analyze, ids as _ids, render, transcribe
        rc = 0
        prov = None
        if args.ids == ["all"]:
            args.ids = sorted(str(f.name)[:-5].replace("__", ":", 1) for f in vault.transcripts.glob("*.json")
                              if not f.name.endswith((".analysis.json", ".mentions.json")))
        if getattr(args, "reset_entities", False):
            from pipeline.entities import Registry
            reg = Registry.load(vault)
            reg.data = {k: v for k, v in reg.data.items() if v.get("status") == "potvrzeno"}
            reg.save()
            print(f"Registr entit vyčištěn (ponecháno potvrzených: {len(reg.data)})")
        for u in args.ids:
            sid = u if ":" in u and not u.startswith("http") else f"yt:{_ids.youtube_id(u) or u}"
            print(f"=== {sid}")
            try:
                if args.cmd == "process":
                    transcribe.transcribe_youtube(sid.split(":", 1)[1], vault, force=args.force)
                if args.cmd in ("analyze", "process"):
                    prov = prov or analyze.default_provider(vault, "youtube" if sid.startswith("yt:") else "private")
                    analyze.run(vault, sid, prov, force=getattr(args, "force", False) and args.cmd == "analyze")
                render.run(vault, sid)
            except Exception as e:  # noqa: BLE001
                rc = 1
                print(f"CHYBA {sid}: {e}")
        return rc
    if args.cmd == "mentions":
        from pipeline import search
        eids, hits = search.mentions(vault, args.entity, args.since, args.until, args.speaker,
                                     args.attitude, args.children)
        if not eids:
            print(f"Entita „{args.entity}“ není v registru (99_System/config/entities.yaml). "
                  f"Zkuste: python -m pipeline search \"{args.entity}\"")
            return 1
        search.print_hits(hits, args.json, header=f"Entity: {', '.join(eids)}\n")
        return 0
    if args.cmd == "search":
        from pipeline import search
        search.print_hits(search.search(vault, args.text, args.since, args.until, args.speaker), args.json)
        return 0
    if args.cmd == "entities":
        from pipeline.entities import Registry
        reg = Registry.load(vault)
        for k, e in sorted(reg.data.items(), key=lambda kv: (kv[1]["type"], kv[1]["name"].lower())):
            if (args.type and e["type"] != args.type) or (args.status and e.get("status") != args.status):
                continue
            al = f"  (aliasy: {', '.join(e['aliases'])})" if e.get("aliases") else ""
            print(f"{e['type']:11} {e.get('status', ''):10} {k:30} {e['name']}{al}")
        return 0
    if args.cmd == "worker":
        from pipeline import worker
        try:
            return worker.run(vault, interval=args.interval, once=args.once, local=args.local)
        except KeyboardInterrupt:
            return 0
    if args.cmd == "web":
        import uvicorn
        from pipeline.web.app import create_app
        uvicorn.run(create_app(vault), host=args.host, port=args.port, log_level="info")
        return 0
    if args.cmd == "voice-retry":
        from pipeline import voice
        r = voice.retry(vault)
        print(f"Vráceno do zpracování: {len(r)}", *r)
        return 0
    if args.cmd == "doc-add":
        from pipeline import documents, index
        from pipeline.docparse import Unsupported
        try:
            if args.path.startswith(("http://", "https://")):
                r = documents.ingest_url(vault, args.path, visibility="public" if args.public else "private",
                                         force_new=args.new)
            else:
                from pathlib import Path as _P
                f = _P(args.path)
                r = documents.ingest(vault, f.name, f.read_bytes(), visibility="public" if args.public else "private",
                                     force_new=args.new, origin="cli")
        except Unsupported as e:
            print("Nepodporováno:", e)
            return 2
        print(r)
        if r["status"] == "ok":
            index.build(vault)
        return 0 if r["status"] in ("ok", "duplicate") else 1
    if args.cmd == "vectors":
        from pipeline import index, vectors
        if args.rebuild and vectors.store_path(vault).exists():
            vectors.store_path(vault).unlink()
        if not index.path(vault).exists():
            index.build(vault)
        n = vectors.ensure(vault)
        print({"nove": n, **vectors.stats(vault)})
        return 0
    if args.cmd == "reindex":
        from pipeline import index
        print(index.build(vault))
        return 0
    if args.cmd == "models":
        import os
        from dotenv import load_dotenv
        from google import genai
        load_dotenv(vault.root / ".env")
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        for m in client.models.list():
            acts = ",".join(getattr(m, "supported_actions", None) or [])
            if "generateContent" in acts or not acts:
                print(f"{m.name:55} in={getattr(m, 'input_token_limit', '')} out={getattr(m, 'output_token_limit', '')}")
        return 0
    if args.cmd == "runner":
        from pipeline import runner
        try:
            return runner.main(vault)
        except KeyboardInterrupt:
            return 0
    if args.cmd == "legacy-add":
        from pipeline import legacy_v1
        legacy_v1.process(args.url, vault)
        return 0
    return 2
