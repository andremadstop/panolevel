import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const V = createRequire(import.meta.url)("../src/panolevel/web/viewmath.js");

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
const W = 1024, H = 512, VW = 800, VH = 600;

test("wrapX", () => {
  near(V.wrapX(-1, W), W - 1); near(V.wrapX(W, W), 0); near(V.wrapX(2.5 * W, W), 0.5 * W);
});

test("fitView passt ganzes Bild ein und zentriert", () => {
  const v = V.fitView(W, H, VW, VH);
  near(v.s, VW / W); near(v.cx, W / 2); near(v.cy, H / 2);
});

test("Bild <-> Bildschirm ist umkehrbar", () => {
  const v = { s: 2.5, cx: 300, cy: 200 };
  for (const [x, y] of [[0, 0], [123.4, 55.5], [900, 500]]) {
    const [sx, sy] = V.toScreen(v, VW, VH, x, y);
    const [ix, iy] = V.toImage(v, VW, VH, sx, sy);
    near(ix, x, 1e-9); near(iy, y, 1e-9);
  }
  const [sx, sy] = V.toScreen(v, VW, VH, v.cx, v.cy);
  near(sx, VW / 2); near(sy, VH / 2);
});

test("zoomAt haelt den Bildpunkt unter dem Zeiger fest", () => {
  let v = V.fitView(W, H, VW, VH);
  const [px, py] = [610, 140];
  const before = V.toImage(v, VW, VH, px, py);
  for (const f of [1.5, 2, 0.8, 3]) {
    v = V.zoomAt(v, VW, VH, px, py, f, 0.1, 50, W, H);
    const now = V.toImage(v, VW, VH, px, py);
    near(V.wrapX(now[0], W), V.wrapX(before[0], W), 1e-6);
    near(now[1], before[1], 1e-6);
  }
});

test("zoomAt begrenzt Zoomstufe", () => {
  let v = V.fitView(W, H, VW, VH);
  v = V.zoomAt(v, VW, VH, 400, 300, 1e6, 0.5, 8, W, H); near(v.s, 8);
  v = V.zoomAt(v, VW, VH, 400, 300, 1e-9, 0.5, 8, W, H); near(v.s, 0.5);
});

test("zoomAt zur Mitte aendert Mitte nicht", () => {
  const v = V.zoomAt({ s: 1, cx: 400, cy: 250 }, VW, VH, VW / 2, VH / 2, 2, 0.1, 20, W, H);
  near(v.cx, 400); near(v.cy, 250);
});

test("panBy: Ziehen nach rechts zeigt weiter links, Umlauf ueber die Naht", () => {
  let v = { s: 2, cx: 10, cy: 250 };
  v = V.panBy(v, 100, 0, W, H);          // 100 px nach rechts -> Mitte 50 Bildpixel nach links
  near(v.cx, W - 40);                     // 10 - 50 = -40 -> umgelaufen
  v = V.panBy(v, -200, 0, W, H);
  near(v.cx, 60);                         // zurueck ueber die Naht
});

test("panBy begrenzt vertikal auf das Bild", () => {
  let v = V.panBy({ s: 2, cx: 100, cy: 10 }, 0, 10000, W, H); near(v.cy, 0);
  v = V.panBy({ s: 2, cx: 100, cy: 10 }, 0, -10000, W, H); near(v.cy, H);
});

test("100 % entspricht 1 Originalpixel = 1 Bildschirmpixel", () => {
  const R = 8;                            // 16k-Original, Vorschau 2k
  const s = V.scaleForPercent(100, R);
  near(s, 8);                             // 1 Vorschau-Pixel = 8 Bildschirm-Pixel
  near(V.zoomPercent({ s, cx: 0, cy: 0 }, R), 100);
  near(V.zoomPercent({ s: 16, cx: 0, cy: 0 }, R), 200);
  near(V.zoomPercent({ s: 1 / 4, cx: 0, cy: 0 }, R), 1 / 4 / 8 * 100);
});

test("sichtbares Rechteck und Kachel-Verschiebungen", () => {
  const v = { s: 1, cx: 100, cy: 200 };   // zeigt x von -300 bis 500 -> Kacheln -1 und 0
  const r = V.visibleRect(v, VW, VH);
  near(r.x0, -300); near(r.x1, 500); near(r.y0, -100); near(r.y1, 500);
  assert.deepEqual(V.tileShifts(v, VW, W), [-1, 0]);
  assert.deepEqual(V.tileShifts({ s: 1, cx: 512, cy: 0 }, 1024, W), [0]);
});

test("shortestX / canonLine nehmen den kurzen Weg ueber die Naht", () => {
  near(V.shortestX(1010, 5, W), W + 5);
  near(V.shortestX(5, 1010, W), -14);
  const c = V.canonLine([1010, 10, 5, 400], W, H);
  near(c[0], 1010); near(c[2], 1029);
  const c2 = V.canonLine([-5, 10, 3, 400], W, H);
  near(c2[0], 1019); near(c2[2], 1019 + 8);
});

test("wrapLineForSend liegt in [0,w) und kappt y", () => {
  const l = V.wrapLineForSend([1029, -5, -3, 999], W, H);
  near(l[0], 5); near(l[1], 0); near(l[2], W - 3); near(l[3], H);
});

test("distToSegment", () => {
  near(V.distToSegment(5, 5, 0, 0, 10, 0), 5);
  near(V.distToSegment(-3, 4, 0, 0, 10, 0), 5);   // Endpunkt
  near(V.distToSegment(1, 1, 2, 2, 2, 2), Math.SQRT2);  // degeneriert
});
