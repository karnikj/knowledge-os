"""Převod dokumentů na strukturovaný text: odstavce s kapitolou (nadpisem) a číslem strany.

Podporováno: Markdown (.md), prostý text (.txt), Word (.docx), PDF s textovou vrstvou (.pdf)
a webové stránky (HTML). Skeny, šifrovaná PDF a ostatní formáty se odmítnou s čitelnou chybou.

Výstup `parse_*` = seznam bloků {"text", "section", "page"}; `chunk()` z nich udělá úseky
(~1000 znaků, nikdy přes hranici kapitoly), které se ukládají jako „segmenty“ zdroje.
"""
from __future__ import annotations

import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

SUPPORTED = {".md": "md", ".markdown": "md", ".txt": "txt", ".docx": "docx", ".pdf": "pdf"}
MAX_BYTES = 40 * 1024 * 1024
CHUNK_CHARS = 1100
CHUNK_MAX = 1700


class Unsupported(ValueError):
    """Soubor nejde zpracovat (formát, sken, šifrování, prázdný) — zpráva je pro uživatele."""


# ------------------------------------------------------------------ společné
def _clean(s: str) -> str:
    s = s.replace("­", "").replace("​", "").replace("﻿", "")
    s = re.sub(r"[ \t ]+", " ", s)
    return s.strip()


def _block(text: str, section: str, page: int | None) -> dict:
    return {"text": _clean(text), "section": _clean(section)[:160], "page": page}


def kind_of(filename: str) -> str:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in SUPPORTED:
        raise Unsupported(f"Formát „{ext or 'bez přípony'}“ není podporovaný. Podporované jsou MD, TXT, DOCX a PDF.")
    return SUPPORTED[ext]


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


# ------------------------------------------------------------------ Markdown / text
def parse_markdown(data: bytes) -> tuple[list[dict], str | None]:
    text = _decode(data)
    text = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)       # frontmatter pryč
    blocks, path, title, buf, in_code = [], [], None, [], False

    def flush():
        if buf:
            t = "\n".join(buf).strip()
            if t:
                blocks.append(_block(t, " › ".join(path), None))
            buf.clear()

    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            buf.append(line)
            if not in_code:
                flush()
            continue
        if in_code:
            buf.append(line)
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            flush()
            level, head = len(m.group(1)), re.sub(r"[*_`]", "", m.group(2)).strip()
            if level == 1 and title is None:
                title = head
            path[:] = path[:level - 1] + [head]
            continue
        if not line.strip():
            flush()
            continue
        buf.append(line.rstrip())
    flush()
    return blocks, title


def parse_text(data: bytes) -> tuple[list[dict], str | None]:
    text = _decode(data)
    blocks = [_block(p, "", None) for p in re.split(r"\n\s*\n", text) if p.strip()]
    first = next((b["text"].splitlines()[0] for b in blocks if b["text"]), None)
    return blocks, (first[:120] if first and len(first) <= 120 else None)


# ------------------------------------------------------------------ Word
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEAD = re.compile(r"^(heading|nadpis|berschrift|titre|titulo)\s*(\d)$", re.I)


def parse_docx(data: bytes) -> tuple[list[dict], str | None]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise Unsupported("Soubor DOCX je poškozený nebo to není dokument Wordu.")
    styles = {}
    try:
        for s in ET.fromstring(z.read("word/styles.xml")).iter(W + "style"):
            sid, name = s.get(W + "styleId"), s.find(W + "name")
            if sid and name is not None:
                styles[sid] = name.get(W + "val") or sid
    except (KeyError, ET.ParseError):
        pass
    body = root.find(W + "body")
    blocks, path, title, buf = [], [], None, []

    def flush():
        if buf:
            blocks.append(_block("\n".join(buf), " › ".join(path), None))
            buf.clear()

    def ptext(p) -> str:
        return "".join(t.text or "" for t in p.iter() if t.tag in (W + "t", W + "tab") and (t.text or t.tag == W + "tab"))

    for el in list(body) if body is not None else []:
        if el.tag == W + "p":
            t = ptext(el).strip()
            ps = el.find(f"{W}pPr/{W}pStyle")
            style = styles.get(ps.get(W + "val"), ps.get(W + "val")) if ps is not None else ""
            m = _HEAD.match((style or "").replace(" ", ""))
            if style and style.lower() == "title" and t:
                title = title or t
                continue
            if m and t:
                flush()
                level = int(m.group(2))
                if title is None and level == 1:
                    title = t
                path[:] = path[:level - 1] + [t]
                continue
            if not t:
                flush()
                continue
            buf.append(t)
            if sum(len(x) for x in buf) > CHUNK_CHARS:
                flush()
        elif el.tag == W + "tbl":
            flush()
            rows = []
            for tr in el.iter(W + "tr"):
                cells = [" ".join(ptext(p) for p in tc.iter(W + "p")).strip() for tc in tr.iter(W + "tc")]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append(_block("\n".join(rows), " › ".join(path), None))
    flush()
    return blocks, title


# ------------------------------------------------------------------ PDF
def parse_pdf(data: bytes) -> tuple[list[dict], str | None, int]:
    try:
        import pymupdf
    except ImportError:                                   # starší instalace
        import fitz as pymupdf  # type: ignore
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # noqa: BLE001
        raise Unsupported("Soubor PDF je poškozený nebo ho nejde otevřít.")
    if doc.needs_pass or doc.is_encrypted:
        raise Unsupported("PDF je chráněné heslem — nejde přečíst.")
    pages = doc.page_count
    if not pages:
        raise Unsupported("PDF nemá žádné stránky.")
    # 1) řádky se stylem písma
    from collections import Counter
    lines_by_page: list[list[tuple[str, float, bool, int]]] = []
    sizes: Counter = Counter()                      # velikost písma → počet znaků (nejčastější = běžný text)
    chars = 0
    for page in doc:
        d = page.get_text("dict", flags=0)
        plines = []
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                spans = [s for s in ln.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                t = "".join(s["text"] for s in ln["spans"]).strip()
                size = max(s["size"] for s in spans)
                bold = all((s.get("flags", 0) & 16) or "Bold" in s.get("font", "") for s in spans)
                plines.append((t, size, bold, b.get("number", 0)))
                for s in spans:
                    sizes[round(s["size"], 1)] += len(s["text"])
                chars += len(t)
        lines_by_page.append(plines)
    if chars / pages < 40:
        raise Unsupported("PDF nemá textovou vrstvu (vypadá jako sken). Skeny zatím nepodporujeme.")
    body_size = sizes.most_common(1)[0][0] if sizes else 10
    # 2) opakující se záhlaví / zápatí (stejný text na > 40 % stran) pryč
    norm = lambda s: re.sub(r"\d+", "#", s.lower()).strip()           # noqa: E731
    cnt = Counter(k for pl in lines_by_page for k in {norm(x[0]) for x in pl})
    repeated = {k for k, v in cnt.items() if pages >= 4 and v > 0.4 * pages and len(k) < 120}
    # 3) bloky
    meta_title = (doc.metadata or {}).get("title") or ""
    blocks, path, title, buf, cur_block, cur_page = [], [], None, [], None, 1

    def flush():
        if buf:
            txt = " ".join(buf)
            txt = re.sub(r"(\w)- (\w)", r"\1\2", txt)                   # dělení slov na konci řádku
            blocks.append(_block(txt, " › ".join(path), cur_page))
            buf.clear()

    for pno, plines in enumerate(lines_by_page, start=1):
        for t, size, bold, bno in plines:
            if norm(t) in repeated or re.fullmatch(r"(strana|page|str\.?)?\s*\d+\s*(/\s*\d+)?", t.lower()):
                continue
            is_head = len(t) <= 120 and not t.endswith((".", ",", ";")) and (
                size >= body_size * 1.25 or (bold and size >= body_size * 1.05 and len(t) <= 80))
            if is_head:
                flush()
                level = 1 if size >= body_size * 1.6 else 2 if size >= body_size * 1.25 else 3
                if title is None and pno <= 2 and level == 1:
                    title = t
                path[:] = path[:level - 1] + [t]
                cur_block = None
                continue
            if bno != cur_block or pno != cur_page:
                if buf and (pno != cur_page and not buf[-1].endswith((".", ":", "!", "?"))):
                    pass                                           # věta pokračuje na další straně
                else:
                    flush()
                cur_block = bno
                if not buf:
                    cur_page = pno
            buf.append(t)
            if sum(len(x) for x in buf) > CHUNK_CHARS:
                flush()
    flush()
    good_meta = meta_title and len(meta_title) > 3 and not re.search(r"\.(docx?|pdf|pptx?)$|^untitled|^microsoft word", meta_title, re.I)
    return blocks, (meta_title.strip() if good_meta else title), pages


# ------------------------------------------------------------------ HTML (webové stránky)
class _Html(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "form", "button", "iframe"}
    BLOCK = {"p", "li", "pre", "td", "th", "blockquote", "dd", "dt", "figcaption", "caption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []            # (tag, text)
        self.skip = 0
        self.stack: list[str] = []
        self.buf: list[str] = []
        self.title = ""
        self.in_title = False
        self.main_depth = None
        self.main_items: list[tuple[str, str]] | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP or a.get("hidden") is not None or a.get("aria-hidden") == "true":
            if tag not in ("br",):
                self.skip += 1
                self.stack.append("!" + tag)
                return
        if tag == "title":
            self.in_title = True
        if tag in ("main", "article") or a.get("role") == "main":
            if self.main_items is None:
                self.main_items = []
                self.main_depth = len(self.stack)
        if tag in self.BLOCK or re.fullmatch(r"h[1-6]", tag):
            self._end()
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            return
        if self.stack[-1] == "!" + tag:
            self.stack.pop()
            self.skip -= 1
            return
        if tag == "title":
            self.in_title = False
        if tag in self.BLOCK or re.fullmatch(r"h[1-6]", tag):
            self._end(tag)
        while self.stack and self.stack[-1] != tag and not self.stack[-1].startswith("!"):
            self.stack.pop()
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        if self.main_depth is not None and len(self.stack) <= self.main_depth:
            self.main_depth = -1                             # main skončil (další sbíráme jen do items)

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.skip:
            return
        self.buf.append(data)

    def _end(self, tag: str | None = None):
        t = _clean(" ".join(self.buf))
        self.buf = []
        if not t:
            return
        tag = tag or (self.stack[-1] if self.stack else "p")
        item = (tag if re.fullmatch(r"h[1-6]", tag or "") else "p", t)
        self.items.append(item)
        if self.main_items is not None and self.main_depth not in (None, -1):
            self.main_items.append(item)


def parse_html(data: bytes) -> tuple[list[dict], str | None]:
    p = _Html()
    p.feed(_decode(data))
    p._end()
    items = p.main_items if p.main_items and sum(len(t) for _, t in p.main_items) > 400 else p.items
    blocks, path, title, buf = [], [], None, []

    def flush():
        if buf:
            blocks.append(_block("\n".join(buf), " › ".join(path), None))
            buf.clear()

    for tag, t in items:
        if tag.startswith("h"):
            flush()
            level = int(tag[1])
            if level == 1 and title is None:
                title = t
            path[:] = path[:level - 1] + [t]
            continue
        buf.append(t)
        if sum(len(x) for x in buf) > CHUNK_CHARS:
            flush()
    flush()
    if sum(len(b["text"]) for b in blocks) < 300:
        raise Unsupported("Ze stránky se nepodařilo získat text (obsah se nejspíš vytváří až v prohlížeči). "
                          "Zkuste stáhnout PDF a nahrát ho jako soubor.")
    page_title = _clean(p.title).split(" | ")[0].split(" - Microsoft Learn")[0]
    return blocks, title or page_title or None


# ------------------------------------------------------------------ úseky
def _split_long(text: str) -> list[str]:
    if len(text) <= CHUNK_MAX:
        return [text]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?:;])\s+|\n", text):
        if cur and len(cur) + len(sent) > CHUNK_CHARS:
            parts.append(cur.strip()); cur = ""
        while len(sent) > CHUNK_MAX:                     # extrémně dlouhá „věta“ (tabulka, kód)
            parts.append(sent[:CHUNK_CHARS]); sent = sent[CHUNK_CHARS:]
        cur += " " + sent
    if cur.strip():
        parts.append(cur.strip())
    return parts


def chunk(blocks: list[dict]) -> list[dict]:
    """Bloky → úseky ~1000 znaků. Úsek nikdy nepřekročí hranici kapitoly; stranu nese první blok."""
    out: list[dict] = []
    for b in blocks:
        if not b["text"]:
            continue
        for piece in _split_long(b["text"]):
            last = out[-1] if out else None
            if last and last["section"] == b["section"] and len(last["text"]) + len(piece) < CHUNK_CHARS:
                last["text"] += "\n\n" + piece
                last["page_end"] = b["page"] or last["page_end"]
            else:
                out.append({"text": piece, "section": b["section"], "page": b["page"], "page_end": b["page"]})
    return out


def language(text: str) -> str:
    sample = text[:20000].lower()
    cz = len(re.findall(r"[ěščřžýáíéůú]", sample))
    return "cs" if cz > len(sample) / 300 else "en"


def parse(filename: str, data: bytes) -> dict:
    """Hlavní vstup: vrací {kind, title, pages, blocks, chunks, language, chars} nebo vyhodí Unsupported."""
    if len(data) > MAX_BYTES:
        raise Unsupported(f"Soubor je příliš velký ({len(data) // (1024 * 1024)} MB, limit {MAX_BYTES // (1024 * 1024)} MB).")
    kind = "html" if filename.lower().endswith((".html", ".htm")) else kind_of(filename)
    pages = 0
    if kind == "md":
        blocks, title = parse_markdown(data)
    elif kind == "txt":
        blocks, title = parse_text(data)
    elif kind == "docx":
        blocks, title = parse_docx(data)
    elif kind == "pdf":
        blocks, title, pages = parse_pdf(data)
    else:
        blocks, title = parse_html(data)
    chunks = chunk(blocks)
    chars = sum(len(c["text"]) for c in chunks)
    if chars < 20:
        raise Unsupported("Dokument neobsahuje žádný text.")
    return {"kind": kind, "title": (title or "").strip()[:200] or None, "pages": pages, "chunks": chunks,
            "language": language(" ".join(c["text"] for c in chunks[:40])), "chars": chars}
