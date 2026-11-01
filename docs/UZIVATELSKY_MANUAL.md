# Knowledge OS — uživatelský manuál

Co aplikace umí a jak se s ní pracuje den za dnem. Instalaci a architekturu popisuje [`INSTALACE.md`](INSTALACE.md).

## 1. Základní myšlenka

Všechno, co do báze vložíte (hlasová poznámka, nahrávka schůzky, dokument, video), aplikace **přepíše, pochopí
a uloží tak, aby šlo dohledat přesné místo**. Když se pak zeptáte, odpovídá jen z vašich podkladů a u každé věty
ukáže, odkud je. Nic si nedomýšlí; když odpověď v bázi není, řekne to.

Dva typy obsahu:

- **veřejné** (videa z YouTube, veřejné dokumenty a stránky: manuály, zákony, články) → karty v `01_Zdroje/`,
- **soukromé** (hlasové poznámky, schůzky, interní dokumenty) → karty v `14_Internal/`.

Rozdělení určuje, kterou cestou k modelům data jdou (`pipeline.yaml`), a je vidět u každé citace. Není to oprávnění:
všichni, kdo mají do aplikace přístup, vidí všechno.

## 2. Aplikace na telefonu a na PC

Jedna webová aplikace pro obojí. V telefonu si ji přidejte na plochu (Chrome → *Přidat na plochu*); pak se chová
jako běžná aplikace, nahrává i při zhasnutém displeji a objeví se v nabídce *Sdílet*: video z YouTube nebo soubor
pošlete rovnou do ní.

- **Telefon**: spodní lišta *Hledat · Chat · Hlas · Schůzky · Více* (ve *Více*: Přidat video z YouTube, Dokumenty,
  Úlohy a stav, Slovník názvů, Entity).
- **PC**: horní lišta ve třech skupinách: *Zeptat se* (Hledat, Chat, Entity), *Přidat znalosti* (Přidat YT, Hlas,
  Schůzky, Dokumenty) a *Úlohy*. Vpravo nahoře je měsíční spotřeba AI v Kč; klepnutím se rozbalí rozpis podle
  účelu a modelu.

Vše, co aplikace vytvoří, je zároveň čitelné v **Obsidianu** (otevřete složku repozitáře jako vault) a v Gitu.

## 3. Hlasové poznámky

Záložka **Hlas**. Klepněte na *Nahrát*, mluvte, případně *Pauza*, nakonec *Odeslat k přepisu* (název je nepovinný)
nebo *Zahodit*.

- Nahrávka se ukládá **po 30sekundových kusech** přímo v telefonu; výpadek signálu, zavřený displej ani přepnutí
  aplikace o nic nepřipraví. Trvalé oznámení v liště ukazuje, že nahrávání běží.
- Po odeslání přepis obvykle trvá desítky sekund (podle délky). Výsledek najdete pod nahrávacím tlačítkem
  v *Posledních poznámkách* a jako kartu v `14_Internal/VoiceNotes/`: shrnutí, klíčové body, tvrzení s časem
  a doslovný přepis.
- **Slovník** (záložka Slovník názvů) opravuje názvy produktů, firem a lidí, které přepis plete. Zapíšete správný
  tvar a jak ho přepis slyší špatně; tlačítko *Opravit i starší přepisy* to aplikuje zpětně.
- Nepovedené zpracování: v Úlohách je poznámka s chybou a tlačítkem *Zkusit znovu zpracovat*; nahrávka se neztrácí.

## 4. Schůzky a zápisy

Záložka **Schůzky** → *Nový zápis*. Vyberte šablonu (*Zápis ze schůzky* pro jednání s firmou nebo klientem,
*Zápis z porady* pro interní poradu) a vyplňte, co znáte předem: název, účastníky ve tvaru „Jméno Příjmení, firma“,
cíl. Pak *Nahrát*.

Po skončení nahrávky:

1. Přepis rozliší mluvčí. V **Kdo je kdo** vidíte u každého typickou větu s odkazem ▶ do nahrávky; jméno přiřadíte
   jednou a propíše se do celého zápisu i do hledání.
2. AI vyplní pole šablony: cíl, shrnutí, projednané body, rozhodnutí, úkoly (kdo, co, do kdy — termín jen pokud
   zazněl). **Každá položka od AI má štítek AI a odkaz ▶** na místo v nahrávce, ze kterého vychází.
3. Zápis je v **předpokoji ke kontrole**: cokoli upravíte, doplníte, škrtnete. Úpravy během zpracování se bezpečně
   sloučí s tím, co mezitím AI vyplnila.
4. *Schválit a uložit trvale* → karta v `14_Internal/Schuzky/`, **PDF ke stažení** v grafice vaší značky
   (sekce v pořadí šablony, úkoly v rámečku) a plná dohledatelnost v Hledat i Chat.

Zápis bez kontroly se po 14 dnech uloží automaticky a dostane poznámku, že nebyl zkontrolován. Schůzku je možné
zapsat i bez nahrávky (*Schválit bez nahrávky*). Šablony upravíte v aplikaci (Schůzky → Šablony → pole a pokyn pro
AI u každého pole); dvě výchozí nelze smazat.

## 5. Dokumenty a webové stránky

Záložka **Dokumenty**: *Vybrat soubory* (MD, TXT, Word DOCX, PDF s textovou vrstvou, do 40 MB) nebo *adresa webové
stránky* → *Přidat stránku*. Před nahráním zvolte **Soukromý / Veřejný**; u stránky lze zaškrtnout hlídání změn.

- **Převod se strukturou**: nadpisy a kapitoly se zachovají, aby citace vedla na správné místo (u PDF na stranu).
  Skenované PDF bez textu aplikace odmítne a řekne proč.
- **Duplicita a nová verze**: stejný obsah pozná podle otisku a nepřidá ho podruhé. Podobný název nebo obsah nabídne
  jako *nová verze?* — rozhodnete vy; stará verze jde do archivu, nic se neztratí.
- **AI karta** vznikne na pozadí: typ dokumentu, pro koho platí, platnost, shrnutí, hlavní body, pojmy, témata.
  Otevřete ji klepnutím na dokument; vpravo je náhled s kapitolami.
- **Hlídané stránky** worker jednou týdně znovu stáhne; změna = nová verze s poznámkou.
- **Chat nad dokumentem**: v náhledu dokumentu je pole *Zeptat se*; menší dokument dostane model celý, odpověď má
  citace na kapitoly.
- Hromadně: soubory vložené do složky `16_Inbox_Dokumenty/` (ve vaultu nebo přes Obsidian) worker sám zpracuje;
  nepodporované přesune do podsložky `_nepodporovane/`.

## 6. Videa z YouTube

Záložka **Přidat YT**: vložte adresu → *Přidat do zpracování* (v telefonu stačí video *sdílet* do aplikace).
Worker stáhne audio a titulky, přepíše po kusech, zarovná časy na titulky, zkontroluje úplnost, provede analýzu
a vytvoří kartu v `01_Zdroje/`: shrnutí, kapitoly, klíčové body, **tvrzení a názory s citací a stupněm ověření**,
zmínky entit, přepis s odkazy na čas (`https://youtu.be/ID?t=…`). Stav sledujete ve *Frontě a stavu* a v Úlohách.
Když YouTube ze serveru stažení zablokuje, úloha se předá na PC (`python -m pipeline worker --local --once`).

## 7. Hledat

Záložka **Hledat**: fulltext ve všech přepisech, zápisech a dokumentech, **tolerantní ke skloňování** („agentech“
najde i „agenti“). Filtry: mluvčí, typ zdroje (video, hlasová poznámka, schůzka, dokument), období. Výsledek je
jeden úryvek s časem nebo kapitolou; klepnutí na čas skočí do videa, *souvislosti* otevře okolí v přepisu, odkaz
u dokumentu otevře originál (stranu PDF nebo původní web). Hledání je zdarma a trvá milisekundy.

## 8. Chat

Záložka **Chat**: otázka vlastními slovy, česky. Tři úrovně:

| Úroveň | Kdy | Co dostanete |
|---|---|---|
| **Light** | rychlá odpověď | 2–4 věty z nejlepších úryvků |
| **Standard** | běžná otázka | odpověď + 5 nejdůležitějších zmínek se zdroji |
| **Full** | přehled všeho, co k tématu zaznělo | AI nejdřív projde nalezené úryvky a nesouvisející vyřadí, pak úplný výčet po 15 s možností *další* |

- **Citace**: číslo za větou vede na zdroj; **barva čísla** říká, o jaký zdroj jde (dokument modře, schůzka zeleně,
  hlasová poznámka žlutě, video červeně). Klepnutím se odpověď posune na daný úryvek; odtud vede odkaz na originál.
- Pod odpovědí je **cena** (tokeny a Kč). Běžná otázka stojí haléře.
- *Období* omezí hledání na dané datum od–do. Chat nad jedním dokumentem je v Dokumentech.
- Když v bázi odpověď není, aplikace to napíše; nevymýšlí si.

## 9. Entity

Záložka **Entity**: rejstřík osob, firem, nástrojů, AI modelů, služeb, projektů, publikací, míst, událostí a témat,
který se plní sám z analýz. Výchozí pohled je *hlavní (bez témat a míst)*. U každé entity: počet zmínek a zdrojů,
poslední výskyt, **postoj** (kolikrát se o ní mluvilo pozitivně, negativně, smíšeně) a seznam zmínek s odkazem
na čas nebo kapitolu.

- *✓ Potvrdit* (zkontrolováno), *Sloučit s…* (vyhledáte cílovou entitu, zmínky se přenesou), *Zamítnout — není to
  entita* (šum jako „firma“ nebo „model“; zamítnuté se skryjí a znovu se nenavrhnou, lze *Obnovit*), změna typu.
- **🧹 Úklid s AI** projde celý rejstřík po dávkách na pozadí a navrhne, co zamítnout, sloučit nebo přetypovat.
  Návrhy odkliknete hromadně (*Provést vybrané*); bez vašeho potvrzení se nic nemění.
- Zmínka vznikne jen tehdy, když je název v daném místě přepisu skutečně přítomný; AI si nemůže zmínku vymyslet.

## 10. Úlohy, spotřeba, slovník

- **Úlohy**: co se právě zpracovává, co čeká, co selhalo a proč; *Zkusit znovu* nebo *Smazat ze systému* (celý
  zdroj včetně přepisu a karty, vždy na dvě potvrzení). Hledání i v obsahu úloh.
- **Spotřeba AI**: každé volání modelu s počtem tokenů a cenou v Kč, měsíční součet v záhlaví, rozpis podle účelu
  (přepis, analýza, chat, úklid entit) a modelu.
- **Slovník názvů**: správné psaní názvů pro přepis (viz Hlas). Nejčastější názvy z entit se přidávají samy.

## 11. Práce v Obsidianu a Gitu

Karty jsou obyčejné soubory Markdown s YAML hlavičkou (`source_id`, datum, mluvčí, osoby, organizace, nástroje,
témata). Generovaná část je mezi značkami `<!-- gen:start -->` a `<!-- gen:end -->`; **vlastní poznámky pište
pod ni**, přegenerování je zachová. Přepisy jsou v `00_Assets/transcripts/`, do nich ručně nezasahujte (strojová
pravda, ze které vychází ověřování citací). Všechno je v Gitu: historie každé změny, návrat ke kterémukoli stavu,
záloha na GitHubu.

## 12. Ukázková data

`python demo/load.py` načte smyšlenou firmu Alfa Trade s.r.o.: tři hlasové poznámky, schválený zápis ze schůzky,
soukromou směrnici, veřejný postup a jeden „rozhovor“ (video s neexistujícím ID, odkazy na YouTube nevedou nikam).
Všechno má v názvu **[DEMO]**. Zkuste: hledat „faktury“, zeptat se v chatu „Jaký je schvalovací limit faktur?“,
otevřít entitu *Alfa Trade s.r.o.*, stáhnout PDF zápisu. Až si to prohlédnete, `python demo/remove.py` vše odstraní
(soubory, entity i index).

## 13. Časté otázky

**Nahrávání v telefonu nejde spustit.** Prohlížeč potřebuje HTTPS: otevřete aplikaci přes adresu z Tailscale
(`https://…ts.net`), ne přes IP.

**Přepis má v kartě varování STT_WARNING / STT_INCOMPLETE.** Kontrola pokrytí řeči nebo porovnání s titulky
našla mezeru; karta říká kde. Většinou stačí *Zkusit znovu* (přepis se dělá po kusech, cache zůstává).

**Odpověď v chatu je příliš obecná nebo chybí.** Zkuste Standard místo Light, konkrétnější dotaz s názvy
(entity z otázky se hledají zvlášť), nebo Hledat pro přesné slovo.

**Chci jiný model.** `99_System/config/pipeline.yaml`, bloky `stt` a `analysis`; po změně restart služeb.

**Kolik to stojí.** Hodina přepisu zhruba 1 Kč, otázka v chatu haléře, hledání zdarma. Přesná čísla v Spotřebě AI.
