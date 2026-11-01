# PROJECT_PRINCIPLES.md — Knowledge OS

Projektová ústava a implementační pravidla.
Tento dokument popisuje konkrétní technologická rozhodnutí, strukturu systému a implementační zásady pro aktuální verzi Knowledge OS.

## Vision
Knowledge OS je osobní výzkumná platforma.
Cílem není ukládat informace. Cílem je:
- budovat dlouhodobou znalostní bázi
- sledovat vývoj názorů
- propojovat lidi, firmy, produkty a koncepty
- vytvářet nové syntézy
- podporovat výzkum a rozhodování

## Current Technology Stack

### Knowledge Layer
- **Primary System**: Obsidian
- **Storage Format**: Markdown with YAML Frontmatter
- **Source of Truth**: Obsidian Vault

### AI Layer
- **Primary Model**: Gemini Flash (API via Google AI Studio Free Tier)
- **Use Cases**: transkripce, extrakce entit, sumáře, kapitoly, klasifikace.
- **Fallback**: Gemini Pro

### Data Layer
- **Metadata Database**: SQLite
- **Purpose**: historie zpracování, stav pipeline, deduplikace.

### Semantic Search & Graph
- **Current**: Obsidian wiki links (`[[odkazy]]`) for relationships.
- **Planned**: Qdrant (for vector search/embeddings) and GraphRAG.
- **Trigger Condition**: GraphRAG/Qdrant will be implemented incrementally in later phases as the database scales.

### Testing Layer
- **Primary Framework**: pytest
- **Testing Approach**: pyramid pattern (unit tests, mock-based integration, regression/golden tests).


## Architecture Decisions

*   **AD-001: Markdown First**
    Veškeré informace musí být reprezentovatelné v Markdownu. Nikdy neukládej kritickou znalost pouze do databáze.
*   **AD-002: Asset Preservation**
    Každý zdroj musí mít archivovaný originál. Ukládá se do `00_Assets/`.
*   **AD-003: Entity-Centric Design**
    Systém je organizován kolem entit (People, Organizations, Products, Concepts), ne kolem zdrojů. Zdroj je pouze důkaz.
*   **AD-004: Questions Drive Research**
    Výzkum je organizován kolem otázek. Každá významná oblast má vlastní výzkumnou otázku.
*   **AD-005: Temporal Knowledge Management (TKM)**
    Každá informace v systému nese časový kontext (včetně volatility tématu a data zveřejnění). Při syntéze se informace hodnotí vícekriteriálně podle stáří, volatility, důvěryhodnosti zdroje a následného ověření. Historie se nemaže, ale slouží ke sledování vývoje poznání.
*   **AD-006: Provenance-First Design**
    Žádné tvrzení (Claim) v systému nesmí existovat bez dohledatelného původu (zdroj, kapitola, mluvčí, časový otisk). Původ je základem auditu.
*   **AD-007: Versioned Data Governance**
    Každý vygenerovaný dokument obsahuje verzi schématu (`schema_version`) a promptu (`prompt_version`). To minimalizuje potřebu zpětné migrace a umožňuje postupné rozšiřování schématu. Tyto verze slouží k identifikaci způsobu vzniku dokumentu, nikoli k označení jeho správnosti či kvality.
*   **AD-008: Evidence Graph & Conflict Detection (Budoucí cíl)**
    Vztahy mezi tvrzeními (podpora, rozpor, nahrazení) budou v budoucích fázích modelovány jako hrany v grafu. Systém bude detekovat konflikty a sledovat životní cyklus tvrzení.
*   **AD-009: Deterministic over Generative**
    Všechny informace, které lze získat deterministicky (metadata souboru, datum, URL, délka videa, časové značky, ID videa, verze schématu), musí být generovány softwarem, nikoliv LLM. Jazykový model se používá výhradně pro úlohy vyžadující interpretaci nebo porozumění obsahu.
*   **AD-010: Knowledge Domains & Storage Separation**
    Fyzická složka na disku řeší bezpečnost a pravidla nasazení (Git, zálohy, Dropbox), zatímco YAML metadata řeší sémantický význam (knowledge_domain, source_type, visibility, origin). Vyhledávání a graf pracují nad metadaty napříč složkami.
*   **AD-011: Test Pyramid**
    Projekt používá testovací pyramidu: pytest jednotkové testy pro deterministickou logiku (utility, parsery, validátory), integrační testy s mockovanými externími službami (Gemini) a oddělené regresní (golden) testy výstupních souborů. Síťové a placené služby nejsou součástí běžného testovacího běhu.



*   **AD-012: Analysis Never Modifies the Transcript**
    Přepis (`00_Assets/transcripts/{source_id}.json`) je strojová vrstva pravdy. Analýza (shrnutí, claims, entity, postoje) je odvozená, kdykoli přegenerovatelná a nikdy přepis nemění. Provenance claimu je `segment_ids`; doslovný úryvek slouží jen jako lidsky čitelná kontrola.
*   **AD-013: Source Identity & Versioned State**
    Každý zdroj má `source_id = namespace:stabilní_id` (`yt:`, `file:`, `notta:`, `voice:`, `note:`) a `source_version`. Deduplikace se odvozuje z frontmatteru karet (ne z lokální databáze). Stav kroků pipeline je v `99_System/state/{source_id}.json` včetně enginu, verze promptu a hashů vstupu/výstupu.

## Folder Structure
- `00_Assets/`
- `01_Zdroje/`
- `02_Lide/`
- `03_Organizace/`
- `04_Projekty_a_Produkty/`
- `05_Koncepty_a_Tema/`
- `08_Summaries/`
- `09_Research/`
- `10_Otazky/`
- `11_Sources/`
- `12_Observations/` (Planned V5)
- `13_Kontakty/`
- `14_Internal/`                 # Interní a osobní soubory (VoiceNotes, poznámky) — V GITU (privátní repo); jen `14_Internal/Confidential/` je mimo Git
- `15_Inbox_Text/`               # vstup: .md/.txt
- `16_Inbox_Dokumenty/`          # vstup: .pdf/.docx/.xlsx/.pptx
- `17_Inbox_Prepisy/`            # vstup: exporty z Notta, SRT/VTT, Word přepisy
- `18_Inbox_Hlas/`               # vstup: hlasové poznámky z telefonu (PWA)
- `99_System/`                   # prompty, config, state, index, queue.txt, docs
- `pipeline/`                    # Python balíček (spouštění: `python -m pipeline <příkaz>`)

## Policies

*   **Source Policy**: Nové sledované kanály a weby se registrují v `11_Sources/` a konfigurují v `sources.yaml`.
*   **Entity Policy**: Každá entita (např. [[Jan Novák]]) má vlastní soubor. Před vytvořením nové entity normalizujeme její název a zkontrolujeme duplicity.
*   **Claims Policy**: Tvrzení (Claims) se ve V1 ukládají přímo do zdrojového souboru v `/01_Zdroje` pod sekci `## Claims`. Každé tvrzení musí obsahovat autora, typ (fact / opinion / prediction / recommendation / hypothesis), výchozí stav (active) a odhadovanou volatilitu s mírou jistoty (low/medium/high). Skutečné ověřování pravdivosti (verifikace) je odloženo do verze V2/V3.
*   **Contact Policy**: Veřejná data o lidech patří do `/02_Lide/`. Osobní CRM (schůzky, maily) patří do `/13_Kontakty/`. Nikdy nemíchat.
*   **Domain Policy**: Každý dokument obsahuje metadata určující jeho doménu (`knowledge_domain`: public, internal, personal, crm), původ (`origin`: external, internal, personal, imported) a úroveň viditelnosti (`visibility`: public, private, confidential). Interní a osobní soubory (doména internal/personal) se ukládají do `/14_Internal/` a jsou součástí privátního Git repozitáře; skutečně důvěrné soubory patří do `/14_Internal/Confidential/`, která je v Gitu ignorována.


## MVP Definition
Verze V1 (MVP) je dokončena, pokud:
1. Lze stáhnout video přes `yt-dlp`.
2. Lze získat přepis a analýzu přes Gemini API.
3. Lze uložit vygenerovaný Markdown do Obsidianu.
4. Funguje deduplikace přes SQLite.

## Success Metrics
- Po 3 měsících: 500+ zdrojů
- Po 12 měsících: 5 000+ zdrojů
- Po 5 letech: 50 000+ zdrojů

## Non-Goals
Knowledge OS není druhý Notion, sociální síť, task manager ani workflow systém. Je to výzkumná platforma a znalostní graf.
