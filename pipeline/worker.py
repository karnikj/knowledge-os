"""Worker: zpracování na pozadí (VPS jako systemd služba, nebo `python -m pipeline worker` na PC).

Smyčka:
  1. hlasové poznámky (priorita — uživatel čeká na text),
  2. fronta YouTube (99_System/queue.txt): přepis → analýza → karta,
  3. po změnách přestavba indexu,
  4. git: pull --rebase (každých N minut) a commit + push po změnách; nikdy force push.
Stav pro webovou aplikaci: 99_System/logs/worker.json (mimo Git).
Chyby: URL se přesune do 99_System/queue_failed.txt s důvodem (web nabídne „Zkusit znovu“).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import traceback
from datetime import datetime

from pipeline import ids, index, meetings, queue, voice
from pipeline.config import Vault

STATUS: dict = {"state": "start", "job": None, "last_error": None, "processed": 0, "since": None}


def _status(vault: Vault, **kw) -> None:
    if kw.get("job") and kw["job"] != STATUS.get("job"):
        STATUS["log"] = []  # nová úloha → nový průběh
    STATUS.update(kw, at=datetime.now().isoformat(timespec="seconds"))
    p = vault.system / "logs" / "worker.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(STATUS, ensure_ascii=False), encoding="utf-8")


def read_status(vault: Vault) -> dict:
    p = vault.system / "logs" / "worker.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _git(vault: Vault, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", str(vault.root), *args], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def git_sync(vault: Vault, message: str | None) -> str:
    if not (vault.root / ".git").exists() or os.getenv("KOS_NO_GIT") == "1":
        return "git vypnut"
    _, before = _git(vault, "rev-parse", "HEAD")
    rc, out = _git(vault, "pull", "--rebase", "--autostash")
    if rc != 0:
        _git(vault, "rebase", "--abort")
        return f"git pull selhal (konflikt?) — zastaveno, bez push: {out[-300:]}"
    _, after = _git(vault, "rev-parse", "HEAD")
    if before and after and before != after:
        _, changed = _git(vault, "diff", "--name-only", before, after, "--", "pipeline", "requirements.txt", "deploy")
        if changed.strip():
            request_restart(vault, changed.split())
            STATUS["restart_pending"] = True
    if message:
        _git(vault, "add", "-A")
        rc, out = _git(vault, "commit", "-m", message)
        if rc == 0:
            rc, out = _git(vault, "push")
            if rc != 0:
                return f"git push selhal: {out[-300:]}"
            return "commit + push"
    return "pull"


def request_restart(vault: Vault, files: list[str]) -> None:
    """Nový kód z Gitu → požádat systemd o restart služeb (kos-restart.path sleduje tento soubor)."""
    p = vault.system / "logs" / "restart_request"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(datetime.now().isoformat(timespec="seconds") + "\n" + "\n".join(files[:50]), encoding="utf-8")
    print("nový kód → žádost o restart:", ", ".join(files[:5]))


def failed_list(vault: Vault) -> list[dict]:
    p = vault.system / "queue_failed.txt"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            url, when, why = (line.split("\t") + ["", ""])[:3]
            out.append({"url": url, "at": when, "error": why})
    return out


def _fail(vault: Vault, url: str, err: str) -> None:
    with open(vault.system / "queue_failed.txt", "a", encoding="utf-8") as f:
        f.write(f"{url}\t{datetime.now().isoformat(timespec='seconds')}\t{err.replace(chr(10), ' ')[:300]}\n")


def retry_failed(vault: Vault, url: str) -> str:
    p = vault.system / "queue_failed.txt"
    if p.exists():
        keep = [l for l in p.read_text(encoding="utf-8").splitlines() if not l.startswith(url + "\t")]
        p.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return queue.add(vault, url)


def process_youtube(vault: Vault, url: str) -> None:
    from pipeline import analyze, render, transcribe
    vid = ids.youtube_id(url)
    t = transcribe.transcribe_youtube(vid, vault)
    sid = t["source_id"]
    analyze.run(vault, sid, analyze.default_provider(vault, "youtube"))
    render.run(vault, sid)


BLOCKED = ("sign in to confirm", "not a bot", "http error 403", "http error 429", "requested format is not available",
           "unable to download video data")


def _youtube_blocked(err: str) -> bool:
    e = err.lower()
    return any(b in e for b in BLOCKED)


QUOTA_PAUSE_MIN = 60


def quota_pause(vault: Vault) -> dict | None:
    """Pauza po vyčerpání denního limitu Gemini ({until, reason}), nebo None."""
    p = vault.system / "logs" / "quota_pause.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("until", "") <= datetime.now().isoformat(timespec="seconds"):
        p.unlink(missing_ok=True)
        return None
    return d


def _set_quota_pause(vault: Vault, err: Exception, minutes: int = QUOTA_PAUSE_MIN) -> None:
    from datetime import timedelta
    until = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    p = vault.system / "logs" / "quota_pause.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"until": until, "reason": str(err)[:300]}, ensure_ascii=False), encoding="utf-8")
    what = "Denní limit Gemini vyčerpán" if minutes == QUOTA_PAUSE_MIN else "Gemini je přetížené"
    _status(vault, state="quota", last_error=f"{what} — další pokus v {until[11:16]}")
    print(f"{what} → pauza do {until}")


def _temporary(err: Exception) -> bool:
    """Výpadek na straně Gemini, který přetrval všechny pokusy → zkusit později, ne označit jako chybu."""
    from pipeline.providers.gemini_stt import classify
    return classify(err) in ("overloaded", "transient", "rate")


def step(vault: Vault, local: bool = False) -> list[str]:
    """Jedno kolo práce. Vrací popisy hotových věcí (pro commit zprávu).

    local=True (PC): jen fronta queue_local.txt — videa, která VPS nemohl stáhnout.
    """
    from pipeline.providers.gemini_stt import QuotaExhausted
    done: list[str] = []
    if quota_pause(vault):
        return done
    qfile = queue.local_file(vault) if local else vault.queue_file
    if not local:
        for mid in meetings.auto_approve_due(vault):
            done.append(f"schůzka {mid} uložena automaticky (14 dní bez kontroly)")
    for s in ([] if local else voice.ready_sessions(vault)):
        meeting = voice.session_meta(vault, s).get("meeting")
        _status(vault, state="busy", job=f"{'schůzka' if meeting else 'hlasová poznámka'} {s}",
                since=datetime.now().isoformat(timespec="seconds"))
        try:
            if meeting:
                meetings.process(vault, meeting, s)
                done.append(f"schůzka {meeting} ke kontrole")
                continue
            r = voice.process(vault, s)
            done.append(f"hlas {r['source_id']}")
        except QuotaExhausted as e:
            _set_quota_pause(vault, e)          # relace zůstává připravená, zpracuje se po pauze
            return done
        except Exception as e:  # noqa: BLE001
            if _temporary(e):                   # přetížené Gemini: relace zůstává, zkusit za 30 min
                _set_quota_pause(vault, e, minutes=30)
                return done
            traceback.print_exc()
            _status(vault, last_error=f"hlas {s}: {e}")
            if meeting:
                try:
                    m = meetings.load(vault, meeting)
                    m.update(status="error", error=str(e)[:300])
                    meetings.save(vault, m)
                except Exception:  # noqa: BLE001
                    pass
            # relaci ponecháme, ale označíme, aby se nezacyklila
            m = voice.inbox(vault) / s / "meta.json"
            meta = json.loads(m.read_text(encoding="utf-8"))
            meta.update(finished=False, error=str(e)[:300])
            m.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if not local and not done:                 # doplnit analýzu hlasových poznámek, kterým chybí
        for sid in voice.missing_analysis(vault)[:1]:
            _status(vault, state="busy", job=f"analýza {sid}", since=datetime.now().isoformat(timespec="seconds"))
            try:
                voice.complete_analysis(vault, sid)
                done.append(f"analýza {sid}")
            except QuotaExhausted as e:
                _set_quota_pause(vault, e)
                return done
    if not local:
        r = _documents_step(vault)
        if r is None:                          # vyčerpaný limit / přetížené Gemini → pauza nastavena
            return done
        done += r
    for url in queue.read(vault, qfile)[:1]:    # jedno video za kolo; hlas má mezitím přednost
        _status(vault, state="busy", job=f"video {url}", since=datetime.now().isoformat(timespec="seconds"))
        try:
            process_youtube(vault, url)
            done.append(f"video {ids.youtube_id(url)}")
        except QuotaExhausted as e:
            _set_quota_pause(vault, e)          # URL zůstává ve frontě; hotové kusy přepisu jsou v cache
            return done
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            if _temporary(e):                   # přetížené Gemini: nechat ve frontě, zkusit za 30 min
                _set_quota_pause(vault, e, minutes=30)
                return done
            if not local and _youtube_blocked(str(e)):
                queue.add(vault, url, queue.local_file(vault))
                done.append(f"video {ids.youtube_id(url)} → PC (YouTube blokuje VPS)")
                _status(vault, last_error=f"{url}: YouTube blokuje stažení z VPS — předáno PC (queue_local.txt)")
            else:
                _fail(vault, url, str(e))
                _status(vault, last_error=f"{url}: {e}")
        queue.remove(vault, url, qfile)
    return done


_DOC_SCAN = {"at": 0.0, "refresh_at": 0.0}


def _documents_step(vault: Vault) -> list[str] | None:
    """Dokumenty: složka 16_Inbox_Dokumenty → příjem; AI karta (1 za kolo); týdenní kontrola sledovaných stránek.
    Vrací None, když se má worker pozastavit (limit Gemini)."""
    from pipeline import documents
    from pipeline.providers.gemini_stt import QuotaExhausted
    done = documents.intake_inbox(vault)
    now = time.time()
    if now - _DOC_SCAN["refresh_at"] > 1800:                 # sledované webové stránky: kontrola 2× za hodinu
        _DOC_SCAN["refresh_at"] = now
        for sid in documents.refresh_due(vault)[:3]:
            _status(vault, state="busy", job=f"kontrola stránky {sid}", since=datetime.now().isoformat(timespec="seconds"))
            r = documents.refresh(vault, sid)
            if r.get("status") == "ok":
                done.append(f"nová verze stránky {r['title']}")
    if done or now - _DOC_SCAN["at"] > 60:
        _DOC_SCAN["at"] = now
        for sid in documents.pending_cards(vault)[:1]:
            _status(vault, state="busy", job=f"dokument {sid}", since=datetime.now().isoformat(timespec="seconds"))
            try:
                documents.make_card(vault, sid, documents.provider_for(vault, sid))
                done.append(f"karta dokumentu {sid}")
            except QuotaExhausted as e:
                _set_quota_pause(vault, e)
                return None
            except Exception as e:  # noqa: BLE001
                if _temporary(e):
                    _set_quota_pause(vault, e, minutes=30)
                    return None
                traceback.print_exc()
                documents.card_failed(vault, sid, str(e))
                _status(vault, last_error=f"dokument {sid}: {e}")
    return done


class _Progress:
    """Kopíruje výpis workeru i do worker.json (posledních 12 řádků) → web ukáže průběh běžící úlohy."""

    def __init__(self, vault: Vault, out):
        self.vault, self.out, self.buf = vault, out, ""

    def write(self, s: str) -> int:
        self.out.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            line = line.strip()
            if line and STATUS.get("job") and "AFC" not in line:
                STATUS.setdefault("log", []).append({"at": datetime.now().strftime("%H:%M:%S"), "text": line[:220]})
                del STATUS["log"][:-12]
                try:
                    _status(self.vault)
                except OSError:
                    pass
        return len(s)

    def flush(self) -> None:
        self.out.flush()

    def __getattr__(self, name):
        return getattr(self.out, name)


def run(vault: Vault | None = None, interval: int = 10, pull_every: int = 300, once: bool = False,
        local: bool = False) -> int:
    vault = vault or Vault.default()
    import sys
    if not isinstance(sys.stdout, _Progress):
        sys.stdout = _Progress(vault, sys.stdout)
    print(f"Worker běží ve {vault.root}" + (" (PC: jen queue_local.txt)" if local else ""))
    last_pull = 0.0
    if not index.path(vault).exists():
        index.build(vault)
    while True:
        try:
            if time.time() - last_pull > pull_every:
                _status(vault, state="git")
                print("git:", git_sync(vault, None))
                last_pull = time.time()
                if STATUS.pop("restart_pending", False) and os.getenv("INVOCATION_ID"):
                    _status(vault, state="restart", job=None)   # pod systemd: počkat na restart s novým kódem
                    time.sleep(90)
                    return 0
            dirty = bool(_git(vault, "status", "--porcelain")[1].strip()) if (vault.root / ".git").exists() else False
            done = step(vault, local=local)
            if once and local and queue.read(vault, queue.local_file(vault)):
                while True:                      # --local --once: dokončit celou lokální frontu
                    more = step(vault, local=True)
                    done += more
                    if not more or not queue.read(vault, queue.local_file(vault)):
                        break
            if not done and dirty:
                # změny z webové aplikace (odstranění zdroje, úprava entit, přidání do fronty) → uložit do Gitu
                print("web:", git_sync(vault, "web: změny z aplikace\n\nCo-Authored-By: Knowledge OS worker <noreply@localhost>"))
                last_pull = time.time()
            if done:
                stats = index.build(vault)
                STATUS["processed"] = STATUS.get("processed", 0) + len(done)
                msg = "worker: " + ", ".join(done)
                print(msg, "|", git_sync(vault, msg + "\n\nCo-Authored-By: Knowledge OS worker <noreply@localhost>"), "|", stats)
                last_pull = time.time()
            _status(vault, state="idle", job=None, since=None)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            _status(vault, state="error", last_error=str(e)[:300])
        if once:
            return 0
        time.sleep(interval)
