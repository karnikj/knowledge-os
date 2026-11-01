# Knowledge OS — pokyny pro AI asistenta (Claude Code apod.)

Tento adresář je znalostní báze (Obsidian vault + Git) a zároveň kód aplikace. Obsahuje přepisy a analýzy videí,
dokumenty, zápisy ze schůzek a hlasové poznámky. Architektura a návod: `README.md`, `docs/INSTALACE.md`,
principy návrhu: `PROJECT_PRINCIPLES.md`.

## Režim instalace (uživatel chce systém zprovoznit)

Když uživatel žádá instalaci, spuštění nebo nasazení, veď ho podle `docs/PRUVODCE.md` (cesta A na PC, pak cesta B
na serveru). Pravidla:

1. **Po jednom kroku.** Řekni, co uděláš, udělej to, ověř výsledek (`python -m pipeline doctor`, `setup`,
   `vps_health.sh`) a teprve pak pokračuj. Nepřeskakuj kroky, i když se zdají samozřejmé.
2. **Klíče a hesla nikdy do chatu.** Klíč OpenRouter/Gemini, heslo k serveru ani token GitHubu si nenech poslat.
   Řekni, kam je uživatel vloží sám (`python -m pipeline setup`, soubor `.env`, `nano /root/.env`). Obsah `.env`
   nikdy nevypisuj.
3. **Co může udělat jen člověk, popiš přesně:** založení účtů (OpenRouter, GitHub, Tailscale, VPS), platby, kliknutí
   v nastavení repozitáře (deploy klíč), potvrzení přihlášení Tailscale, instalace do telefonu. U každého řekni,
   kde to najde a jak pozná, že je hotovo.
4. **Vlastní data jen do soukromého repozitáře.** Před prvním přidáním skutečného obsahu zkontroluj
   `git remote -v`; pokud ukazuje na veřejnou předlohu, nejdřív krok B1 průvodce.
5. **Na serveru nic nespouštěj bez souhlasu** (instalace, restart služeb, mazání). Instalační skript je idempotentní,
   při zastavení (`DEPLOY_KEY_NEEDED`, `TAILSCALE_LOGIN`) vysvětli, co má člověk udělat, a spusť ho znovu.
6. **Chyby čti celé** a hledej je v tabulce *Když něco nejde* v průvodci dřív, než začneš měnit kód.
7. **Na konci ověř výsledek:** u cesty A podmínky *Hotovo, když* v A4 (stav přes `curl …/api/status`, hledání
   „faktury“), u cesty B kontrolní seznam B6. Ukázková data nabídni odstranit (`python demo/remove.py`).
8. **Bez klíče jde pokračovat.** Když člověk klíč zatím nemá, dokonči cestu A bez něj a řekni, co bude fungovat až s klíčem.

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
