"""Hledání podle významu (vektory) pro celou znalostní bázi — druhá polovina hybridního hledání.

- Úseky („chunks“) staví index: u přepisů okna po sobě jdoucích segmentů (~600 znaků), u dokumentů
  jeden úsek = jeden segment (~1000 znaků). Každý úsek nese „hlavičku“ (název zdroje › kapitola / mluvčí),
  aby otisk věděl, odkud text je.
- Otisky počítá malý vícejazyčný model přímo na serveru (fastembed / ONNX, nic neodchází ven).
  Česká otázka tak najde i anglický manuál.
- Otisky se ukládají do 99_System/index/vectors.sqlite podle hashe textu úseku → index se může
  kdykoli přestavět, přepočítá se jen to, co je nové nebo změněné.
- Při dotazu: otisk otázky × matice otisků (kosinová podobnost) → nejbližší úseky. Spojení s hledáním
  podle slov (RRF) dělá pipeline.ask.retrieve.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path

from pipeline.config import Vault

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
WINDOW_CHARS = 600


def chunk_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def windows(segments: list[dict], max_chars: int = WINDOW_CHARS) -> list[list[dict]]:
    """Po sobě jdoucí segmenty → okna ~max_chars (bez překročení velké mezery / změny kapitoly u dokumentů)."""
    out, cur, n = [], [], 0
    for s in segments:
        if cur and n + len(s["text"]) > max_chars:
            out.append(cur); cur, n = [], 0
        cur.append(s); n += len(s["text"]) + 1
    if cur:
        out.append(cur)
    return out


# ------------------------------------------------------------------ modely
class HashEmbedder:
    """Náhradní „model“ pro testy: pytel slov → hashované dimenze (bez sítě a bez stahování)."""
    name, dim = "hash-test", 256

    def _one(self, text: str):
        import numpy as np
        import re
        from pipeline.textnorm import norm_word
        v = np.zeros(self.dim, dtype=np.float32)
        for w in re.findall(r"\w+", text):
            w = norm_word(w)[:5]
            if len(w) >= 2:
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
        n = np.linalg.norm(v)
        return v / n if n else v

    def passages(self, texts):
        import numpy as np
        return np.array([self._one(t) for t in texts], dtype=np.float32)

    def query(self, text):
        return self._one(text)


class FastEmbedder:
    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: str | None = None, threads: int = 2):
        from fastembed import TextEmbedding
        self.name = model
        self.m = TextEmbedding(model, cache_dir=cache_dir, threads=threads)
        self.e5 = "e5" in model.lower()
        self.dim = None

    def passages(self, texts):
        import numpy as np
        pre = "passage: " if self.e5 else ""
        v = np.array(list(self.m.embed([pre + t for t in texts], batch_size=32)), dtype=np.float32)
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
        self.dim = v.shape[1]
        return v

    def query(self, text):
        import numpy as np
        v = np.array(list(self.m.embed([("query: " if self.e5 else "") + text])), dtype=np.float32)[0]
        return v / max(float(np.linalg.norm(v)), 1e-9)


_EMB: dict = {}
_EMB_LOCK = threading.Lock()


def get_embedder(vault: Vault):
    """Model podle 99_System/config/pipeline.yaml (vectors: {enabled, model, cache_dir}); None = vypnuto / chybí knihovna."""
    from pipeline import settings
    cfg = settings.load(vault).get("vectors", {})
    if not cfg.get("enabled", True) or os.getenv("KOS_NO_VECTORS") == "1":
        return None
    model = os.getenv("KOS_VECTOR_MODEL") or cfg.get("model") or DEFAULT_MODEL
    if model == "hash-test":
        return HashEmbedder()
    with _EMB_LOCK:
        if model not in _EMB:
            try:
                cache = os.path.expanduser(cfg.get("cache_dir") or "~/.cache/fastembed")
                _EMB[model] = FastEmbedder(model, cache_dir=cache, threads=int(cfg.get("threads") or 2))
            except Exception as e:  # noqa: BLE001 — bez modelu jede hledání jen podle slov
                print(f"vektory: model nejde načíst ({e}) → jen hledání podle slov")
                _EMB[model] = None
        return _EMB[model]


# ------------------------------------------------------------------ úložiště otisků
def store_path(vault: Vault) -> Path:
    return vault.index_dir / "vectors.sqlite"


def _store(vault: Vault) -> sqlite3.Connection:
    vault.index_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(store_path(vault), timeout=30)
    con.execute("CREATE TABLE IF NOT EXISTS vec(h TEXT, model TEXT, v BLOB, PRIMARY KEY(h, model))")
    return con


def ensure(vault: Vault, embedder=None, index_con: sqlite3.Connection | None = None, batch: int = 64,
           max_seconds: float | None = None) -> int:
    """Dopočítá otisky úseků, které v úložišti chybí. Vrací počet nově spočítaných."""
    from pipeline import index
    embedder = embedder or get_embedder(vault)
    if embedder is None or not index.path(vault).exists():
        return 0
    con = index_con or index.connect(vault)
    try:
        rows = con.execute("SELECT h, embed_text FROM chunks").fetchall()
    except sqlite3.OperationalError:
        return 0
    st = _store(vault)
    have = {r[0] for r in st.execute("SELECT h FROM vec WHERE model=?", (embedder.name,))}
    todo = [(h, t) for h, t in {r[0]: r[1] for r in rows}.items() if h not in have]
    done, t0 = 0, time.time()
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        vecs = embedder.passages([t for _, t in part])
        st.executemany("INSERT OR REPLACE INTO vec VALUES (?,?,?)",
                       [(h, embedder.name, v.astype("float32").tobytes()) for (h, _), v in zip(part, vecs)])
        st.commit()
        done += len(part)
        if max_seconds and time.time() - t0 > max_seconds:
            break
    st.close()
    if done:
        print(f"vektory: +{done} úseků (celkem chybělo {len(todo)})")
    return done


# ------------------------------------------------------------------ hledání
class Searcher:
    """Drží matici otisků v paměti (obnoví se, když se změní index nebo úložiště)."""

    def __init__(self, vault: Vault):
        self.vault = vault
        self.key = None
        self.rows: list[tuple] = []
        self.mat = None
        self.lock = threading.Lock()

    def _load(self, con: sqlite3.Connection, embedder) -> None:
        import numpy as np
        from pipeline import index
        sp = store_path(self.vault)
        key = (index.path(self.vault).stat().st_mtime if index.path(self.vault).exists() else 0,
               sp.stat().st_mtime if sp.exists() else 0, embedder.name)
        if key == self.key:
            return
        try:
            rows = con.execute("""SELECT c.h, c.source_id, c.seg_from, c.seg_to, src.published_at
                                  FROM chunks c JOIN sources src ON src.source_id = c.source_id""").fetchall()
        except sqlite3.OperationalError:
            rows = []
        vecs = {}
        if sp.exists():
            st = _store(self.vault)
            for h, v in st.execute("SELECT h, v FROM vec WHERE model=?", (embedder.name,)):
                vecs[h] = v
            st.close()
        keep = [r for r in rows if r[0] in vecs]
        self.rows = keep
        self.mat = (np.frombuffer(b"".join(vecs[r[0]] for r in keep), dtype=np.float32).reshape(len(keep), -1)
                    if keep else None)
        self.key = key

    def search(self, con: sqlite3.Connection, query: str, k: int = 40, since: str | None = None,
               until: str | None = None, source_id: str | None = None) -> list[dict]:
        import numpy as np
        embedder = get_embedder(self.vault)
        if embedder is None or not query.strip():
            return []
        with self.lock:
            self._load(con, embedder)
            if self.mat is None or not len(self.rows):
                return []
            q = embedder.query(query)
            sims = self.mat @ q
        order = np.argsort(-sims)
        out = []
        for idx in order:
            h, sid, a, b, date = self.rows[idx]
            if source_id and sid != source_id:
                continue
            if since and (date or "") < since:
                continue
            if until and (date or "") > until:
                continue
            out.append({"source_id": sid, "seg_from": a, "seg_to": b, "score": float(sims[idx])})
            if len(out) >= k:
                break
        return out


_SEARCHERS: dict = {}


def searcher(vault: Vault) -> Searcher:
    key = str(vault.root)
    if key not in _SEARCHERS:
        _SEARCHERS[key] = Searcher(vault)
    return _SEARCHERS[key]


def stats(vault: Vault, con: sqlite3.Connection | None = None) -> dict:
    from pipeline import index, settings
    cfg = settings.load(vault).get("vectors", {})
    model = os.getenv("KOS_VECTOR_MODEL") or cfg.get("model") or DEFAULT_MODEL
    out = {"enabled": cfg.get("enabled", True), "model": model, "chunks": 0, "embedded": 0}
    try:
        con = con or index.connect(vault)
        hs = {r[0] for r in con.execute("SELECT h FROM chunks")}
        out["chunks"] = len(hs)
        if store_path(vault).exists():
            st = _store(vault)
            have = {r[0] for r in st.execute("SELECT h FROM vec WHERE model=?", (model,))}
            st.close()
            out["embedded"] = len(hs & have)
    except sqlite3.OperationalError:
        pass
    return out


class Background:
    """Vlákno ve webové aplikaci: když se změní index, dopočítá otisky nových úseků (model je načtený jen tady)."""

    def __init__(self, vault: Vault, interval: int = 20):
        self.vault, self.interval = vault, interval
        self.last = None
        self.kick = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="kos-vectors").start()

    def _loop(self):
        from pipeline import index
        time.sleep(5)
        while True:
            try:
                p = index.path(self.vault)
                m = p.stat().st_mtime if p.exists() else None
                if m and m != self.last:
                    ensure(self.vault, max_seconds=600)
                    self.last = m if stats(self.vault)["embedded"] >= stats(self.vault)["chunks"] else None
            except Exception as e:  # noqa: BLE001
                print(f"vektory: {e}")
            self.kick.wait(self.interval)
            self.kick.clear()
