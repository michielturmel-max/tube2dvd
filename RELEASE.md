# Tag en release maken via de GitHub-website

Deze uitleg is voor als je alles handmatig in de browser wilt doen. Een **tag** is
alleen een label op een commit (bijvoorbeeld `v1.2.0`); een **release** is die tag
plus een titel, release-notes en eventuele downloadbare bestanden. In deze
repository maak je ze het beste samen: bij het publiceren van de release maakt
GitHub de tag automatisch aan.

## Voorbereiding (lokaal)

1. Zorg dat alle wijzigingen op `main` staan:

   ```bash
   git status
   git add -A
   git commit -m "Omschrijving van de wijziging"
   git push origin main
   ```

2. Verhoog de versie in `tube2dvd.py`:

   ```python
   VERSION = "1.3.0"
   ```

   En in de eerste alinea van `README.md`: `Versie: **1.3.0**`.

3. Commit en push ook die versiewijziging:

   ```bash
   git add tube2dvd.py README.md
   git commit -m "Versie 1.3.0"
   git push origin main
   ```

Waarom: `tube2dvd --json update` haalt de nieuwste release op, haalt `v` van de
tag af en vergelijkt dat getal met `VERSION` in `tube2dvd.py`. Als de tag niet
hoger is dan `VERSION`, meldt de app dat er geen update is. De inhoud van de
update komt uit `main`, dus de release hoeft geen bestanden te bevatten.

## Tag + release maken in de browser

1. Ga naar https://github.com/michielturmel-max/tube2dvd.
2. Klik rechts in de zijbalk op **Releases** (of op het tabblad **Releases** naast
   *Code*, *Issues*, *Actions*).
3. Klik op **Create a new release** (soms: **Draft a new release**).
4. Vul bij **Choose a tag** de nieuwe tag in, bijvoorbeeld `v1.3.0`.
   Onder het invoerveld verschijnt: *Create new tag: v1.3.0 on publish*.
5. Bij **Target** kies je `main` (de commit met de verhoogde versie).
6. Geef een **Release title**, bijvoorbeeld `tube2dvd 1.3.0`.
7. Schrijf korte **release notes**, of klik op **Generate release notes**;
   GitHub vat dan de commits sinds de vorige release samen. Handig om hier te
   zetten wat er nieuw is, bijvoorbeeld "video's tonen opnieuw hun poster na
   afspelen".
8. Optioneel: vink **Set as a pre-release** aan als het een testversie is. Laat het
   uit voor een gewone release. **Set as the latest release** staat standaard aan.
9. Klik op **Publish release**.

Daarna bestaat de tag `v1.3.0` en staat de release op
`https://github.com/michielturmel-max/tube2dvd/releases/tag/v1.3.0`.

## Controleren

```bash
git fetch --tags
git tag --list
tube2dvd --json update --check
```

Het laatste commando moet `latest` gelijk aan de nieuwe versie tonen. Bestaande
gebruikers krijgen de update met:

```bash
tube2dvd --json update -y
```

Dat vervangt `tube2dvd.py` door de versie op `main` en bewaart een backup als
`tube2dvd.py.bak`. Wie de repo als git-checkout heeft, gebruikt in plaats daarvan
`./update.sh` (doet `git pull --ff-only` en draait `install.sh` opnieuw).

## Versienummer-regels

Gebruik `v` + drie getallen: `vMAJOR.MINOR.PATCH`.

- **PATCH** (`v1.2.1`): kleine fix, bijvoorbeeld een knop die niet werkte.
- **MINOR** (`v1.3.0`): nieuwe functie, bijvoorbeeld een nieuw filter of een
  extra scherm, zonder oude dingen te breken.
- **MAJOR** (`v2.0.0`): grote verandering waardoor oude gewoontes niet meer
  werken.

Het getal moet altijd hoger zijn dan de vorige tag en dan `VERSION` in de code.

## Als je het liever via de terminal doet

```bash
git tag -a v1.3.0 -m "Release 1.3.0"
git push origin v1.3.0
gh release create v1.3.0 --generate-notes --title "tube2dvd 1.3.0"
```

## Waar moet je op letten

- Push eerst `main`, anders wijst de tag naar een oude commit.
- ISO's, video's en bibliotheekdata komen niet in de repo (`zie .gitignore`) en
  dus ook niet in een release.
- Een release is niet hetzelfde als een tag: alleen een tag pushen geeft nog geen
  release-notes. `tube2dvd update` kijkt specifiek naar releases.
