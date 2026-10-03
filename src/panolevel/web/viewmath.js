/* Reine Zoom-/Pan-Mathematik fuer den Equirect-Betrachter (ohne DOM, per node testbar).
 *
 * View v = {s, cx, cy}: s = Bildschirm-Pixel je Bild-Pixel, (cx, cy) = Bildpunkt in der
 * Fenstermitte. Bild-Koordinaten sind die der Vorschau; x laeuft nahtlos um (Breite imgW).
 */
(function (root) {
  "use strict";

  function wrapX(x, w) { return ((x % w) + w) % w; }

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

  function fitView(imgW, imgH, vw, vh) {
    return { s: Math.min(vw / imgW, vh / imgH), cx: imgW / 2, cy: imgH / 2 };
  }

  function toScreen(v, vw, vh, x, y) {
    return [(x - v.cx) * v.s + vw / 2, (y - v.cy) * v.s + vh / 2];
  }

  function toImage(v, vw, vh, sx, sy) {
    return [(sx - vw / 2) / v.s + v.cx, (sy - vh / 2) / v.s + v.cy];
  }

  /* cx umlaufen lassen, cy so begrenzen, dass die Bildmitte-Linie im Bild bleibt. */
  function normalize(v, imgW, imgH) {
    return { s: v.s, cx: wrapX(v.cx, imgW), cy: clamp(v.cy, 0, imgH) };
  }

  /* Zoom um `factor`, der Bildpunkt unter dem Zeiger (sx, sy) bleibt unter dem Zeiger. */
  function zoomAt(v, vw, vh, sx, sy, factor, minS, maxS, imgW, imgH) {
    const s = clamp(v.s * factor, minS, maxS);
    const [ix, iy] = toImage(v, vw, vh, sx, sy);
    const nv = { s, cx: ix - (sx - vw / 2) / s, cy: iy - (sy - vh / 2) / s };
    return normalize(nv, imgW, imgH);
  }

  /* Verschieben um (dx, dy) Bildschirm-Pixel; horizontal mit Umlauf. */
  function panBy(v, dx, dy, imgW, imgH) {
    return normalize({ s: v.s, cx: v.cx - dx / v.s, cy: v.cy - dy / v.s }, imgW, imgH);
  }

  /* Zoomstufe so setzen, dass Bildpunkt (ix, iy) in der Fenstermitte liegt. */
  function centerOn(v, ix, iy, s, minS, maxS, imgW, imgH) {
    return normalize({ s: clamp(s, minS, maxS), cx: ix, cy: iy }, imgW, imgH);
  }

  /* Zoom in Prozent des Originals; R = Original-Pixel je Vorschau-Pixel. */
  function zoomPercent(v, R) { return v.s / R * 100; }
  function scaleForPercent(pct, R) { return pct / 100 * R; }

  /* Sichtbares Rechteck in Bild-Koordinaten (x ggf. ausserhalb [0, imgW) wegen Umlauf). */
  function visibleRect(v, vw, vh) {
    return { x0: v.cx - vw / 2 / v.s, y0: v.cy - vh / 2 / v.s,
             x1: v.cx + vw / 2 / v.s, y1: v.cy + vh / 2 / v.s };
  }

  /* Welche Kacheln k (Verschiebung k*imgW) schneiden das sichtbare Rechteck? */
  function tileShifts(v, vw, imgW) {
    const r = visibleRect(v, vw, 1);
    const out = [];
    for (let k = Math.floor(r.x0 / imgW); k <= Math.floor((r.x1 - 1e-9) / imgW); k++) out.push(k);
    return out;
  }

  /* x2 so verschieben (um Vielfache von w), dass |x2 - x1| <= w/2: kuerzester Weg ueber die Naht. */
  function shortestX(x1, x2, w) {
    let d = wrapX(x2 - x1, w);
    if (d > w / 2) d -= w;
    return x1 + d;
  }

  /* Linie in kanonische Form: Endpunkt 2 auf kuerzestem Weg, Endpunkt 1 in [0, w). */
  function canonLine(l, w, h) {
    const x1 = wrapX(l[0], w), x2 = shortestX(l[0], l[2], w) - (l[0] - x1);
    return [x1, clamp(l[1], 0, h), x2, clamp(l[3], 0, h)];
  }

  /* Fuer den Server: beide Endpunkte in [0, w). */
  function wrapLineForSend(l, w, h) {
    return [wrapX(l[0], w), clamp(l[1], 0, h), wrapX(l[2], w), clamp(l[3], 0, h)];
  }

  /* Abstand Punkt (px, py) zu Strecke (ax, ay)-(bx, by). */
  function distToSegment(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy;
    const t = L === 0 ? 0 : clamp(((px - ax) * dx + (py - ay) * dy) / L, 0, 1);
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }

  const api = { wrapX, clamp, fitView, toScreen, toImage, normalize, zoomAt, panBy, centerOn,
                zoomPercent, scaleForPercent, visibleRect, tileShifts, shortestX, canonLine,
                wrapLineForSend, distToSegment };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ViewMath = api;
})(typeof self !== "undefined" ? self : this);
