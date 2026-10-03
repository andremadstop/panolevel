# PTGui `.pts`-Format — verifizierte Notizen (2026-06-17)

Quelle: echte PTGui-13.9-Projekte (Ein-Bild-Equirect-Re-Import)
(PTGui Pro 13.9, `$schema` project_v55, `compatibleapplication` PTGui 11).

## Grundform

- Reines **JSON**. Zahlen teils in E-Notation (`3.6e2` = 360). Beim Zurückschreiben PTGui-tolerant bleiben (normales JSON lädt PTGui ebenfalls — in Phase 1 verifizieren).
- Top-Level: `project` (alles Wesentliche) + `assets` (eingebettete ICC-Profile o.ä.).

## Ein-Bild-Equirect-Projekt (Andrés Re-Import-Trick)

Das fertige Pano wird als **ein** Bild mit equirektangularer „Linse" re-importiert:

```
project.globallenses[0].lens.params.projection = "equirectangular", hfov = 360
project.imagegroups            -> genau 1 Eintrag
project.imagegroups[0].size    -> [16384, 8192]
project.imagegroups[0].images[0].filename -> "<name>.png"
```

## Globale Ausrichtung (Weg A)

```
project.imagegroups[0].position.params = { yaw, pitch, roll, vpx,vpy,vpd,vppan,vptilt }
```
- `pitch` + `roll` = das Kippen. Direkt setzen ⇒ Pano gerade, **ohne** Optimize.
- `optimizerflags.{yaw,pitch,roll}` steuern, ob der Optimizer sie verändert.
- Beobachtet: `13.44.31` roll 0.82 / pitch −0.34 (in Arbeit); `10.43.40` nach Optimize roll −0.10 / pitch 0.13 (≈ gerade).

## Vertical-Control-Points (Weg B — v1)

```
project.controlpoints = [
  {"t":1, "0":[ig, img, x, y], "1":[ig, img, x, y]},
  ...
]
```
- `t`: `0` = normaler CP, `1` = **vertical line**, `2` = horizontal line.
- `"0"`/`"1"` = die zwei Linien-Endpunkte: `[imagegroup_index, image_index, x_px, y_px]`.
- Da Ein-Bild-Projekt: `ig=0, img=0`, und **x/y sind direkte Pixel im Equirect** (volle Auflösung, hier 16384×8192) — kein Rückmapping nötig.
- Echte Beispiele (`10.43.40`, 4 Linien, über die Breite verteilt, y≈3200–3800 auf 8192 = nahe/leicht über Horizont):
  ```
  {"t":1,"0":[0,0,716,3379], "1":[0,0,718,3774]}
  {"t":1,"0":[0,0,3402,3202],"1":[0,0,3401,3439]}
  {"t":1,"0":[0,0,10158,3427],"1":[0,0,10155,3291]}
  {"t":1,"0":[0,0,14312,3444],"1":[0,0,14313,3241]}
  ```

## Konsequenz fürs Tool

- **v1:** detektierte Vertikale als `t:1`-Einträge in `project.controlpoints` einer `.pts`-**Kopie** schreiben → PTGui-Optimizer macht roll/pitch. Trivial, nutzt PTGuis bewährte Stabilität.
- **Offen (Phase-1-Gate):** ob die geschriebene Kopie in PTGui sauber lädt + optimiert — echter Round-Trip nötig (PTGui auf kinder-pc).
