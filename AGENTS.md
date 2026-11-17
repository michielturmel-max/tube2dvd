# tube2dvd — gebruik door een agent

Eén script: `python3 tube2dvd.py` (of `./tube2dvd.py`). Draait op Arch/Omarchy met
`yt-dlp`, `ffmpeg`, `cdrtools`, `dvdauthor`, `dvd+rw-tools` en `libisoburn`.
Controleer bij twijfel met `./tube2dvd.py doctor`.

## Regels voor agents

- Gebruik altijd `--json` (vóór of na het subcommando) voor leesbare machine-uitvoer;
  alle uitvoer is dan één JSON-regel met `"ok": true` of `"ok": false, "error": ...`.
- Gebruik `-d/--dir` of `TUBE2DVD_DIR` als de bibliotheek niet de standaard
  `~/Videos/tube2dvd` moet zijn.
- Stel nooit interactieve vragen aan de gebruiker als het met flags kan: `add -c`,
  `add -y`, `burn -y`.
- Video's blijven "pending" tot ze gebrand zijn. `status` geeft `pending`,
  `list` geeft alle `id`s. `burn` pakt standaard alleen pending items;
  `burn --all` alles, `clean` ruimt gebrande bestanden op.
- De standaardstijl is `scrapbook` (plakboek) voor zowel HTML- als DVD-menu;
  `--style classic` geeft het oude donkere menu.

## Plakboek-app in de browser (voor gebruikers)

```bash
./tube2dvd.py serve                 # http://127.0.0.1:8765/
./tube2dvd.py serve --port 9000 --host 0.0.0.0
./tube2dvd.py serve --port 8769 --tailscale   # luister op het Tailscale-IP van deze machine
```

Formulier met: YouTube-URL + categorie, nieuws ophalen (volledige artikelen
lokaal), eigen tekst, video uploaden vanaf telefoon/laptop, preview van het
scrapbook-menu en knop om de data-DVD-ISO met HTML-menu te bouwen. Alles komt direct in
hetzelfde plakboek-menu terecht; de HTML wordt na elke actie ververst. Er is ook een
knop voor een nieuwe dvd, een ISO-lijst met bladerbare HTML-preview en een knop om
de gekozen ISO te branden; de brandstatus staat op de pagina.

## Veelgebruikte commando's

```bash
./tube2dvd.py --json status
./tube2dvd.py --json list
./tube2dvd.py --json add "funny cat compilation" -c Katten -y
./tube2dvd.py --json news --count 12
./tube2dvd.py --json menu
./tube2dvd.py --json note "Boodschap" "Tekst van de boodschap" --title "Welkom"
./tube2dvd.py --json import ~/Videos/vakantie.mp4 -c "Vakantie"
```

## Alles-in-één: "verzamel video's tot 4 GB en maak een leuke dvd"

```bash
./tube2dvd.py --json dvd \
  "Katten=funny cat videos" \
  "Telefoons=beste mobiele telefoon reviews" \
  "Tech=tech news" \
  "Space=space x news" \
  --target 4GB --news 12 --style scrapbook
```

`dvd` doet: zoeken per categorie → round-robin downloaden tot `--target` → nieuws
uit de Omarchy-plugin importeren → data-DVD-ISO met HTML-plakboekmenu bouwen.
Zonder `--burn` wordt alleen de ISO gebouwd (pad staat in JSON als `iso`); met
`--burn -y` wordt er echt naar `/dev/sr0` gebrand. `--device` overschrijft de brander.
`--max-duration` (default 900 s / 15 min) voorkomt dat één lange video de hele
collectie domineert. JSON geeft `bytes` (bibliotheek) en `iso_bytes` (ISO).

## Nieuws uit de Omarchy-plugin

`news` leest `~/.config/omarchy/plugins/tweakers.nieuws/bin/tweakers-nieuws list`
(JSON: `url`, `title`, `lead`, `date`). Berichten komen in categorie
`Nieuws van vandaag`, met een koppeling in het HTML-menu en een diaserie op de
DVD-Video. URL's zijn de unieke sleutel; dubbele import wordt overgeslagen.
Andere pluginlocatie: `TUBE2DVD_NEWS=/pad/tweakers-nieuws`. Afbeeldingen uit
artikelen (og:image/twitter:image) worden lokaal in `thumbs/` bewaard en in het
scrapbook getoond. `collect`/`dvd` sturen een desktopmelding zodra de doelgrootte
(4 GB) is bereikt.

## Dagelijkse toevoegingen

Voeg op elk moment toe met `add` of `collect`; niets wordt automatisch gebrand.
`./tube2dvd.py --json status` toont hoe vol de volgende schijf is. Als de
bibliotheek klaar is: `./tube2dvd.py --json burn --mode video -y`
(of `--mode data` voor de originele MP4's + HTML-menu).
