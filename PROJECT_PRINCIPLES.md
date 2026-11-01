# PROJECT_PRINCIPLES.md — Knowledge OS

Projektová ústava: proč systém vypadá, jak vypadá, a která rozhodnutí se nemění. Konkrétní stav kódu popisuje
`docs/INSTALACE.md`, práci s aplikací `docs/UZIVATELSKY_MANUAL.md`. Tento dokument odpovídá stavu k listopadu 2026;
starší verze mluvila o plánech (Qdrant, GraphRAG, SQLite jako databáze historie), které byly nahrazeny jednodušším řešením.

## Vize

Knowledge OS je sdílená znalostní báze pro jednotlivce a malé týmy. Cílem není ukládat informace, ale:
- mít znalosti z porad, poznámek, dokumentů a videí na jednom místě a dohledatelné během sekund,
- dostávat odpovědi **jen z vlastních podkladů**, s odkazem na přesné místo ve zdroji,
- propojovat lidi, firmy, nástroje a témata napříč zdroji,
- vydržet 5–10 let bez závislosti na konkrétním modelu, databázi nebo dodavateli.

## Technologický stav (skutečnost, ne plán)

| Vrstva | Řešení | Poznámka |
|---|---|---|
| Znalosti | Obsidian vault = Git repozitář; Markdown s YAML hlavičkou, JSON pro přepisy a analýzy, YAML pro entity a konfiguraci | jediný zdroj pravdy je Git |
| AI modely | vyměnitelné přes `99_System/config/pipeline.yaml`: OpenRouter (doporučeno) nebo Google Gemini API; oddělené cesty pro veřejná videa, soukromý obsah a chat, vždy s náhradními modely | model je součástka |
| Hledání | **hybridní RAG, implementováno**: SQLite FTS5 s českým stemmingem + hledání podle významu (lokální vícejazyčný model `paraphrase-multilingual-MiniLM-L12-v2` přes fastembed, vektory v SQLite) + zmínky entit z rejstříku; v režimu Full AI nejdřív vyřadí nesouvisející úryvky | nic z hledání neodchází ven |
| Graf vztahů | registr entit (`entities.yaml`: aliasy, typ, nadřazená entita) + zmínky po segmentech + wiki-odkazy `[[…]]` v kartách pro Obsidian | samostatná grafová databáze není potřeba |
| Stav a deduplikace | `99_System/state/{source_id}.json` (kroky, engine, verze promptu, hashe); katalog zdrojů se odvozuje z frontmatteru karet | žádná lokální databáze historie |
| Aplikace | FastAPI + PWA (telefon i PC), worker na pozadí, systemd na malém VPS, přístup jen přes Tailscale | žádný veřejný port |
| Testy | pytest: jednotkové, integrační s napodobeninami modelů, zlatý test karty; `doctor` pro prostředí | bez sítě, bez plateb |

Záměrně **není** a neplánuje se: Qdrant ani jiná vektorová databáze (vektory v SQLite stačí do desítek tisíc úseků),
GraphRAG, uživatelské účty a role (všichni v týmu sdílejí vše; více týmů = více instancí), vícejazyčné rozhraní.

## Architektonická rozhodnutí (AD)

*   **AD-001: Markdown First.** Veškerá znalost musí být reprezentovatelná v Markdownu nebo otevřeném textovém formátu.
    Kritická znalost nikdy jen v databázi; index a vektory jsou odvozené a kdykoli přestavitelné.
*   **AD-002: Asset Preservation.** Každý zdroj má archivovaný originál v `00_Assets/` (dokument, audio hlasu a schůzek;
    audio videí jde znovu stáhnout, proto je mimo Git).
*   **AD-003: Entity-Centric Design.** Systém je organizován kolem entit (lidé, firmy, nástroje, témata), zdroj je důkaz.
    Entity žijí v jednom registru (`99_System/config/entities.yaml`), ne v souborech po jedné.
*   **AD-004: Questions Drive Use.** Hodnota systému se měří tím, jak odpovídá na otázky; chat a hledání jsou hlavní
    rozhraní, karty jsou pro čtení v Obsidianu.
*   **AD-005: Temporal Knowledge Management.** Každá informace nese datum zveřejnění a odhad volatility; historie se nemaže.
*   **AD-006: Provenance-First Design.** Žádné tvrzení bez dohledatelného původu (zdroj, segment, mluvčí, čas nebo strana).
*   **AD-007: Versioned Data Governance.** Generované soubory nesou `schema_version` a `prompt_version`; říkají, jak
    dokument vznikl, ne jak je kvalitní.
*   **AD-008: Evidence Graph (budoucí).** Vztahy mezi tvrzeními (podpora, rozpor, nahrazení) zatím nejsou modelovány;
    případně jako hrany nad stávajícími zmínkami, bez nové databáze.
*   **AD-009: Deterministic over Generative.** Co jde získat deterministicky (metadata, datum, URL, časy, ID, verze),
    generuje software, ne model. Model jen tam, kde je třeba porozumění.
*   **AD-010: Knowledge Domains & Storage Separation.** Složka na disku řeší pravidla nasazení (Git, zálohy); YAML
    metadata (`knowledge_domain`, `source_type`, `visibility`, `origin`) řeší význam. Hledání pracuje nad metadaty
    napříč složkami.
*   **AD-011: Test Pyramid.** Jednotkové testy deterministické logiky, integrační testy s napodobeninami modelů, zlaté
    testy výstupů. Síť a placené služby nejsou součástí běžného testovacího běhu.
*   **AD-012: Analysis Never Modifies the Transcript.** Přepis je strojová vrstva pravdy. Analýza je odvozená,
    kdykoli přegenerovatelná, přepis nemění. Původ tvrzení je `segment_ids`, doslovný úryvek je lidská kontrola.
*   **AD-013: Source Identity & Versioned State.** `source_id = namespace:stabilní_id` (`yt:`, `voice:`, `meeting:`,
    `file:`). Deduplikace z frontmatteru karet, stav kroků v `99_System/state/`.
*   **AD-014: Verification Is Code, Not a Model.** Citace tvrzení se ověřují deterministicky proti přepisu
    (structural / exact / canonical / fuzzy / weak); co nesedí, do karty jde jen jako neověřené. Zmínka entity se zapíše,
    jen když je název v segmentu přítomný. Člověk má poslední slovo u zápisů, úklidu entit a nových verzí dokumentů.
*   **AD-015: Model Is a Component.** Který model co dělá, určuje jen konfigurace; kód nezná jména modelů. Soukromý
    obsah může jít jinou cestou než veřejný.
*   **AD-016: Private by Network, Not by Password.** Aplikace poslouchá jen lokálně a je dostupná jen zařízením
    v soukromé síti (Tailscale). Klíče jen v `.env` mimo Git. Mazání vždy na dvě potvrzení, nikdy force push.

## Struktura složek (skutečná)

- `00_Assets/` originály: `transcripts/` (JSON přepisy, analýzy, zmínky), `documents/`, `voice/`, `meetings/`, `captions/`, `audio/` (mimo Git)
- `01_Zdroje/` karty veřejných zdrojů; `01_Zdroje/Dokumenty/` veřejné dokumenty
- `14_Internal/` soukromé: `VoiceNotes/`, `Schuzky/`, `Dokumenty/`; `14_Internal/Confidential/` je mimo Git
- `15_Inbox_Text/`, `16_Inbox_Dokumenty/`, `17_Inbox_Prepisy/`, `18_Inbox_Hlas/` vstupní složky
- `99_System/` `config/` (pipeline.yaml, entities.yaml), `Slovník.md`, `templates/`, `prompts/`, `meetings/`, `state/`, `index/` (mimo Git), `logs/` (mimo Git)
- `pipeline/` kód, `tests/`, `deploy/`, `docs/`, `demo/`

## Zásady

*   **Entity:** jeden registr s kanonickým názvem, typem, aliasy a nadřazenou entitou; nové navrhuje analýza (`navrženo`),
    člověk potvrzuje, slučuje nebo zamítá. Obecná slova („firma“, „model“) entitou nejsou.
*   **Tvrzení:** typ (fact / opinion / prediction / recommendation / hypothesis), volatilita, mluvčí, segment, stupeň
    ověření. Pravdivost se neověřuje, ověřuje se, že to opravdu zaznělo.
*   **Domény:** `knowledge_domain` (public, personal), `visibility` (public, private), `origin` (external, personal).
    Soukromé jde do `14_Internal/` a je v soukromém Git repozitáři; důvěrné do `Confidential/` mimo Git.
*   **Změny kódu:** před commitem `python -m pytest -q` a `python -m pipeline doctor`; žádná tajemství do Gitu;
    analýza nikdy needituje `00_Assets/` ani generované bloky karet.

## Co Knowledge OS není

Není to druhý Notion, sociální síť, task manager ani workflow systém. Není to systém s rolemi a oprávněními.
Je to znalostní báze s ověřitelnými odpověďmi pro jednoho člověka nebo tým, který si věří.
