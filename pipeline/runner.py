"""Runner: spouští na tomto PC příkazy pipeline zadané přes soubory (pro vzdáleného asistenta).

Spuštění (PowerShell, okno nechat otevřené):   python -m pipeline runner

Úloha = 99_System/runner/inbox/<id>.json  {"id": "...", "args": ["transcribe", "ID"]}
        nebo {"id": "...", "git_sync": "zpráva commitu"}
Výstup = 99_System/runner/logs/<id>.log, stav = 99_System/runner/done/<id>.json
Tep    = 99_System/runner/heartbeat.json (každé 2 s)

Bezpečnost: povolené jsou jen podpříkazy `python -m pipeline` z ALLOWED (bez shellu,
argumenty jako seznam) a pevně daná git synchronizace. Složka runner/ je mimo Git.

Správa VPS (uživatel si přál, aby VPS spravoval asistent):
  {"ssh": "příkaz", "stdin_file": "cesta/ve/vaultu"}  → příkaz na VPS přes SSH klíč ~/.ssh/kos_vps
      (jen na jeden server: 99_System/runner/vps.txt, např. root@1.2.3.4)
  {"gh_deploy_key": "cesta/k/klic.pub", "repo": "uzivatel/x"} → přidá deploy klíč VPS k repozitáři
      (gh; výchozí = 99_System/runner/repo.txt, jen repozitáře téhož vlastníka)
Klíč ~/.ssh/kos_vps runner vytvoří sám při startu; veřejná část → 99_System/runner/kos_vps.pub.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from pipeline.config import Vault

REPO = ""                         # 99_System/runner/repo.txt, např. "uzivatel/knowledge-os"
VPS_DEFAULT = ""                  # 99_System/runner/vps.txt, např. "root@1.2.3.4"
SSH_KEY = Path.home() / ".ssh" / "kos_vps"
PIP_ALLOWED = {"yt-dlp"}          # balíčky, které smí runner aktualizovat (YouTube se často mění)
STATUS = {"job": None, "last": "", "since": None}

ALLOWED = {"transcribe", "analyze", "render", "process", "pilot", "doctor", "catalog",
           "mentions", "search", "entities", "add", "queue", "state", "models", "reindex", "worker", "voice-retry", "remove"}


def _dirs(vault: Vault) -> dict[str, Path]:
    base = vault.system / "runner"
    d = {k: base / k for k in ("inbox", "logs", "done")}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    d["base"] = base
    return d


CANCEL_FILE: Path | None = None


def _watch_cancel(p) -> None:
    """Soubor 99_System/runner/cancel ukončí běžící úlohu (asistent tak zruší zaseknutou úlohu)."""
    while p.poll() is None:
        if CANCEL_FILE and CANCEL_FILE.exists():
            try:
                CANCEL_FILE.unlink()
            except OSError:
                pass
            print("   ✗ úloha zrušena (cancel)", flush=True)
            p.terminate()
            return
        time.sleep(1)


def _run(cmd: list[str], log, cwd: Path, env: dict, stdin: Path | None = None) -> int:
    """Spustí příkaz; výstup jde do logu i živě do okna runneru (bez průběhových řádků stahování)."""
    log.write(f"$ {' '.join(cmd)}\n")
    log.flush()
    fin = open(stdin, "rb") if stdin else None
    p = subprocess.Popen(cmd, cwd=cwd, stdin=fin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    threading.Thread(target=_watch_cancel, args=(p,), daemon=True).start()
    for line in p.stdout:
        log.write(line)
        log.flush()
        clean = line.replace("\r", "\n").strip().splitlines()[-1] if line.strip() else ""
        if clean and not clean.startswith("[download]"):
            print("   " + clean[:160], flush=True)
            STATUS["last"] = clean[:200]
    rc = p.wait()
    if stdin:
        fin.close()
    log.write(f"[exit {rc}]\n")
    log.flush()
    return rc


def ensure_ssh_key(base: Path) -> None:
    """Vytvoří SSH klíč pro správu VPS (jen jednou) a zveřejní jeho veřejnou část pro asistenta."""
    try:
        if not SSH_KEY.exists():
            SSH_KEY.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "kos-runner", "-f", str(SSH_KEY)],
                           check=True, capture_output=True)
        (base / "kos_vps.pub").write_text(SSH_KEY.with_suffix(".pub").read_text(encoding="utf-8"), encoding="utf-8")
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"   (SSH klíč pro VPS se nepodařilo vytvořit: {e})")


def _read_setting(base: Path, name: str, default: str) -> str:
    f = base / name
    t = f.read_text(encoding="utf-8").strip() if f.exists() else ""
    return t or default


def _vps_target(base: Path) -> str:
    t = _read_setting(base, "vps.txt", VPS_DEFAULT)
    if not t:
        raise SystemExit("Chybí 99_System/runner/vps.txt (např. root@1.2.3.4)")
    return t


def _heartbeat(path: Path) -> None:
    while True:
        try:
            path.write_text(json.dumps({"pid": os.getpid(), "at": datetime.now().isoformat(timespec="seconds"),
                                        **STATUS}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        time.sleep(5)


def handle(vault: Vault, job: dict, d: dict) -> int:
    jid = str(job.get("id") or int(time.time()))
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    with open(d["logs"] / f"{jid}.log", "w", encoding="utf-8") as log:
        log.write(f"# {datetime.now():%Y-%m-%d %H:%M:%S} úloha {jid}\n")
        if "pip_upgrade" in job:
            pkg = str(job["pip_upgrade"])
            if pkg not in PIP_ALLOWED:
                log.write(f"ODMÍTNUTO: aktualizovat lze jen {sorted(PIP_ALLOWED)}\n")
                return 2
            return _run([sys.executable, "-m", "pip", "install", "-U", pkg], log, vault.root, env)
        if "ssh" in job:
            cmd = ["ssh", "-i", str(SSH_KEY), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                   "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30", _vps_target(d["base"]), str(job["ssh"])]
            stdin = None
            if job.get("stdin_file"):
                src = (vault.root / str(job["stdin_file"])).resolve()
                if vault.root.resolve() not in src.parents:
                    log.write("ODMÍTNUTO: stdin_file musí být uvnitř vaultu\n")
                    return 2
                stdin = src
            return _run(cmd, log, vault.root, env, stdin=stdin)
        if "gh_deploy_key" in job:
            src = (vault.root / str(job["gh_deploy_key"])).resolve()
            if vault.root.resolve() not in src.parents:
                log.write("ODMÍTNUTO: klíč musí být uvnitř vaultu\n")
                return 2
            default_repo = _read_setting(d["base"], "repo.txt", REPO)
            repo = str(job.get("repo") or default_repo)
            owner = default_repo.split("/")[0] if default_repo else ""
            if not owner or not repo.startswith(owner + "/") or any(c in repo for c in " ;&|"):
                log.write("ODMÍTNUTO: repo musí být <vlastník z repo.txt>/<název>\n")
                return 2
            return _run(["gh", "repo", "deploy-key", "add", str(src), "--allow-write", "-R", repo,
                         "-t", str(job.get("title") or "kos-vps")], log, vault.root, env)
        if "git_sync" in job:
            msg = str(job["git_sync"])[:200] or "runner: sync"
            rc = _run(["git", "pull", "--rebase", "--autostash"], log, vault.root, env)
            if rc == 0:
                _run(["git", "add", "-A"], log, vault.root, env)
                _run(["git", "commit", "-m", msg], log, vault.root, env)   # rc 1 = nic ke commitu
                rc = _run(["git", "push"], log, vault.root, env)
            return rc
        args = [str(a) for a in job.get("args", [])]
        if not args or args[0] not in ALLOWED:
            log.write(f"ODMÍTNUTO: povolené příkazy jsou {sorted(ALLOWED)}\n")
            return 2
        return _run([sys.executable, "-m", "pipeline", *args], log, vault.root, env)


def main(vault: Vault | None = None, once: bool = False) -> int:
    vault = vault or Vault.default()
    d = _dirs(vault)
    global CANCEL_FILE
    CANCEL_FILE = d["base"] / "cancel"
    if CANCEL_FILE.exists():
        CANCEL_FILE.unlink()
    print(f"Runner běží ve {vault.root}. Úlohy: {d['inbox']}  (Ctrl+C ukončí)")
    ensure_ssh_key(d["base"])
    if not once:
        threading.Thread(target=_heartbeat, args=(d["base"] / "heartbeat.json",), daemon=True).start()
    else:
        (d["base"] / "heartbeat.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    while True:
        jobs = sorted(d["inbox"].glob("*.json"))
        for f in jobs:
            try:
                job = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            f.unlink()
            jid = str(job.get("id") or f.stem)
            job["id"] = jid
            what = job.get("args") or (f"VPS: {str(job['ssh'])[:80]}" if "ssh" in job else
                                        "deploy klíč" if "gh_deploy_key" in job else "git sync")
            print(f"{datetime.now():%H:%M:%S} ▶ {jid}: {what}", flush=True)
            STATUS.update(job=jid, last="", since=datetime.now().isoformat(timespec="seconds"))
            start = time.time()
            rc = handle(vault, job, d)
            STATUS.update(job=None, since=None)
            (d["done"] / f"{jid}.json").write_text(json.dumps({"id": jid, "rc": rc, "seconds": round(time.time() - start),
                                                              "finished": datetime.now().isoformat(timespec="seconds")}),
                                                  encoding="utf-8")
            print(f"{datetime.now():%H:%M:%S} {'✓' if rc == 0 else '✗'} {jid} (rc {rc})")
        if once:
            return 0
        time.sleep(2)
