# Průvodce: od nuly k běžící znalostní bázi (pro člověka bez IT znalostí, s AI asistentem)

Tento průvodce je psaný pro dvojici **člověk + AI asistent** (Claude Code, Codex, Gemini CLI apod.), který má
otevřenou složku s tímto repozitářem. Asistent dělá technickou práci, člověk klikne tam, kde to nikdo jiný udělat
nemůže (účty, platby, telefon). Obě strany čtou totéž.

**Pro asistenta:** pokyny, jak průvodce vést, jsou v `CLAUDE.md` v části *Režim instalace*. Držte se jich.
**Pro člověka:** kdykoli něčemu nerozumíte, zeptejte se asistenta; má k dispozici celý text a umí vysvětlit každý krok.

Dvě cesty:

| Cesta | Co dostanete | Čas | Co je potřeba |
|---|---|---|---|
| **A. Zkouška na počítači** | aplikaci na vašem PC, vyzkoušíte hledání, chat, dokumenty, hlas z mikrofonu PC | 30–60 min | počítač; klíč k AI modelům (bez něj funguje hledání a prohlížení, ne chat a přepis) |
| **B. Plný provoz** | aplikaci na vlastním serveru, telefon i PC, nahrávání kdekoli, sdílení v týmu, zálohu v Gitu | odpoledne | cesta A + účet GitHub, Tailscale, pronajatý server (cca 100–200 Kč měsíčně) |

Začněte vždy cestou A. Cesta B na ni navazuje.

---

## Cesta A: zkouška na počítači

### A1. Nástroje (člověk instaluje, asistent kontroluje)

Potřebujete tři programy. Asistent vám řekne, které už máte (`python -m pipeline doctor` nebo `python --version`,
`ffmpeg -version`, `git --version`).

1. **Python** 3.10 nebo novější: https://www.python.org/downloads/ → Download → při instalaci na Windows
   zaškrtněte *Add python.exe to PATH*.
2. **Git**: https://git-scm.com/downloads → výchozí volby.
3. **ffmpeg** (zvuk): Windows: v PowerShellu `winget install Gyan.FFmpeg`; macOS: `brew install ffmpeg`;
   Ubuntu: `sudo apt install ffmpeg`.

Po instalaci zavřete a znovu otevřete terminál (PowerShell / Terminál), jinak nové programy nevidí.

Poznámka k příkazům: na Linuxu a macOS se Python často volá `python3` místo `python`. Po aktivaci virtuálního
prostředí (krok A2) už `python` funguje všude; všechny další příkazy předpokládají aktivované prostředí.

*Hotovo, když:* asistent potvrdí, že všechny tři příkazy vypisují verzi.

### A2. Stažení a příprava (asistent)

```bash
git clone https://github.com/karnikj/knowledge-os.git
cd knowledge-os
python -m venv .venv
. .venv/bin/activate            # Windows PowerShell:  .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Instalace balíčků trvá několik minut. Model pro hledání podle významu (asi 120 MB) se stáhne sám při prvním
spuštění aplikace; do té doby a bez internetu funguje hledání jen podle slov.

*Hotovo, když:* `python -m pipeline doctor` ukazuje ✓ u Pythonu, ffmpeg, gitu a všech balíčků; chybí jen klíč.

### A3. Klíč k AI modelům (člověk)

Aplikace používá AI modely přes službu **OpenRouter** (jeden klíč pro všechny modely, platíte jen použití,
hodina přepisu asi 1 Kč, otázka v chatu haléře).

1. Otevřete https://openrouter.ai a přihlaste se (Google, GitHub nebo e-mail).
2. Vpravo nahoře *Credits* → *Add credits* → stačí 5 USD (karta nebo PayPal).
3. *Keys* (https://openrouter.ai/keys) → *Create key* → název třeba `knowledge-os` → zkopírujte klíč.
   Začíná `sk-or-v1-…`. **Nikomu ho neposílejte, ani asistentovi do chatu.**
4. **Vy sami ve svém terminálu** (ne přes asistenta, ten klíč vidět nemá) spusťte `python -m pipeline setup`
   a klíč vložte, až se průvodce zeptá (nezobrazí se). Nebo otevřete soubor `.env` (vzniká ze vzoru `.env.example`)
   v poznámkovém bloku a doplňte `OPENROUTER_API_KEY=sk-or-v1-…`.

Bez klíče můžete pokračovat do A4: hledání, prohlížení karet a ukázková data fungují, chat, přepis hlasu, karty
dokumentů a videa ne (v chatu uvidíte hlášku „Chybí klíč k AI modelům“). Klíč doplníte kdykoli později.

Alternativa zdarma: klíč Google AI Studio (https://aistudio.google.com/ → *Get API key*) do `GEMINI_API_KEY`.
Bezplatný tier má denní limity a bývá přetížený; na zkoušku stačí, na provoz ne.

*Hotovo, když:* `python -m pipeline doctor` ukazuje ✓ „klíč k AI modelům“ (nebo jste klíč vědomě přeskočili).

### A4. První spuštění (asistent)

```bash
python -m pipeline setup --demo     # složky, index, ukázková data [DEMO]
python -m pipeline web              # aplikace: http://127.0.0.1:8080  (když je port obsazený: --port 8790)
```

Okno terminálu s běžící aplikací nechte otevřené; zavřením aplikaci vypnete. Při prvním spuštění se v něm
stahuje model pro hledání podle významu; červené řádky `fastembed … Could not download` znamenají, že stažení
nejde (bez internetu nebo za firemní proxy); aplikace přesto běží, hledá jen podle slov.

Druhé okno terminálu: otevřete nový terminál, přejděte do složky `knowledge-os` (`cd …`), znovu aktivujte
prostředí (`. .venv/bin/activate`, Windows `.venv\Scripts\Activate.ps1`) a spusťte `python -m pipeline worker`.
Zpracovává nahrávky, dokumenty a videa na pozadí. Bez něj jde hledat a prohlížet, ale nic nového se nezpracuje.

*Hotovo, když:* v prohlížeči na http://127.0.0.1:8080 běží aplikace a v Hledat najdete „faktury“ (11 výsledků
z ukázkových dat). S klíčem navíc Chat odpoví na „Jaký je schvalovací limit faktur?“ s číslovanými citacemi.
Asistent to může ověřit i bez prohlížeče: `curl http://127.0.0.1:8080/api/status` vrátí počty zdrojů v indexu.

Poznámka: `doctor` po načtení ukázkových dat může hlásit varování u audia nebo Gitu; ukázková data jsou záměrně
smyšlená (video neexistuje). Ukázková data se do Gitu nehodí: před krokem B1 je odstraňte (`python demo/remove.py`).

### A5. Co vyzkoušet (člověk, s manuálem `UZIVATELSKY_MANUAL.md`)

- Hlas: nahrajte poznámku mikrofonem PC, počkejte na přepis (worker musí běžet).
- Dokumenty: nahrajte jakýkoli PDF s textem nebo Word, zeptejte se na něj.
- Přidat YT: vložte adresu videa, sledujte Úlohy.
- Až ukázková data nebudete chtít: `python demo/remove.py`.

Tady můžete skončit; aplikace na PC plně funguje pro jednoho člověka. Nevýhody: běží jen, když běží počítač,
telefon se k ní nedostane a bez Gitu nemáte historii ani zálohu.

---

## Cesta B: plný provoz na serveru

### B1. Vlastní soukromý repozitář na GitHubu (člověk + asistent)

Do báze půjdou vaše přepisy a zápisy, proto musí být ve **vašem soukromém** repozitáři, ne ve veřejné předloze.

1. Člověk: účet na https://github.com (zdarma). Pak https://github.com/new → *Repository name* `knowledge-os`
   (nebo jiný), **Private**, nic dalšího nezaškrtávat → *Create repository*.
2. Asistent: ve složce z cesty A přesměruje Git na nový repozitář a odešle obsah:
   ```bash
   git remote remove origin
   git remote add origin https://github.com/<váš-účet>/<název>.git
   git push -u origin main
   ```
   Git se zeptá na přihlášení: na Windows se otevře okno GitHubu, jinde vložte *personal access token*
   (GitHub → Settings → Developer settings → Tokens; stačí právo `repo`).

*Hotovo, když:* na GitHubu ve vašem repozitáři vidíte soubory a nahoře je zámek *Private*.

### B2. Server (člověk)

Pronajměte malý virtuální server (VPS) s **Ubuntu 24.04**, 1–2 vCPU, **4 GB RAM**, 20–40 GB disk. V Česku
například Wedos, Forpsi, Hetzner (Německo) nebo DigitalOcean; cena 100–200 Kč měsíčně. Při objednávce:

- zvolte Ubuntu 24.04,
- nechte si poslat heslo roota, nebo vložte veřejný SSH klíč, který vám asistent připraví (`ssh-keygen`),
- poznamenejte si IP adresu serveru.

*Hotovo, když:* asistent se přihlásí `ssh root@IP` a vidí příkazovou řádku serveru.

### B3. Tailscale: soukromá síť (člověk)

Aplikace nebude na internetu veřejně; uvidí ji jen zařízení přihlášená do vaší sítě Tailscale.

1. Účet na https://tailscale.com (přihlášení Google/Microsoft/GitHub, zdarma do 100 zařízení).
2. Do **telefonu** nainstalujte aplikaci Tailscale (Google Play / App Store) a přihlaste se.
3. Na **PC** nainstalujte Tailscale z https://tailscale.com/download a přihlaste se stejným účtem.
4. V administraci https://login.tailscale.com/admin/dns zapněte *MagicDNS* a *HTTPS Certificates*.

### B4. Instalace na server (asistent, člověk potvrzuje)

Nejdřív člověk uloží klíč k modelům na server (asistent ho k tomu navede, klíč píše člověk):

```bash
ssh root@IP
nano /root/.env          # napsat řádek  OPENROUTER_API_KEY=sk-or-v1-…   a uložit (Ctrl+O, Enter, Ctrl+X)
```

Pak asistent na serveru (jako root) spustí:

```bash
apt-get update && apt-get install -y git
git clone https://github.com/karnikj/knowledge-os.git /root/knowledge-os     # jen kvůli instalačnímu skriptu
bash /root/knowledge-os/deploy/install_vps.sh <váš-účet>/<název-repozitáře>
```

Skript je idempotentní: dá se spouštět opakovaně, pokračuje tam, kde skončil. Zastaví se na dvou místech, kde
potřebuje člověka:

1. **Deploy klíč**: vypíše dlouhý řádek začínající `ssh-ed25519` (bez terminálu, např. když ho asistent spouští přes
   `ssh … bash …`, vypíše `DEPLOY_KEY_NEEDED …` a skončí). Člověk řádek zkopíruje do GitHubu: váš repozitář →
   *Settings* → *Deploy keys* → *Add deploy key*, název `kos-vps`, zaškrtnout **Allow write access** → *Add key*.
   Pak Enter v terminálu, nebo skript spustit znovu.
2. **Tailscale**: vypíše odkaz `https://login.tailscale.com/…` (bez terminálu `TAILSCALE_LOGIN …` a skončí); člověk ho
   otevře a potvrdí přihlášení serveru stejným účtem jako na telefonu. Pak skript spustit znovu, dokončí `tailscale serve`.

Na konci skript vypíše adresu `https://kos.<vaše-síť>.ts.net`.

*Hotovo, když:* `bash /home/kos/vault/deploy/vps_health.sh` ukazuje služby `active` a web odpovídá.

### B5. Telefon a PC (člověk)

- Telefon: v Chrome otevřete adresu z B4 → menu ⋮ → *Přidat na plochu*. Ikona Knowledge OS je pak jako běžná
  aplikace: nahrávání běží na pozadí, video z YouTube nebo soubor jí pošlete přes *Sdílet*.
- PC: tatáž adresa v prohlížeči. Pro čtení v Obsidianu naklonujte svůj soukromý repozitář a otevřete složku jako vault.

### B6. Kontrolní seznam na závěr (asistent projde s člověkem)

- [ ] `python -m pipeline doctor` na PC bez ✗
- [ ] na serveru `vps_health.sh`: `kos-web`, `kos-worker`, `tailscaled` active
- [ ] z telefonu se aplikace otevře a nahrávka hlasu se do minuty přepíše
- [ ] na GitHubu přibyl commit od „Knowledge OS (VPS)“ (worker pushuje změny)
- [ ] `.env` není v Gitu (`git ls-files .env` nic nevypíše)
- [ ] ukázková data odstraněna (`python demo/remove.py`), pokud už nejsou potřeba

---

## Když něco nejde

| Příznak | Co to znamená | Řešení |
|---|---|---|
| `python` není rozpoznán | Python není v PATH | Windows: přeinstalovat s *Add to PATH*, nebo `py -m pipeline …`; Linux/macOS: `python3` |
| `address already in use` při spuštění webu | port 8080 používá jiný program | `python -m pipeline web --port 8790` a otevřít http://127.0.0.1:8790 |
| chat hlásí „Chybí klíč k AI modelům“ | `.env` nemá klíč | krok A3; po doplnění klíče aplikaci vypnout (Ctrl+C) a spustit znovu |
| v okně webu červené `fastembed … Could not download model` | model pro hledání podle významu se nestáhl (internet, proxy) | aplikace funguje, hledá jen podle slov; zkusit později, nebo `KOS_NO_VECTORS=1` před `python -m pipeline web` pro vypnutí |
| `FutureWarning` nebo jiné žluté varování při spuštění | jen informace z knihoven | ignorovat |
| `pip install` hlásí chybu u `weasyprint` | chybí systémové knihovny pro PDF | PC: ignorovat (PDF zápisů funguje na serveru); Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0` |
| `[SSL: CERTIFICATE_VERIFY_FAILED]` na Windows | Python nevidí systémové certifikáty | `pip install pip-system-certs` |
| doctor: „klíč k AI modelům chybí“ | `.env` není nebo je prázdný | `python -m pipeline setup`, nebo otevřít `.env` a doplnit |
| chat odpovídá „nenašel jsem“ | index prázdný nebo worker nic nezpracoval | `python -m pipeline reindex`; zkontrolovat Úlohy |
| nahrávání v telefonu nejde spustit | prohlížeč vyžaduje HTTPS | otevřít adresu `https://…ts.net` z Tailscale, ne IP |
| YouTube video skončí chybou na serveru | YouTube blokuje datová centra | na PC: `python -m pipeline worker --local --once` |
| server po `git pull` nereaguje | restart služeb po novém kódu | `systemctl restart kos-web kos-worker`; `journalctl -u kos-worker -n 50` |
| `tailscale serve` hlásí chybu HTTPS | v administraci není zapnuté HTTPS | https://login.tailscale.com/admin/dns → *Enable HTTPS*; pak `tailscale serve --bg 8080` |

Cokoli jiného: pošlete asistentovi celý text chyby a výstup `python -m pipeline doctor`.
