"use strict";
const VM = ViewMath;
const $ = (id) => document.getElementById(id);
const stage = $("stage"), cv = $("view"), ctx = cv.getContext("2d");
const loupe = $("loupe"), lctx = loupe.getContext("2d");
const pip = $("pip"), pctx = pip.getContext("2d");
const statusEl = $("status");

// ---- Zustand ------------------------------------------------------------------
let base = new Image();        // Original-Vorschau (hier werden Linien gesetzt)
let result = new Image();      // gerades Ergebnis (nur Ansicht)
let hasBase = false, hasResult = false;
let iw = 1024, ih = 512;       // Vorschau-Groesse = Bild-Koordinaten
let R = 1;                     // Original-Pixel je Vorschau-Pixel
let lines = [];                // [[x1,y1,x2,y2]] Vorschau-Koordinaten (x ggf. ausserhalb, umlaufend)
let pending = null;            // erster Klick einer neuen Linie
let yaw = 0;
let view = { s: 1, cx: 512, cy: 256 };
let fitMode = true;
let showResult = false;
let hires = null;              // {bmp, x0,y0,x1,y1}
let hiresTok = 0, hiresTimer = null;
let folderMode = false, items = [], cur = -1, seq = 0, busy = false;
let settings = null, outDir = "";
let drag = null;               // {kind:'pan'|'yaw'|'end', ...}
let space = false, pointer = null, acts = [];
const MIN_LINES = 3, HIRES_FROM = 0.45, ENDPOINT_PX = 10, MAX_SCALE_PCT = 800;

function setStatus(t) { statusEl.textContent = t; }
function sz() { return [cv.width, cv.height]; }
function minS() { return VM.fitView(iw, ih, cv.width, cv.height).s; }
function maxS() { return Math.max(minS(), VM.scaleForPercent(MAX_SCALE_PCT, R)); }

async function post(url, body) {
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : null });
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (e) {}
      return { ok: false, reason: String(msg) };
    }
    return await r.json();
  } catch (e) { return { ok: false, reason: "keine Verbindung zum Programm" }; }
}
const base_ = () => (folderMode ? `/api/folder/${cur}` : "");
function sendLines() { return lines.map((l) => VM.wrapLineForSend(l, iw, ih)); }

function toast(text, err) {
  const t = $("toast");
  t.textContent = text; t.className = "show" + (err ? " err" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.className = ""; }, err ? 4500 : 1700);
}

// ---- Zeichnen -------------------------------------------------------------------
let raf = 0;
function invalidate() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; render(); }); }

function drawScene(c, v, vw, vh, thin) {
  c.fillStyle = "#000"; c.fillRect(0, 0, vw, vh);
  const src = showResult ? (hasResult ? result : null) : (hasBase ? base : null);
  if (src) {
    for (const k of VM.tileShifts(v, vw, iw)) {
      const [sx, sy] = VM.toScreen(v, vw, vh, k * iw, 0);
      c.drawImage(src, sx, sy, iw * v.s, ih * v.s);
    }
  }
  if (!showResult && hires) {
    const [sx, sy] = VM.toScreen(v, vw, vh, hires.x0, hires.y0);
    c.drawImage(hires.bmp, sx, sy, (hires.x1 - hires.x0) * v.s, (hires.y1 - hires.y0) * v.s);
  }
  if (showResult) return;
  c.lineWidth = thin ? 1 : 2;
  const shifts = VM.tileShifts(v, vw, iw);
  lines.forEach((l, idx) => {
    c.strokeStyle = "#ff3b3b";
    for (const k of shifts.concat([shifts[0] - 1, shifts[shifts.length - 1] + 1])) {
      const [ax, ay] = VM.toScreen(v, vw, vh, l[0] + k * iw, l[1]);
      const [bx, by] = VM.toScreen(v, vw, vh, l[2] + k * iw, l[3]);
      c.beginPath(); c.moveTo(ax, ay); c.lineTo(bx, by); c.stroke();
      if (!thin) {
        c.fillStyle = "#ffd479";
        for (const [x, y] of [[ax, ay], [bx, by]]) { c.beginPath(); c.arc(x, y, 3.5, 0, 7); c.fill(); }
      }
    }
  });
  if (pending) {
    for (const k of shifts) {
      const [x, y] = VM.toScreen(v, vw, vh, pending[0] + k * iw, pending[1]);
      c.fillStyle = "#ffd479"; c.beginPath(); c.arc(x, y, 5, 0, 7); c.fill();
    }
  }
}

function render() {
  const [vw, vh] = sz();
  drawScene(ctx, view, vw, vh, false);
  $("zoom").textContent = `Zoom ${Math.round(VM.zoomPercent(view, R))} %`;
  // kleines Ergebnisbild
  if (hasResult) {
    pctx.clearRect(0, 0, pip.width, pip.height);
    pctx.drawImage(result, 0, 0, pip.width, pip.height);
  }
  renderLoupe();
  updateSave();
  scheduleHires();
}

function renderLoupe() {
  const want = !showResult && pointer && (pending || (drag && drag.kind === "end"));
  loupe.style.display = want ? "block" : "none";
  if (!want) return;
  const [ix, iy] = VM.toImage(view, cv.width, cv.height, pointer[0], pointer[1]);
  const lv = { s: view.s * 4, cx: ix, cy: iy };
  drawScene(lctx, lv, loupe.width, loupe.height, true);
  lctx.strokeStyle = "#ffd479"; lctx.lineWidth = 1;
  lctx.beginPath();
  lctx.moveTo(loupe.width / 2, 0); lctx.lineTo(loupe.width / 2, loupe.height);
  lctx.moveTo(0, loupe.height / 2); lctx.lineTo(loupe.width, loupe.height / 2);
  lctx.stroke();
  const lw = loupe.width + 4;
  let x = pointer[0] + 24, y = pointer[1] + 24;
  if (x + lw > cv.width) x = pointer[0] - lw - 24;
  if (y + lw > cv.height) y = pointer[1] - lw - 24;
  loupe.style.left = Math.max(0, x) + "px"; loupe.style.top = Math.max(0, y) + "px";
}

function resize() {
  const w = Math.max(200, stage.clientWidth), h = Math.max(200, stage.clientHeight);
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  if (fitMode) view = VM.fitView(iw, ih, w, h);
  invalidate();
}
new ResizeObserver(resize).observe(stage);

function setView(v, keepFit) {
  view = VM.normalize(v, iw, ih);
  fitMode = !!keepFit;
  if (fitMode) view = VM.fitView(iw, ih, cv.width, cv.height);
  invalidate();
}
function zoomBy(factor, sx, sy) {
  const [vw, vh] = sz();
  if (sx === undefined) { sx = vw / 2; sy = vh / 2; }
  setView(VM.zoomAt(view, vw, vh, sx, sy, factor, minS(), maxS(), iw, ih));
}
function zoomTo(pct) {
  const [vw, vh] = sz();
  const [ix, iy] = VM.toImage(view, vw, vh, vw / 2, vh / 2);
  setView(VM.centerOn(view, ix, iy, VM.scaleForPercent(pct, R), minS(), maxS(), iw, ih));
}

// ---- Volle Aufloesung nachladen ------------------------------------------------------
function scheduleHires() {
  if (!folderMode || showResult || cur < 0) return;
  clearTimeout(hiresTimer);
  if (view.s / R < HIRES_FROM) { if (hires) { hires = null; } return; }
  hiresTimer = setTimeout(loadHires, 140);
}

async function loadHires() {
  const [vw, vh] = sz();
  const r = VM.visibleRect(view, vw, vh);
  const padx = (r.x1 - r.x0) * 0.1, pady = (r.y1 - r.y0) * 0.1;
  let x0 = r.x0 - padx, x1 = r.x1 + padx;
  if (x1 - x0 > iw) { const c = (x0 + x1) / 2; x0 = c - iw / 2; x1 = c + iw / 2; }
  const y0 = Math.max(0, r.y0 - pady), y1 = Math.min(ih, r.y1 + pady);
  if (hires && hires.x0 <= x0 && hires.x1 >= x1 && hires.y0 <= y0 && hires.y1 >= y1
      && hires.scale >= view.s * 0.9) return;                // vorhandener Ausschnitt reicht
  const my = ++hiresTok, idx = cur, w = Math.min(2600, Math.ceil((x1 - x0) * view.s));
  const q = new URLSearchParams({ x0, y0, x1, y1, w });
  try {
    const resp = await fetch(`/api/folder/${idx}/crop?${q}`);
    if (!resp.ok || my !== hiresTok || idx !== cur) return;
    const rect = resp.headers.get("X-Rect").split(",").map(Number);
    const bmp = await createImageBitmap(await resp.blob());
    if (my !== hiresTok || idx !== cur) return;
    hires = { bmp, x0: rect[0], y0: rect[1], x1: rect[2], y1: rect[3], scale: view.s };
    invalidate();
  } catch (e) { /* Ausschnitt ist nur Komfort */ }
}

// ---- Linien / Maus ------------------------------------------------------------------
function canvasXY(e) {
  const r = cv.getBoundingClientRect();
  return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height];
}
function imgXY(sx, sy) { return VM.toImage(view, cv.width, cv.height, sx, sy); }

function hitEndpoint(sx, sy) {
  let best = null, bd = ENDPOINT_PX;
  lines.forEach((l, li) => {
    for (const ep of [0, 1]) for (const k of [-1, 0, 1]) {
      const [x, y] = VM.toScreen(view, cv.width, cv.height, l[ep * 2] + k * iw, l[ep * 2 + 1]);
      const d = Math.hypot(x - sx, y - sy);
      if (d < bd) { bd = d; best = { li, ep, k }; }
    }
  });
  return best;
}

function nearestLine(sx, sy) {
  let best = -1, bd = 14;
  lines.forEach((l, i) => {
    for (const k of [-1, 0, 1]) {
      const [ax, ay] = VM.toScreen(view, cv.width, cv.height, l[0] + k * iw, l[1]);
      const [bx, by] = VM.toScreen(view, cv.width, cv.height, l[2] + k * iw, l[3]);
      const d = VM.distToSegment(sx, sy, ax, ay, bx, by);
      if (d < bd) { bd = d; best = i; }
    }
  });
  return best;
}

function place(sx, sy) {
  const [x, y] = imgXY(sx, sy);
  const act = { t: Date.now(), prevPending: pending, added: false };
  if (!pending) { pending = [x, y]; }
  else {
    const [px, py] = VM.toScreen(view, cv.width, cv.height, pending[0], pending[1]);
    if (Math.hypot(px - sx, py - sy) < 4) return;             // zu kurz: ignorieren
    lines.push(VM.canonLine([pending[0], pending[1], x, y], iw, ih));
    pending = null; act.added = true;
  }
  acts.push(act); if (acts.length > 6) acts.shift();
  invalidate();
  if (act.added) estimate();
}

cv.addEventListener("mousedown", (e) => {
  const [sx, sy] = canvasXY(e);
  pointer = [sx, sy];
  if (e.button === 1 || (e.button === 0 && space)) {
    drag = { kind: "pan", lx: sx, ly: sy }; document.body.classList.add("panning"); e.preventDefault(); return;
  }
  if (e.button !== 0 || busy) return;
  if (e.shiftKey) { drag = { kind: "yaw", x0: sx, yaw0: yaw }; return; }
  if (showResult) return;
  const h = hitEndpoint(sx, sy);
  if (h) { drag = { kind: "end", ...h, moved: false }; return; }
  drag = { kind: "click", x0: sx, y0: sy };
});

window.addEventListener("mousemove", (e) => {
  const [sx, sy] = canvasXY(e);
  pointer = [sx, sy];
  if (!drag) { if (pending) invalidate(); return; }
  if (drag.kind === "pan") {
    setView(VM.panBy(view, sx - drag.lx, sy - drag.ly, iw, ih));
    drag.lx = sx; drag.ly = sy;
  } else if (drag.kind === "yaw") {
    yaw = drag.yaw0 + (sx - drag.x0) / cv.width * 360;
    yaw = ((yaw + 180) % 360 + 360) % 360 - 180;
    estimateSoon();
  } else if (drag.kind === "end") {
    drag.moved = true;
    const [x, y] = imgXY(sx, sy);
    const l = lines[drag.li];
    l[drag.ep * 2] = x; l[drag.ep * 2 + 1] = Math.min(ih, Math.max(0, y));
    invalidate();
  } else if (drag.kind === "click" && Math.hypot(sx - drag.x0, sy - drag.y0) > 5) {
    drag.kind = "none";
  }
});

window.addEventListener("mouseup", (e) => {
  if (!drag) return;
  const d = drag; drag = null;
  document.body.classList.remove("panning");
  if (d.kind === "end" && d.moved) {
    lines[d.li] = VM.canonLine(lines[d.li], iw, ih);
    estimate();
  } else if (d.kind === "click" && e.button === 0) {
    place(d.x0, d.y0);
  }
  invalidate();
});

cv.addEventListener("dblclick", (e) => {
  if (e.shiftKey) return;
  const now = Date.now();
  let changed = false;
  for (let n = 0; n < 2 && acts.length && now - acts[acts.length - 1].t < 700; n++) {
    const a = acts.pop();
    if (a.added) { lines.pop(); changed = true; }
    pending = a.prevPending;
  }
  const [sx, sy] = canvasXY(e), [ix, iy] = imgXY(sx, sy);
  const target = VM.zoomPercent(view, R) < 100 ? 100 : 0;
  if (target) setView(VM.centerOn(view, ix, iy, VM.scaleForPercent(target, R), minS(), maxS(), iw, ih));
  else setView(view, true);
  if (changed) estimate();
});

cv.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (busy || showResult) return;
  const [sx, sy] = canvasXY(e);
  const i = nearestLine(sx, sy);
  if (i >= 0) { lines.splice(i, 1); estimate(); invalidate(); }
  else if (pending) { pending = null; invalidate(); }
});

cv.addEventListener("wheel", (e) => {
  e.preventDefault();
  const [sx, sy] = canvasXY(e);
  let dy = e.deltaY;
  if (e.deltaMode === 1) dy *= 16; else if (e.deltaMode === 2) dy *= cv.height;
  zoomBy(Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0015)), sx, sy);
}, { passive: false });

// ---- Ergebnis berechnen --------------------------------------------------------------
let estTimer = 0;
function estimateSoon() { clearTimeout(estTimer); estTimer = setTimeout(estimate, 60); }

function setQuality(q) {
  const el = $("quality");
  if (!folderMode || !q) { el.textContent = ""; el.className = ""; return; }
  el.className = q;
  el.textContent = q === "gut" ? "Ausrichtung: gut ✓"
    : "Bitte prüfen – die Linien widersprechen sich";
  el.title = q === "gut" ? "Die gesetzten Linien passen gut zusammen."
    : "Eine Linie prüfen (nur echte Senkrechte?) oder mehr Linien rund ums Bild setzen.";
}

function setResultImage(url) {
  return new Promise((res) => { result.onload = () => { hasResult = true; invalidate(); res(); }; result.src = url; });
}

async function estimate() {
  const my = seq;
  const d = await post(base_() + "/estimate", { lines: sendLines(), yaw });
  if (my !== seq) return;
  if (d.ok) {
    $("angles").textContent = `roll ${d.roll.toFixed(2)}  pitch ${d.pitch.toFixed(2)}  yaw ${yaw.toFixed(1)}`;
    setQuality(d.quality);
    await setResultImage(d.preview); setStatus("");
  } else {
    setQuality(null);
    setStatus(folderMode ? "" : (d.reason || "zu wenige Linien"));
  }
  invalidate();
}

function setShowResult(v) {
  showResult = v;
  document.body.classList.toggle("result", v);
  $("viewlabel").textContent = "Ergebnis (nur ansehen) – V zum Zurückkehren";
  if (v) hires = null;
  invalidate();
}
$("pipbox").onclick = () => setShowResult(true);

// ---- Einzelbild-Modus ------------------------------------------------------------------
function applyImage(d) {
  iw = d.width; ih = d.height;
  R = d.full_width ? d.full_width / d.width : 1;
  hires = null; hiresTok++; hasResult = false; acts = [];
  fitMode = true; view = VM.fitView(iw, ih, cv.width, cv.height);
  return new Promise((res) => { base.onload = () => { hasBase = true; invalidate(); res(); }; base.src = d.preview; });
}

$("reset").onclick = async () => {
  if (folderMode) return;
  const d = await post("/detect");
  if (!d.ok) { setStatus(d.reason || "Fehler"); return; }
  lines = d.lines; pending = null; yaw = 0;
  await applyImage(d); estimate();
};

async function exportAs(mode) {
  setStatus("Export läuft…");
  const d = await post("/export", { lines: sendLines(), yaw, mode });
  setStatus(d.ok ? `Gespeichert: ${d.output}` : `Fehler: ${d.reason}`);
}
$("exportPng").onclick = () => exportAs("png");
$("exportPts").onclick = () => exportAs("pts");

// ---- Ordner-Modus ----------------------------------------------------------------------
const listEl = $("list");
const MARK = { done: "✓", skipped: "–", open: "·" };

function renderList() {
  const done = items.filter((x) => x.status === "done").length;
  $("progress").textContent = `${Math.max(cur + 1, 1)} von ${items.length} · ${done} erledigt`;
  listEl.innerHTML = "";
  items.forEach((it, i) => {
    const li = document.createElement("li");
    li.className = `${it.status}${i === cur ? " current" : ""}`;
    li.innerHTML = `<span class="mark">${MARK[it.status]}</span><span></span>`;
    li.lastChild.textContent = it.name;
    li.title = { done: "erledigt", skipped: "übersprungen", open: "offen" }[it.status];
    li.onclick = () => go(i);
    listEl.appendChild(li);
  });
  const c = listEl.children[cur];
  if (c) c.scrollIntoView({ block: "nearest" });
}

function updateSave() {
  if (!folderMode) return;
  const need = MIN_LINES - lines.length;
  $("save").disabled = busy || need > 0;
  const hint = need > 0 ? `noch ${need} ${need === 1 ? "Linie" : "Linien"} nötig` : "E / Enter";
  const html = `Speichern &amp; weiter → <small>${hint}</small>`;
  if ($("save").innerHTML !== html) $("save").innerHTML = html;
  $("prev").disabled = busy || cur <= 0;
  $("skip").disabled = busy;
}

async function go(i) {
  if (busy || i < 0 || i >= items.length) return;
  busy = true; const my = ++seq; cur = i; renderList(); updateSave();
  setStatus("Lade Bild…");
  lines = []; pending = null; yaw = 0; hasResult = false; hires = null; setQuality(null);
  setShowResult(false);
  try {
    const d = await post(`${base_()}/load`);
    if (my !== seq) return;
    if (!d.ok) { toast(`Fehler: ${d.reason}`, true); return; }
    lines = d.lines;
    await applyImage(d);
    setStatus("");
  } finally {
    if (my === seq) { busy = false; updateSave(); }
  }
  if (my === seq) estimate();
}

async function save(overwrite) {
  if (busy || lines.length < MIN_LINES || cur < 0) return;
  busy = true; updateSave(); setStatus("Speichere…");
  const my = cur;
  let d;
  try {
    d = await post(`${base_()}/export`, { lines: sendLines(), yaw, overwrite: !!overwrite });
  } finally { busy = false; setStatus(""); updateSave(); }
  if (!d.ok && d.exists) {
    if (window.confirm(`„${d.name}“ gibt es im Ergebnisordner schon.\nErsetzen?`)) return save(true);
    return;
  }
  if (!d.ok) { toast(`Fehler: ${d.reason}`, true); return; }
  items[my].status = "done";
  toast(`gespeichert: ${d.name}` + (d.warning ? "  (Metadaten: Hinweis im Bericht)" : ""));
  if (my + 1 < items.length) go(my + 1);
  else { renderList(); toast("Alle Bilder durch – fertig"); }
}

async function skip() {
  if (busy || cur < 0) return;
  const d = await post(`${base_()}/skip`);
  if (!d.ok) { toast(`Fehler: ${d.reason}`, true); return; }
  items[cur].status = "skipped";
  if (cur + 1 < items.length) go(cur + 1); else renderList();
}

$("prev").onclick = () => go(cur - 1);
$("skip").onclick = skip;
$("save").onclick = () => save(false);

// ---- Einstellungen + Hilfe ---------------------------------------------------------------
function showSettings() {
  $("set-format").value = settings.format; $("set-quality").value = settings.jpg_quality;
  $("set-meta").checked = settings.keep_metadata; $("set-suffix").checked = settings.suffix;
  $("set-pts").checked = settings.pts; $("set-pts").disabled = !settings.pts_available;
  $("set-pts-note").textContent = settings.pts_available ? ""
    : "Für das PTGui-Projekt das Programm mit --template DATEI.pts starten.";
  $("set-exif-note").textContent = settings.exiftool ? ""
    : "exiftool ist nicht installiert – Metadaten können nicht übernommen werden.";
  $("set-out").textContent = outDir;
  $("settings").hidden = false;
}
async function pushSettings(patch) {
  const d = await post("/api/settings", patch);
  if (d.ok) Object.assign(settings, d.settings); else toast(d.reason || "Fehler", true);
  showSettings(); $("settings").hidden = false;
}
$("gear").onclick = showSettings;
$("settingsClose").onclick = () => { $("settings").hidden = true; };
$("set-format").onchange = (e) => pushSettings({ format: e.target.value });
$("set-quality").onchange = (e) => pushSettings({ jpg_quality: parseInt(e.target.value, 10) });
$("set-meta").onchange = (e) => pushSettings({ keep_metadata: e.target.checked });
$("set-suffix").onchange = (e) => pushSettings({ suffix: e.target.checked });
$("set-pts").onchange = (e) => pushSettings({ pts: e.target.checked });
$("openOut").onclick = async () => { const d = await post("/api/open-out"); toast(d.ok ? "Ordner geöffnet" : d.reason, !d.ok); };
$("reportBtn").onclick = async () => { const d = await post("/api/report"); toast(d.ok ? `Bericht gespeichert: ${d.path}` : d.reason, !d.ok); };

const modalOpen = () => !$("settings").hidden || !$("helpdlg").hidden;
function showHelp() { $("helpdlg").hidden = false; }
function closeHelp() {
  $("helpdlg").hidden = true;
  try { localStorage.setItem("panolevel_help_seen", "1"); } catch (e) { /* privat/gesperrt */ }
}
$("helpBtn").onclick = showHelp;
$("helpClose").onclick = closeHelp;
for (const b of document.querySelectorAll("button")) b.addEventListener("click", () => b.blur());

// ---- Tastatur -----------------------------------------------------------------------------
window.addEventListener("keydown", (e) => {
  const t = e.target, tag = t && t.tagName;
  if (e.key === "Escape") { $("settings").hidden = true; if (!$("helpdlg").hidden) closeHelp(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) return;
  if (modalOpen()) return;
  const k = e.key.toLowerCase();
  if (k === " ") { space = true; document.body.classList.add("space"); e.preventDefault(); return; }
  if (k === "+" || k === "=") zoomBy(1.25);
  else if (k === "-" || k === "_") zoomBy(1 / 1.25);
  else if (k === "0" || k === "f") setView(view, true);
  else if (k === "1") zoomTo(100);
  else if (k === "v") setShowResult(!showResult);
  else if (k === "?" || k === "h") showHelp();
  else if (folderMode && (k === "n" || k === "arrowright")) go(cur + 1);
  else if (folderMode && (k === "p" || k === "arrowleft")) go(cur - 1);
  else if (folderMode && k === "s") skip();
  else if (folderMode && (k === "e" || k === "enter")) save(false);
  else return;
  e.preventDefault();
});
window.addEventListener("keyup", (e) => {
  if (e.key === " ") { space = false; document.body.classList.remove("space"); }
});

// ---- Start ----------------------------------------------------------------------------------------
async function init() {
  resize();
  let r;
  try { r = await fetch("/api/folder"); } catch (e) { r = { ok: false }; }
  if (r.ok) {
    folderMode = true; document.body.classList.add("folder");
    const d = await r.json();
    items = d.images; outDir = d.out_dir;
    settings = { ...d.settings, pts_available: d.pts_available, exiftool: d.exiftool };
    document.title = "PanoLevel – " + d.folder.split("/").pop();
    updateSave();
    let seen = false;
    try { seen = localStorage.getItem("panolevel_help_seen") === "1"; } catch (e) {}
    if (!seen) showHelp();
    go(d.first_open);
  } else {
    $("reset").click();   // initial auto-detect on load
  }
}
init();
