"""Registr entit: 99_System/config/entities.yaml (PLAN §12.1).

Formát (čitelný a editovatelný ručně i z webové aplikace):

    pandas:
      name: pandas
      type: software
      aliases: [Pandas, pandas knihovna]
      parent: null
      status: potvrzeno      # navrženo | potvrzeno | zamítnuto
      first_seen: yt:dQw4w9WgXcQ

Nové entity z analýzy se přidají jako `navrženo`. `zamítnuto` = není to entita (šum): zůstává v registru,
aby ji analýza příště znovu nenavrhla, ale její zmínky se neindexují a v seznamu je skrytá. Rozlišení názvu → ID jde přes
normalizovaný název a aliasy (bez diakritiky, malá písmena, stemming slov).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from pipeline.config import Vault
from pipeline.czech import stems
from pipeline.textnorm import norm_word

TYPES = {
    "osoba": "Osoba",
    "organizace": "Organizace",
    "software": "Software",
    "ai_model": "AI model",
    "hardware": "Hardware",
    "sluzba": "Služba / platforma",
    "projekt": "Projekt",
    "publikace": "Kniha / publikace",
    "misto": "Místo",
    "udalost": "Událost / konference",
    "tema": "Téma",
}


REJECTED = "zamítnuto"


def slug(name: str) -> str:
    s = "-".join(norm_word(w) for w in name.split() if norm_word(w))
    return re.sub(r"-+", "-", s).strip("-") or "entita"


def _key(name: str) -> str:
    return " ".join(stems(name))


class Registry:
    def __init__(self, path: Path, data: dict | None = None):
        self.path = path
        self.data: dict[str, dict] = data or {}
        self._index: dict[tuple[str, str | None], str] = {}
        self._rebuild()

    @classmethod
    def load(cls, vault: Vault) -> "Registry":
        p = vault.config_dir / "entities.yaml"
        data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
        return cls(p, data or {})

    def _rebuild(self) -> None:
        self._index.clear()
        for eid, e in self.data.items():
            for n in [e.get("name", eid), *(e.get("aliases") or [])]:
                k = _key(str(n))
                self._index.setdefault((k, e.get("type")), eid)
                self._index.setdefault((k, None), eid)

    def resolve(self, name: str, etype: str | None = None) -> str | None:
        k = _key(name)
        if etype:
            hit = self._index.get((k, etype))
            if hit:
                return hit
        return self._index.get((k, None)) if not etype else None

    def get_or_propose(self, name: str, etype: str, source_id: str, surface: str | None = None) -> str:
        if etype not in TYPES:
            etype = "tema"
        if etype == "tema":
            # téma pojmenované stejně jako konkrétní entita (ChatGPT, Anthropic…) = ta entita
            other = self.resolve(name)
            if other and self.data[other]["type"] != "tema":
                return other
            surface = None           # u témat povrchový tvar není alias (bývá to obecné slovo)
        eid = self.resolve(name, etype)
        if eid is None and etype != "tema":
            # stejný název už existuje pod jiným (ne-tématickým) typem → tatáž entita;
            # model typ mezi videi střídá (Claude Code = model/software). Skutečná homonyma
            # (Astra firma vs. model) se rozdělí ručně ve správě entit.
            other = self.resolve(name)
            if other and self.data[other]["type"] != "tema":
                eid = other
                seen = self.data[other].setdefault("types_seen", [])
                if etype not in seen and etype != self.data[other]["type"]:
                    seen.append(etype)
        if eid is None:
            eid = slug(name)
            base, n = eid, 2
            while eid in self.data and self.data[eid].get("type") != etype:
                eid = f"{base}-{etype}" if n == 2 else f"{base}-{n}"
                n += 1
            if eid not in self.data:
                self.data[eid] = {"name": name.strip(), "type": etype, "aliases": [], "parent": None,
                                  "status": "navrženo", "first_seen": source_id}
        e = self.data[eid]
        if surface and _key(surface) != _key(e["name"]) and surface not in e["aliases"] \
                and len(e["aliases"]) < 40 and _key(surface) not in {_key(a) for a in e["aliases"]}:
            # jiný (nesklonný) tvar zapisujeme jako alias jen pokud se liší i po stemmingu
            e["aliases"].append(surface)
        self._rebuild()
        return eid

    def rejected(self, eid: str | None) -> bool:
        return bool(eid) and (self.data.get(eid) or {}).get("status") == REJECTED

    def merge(self, vault: Vault, eid: str, into: str) -> None:
        """Sloučí entitu `eid` do `into`: názvy → aliasy, zmínky ve všech souborech se přepíšou."""
        import json
        if eid not in self.data or into not in self.data or eid == into:
            raise ValueError("Neplatné sloučení")
        src, dst = self.data.pop(eid), self.data[into]
        dst.setdefault("aliases", [])
        for a in [src["name"], *(src.get("aliases") or [])]:
            if a not in dst["aliases"] and a != dst["name"]:
                dst["aliases"].append(a)
        for f in vault.transcripts.glob("*.mentions.json"):
            d = json.loads(f.read_text(encoding="utf-8"))
            changed = False
            for m in d.get("mentions", []):
                if m["entity"] == eid:
                    m["entity"], changed = into, True
            if changed:
                f.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for e in self.data.values():
            if e.get("parent") == eid:
                e["parent"] = into
        self._rebuild()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = dict(sorted(self.data.items(), key=lambda kv: (kv[1].get("type", ""), kv[0])))
        header = ("# Registr entit Knowledge OS — kanonický název, typ, aliasy, nadřazená entita.\n"
                  "# status: navrženo (přidala analýza) | potvrzeno (zkontrolováno) | zamítnuto (šum, skryté). Typy: "
                  + ", ".join(TYPES) + "\n")
        self.path.write_text(header + yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False, width=120),
                             encoding="utf-8")

    def descendants(self, eid: str) -> set[str]:
        out, stack = {eid}, [eid]
        while stack:
            cur = stack.pop()
            for k, e in self.data.items():
                if e.get("parent") == cur and k not in out:
                    out.add(k)
                    stack.append(k)
        return out


# Obecná slova, která nejsou pojmenovanou entitou (model je občas vrací jako osobu/firmu/model)
GENERIC = {"firma", "firmy", "urad", "mesto", "ministerstvo", "radnice", "matka", "otec", "programator",
           "vyvojari", "vyvojar", "tajemnik", "cech", "model", "modely", "chatbot", "ai chatbot", "ai asistent",
           "jazykove modely", "databaze", "workspace", "pamet", "ai nastroje", "hlasovy asistent",
           "emailovy agent", "e mailovy agent", "emailovy asistent", "e mailovy asistent", "socialni site",
           "it oddeleni", "pravni oddeleni", "financni vybor", "rada mesta", "politicke vedeni",
           "dopravni skupina", "mestsky urad", "druhy mozek", "voicebot", "agent", "agenti"}
PROPER_REQUIRED = {"osoba", "organizace", "misto", "udalost", "publikace"}


def is_named(name: str, etype: str) -> bool:
    """Je to pojmenovaná entita (vlastní jméno / produkt), ne obecné slovo?"""
    key = " ".join(norm_word(w) for w in name.replace("-", " ").split())
    if etype == "tema":
        return True
    if key in GENERIC:
        return False
    if etype in PROPER_REQUIRED and not any(c.isupper() or c.isdigit() for c in name):
        return False
    return True
