# tube2dvd

Verzamel YouTube-video's, nieuwsartikelen, eigen teksten en lokale video's in een
plakboekachtig HTML-menu en brand daar een **data-dvd** van: `index.html` plus de
**originele MP4-bestanden**. De computer speelt de video's rechtstreeks vanaf de
schijf af; er wordt niets naar MPEG-2 omgezet. (De oudere DVD-Video-route bestaat
nog via `--mode video`, maar is niet de standaard.)

Gebouwd voor Arch/Omarchy, maar draait overal waar Python 3, `yt-dlp` en
`ffmpeg` beschikbaar zijn. Versie: **1.2.0**.

## Inhoud

- [Wat je krijgt](#wat-je-krijgt)
- [Benodigdheden](#benodigdheden)
- [Snelstart](#snelstart)
- [Installeren](#installeren)
- [Bijwerken](#bijwerken)
- [Verwijderen](#verwijderen)
- [Mappen en omgevingsvariabelen](#mappen-en-omgevingsvariabelen)
- [De web-app](#de-web-app)
- [Nieuws uit de Omarchy-plugin](#nieuws-uit-de-omarchy-plugin)
- [Commandline](#commandline)
- [ISO bouwen en branden](#iso-bouwen-en-branden)
- [Problemen oplossen](#problemen-oplossen)
- [Repository](#repository)

## Wat je krijgt

- Een lokaal HTML-plakboek (`index.html`) met kaarten per video, artikel of tekst,
  categorieën, speelse handgeschreven decoratie en een eigen titel.
- Video's spelen pas af nadat je op de kaart klikt/tikt; de bediening verschijnt
  dan pas. Posters komen uit de video zelf (bij artikelvideo's: een frame op
  ~1 seconde), zodat je geen zwart startscherm ziet.
- Nieuws uit de eerste drie bronnen van de `tweakers.nieuws`-plugin (Tweakers,
  nu.nl, Scientias wetenschap): volledige artikelen, bronlabel, lokale plaatjes
  en ingesloten artikelvideo's worden opgeslagen.
- Per bron een uitklapbalk in de web-app: klik op een kop om eerst het hele
  artikel te lezen (intro + uitklapbare, scrollbare hoofdtekst) en pas daarna toe
  te voegen.
- Categorieën uit de huidige dvd in een dropdown, plus eigen categorieën.
- Artikelen en teksten achteraf bewerken via `/edit?id=...`.
- Een doelgrootte van 4 GB met desktopmelding; ISO's worden bewaard en zijn
  bladerbaar in de web-app voordat je brandt.

## Benodigdheden en afhankelijkheden

`install.sh` controleert alle onderstaande commando's en pakketten en installeert
wat ontbreekt automatisch via `pacman` (met `sudo`). Met `--no-deps` sla je die
stap over en doe je het zelf. De nieuwsplugin zit niet in pacman en moet apart
aanwezig zijn.

Op Arch/Omarchy is het in één regel:

```bash
sudo pacman -S --needed python yt-dlp ffmpeg cdrtools dvdauthor dvd+rw-tools \
  libisoburn ttf-dejavu ttf-liberation curl xdg-utils sudo
```

| Pakket (command) | Waarvoor het nodig is | Zonder dit pakket |
| --- | --- | --- |
| `python` (`python3`) | de runtime van `tube2dvd.py`; alle subcommando's en de webserver | de app start niet |
| `yt-dlp` | zoeken en downloaden van YouTube-video's en artikelvideo's | `add`, `collect`, `dvd` en artikelvideo's werken niet |
| `ffmpeg` | omzetten naar MP4, posters maken op ~1 seconde, frames van uploads | geen video's en geen posters |
| `ffmpeg` (`ffprobe`) | duur en eigenschappen van video's uitlezen | posters en duurberekening falen |
| `cdrtools` (`mkisofs`) | de data-ISO met `index.html` en MP4's opbouwen | `burn`/ISO-bouw stopt |
| `dvd+rw-tools` (`growisofs`) | de ISO naar `/dev/sr0` branden | branden is onmogelijk (ISO maken kan wel) |
| `libisoburn` (`xorriso`) | de gebouwde ISO controleren | minder zekerheid over de schijf |
| `dvdauthor`, `spumux` | alleen de oude DVD-Video-modus (`--mode video`) | de standaard data-dvd werkt gewoon |
| `ttf-dejavu`, `ttf-liberation` | lettertypen voor de menu- en tekstweergave | lelijke of ontbrekende tekst op oude DVD-Video-menu's |
| `curl` | de desktop-launcher checkt of de server al draait | launcher start altijd een tweede server |
| `xdg-utils` (`xdg-open`) | de launcher opent de browser | je moet de URL zelf openen |
| `sudo` | laat `install.sh` pacman als root uitvoeren | installeer de pakketten handmatig |
| `tweakers.nieuws`-plugin | nieuws per bron, volledige artikelen en artikelvideo's | alleen de handmatige/video-onderdelen werken |
| `git` | alleen voor `update.sh` (git pull) | `update.sh` werkt niet; release-update via `tube2dvd update` wel |

De plugin wordt verwacht op
`~/.config/omarchy/plugins/tweakers.nieuws/bin/tweakers-nieuws` (of op `PATH`),
maar is geen pacman-pakket. `install.sh` meldt duidelijk of hij hem vindt.

Controleren kan altijd met:

```bash
python3 tube2dvd.py doctor
# of: tube2dvd --json doctor
```

De nieuwsplugin staat standaard op
`~/.config/omarchy/plugins/tweakers.nieuws/bin/tweakers-nieuws`. Een andere locatie:
`export TUBE2DVD_NEWS=/pad/tweakers-nieuws`.

## Snelstart

```bash
git clone https://github.com/michielturmel-max/tube2dvd.git
cd tube2dvd
./install.sh
tube2dvd --json doctor
tube2dvd --json -d ~/Videos/dvd-2026 serve --port 8769
```

Open daarna `http://127.0.0.1:8769/`. Voor je telefoon in hetzelfde netwerk:
`./tube2dvd.py --json -d ~/Videos/dvd-2026 serve --port 8769 --tailscale` en open
het Tailscale-IP of de MagicDNS-naam van de machine, bijvoorbeeld
`http://jouw-machine.tailXXXX.ts.net:8769/`.

## Installeren

`install.sh` installeert in je home-map; root is alleen nodig als er nog
systeempakketten ontbreken.

```bash
./install.sh                         # symlink in ~/.local/bin + desktop-launcher
./install.sh --port 9000             # andere poort
./install.sh --dir ~/Videos/mijn-dvd # andere bibliotheekmap
./install.sh --copy                  # kopie i.p.v. symlink
./install.sh --no-deps --no-desktop  # alleen de command installeren
./install.sh -y                      # pacman --noconfirm
```

Wat het doet:

1. Controleert `python3`, `yt-dlp`, `ffmpeg`, `ffprobe`, `mkisofs`, `dvdauthor`,
   `spumux`, `growisofs` en `xorriso`, plus de pakketten `python`,
   `ttf-dejavu`, `ttf-liberation`, `curl`, `xdg-utils` en `sudo`. Ontbrekende
   pakketten worden (met `sudo`) via `pacman` geïnstalleerd, tenzij je
   `--no-deps` gebruikt. De aanwezigheid van de nieuwsplugin wordt gemeld.
2. Maakt `~/.local/bin/tube2dvd` aan (symlink naar `tube2dvd.py`, of een kopie
   met `--copy`).
3. Maakt `~/.local/bin/tube2dvd-gui` en
   `~/.local/share/applications/tube2dvd.desktop`: start de server als die nog
   niet draait en opent de browser.
4. Maakt de bibliotheekmap en de `build/`-submap aan.

`~/.local/bin` moet in je `PATH` zitten. Op Omarchy is dat meestal al zo;
anders: `export PATH="$HOME/.local/bin:$PATH"` in je shellprofiel.

## Bijwerken

Twee manieren:

```bash
./update.sh                 # git pull --ff-only + install.sh opnieuw
./update.sh --check         # alleen kijken of er iets nieuws is
./update.sh --no-install    # alleen git pull
```

Of vanaf een GitHub-release, zonder git:

```bash
tube2dvd --json update --check
tube2dvd --json update -y
```

De release-update maakt een backup `tube2dvd.py.bak` naast het script.
`TUBE2DVD_REPO=gebruiker/repo` overschrijft de repository voor die update.

## Verwijderen

```bash
./uninstall.sh                       # command + launcher weg, data blijft staan
./uninstall.sh --prefix ~/.local/bin --dir ~/Videos/dvd-2026
./uninstall.sh --purge-data          # ook video's, thumbnails, ISO's, index.html
./uninstall.sh --remove-source       # ook deze repository-map
./uninstall.sh -y                    # zonder bevestiging
```

`--purge-data` en `--remove-source` vragen standaard om bevestiging. Je
bibliotheek (en dus je gebrande collectie) wordt zonder expliciete vlag nooit
verwijderd.

## Mappen en omgevingsvariabelen

| Variabele | Betekenis | Standaard |
| --- | --- | --- |
| `TUBE2DVD_DIR` | bibliotheekmap | `~/Videos/tube2dvd` |
| `TUBE2DVD_NEWS` | pad naar nieuwsplugin | plugin in `~/.config/omarchy/...` |
| `TUBE2DVD_REPO` | repo voor `update` | `michielturmel-max/tube2dvd` |
| `XDG_BIN_HOME` | installatiemap voor commands | `~/.local/bin` |
| `XDG_DATA_HOME` | map voor de desktop-launcher | `~/.local/share` |

In de bibliotheekmap:

```
library.json        alle items (ook dvd_name)
categories.json     categorieën
discs.json          log van gebouwde/ gebrande schijven
index.html          het gegenereerde plakboek
videos/<categorie>/ originele MP4's
thumbs/             posters, artikelplaatjes, tekstplaatjes
build/              ISO's, preview, brandstatus en logs
```

Gebruik een map onder `/home`: `/tmp` is op veel systemen een beperkte tmpfs en
geeft snel *Disk quota exceeded* bij ISO-bouw.

## De web-app

```bash
tube2dvd --json -d ~/Videos/dvd-2026 serve --port 8769
tube2dvd --json -d ~/Videos/dvd-2026 serve --port 8769 --tailscale
tube2dvd --json -d ~/Videos/dvd-2026 serve --port 9000 --host 0.0.0.0
```

In de browser kun je:

- een YouTube-URL met categorie toevoegen;
- nieuws ophalen, of per bron artikelen bekijken vóór toevoegen
  (**Nieuws per bron bekijken**): drie uitklapbare bronnen, klik op een kop voor
  het volledige artikel, met intro, uitklapbare hoofdtekst, bronlabel en knop
  **Toevoegen aan dvd**;
- eigen tekst met optioneel plaatje plaatsen;
- een lokale video uploaden (poster wordt automatisch uit de video gemaakt);
- de naam van de dvd invullen — die naam verschijnt daarna in de app én in de
  `<h1>`/`<title>` van het plakboek, en is later nog aanpasbaar;
- artikelen en teksten bewerken;
- **Bekijk voorbeeld-menu** bekijken;
- **Maak data-DVD ISO (HTML-menu)** bouwen;
- **Nieuwe dvd beginnen**: huidige collectie als ISO archiveren, items als
  gebrand markeren en leeg beginnen;
- in de ISO-lijst een ISO previewen, bladeren, branden of verwijderen
  (verwijderen vraagt bevestiging en kan niet tijdens het branden).

De HTML wordt na elke actie opnieuw geschreven. Videos zijn op de telefoon
speelbaar dankzij `/videos/`, `/thumbs/`, `playsinline` en range-requests; de
mobiele layout toont de hele kaart **Volgende dvd**.

## Nieuws uit de Omarchy-plugin

`news` leest `tweakers-nieuws list` (JSON met `url`, `title`, `lead`, `date`).
De unieke sleutel is de URL; dubbele import wordt overgeslagen.

```bash
tube2dvd --json news --count 12 --all-sources
tube2dvd --json news --count 6 --source 0
tube2dvd --json news --count 12 --refresh
```

`--all-sources` pakt de eerste drie bronnen zoals die op dat moment in de
nieuws-app staan. Volledige artikelen, `og:image`/`twitter:image`-plaatjes en
artikelvideo's (via `yt-dlp`) worden lokaal bewaard. `--no-body` haalt alleen
koppen op. In het plakboek staat bij een artikelvideo de video bovenaan, met de
korte videonaam als klikbare titel — geen lange URL in de tekst.

## Commandline

Gebruik `--json` vóór of na het subcommando: alle uitvoer is dan één JSON-regel
met `ok: true` of `ok: false, "error": ...`. Met `-y` sla je vragen over.

```bash
tube2dvd --json status
tube2dvd --json list
tube2dvd --json add "funny cat compilation" -c Katten -y
tube2dvd --json note "Welkom" "Tekst van de boodschap" --title "Welkom"
tube2dvd --json import ~/Videos/vakantie.mp4 -c Vakantie
tube2dvd --json remove <id>
tube2dvd --json clean
tube2dvd --json menu
tube2dvd --json doctor
```

Alles-in-één: zoeken, downloaden, nieuws importeren en ISO bouwen:

```bash
tube2dvd --json dvd \
  "Katten=funny cat compilation 2026" \
  "Wetenschap=wetenschap nieuws 2026" \
  --target 4GB --news 12 --style scrapbook
```

Zonder `--burn` wordt alleen de ISO gebouwd. `--burn -y` brandt naar `/dev/sr0`;
`--device` overschrijft de brander. `--max-duration` (default 900 s) voorkomt dat
één lange video de hele collectie domineert. De standaardstijl is `scrapbook`;
`--style classic` geeft het oude donkere menu.

## ISO bouwen en branden

```bash
tube2dvd --json -d ~/Videos/dvd-2026 burn --dry-run        # alleen ISO
tube2dvd --json -d ~/Videos/dvd-2026 burn --keep-image -y  # branden + ISO houden
tube2dvd --json -d ~/Videos/dvd-2026 burn --all -y         # ook al gebrande items
```

- `--mode data` (standaard): `index.html` + originele MP4's, te lezen in elke
  computer met een browser.
- `--mode video`: de oude DVD-Video-menustructuur met `dvdauthor`.
- Na het branden blijven items "pending" tot `burn` ze markeert; `clean` ruimt
  bestanden van al gebrande schijven op.
- ISO's blijven standaard staan in `build/`. Bij 4 GB wordt een desktopmelding
  verstuurd.

## Problemen oplossen

| Symptoom | Oplossing |
| --- | --- |
| `tube2dvd: command not found` | `~/.local/bin` aan `PATH` toevoegen of opnieuw inloggen |
| `doctor` mist tools | `sudo pacman -S --needed python yt-dlp ffmpeg cdrtools dvdauthor dvd+rw-tools libisoburn` |
| *Disk quota exceeded* | bouw in `~/Videos/...`, niet in `/tmp`; ruim oude ISO's op |
| Video's downloaden met HTTP 403 | door YouTube geblokkeerd; worden overgeslagen, de rest gaat door |
| Zwart startplaatje bij een artikelvideo | poster wordt uit het MP4 gegenereerd; controleer `thumbs/<id>-video.jpg`, anders `tube2dvd --json news --refresh` |
| Video speelt niet op telefoon | gebruik `--tailscale`, open de Tailscale-URL en hard-refresh; range-requests en `playsinline` staan aan |
| Nieuws blijft leeg | controleer de plugin met `TUBE2DVD_NEWS=... tube2dvd --json news --count 3` |
| Server start niet | poort al in gebruik? kies `--port 9000`; kijk in `build/web-requests.log` en het serverlog |

Zie ook [AGENTS.md](AGENTS.md) voor de agent-regels en korte commando's en
[RELEASE.md](RELEASE.md) voor het zelf maken van een tag en release via GitHub.

## Repository

Deze map is een git-repository. Grote data hoort er **niet** in: `.gitignore`
sluit `*.iso`, `build/`, `videos/`, `thumbs/`, `library.json`, `index.html`,
logs en `__pycache__` uit. ISO's en bibliotheekdata blijven dus gewoon onder
`~/Videos/dvd-2026/` staan.
