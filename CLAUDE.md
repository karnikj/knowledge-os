# Knowledge OS — pokyny pro AI asistenta (Claude Code apod.)

Tento adresář je znalostní báze (Obsidian vault + Git) a zároveň kód aplikace. Obsahuje přepisy a analýzy videí,
dokumenty, zápisy ze schůzek a hlasové poznámky. Architektura a návod: `README.md`, `docs/INSTALACE.md`,
principy návrhu: `PROJECT_PRINCIPLES.md`.

## Když se uživatel ptá na obsah báze

1. **Odpovídej výhradně z obsahu tohoto vaultu.** Nepřidávej obecné znalosti. Když se odpověď v bázi nenachází, řekni to.
2. **Každé tvrzení cituj:** název zdroje, datum, mluvčí a čas jako odkaz na video (`https://youtu.be/ID?t=SEKUNDY`)
   nebo odkaz na kartu s blokem `[[karta#^sN]]`. U dokumentů stranu.
3. **Na zmínky a výčty nehledej sám grepem**, použij deterministické příkazy, vrací úplné výsledky:
   - `python -m pipeline mentions "Název" [--since 2026-06-01] [--until …] [--speaker "Jméno"] [--attitude negative] [--json]`
   - `python -m pipeline search "text" [--since …] [--speaker …] [--json]` (fulltext, tolerantní ke skloňování)
   - `python -m pipeline entities [--type osoba|organizace|software|ai_model|hardware|sluzba|projekt|publikace|misto|udalost|tema]`
   - `python -m pipeline catalog` (seznam zdrojů s datem)
4. Tvrzení v kartách mají `ověření:` — `structural/exact/canonical/fuzzy` = doložené v přepisu; sekci **Neověřená tvrzení** necituj jako fakt.
5. Časy s `~` v přepisu jsou dopočtené (přesnost ± desítky sekund); ostatní jsou zarovnané na titulky.

## Kde co je
- `01_Zdroje/` karty veřejných zdrojů (frontmatter: `source_id`, `published_at`, `mluvci`, `osoby`, `organizace`, `nastroje`, `temata`…)
- `14_Internal/` soukromé: hlasové poznámky (`VoiceNotes/`), zápisy (`Schuzky/`), soukromé dokumenty (`Dokumenty/`); `Confidential/` necituj mimo tento počítač
- `00_Assets/transcripts/{id}.json` doslovný přepis (segmenty `i`, `start`, `speaker`, `text`) — strojová pravda;
  `{id}.analysis.json` tvrzení s citacemi, `{id}.mentions.json` zmínky entit po segmentech
- `99_System/config/entities.yaml` registr entit, `99_System/config/pipeline.yaml` modely a prahy, `99_System/Slovník.md` správné názvy pro přepis

## Pravidla pro úpravy (vývoj)
- **Analýza nikdy nemění přepis.** Needituj `00_Assets/` ani generované bloky karet (`<!-- gen:start -->…<!-- gen:end -->`) ručně.
- Poznámky uživatele jsou v kartě za `<!-- gen:end -->` a musí přežít přegenerování.
- Před commitem: `python -m pytest -q` a `python -m pipeline doctor`.
- Kód: balíček `pipeline/`, spouštění `python -m pipeline <příkaz>`; žádné tajemství (`.env`, cookies) do Gitu.
