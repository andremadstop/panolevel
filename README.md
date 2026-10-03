# PanoLevel

**360°-Equirect-Panoramen automatisch lotrecht ausrichten — für den PTGui-Workflow, auf Linux.**

PanoLevel erkennt vertikale Welt-Linien (Säulen, Türrahmen, Standkanten) in einem fertig gestitchten Equirect-Pano und schreibt sie als **Vertical-Control-Points** in eine Kopie des PTGui-Projekts (`.pts`). Du prüfst die Vorschläge an einem Overlay-Bild, prunst sie ggf. in PTGuis Editor und drückst Optimieren — PTGuis bewährter Solver rückt das Pano gerade. Statt pro Pano minutenlang Linien im Control-Point-Editor von Hand zu picken.

## Warum

PTGui ist proprietär (kein SDK/Plugin), aber das `.pts` ist seit v11 **JSON** und Control-Points sind direkt editierbar. PanoLevel automatisiert nur die Linien-*Auswahl* — der Stitch-/Optimize-Kern bleibt PTGui. Das Tool ruft PTGui nicht auf und ist damit maschinen-agnostisch (PTGui darf auf einem anderen Rechner laufen).

## Status

Phase 1–5, Web-Oberfläche und Ordner-Modus gebaut und am echten Material verifiziert.

- **Phase 1** — `.pts`-IO + Vertical-CP-Injektion, non-destruktiv, Round-Trip in PTGui bestätigt.
- **Phase 2** — konservative Vertikal-Detektion (REDUCED_4, adaptiv, RANSAC, „unsicher"-Flag, Konfidenz).
- **Phase 3** — End-to-End `autolevel`: Detektion → `.pts`-Kopie **oder** Projekt aus Template (für Panos ohne `.pts`) → Overlay-PNG → Report. Output-Settings konfigurierbar (Default TIFF 16-bit).
- **Phase 4** — `batch`: Ordnerlauf über alle Pano/`.pts`-Paare, robust gegen Einzelfehler, Sammelreport (sicher/unsicher/fehler/übersprungen).
- **Phase 5** — Weg A: roll/pitch direkt aus den Vertikalen schätzen (Zenit-Pol-Fit). Modus `angle` schreibt die Winkel ins `.pts` (PTGui rendert gerade, ohne Optimieren), Modus `rotate` richtet das **PNG selbst** lotrecht aus — ganz ohne PTGui.

- **Web-Oberfläche** — Linien von Hand setzen, Live-Vorschau, Export; Ordner-Modus für viele Panos nacheinander.

Offen: Modus `angle` – die Vorzeichen-Konvention in PTGui ist noch real zu bestätigen.

## Installation

Python ≥ 3.11, Linux. Optional: `exiftool` (Metadaten im Ordner-Modus), `node` (JS-Tests).

```bash
git clone <repo-url> panolevel && cd panolevel
python -m venv .venv
.venv/bin/pip install -e '.[dev]'     # oder ohne -e/[dev] für reine Nutzung
.venv/bin/panolevel --help            # gleichwertig: python -m panolevel
```

## Projektstruktur

```
src/panolevel/   Paket (CLI, Detektion, .pts-IO, Orientierung, Server)
src/panolevel/web/   Browser-Oberfläche (Vanilla JS, kein Build-Schritt)
tests/           unittest-Suite + tests/fixtures/ (zwei PTGui-.pts)
docs/            .pts-Format
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Drei Detektions-Tests brauchen ein echtes 16384×8192-Pano und werden sonst übersprungen:
`PANOLEVEL_REAL_PANO=/pfad/zu/pano.png`.


## Ausgabe-Modi (`--mode`)

```bash
python -m panolevel autolevel --image pano.png --pts "pano Panorama.pts"            # cp (default): CPs -> PTGui optimiert
python -m panolevel autolevel --image pano.png --pts "pano Panorama.pts" --mode angle  # 5a: roll/pitch ins .pts, kein Optimieren noetig
python -m panolevel autolevel --image pano.png --mode rotate                         # 5b: PNG direkt geraderotiert, kein PTGui/.pts
```

`--mode rotate` braucht weder `.pts` noch `--template`. `batch` erbt `--mode`. Hinweis: ein volles 16384×8192-Pano im `rotate`-Modus braucht ~4 GB RAM (Single-Pass). 5a (`angle`) ist gebaut, aber die PTGui-Vorzeichen-Konvention ist noch zu bestätigen.

## v2: Interaktives Web-UI

```bash
python -m panolevel serve --image pano.png            # rotate-Export (kein .pts noetig)
python -m panolevel serve --image pano.png --pts "pano Panorama.pts"   # .pts-Export moeglich
```

Öffnet ein lokales UI (nur `127.0.0.1`): Auto-Linien als Start, per Klick echte Vertikale ziehen (Rechtsklick löscht), Live-Vorschau des geraden Panos. Shift+Drag richtet horizontal aus (yaw). Export als rotiertes PNG oder `.pts` mit roll/pitch/yaw.

### Ordner-Modus (viele Panos nacheinander)

```bash
python -m panolevel serve /pfad/zu/stitched
# optional: --out-dir OUT (Standard: neben dem Ordner "panolevel_gerade"), --port, --preview-reduce, --template vorlage.pts
```

Öffnet den Browser mit Bildliste links (✓ erledigt, – übersprungen), großem Pano und drei Knöpfen: Zurück (P/←), Überspringen (S), Speichern & weiter (E/Enter; erst ab 3 Linien). N/→ = nächstes. Linien werden auf dem **Original** gesetzt; das kleine Bild „Ergebnis“ zeigt das Resultat (V = groß). „?“ öffnet die Anleitung (beim ersten Start automatisch).

- **Zoom:** Mausrad/Pinch zum Mauszeiger, `+`/`−`, `0`/`F` einpassen, `1` = 100 %, Doppelklick zoomt auf die Stelle; Mitteltaste oder Leertaste + Ziehen verschiebt (läuft über die Naht rund); ab ~45 % wird der Ausschnitt in voller Auflösung nachgeladen; beim Setzen zeigt eine 4×-Lupe den Punkt, Endpunkte lassen sich ziehen, Rechtsklick löscht.
- **Export** (⚙): Format wie Original / JPG (Qualität 80–100, Standard 95) / TIFF 16 Bit / PNG, Metadaten per `exiftool` übernehmen (EXIF, GPS, XMP-GPano wird sichergestellt, Ausrichtung als Notiz in `XMP-photoshop:Instructions`), Zusatz `_gerade`, optional `.pts` (braucht `--template`). Bestehende Ergebnisse werden nur nach Rückfrage ersetzt; Originale werden nie verändert (OUT darf nicht der Eingabeordner sein).
- **Dateien in OUT:** gerade Bilder, `panolevel_status.json` (Neustart macht beim ersten offenen Bild weiter), `panolevel_bericht.csv` (Datei, Status, Linien, Winkel, Restfehler, Bewertung, Zeit).
- **Grenzen:** Das Tool bindet nur 127.0.0.1 und prüft den Host-Header; 16k-Rotation braucht ~4 GB RAM (immer nur ein Bild); JPG/PNG werden 8 Bit gelesen (TIFF 16 Bit ist ein 16-Bit-Container); keine Kugelansicht.

## Benutzung

```bash
# Pano MIT vorhandenem .pts: Auto-Linien in eine Kopie injizieren
python -m panolevel autolevel --image pano.png --pts "pano Panorama.pts"

# Pano OHNE .pts: vollständiges PTGui-Projekt aus einem Settings-Template bauen
python -m panolevel autolevel --image pano.png --template "vorlage Panorama.pts"

# Output-Format überschreiben (Default ist tiff/u16)
python -m panolevel autolevel --image pano.png --template t.pts --output-format jpeg

# Ganzen Ordner in einem Lauf (paart X.png mit "X Panorama.pts", Rest aus Template)
python -m panolevel batch --folder stitched/ --template vorlage.pts --out-dir stitched/autolevel
```

Erzeugt neben dem Bild `pano.autolevel.pts` (PTGui-ladbar) und ein Overlay-PNG zur Sicht-Prüfung. Der Report (JSON/Text) nennt Linienzahl, Spaltenverteilung, Konfidenz und das `unsicher`-Flag.

## Workflow (kein PTGui-Aufruf nötig)

PanoLevel ruft PTGui **nie** selbst auf. Der Ablauf ist halb-automatisch:

1. `autolevel` laufen lassen → `*.autolevel.pts` + Overlay entstehen.
2. **Overlay prüfen:** zeigt das verkleinerte Pano mit den vorgeschlagenen Linien (rot) und dem Suchband (grün). Sitzen die Linien an echten Vertikalen? Bei `unsicher` besonders kritisch hinschauen.
3. **In PTGui:** die `*.autolevel.pts` öffnen, im Control-Point-Tab offensichtlich falsche Linien löschen (prunen), **Optimieren** drücken — PTGui rückt das Pano gerade.

Eine falsch erkannte Linie kann PTGui auf eine falsche Lage optimieren — darum ist das Overlay-Prüfen Pflicht, bevor optimiert wird.

## Leitplanken

- Input: Equirect-Pano (2:1) **+** sein PTGui-`.pts` (JSON, PTGui ≥ 11).
- **Non-destruktiv:** schreibt nur in `<name>.autolevel.pts`, nie ins Original.
- **Confirm ist Pflicht:** eine falsch erkannte Linie kann PTGui auf eine falsche Lage optimieren → Overlay immer prüfen.
- OSS, Linux-first, CLI-skriptbar; kein PTGui zur Laufzeit nötig.

## Lizenz

MIT — siehe [LICENSE](LICENSE).
