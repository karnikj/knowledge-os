# Knowledge OS — technický návod: architektura a instalace

Pro toho, kdo si chce systém postavit sám: na PC pro zkoušku, nebo na vlastním serveru pro každodenní provoz
z telefonu i počítače. Uživatelská část (co aplikace umí a jak se používá) je v [`UZIVATELSKY_MANUAL.md`](UZIVATELSKY_MANUAL.md).
Nejste technik? Začněte průvodcem [`PRUVODCE.md`](PRUVODCE.md), ten vás provede krok za krokem i s AI asistentem.

## 1. Architektura

### Jeden repozitář = kód + data

Celý systém je jeden Git repozitář, který je zároveň **vault pro Obsidian**. Kód (`pipeline/`) leží vedle dat
(`00_Assets/`, `01_Zdroje/`, `14_Internal/`, `99_System/`). Každá změna, kterou aplikace udělá, je commit; Git je
jediný zdroj pravdy, historie i záloha textu. Odvozené věci (vyhledávací index, vektory, logy) jsou mimo Git a dají se
kdykoli přestavět (`python -m pipeline reindex`, `python -m pipeline vectors`).

| Co | Kde | Formát |
|---|---|---|
| doslovný přepis (strojová pravda, nikdy se needituje) | `00_Assets/transcripts/{id}.json` | JSON: segmenty `i`, `start`, `end`, `speaker`, `text` |
| analýza: tvrzení s citacemi, kapitoly, shrnutí | `00_Assets/transcripts/{id}.analysis.json` | JSON |
| zmínky entit po segmentech | `00_Assets/transcripts/{id}.mentions.json` | JSON |
| originály dokumentů, audio hlasu a schůzek | `00_Assets/documents/`, `00_Assets/voice/`, `00_Assets/meetings/` | původní soubor, `.opus` |
| karty zdrojů (čte Obsidian i člověk) | `01_Zdroje/` (veřejné), `14_Internal/` (soukromé) | Markdown s YAML hlavičkou |
| zápisy ze schůzek (data formuláře) | `99_System/meetings/{id}.json` | JSON |
| registr entit | `99_System/config/entities.yaml` | YAML |
| modely, prahy, značka | `99_System/config/pipeline.yaml` | YAML |
| slovník správných názvů pro přepis | `99_System/Slovník.md` | Markdown |
| šablony zápisů | `99_System/templates/meetings/*.yaml` | YAML |
| stav kroků zpracování každého zdroje | `99_System/state/{id}.json` | JSON |
| fronty YouTube | `99_System/queue.txt`, `queue_failed.txt`, `queue_local.txt` | text |
| index pro hledání (mimo Git) | `99_System/index/kos.sqlite`, `vectors.sqlite` | SQLite FTS5, vektory |

Identita zdroje je `source_id` ve tvaru `namespace:id`: `yt:VIDEOID`, `voice:20260924-101500-ab12`,
`meeting:…`, `file:<16 znaků otisku obsahu>`. Nejmenší citovatelná jednotka je segment přepisu (`^s12`).

### Služby

- **`kos-web`** (`python -m pipeline web`): FastAPI + instalovatelná webová aplikace (PWA). Poslouchá jen na
  `127.0.0.1:8080`; ven ji vystavuje `tailscale serve` jako HTTPS adresu v soukromé síti.
- **`kos-worker`** (`python -m pipeline worker`): smyčka každých 10 s: hlasové poznámky a schůzky (priorita),
  fronta YouTube, inbox dokumentů (`16_Inbox_Dokumenty/`), AI karty dokumentů, týdenní kontrola hlídaných webových
  stránek, automatické uložení zápisů po 14 dnech bez kontroly, dopočet vektorů, přestavba indexu, `git pull --rebase`
  každých 5 minut a `commit + push` po změnách. Když pull přinese nový kód, zapíše `99_System/logs/restart_request`
  a systemd (`kos-restart.path`) doinstaluje závislosti a restartuje obě služby.
- **Runner na PC** (`python -m pipeline runner`, volitelné): zpracovává úlohy zadané do
  `99_System/runner/inbox/*.json` (např. AI asistentem), umí jen vyjmenované příkazy pipeline, synchronizaci Gitu a
  SSH na jeden server podle `99_System/runner/vps.txt`.

### Zpracování zdroje: kroky a stav

```
ingest → stt → (glossary) → align → analyze → ground → render → index
```

Každý krok zapíše stav do `99_System/state/{id}.json` (`status`, `engine`, `prompt_version`, `input_hash`,
`output_hash`). Po výpadku pokračuje zpracování tam, kde skončilo; přepis se dělá po kusech 300 s s překryvem 5 s a
každý kus je uložený v cache (`00_Assets/transcripts/raw/`). Kontrola kvality přepisu porovná pokrytí řeči a (u videí)
počet slov s titulky; useknutý kus se přepíše znovu. Výsledek je `STT_OK`, `STT_WARNING` nebo `STT_INCOMPLETE`
a je vidět v kartě.

**Ověření citací je deterministické** (`pipeline/ground.py`): tvrzení od modelu musí mít doslovnou citaci, která se
najde v citovaném segmentu (`structural`), po normalizaci (`exact`/`canonical`), v okolí ±3 segmenty (`fuzzy`)
nebo v celém přepisu s přísnějším prahem (`weak`); jinak jde do karty jen jako neověřené. Zmínka entity se zapíše
jen tehdy, když je název v segmentu opravdu přítomný.

### Hledání a chat

Hybridní hledání (`pipeline/ask.py`): fulltext SQLite FTS5 s českým stemmingem (vzácná slova mají větší váhu),
hledání podle významu lokálním vícejazyčným modelem (`fastembed`, ONNX, bez GPU, nic neodchází ven) a zmínky
entit z otázky. Výsledky se sloučí, v režimu Full je AI nejdřív pročistí (vyřadí nesouvisející) a model pak odpovídá
**jen z vybraných úryvků** s citacemi `[n]`. Dokument do 120 000 znaků dostane model celý.

### Modely jsou součástka

`99_System/config/pipeline.yaml` určuje poskytovatele a model pro každou cestu zvlášť (`stt.youtube`,
`stt.private`, `analysis.youtube`, `analysis.chat`, `analysis.private`), vždy s náhradními modely. Podporovaní
poskytovatelé: **OpenRouter** (`OPENROUTER_API_KEY`, jeden klíč pro stovky modelů, platí se jen použití) a **Google
Gemini API** (`GEMINI_API_KEY`, bezplatný tier s denními limity). Cena každého volání se počítá ze skutečné odpovědi
a je vidět v aplikaci (Úlohy → spotřeba). Jiného poskytovatele přidáte implementací rozhraní v
`pipeline/providers/base.py` (`STTProvider.transcribe`, `AnalysisProvider.analyze_window`/`summarize`, `generate`).

## 2. Požadavky

- **Python 3.10+** (vyvíjeno na 3.12/3.13), `pip`
- **ffmpeg** v PATH (převod audia, nahrávky z telefonu)
- **Git** a účet na GitHubu (nebo jiný Git hosting)
- klíč **OpenRouter** (doporučeno) nebo **Google AI Studio**
- pro provoz z telefonu: **VPS** s Ubuntu 24.04 (stačí 1 vCPU, 4 GB RAM, 20 GB disk; instalace přidá 2 GB swap)
  a účet **Tailscale** (zdarma pro osobní použití)
- pro PDF zápisů na serveru: knihovny `libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core` (instalační skript je přidá)

## 3. Zkouška na PC (bez serveru)

```bash
git clone https://github.com/<účet>/knowledge-os.git
cd knowledge-os
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # doplňte klíč(e)
python -m pipeline setup                               # průvodce: nástroje, klíč (zadání skryté), složky, ukázková data
python -m pipeline doctor                              # 18 kontrol: nástroje, balíčky, klíč, struktura vaultu
python -m pipeline web                                 # http://127.0.0.1:8080
```

Druhé okno: `python -m pipeline worker` (zpracování fronty, hlasu, dokumentů). Bez workeru aplikace funguje pro
hledání, chat a prohlížení; nové zdroje se zpracují, až worker poběží.

Na Windows: pokud `yt-dlp` hlásí chyby SSL certifikátů, `pip install pip-system-certs`. Nahrávání hlasu v prohlížeči
vyžaduje HTTPS nebo `localhost`; na PC tedy funguje, v telefonu až přes Tailscale (bod 4).

Vlastní repozitář pro svoje data založte jako **soukromý**: po naklonování `git remote remove origin`, na GitHubu
vytvořte prázdný soukromý repozitář a `git remote add origin … && git push -u origin main`. Veřejná kopie je jen
výchozí bod; vaše přepisy a zápisy do ní nepatří.

## 4. Provoz na VPS (telefon + PC, sdílení v týmu)

1. **Repozitář**: mějte vlastní soukromý repozitář (bod 3) s případnými úpravami `pipeline.yaml`.
2. **Server**: Ubuntu 24.04, přihlášení jako root. Zkopírujte `deploy/install_vps.sh` na server (nebo naklonujte
   repozitář do `/root`) a připravte `/root/.env` s klíči (stejný obsah jako `.env.example`).
3. **Instalace** (idempotentní, opakované spuštění = aktualizace):
   ```bash
   sudo bash deploy/install_vps.sh <github-účet>/<název-repozitáře>
   ```
   Skript: nainstaluje balíčky (python3, ffmpeg, git, deno pro yt-dlp, pango), založí uživatele `kos`, vygeneruje SSH
   deploy klíč a vypíše ho: přidejte ho v GitHubu do repozitáře (*Settings → Deploy keys*, s právem zápisu) a potvrďte
   Enter. Pak naklonuje vault do `/home/kos/vault`, vytvoří venv, `.env`, index a služby systemd `kos-web`,
   `kos-worker`, `kos-restart.path`, `kos-ytdlp.timer` (týdenní aktualizace yt-dlp).
4. **Tailscale**: skript nainstaluje Tailscale a vypíše přihlašovací odkaz; přihlaste se stejným účtem jako na
   telefonu a PC. V administraci Tailscale (DNS) musí být zapnuté *MagicDNS* a *HTTPS Certificates*. Skript pak
   spustí `tailscale serve --bg 8080` a vypíše adresu `https://kos.<vaše-tailnet>.ts.net`.
5. **Telefon**: nainstalujte Tailscale, přihlaste se, otevřete adresu v Chrome a *Přidat na plochu*. Aplikace se pak
   chová jako nativní: ikona, běh na pozadí při nahrávání, sdílení videa z YouTube nebo souboru přímo do ní.
6. **PC**: tatáž adresa v prohlížeči. Pro Obsidian naklonujte repozitář a otevřete složku jako vault; karty, přepisy
   a zápisy se objeví po `git pull` (worker na serveru pushuje po každé změně).

Kontrola stavu: `bash deploy/vps_health.sh` (služby, disk, paměť, web, git, Tailscale, chyby workeru).
Logy: `journalctl -u kos-worker -f`, `journalctl -u kos-web -f`.

### Když YouTube blokuje stahování ze serveru

Datová centra YouTube někdy blokuje. Worker takové video přesune do `99_System/queue_local.txt` a na PC ho zpracujete
příkazem `python -m pipeline worker --local --once` (stáhne audio z domácí sítě, přepíše a pushne výsledek).

### Aktualizace kódu

Commit do repozitáře → worker na serveru ho do 5 minut stáhne → při změně v `pipeline/`, `requirements.txt` nebo
`deploy/` zapíše žádost o restart → `kos-restart.service` doinstaluje závislosti a restartuje služby. Před commitem
vždy `python -m pytest -q` (106 testů, bez sítě) a `python -m pipeline doctor`.

## 5. Konfigurace

- **Modely** — `99_System/config/pipeline.yaml` (viz komentáře v souboru; všechny klíče a výchozí hodnoty jsou
  v `pipeline/settings.py`). Změna modelu nevyžaduje nic dalšího než restart služeb.
- **Značka v zápisech** — blok `branding` tamtéž (jméno, podtitul, logo). Název aplikace v záhlaví a v PWA:
  `pipeline/web/static/index.html` (`<title>`, `.brand`) a `manifest.webmanifest`; ikona `static/icon.svg`.
- **Šablony zápisů** — `99_System/templates/meetings/*.yaml`; typy polí `short`, `text`, `datetime`, `people`,
  `list`, `topics`, `tasks`; u každého pole pokyn pro AI. Upravují se i v aplikaci (Schůzky → Šablony).
- **Slovník** — `99_System/Slovník.md`: názvy, které má přepis psát přesně, a jak je přepis slyší špatně.
- **Prompt přepisu** — `99_System/prompts/transcription_prompt.txt`.
- **Proměnné prostředí**: `KOS_ROOT` (kořen vaultu, jinak nadřazená složka `pipeline/`), `KOS_NO_VECTORS=1`
  (vypne hledání podle významu), `KOS_NO_GIT=1` (worker bez Gitu), `KOS_USD_CZK` (kurz pro přehled spotřeby),
  `KOS_VECTOR_MODEL`, `KOS_YTDLP_VERIFY_SSL=1`.
- **Runner** (volitelné): `99_System/runner/repo.txt` (`účet/repozitář`) a `vps.txt` (`root@IP`); klíč
  `~/.ssh/kos_vps` si vytvoří sám a jeho veřejnou část uloží do `99_System/runner/kos_vps.pub`.

## 6. Příkazová řádka

```
python -m pipeline setup [--yes] [--demo]          průvodce první instalací
python -m pipeline doctor [--online] [--probe]     kontrola prostředí; --online ověří i API
python -m pipeline add URL                         přidat video do fronty
python -m pipeline process ID…                     přepis → analýza → karta hned (bez workeru)
python -m pipeline transcribe | analyze | render   jednotlivé kroky
python -m pipeline doc-add CESTA|URL [--public]    přidat dokument nebo webovou stránku
python -m pipeline search "text" [--since] [--speaker] [--json]
python -m pipeline mentions "Entita" [--attitude negative] [--children] [--json]
python -m pipeline entities [--type osoba|organizace|software|…]
python -m pipeline catalog | state ID | queue
python -m pipeline remove ID [--yes]               odstranit zdroj (bez --yes jen vypíše, co by smazal)
python -m pipeline reindex | vectors [--rebuild]   přestavět index / otisky významu
python -m pipeline worker [--once] [--local]       zpracování na pozadí
python -m pipeline web [--host] [--port]           webová aplikace
```

## 7. Testy a kvalita

`python -m pytest -q` spustí 106 testů: jednotkové, integrační s napodobeninami modelů (nic nevolá síť ani neplatí)
a zlatý test karty (`tests/golden/`). `python -m pipeline doctor` kontroluje nástroje, balíčky, klíč, strukturu
vaultu, unikátnost `source_id`, dostupnost audia a to, že v Gitu nejsou tajemství. Pilotní sadu videí pro měření kvality
přepisu doplníte v `pipeline/pilot.py` a spustíte `python -m pipeline pilot`.

## 8. Bezpečnost a data

- Žádný veřejný port: web jen na `127.0.0.1`, přístup pouze přes Tailscale (zařízení přihlášená vaším účtem).
  Odchod člověka z týmu = odebrání jeho zařízení v Tailscale.
- Klíče jen v `.env` na serveru; `.env`, cookies a `14_Internal/Confidential/` jsou v `.gitignore`.
- Co odchází ven: audio k přepisu a text k analýze jdou poskytovateli modelu podle `pipeline.yaml`. Pro citlivá data
  nasměrujte cestu `private` na poskytovatele s přísnějšími podmínkami, nebo na vlastní model. Hledání, vektory a
  rejstřík entit běží lokálně.
- Všichni, kdo mají přístup, vidí vše. Role a oprávnění systém nemá; pro oddělené týmy nasaďte oddělené instance
  (každý tým vlastní repozitář a server, nebo více uživatelů `kos` na jednom serveru s jinými porty).
- Záloha: text je v Gitu (GitHub). Audio originálů videí (`00_Assets/audio/`) je mimo Git a jde znovu stáhnout; audio
  hlasových poznámek a schůzek (`.opus`) v Gitu je. Před většími změnami si udělejte značku (`git tag`).
- Mazání v aplikaci vyžaduje dvě potvrzení; worker nikdy nedělá `push --force`.

## 9. Co tu není a kam dál

Projekt vznikl pro jednoho člověka a malý tým metodou vibecodingu: návrh a kód s AI asistentem, s důrazem na
jednoduchost, otevřené formáty a ověřitelnost. Vědomě chybí: uživatelské účty a role, vícejazyčné rozhraní
(aplikace je česky, přepis zvládá i angličtinu), škálování na tisíce uživatelů. Vhodná rozšíření: sada testovacích
otázek pro měření přesnosti hledání, odesílání PDF zápisů e-mailem po schválení, další poskytovatelé modelů.
