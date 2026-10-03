"""Ordner-Modus: Bildliste, persistenter Status und Sitzung fuer die Web-UI."""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
STATUS_FILE = "panolevel_status.json"
JPEG_QUALITY = 95
REDUCE_FLAG = {
    1: cv2.IMREAD_COLOR,
    2: cv2.IMREAD_REDUCED_COLOR_2,
    4: cv2.IMREAD_REDUCED_COLOR_4,
    8: cv2.IMREAD_REDUCED_COLOR_8,
}


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name.casefold())]


def list_images(folder) -> list[Path]:
    """Alle Bilder (JPG/PNG/TIF) direkt im Ordner, natuerlich sortiert."""
    folder = Path(folder)
    out = [
        p
        for p in folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in IMAGE_EXTS
    ]
    return sorted(out, key=lambda p: _natural_key(p.name))


def default_out_dir(folder) -> Path:
    folder = Path(folder).resolve()
    return folder.parent / "panolevel_gerade"


def resolve_out_dir(folder, out_dir=None) -> Path:
    """Liefert das Ausgabeverzeichnis; verweigert den Eingabeordner selbst."""
    folder = Path(folder).resolve()
    out = Path(out_dir).resolve() if out_dir else default_out_dir(folder)
    if out == folder:
        raise ValueError(
            "--out-dir darf nicht der Eingabeordner sein (Originale bleiben unveraendert)"
        )
    return out


DEFAULT_SETTINGS = {
    "format": "original",
    "jpg_quality": 95,
    "keep_metadata": True,
    "suffix": False,
    "pts": False,
}
FORMATS = ("original", "jpg", "tiff16", "png")
REPORT_FILE = "panolevel_bericht.csv"
SUFFIX = "_gerade"


def clean_settings(raw: dict, base: dict | None = None) -> dict:
    """Validiert Export-Einstellungen; unbekannte Schluessel werden verworfen."""
    out = dict(base or DEFAULT_SETTINGS)
    fmt = raw.get("format", out["format"])
    if fmt not in FORMATS:
        raise ValueError(f"unbekanntes Format {fmt!r}")
    out["format"] = fmt
    q = raw.get("jpg_quality", out["jpg_quality"])
    if isinstance(q, bool) or not isinstance(q, (int, float)) or not 80 <= q <= 100:
        raise ValueError("JPG-Qualitaet muss zwischen 80 und 100 liegen")
    out["jpg_quality"] = int(q)
    for k in ("keep_metadata", "suffix", "pts"):
        v = raw.get(k, out[k])
        if not isinstance(v, bool):
            raise ValueError(f"{k} muss true/false sein")
        out[k] = v
    return out


def output_name(src: Path, settings: dict) -> str:
    ext = {"original": src.suffix, "jpg": ".jpg", "tiff16": ".tif", "png": ".png"}[
        settings["format"]
    ]
    return src.stem + (SUFFIX if settings["suffix"] else "") + ext


class StatusStore:
    """Status je Bild + Einstellungen in OUT/panolevel_status.json (atomar geschrieben)."""

    def __init__(self, out_dir):
        self.out_dir = Path(out_dir)
        self.path = self.out_dir / STATUS_FILE
        self._lock = threading.Lock()
        self.images: dict[str, dict] = {}
        self.settings: dict = dict(DEFAULT_SETTINGS)
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.images = dict(data["images"])
                self.settings = clean_settings(data.get("settings", {}))
            except (ValueError, KeyError, TypeError):
                bak = self.path.with_suffix(f".json.defekt-{int(time.time())}")
                self.path.rename(bak)
                self.images = {}
                self.settings = dict(DEFAULT_SETTINGS)

    def get(self, name: str) -> dict | None:
        return self.images.get(name)

    def _write(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {"version": 1, "settings": self.settings, "images": self.images},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def set(self, name: str, **fields) -> None:
        with self._lock:
            self.images[name] = {**fields, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
            self._write()

    def set_settings(self, raw: dict) -> dict:
        with self._lock:
            self.settings = clean_settings(raw, self.settings)
            self._write()
            return dict(self.settings)


def _exiftool() -> str | None:
    return shutil.which("exiftool")


def copy_metadata(src: Path, dst: Path, *, size: tuple[int, int], note: str) -> str | None:
    """EXIF/GPS/XMP vom Original ins Ergebnis, GPano sicherstellen, Ausrichtung dokumentieren.

    Gibt None bei Erfolg zurueck, sonst eine Warnung. Schreibt nur in ``dst``.
    """
    exe = _exiftool()
    if exe is None:
        return "exiftool nicht gefunden - Metadaten nicht uebernommen"
    w, h = size
    args = [
        exe,
        "-m",
        "-overwrite_original",
        "-TagsFromFile",
        str(src),
        "-all:all",
        "-ThumbnailImage=",
        "-PreviewImage=",
        "-Orientation#=1",
        "-XMP-GPano:ProjectionType=equirectangular",
        "-XMP-GPano:UsePanoramaViewer=True",
        f"-XMP-GPano:FullPanoWidthPixels={w}",
        f"-XMP-GPano:FullPanoHeightPixels={h}",
        f"-XMP-GPano:CroppedAreaImageWidthPixels={w}",
        f"-XMP-GPano:CroppedAreaImageHeightPixels={h}",
        "-XMP-GPano:CroppedAreaLeftPixels=0",
        "-XMP-GPano:CroppedAreaTopPixels=0",
        "-XMP-GPano:PoseRollDegrees=0",
        "-XMP-GPano:PosePitchDegrees=0",
        f"-XMP-photoshop:Instructions={note}",
        str(dst),
    ]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"exiftool fehlgeschlagen: {exc}"
    if r.returncode != 0:
        return f"exiftool fehlgeschlagen: {(r.stderr or r.stdout).strip()[:200]}"
    return None


def write_image(
    src: Path, img: np.ndarray, out: Path, settings: dict, *, note: str, out_dir: Path
) -> str | None:
    """Schreibt ``img`` atomar nach ``out`` (nur innerhalb ``out_dir``).

    Liefert eine Warnung oder None.
    """
    if out.resolve().parent != Path(out_dir).resolve():
        raise ValueError("Export-Ziel ausserhalb OUT")
    ext = out.suffix.lower()
    params: list[int] = []
    data = img
    if ext in (".jpg", ".jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, int(settings["jpg_quality"])]
    elif settings["format"] == "tiff16" and ext in (".tif", ".tiff"):
        data = img.astype(np.uint16) * 257
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    if not cv2.imwrite(str(tmp), data, params):
        raise RuntimeError(f"Schreiben fehlgeschlagen: {out.name}")
    warn = None
    if settings["keep_metadata"]:
        warn = copy_metadata(src, tmp, size=(img.shape[1], img.shape[0]), note=note)
    tmp.replace(out)
    return warn


def _open_dir(path: Path) -> None:
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@dataclass
class Entry:
    preview: np.ndarray
    lines: list[list[float]]
    full_size: tuple[int, int]  # (Breite, Hoehe) des Originals


class FolderSession:
    def __init__(
        self, folder, out_dir=None, *, preview_reduce=8, detect_kwargs=None, template=None
    ):
        self.folder = Path(folder).resolve()
        if not self.folder.is_dir():
            raise FileNotFoundError(f"Kein Ordner: {self.folder}")
        self.out_dir = resolve_out_dir(self.folder, out_dir)
        self.images = list_images(self.folder)
        if not self.images:
            raise FileNotFoundError(f"Keine Bilder (JPG/PNG/TIF) in {self.folder}")
        self.preview_reduce = preview_reduce
        self.detect_kwargs = detect_kwargs or {}
        self.template = Path(template) if template else None
        self.status = StatusStore(self.out_dir)
        if self.template is None and self.status.settings["pts"]:
            self.status.set_settings({"pts": False})
        self.heavy = threading.RLock()  # nie zwei grosse Bilder parallel
        self._cache: dict[int, Entry] = {}
        self._full: tuple[int, np.ndarray] | None = None  # genau ein Vollbild
        self._pool = ThreadPoolExecutor(max_workers=1)

    def __len__(self):
        return len(self.images)

    @property
    def settings(self) -> dict:
        return self.status.settings

    def path(self, i: int) -> Path:
        return self.images[i]

    def out_name(self, i: int) -> str:
        return output_name(self.images[i], self.settings)

    def out_path(self, i: int) -> Path:
        out = (self.out_dir / self.out_name(i)).resolve()
        if out.parent != self.out_dir or any(out == p.resolve() for p in self.images):
            raise ValueError("Export-Ziel ausserhalb OUT")
        return out

    def name_collision(self, i: int) -> str | None:
        """Name eines anderen Bildes, das denselben Ausgabenamen bekaeme."""
        mine = self.out_name(i)
        for j, p in enumerate(self.images):
            if j != i and output_name(p, self.settings).casefold() == mine.casefold():
                return p.name
        return None

    def state(self, i: int) -> str:
        rec = self.status.get(self.images[i].name)
        if rec and rec.get("status") == "skipped":
            return "skipped"
        if rec and rec.get("status") == "done":
            return "done" if (self.out_dir / rec.get("output", "")).is_file() else "open"
        return "open"

    def listing(self) -> dict:
        states = [self.state(i) for i in range(len(self))]
        first = next((i for i, s in enumerate(states) if s == "open"), 0)
        return {
            "folder": str(self.folder),
            "out_dir": str(self.out_dir),
            "images": [
                {"index": i, "name": p.name, "status": states[i]} for i, p in enumerate(self.images)
            ],
            "done": states.count("done"),
            "total": len(states),
            "first_open": first,
            "settings": self.settings,
            "pts_available": self.template is not None,
            "exiftool": _exiftool() is not None,
        }

    # ---- Vorschau + Erkennung -------------------------------------------------
    def entry(self, i: int) -> Entry:
        e = self._cache.get(i)
        if e is not None:
            return e
        with self.heavy:
            e = self._cache.get(i)
            if e is None:
                from .detect import detect_vertical_report

                p = self.images[i]
                prev = cv2.imread(str(p), REDUCE_FLAG[self.preview_reduce])
                if prev is None:
                    raise FileNotFoundError(f"Bild nicht lesbar: {p.name}")
                ph, pw = prev.shape[:2]
                full_size = (pw * self.preview_reduce, ph * self.preview_reduce)
                try:
                    rep = detect_vertical_report(p, **self.detect_kwargs)
                    fw, fh = rep.image_size
                    full_size = (fw, fh)
                    sx, sy = pw / fw, ph / fh
                    lines = [[a[0] * sx, a[1] * sy, b[0] * sx, b[1] * sy] for (a, b) in rep.lines]
                except Exception:  # Erkennung ist nur ein Vorschlag
                    lines = []
                e = Entry(prev, lines, full_size)
                self._cache[i] = e
                for k in [k for k in self._cache if abs(k - i) > 1]:
                    del self._cache[k]
        return e

    def prefetch(self, i: int) -> None:
        if 0 <= i < len(self) and i not in self._cache:
            self._pool.submit(lambda: self._safe_entry(i))

    def _safe_entry(self, i: int) -> None:
        try:
            self.entry(i)
        except Exception:
            pass

    # ---- Vollbild (genau eines im RAM) --------------------------------------------
    def release_full(self, keep: int | None = None) -> None:
        with self.heavy:
            if self._full is not None and self._full[0] != keep:
                self._full = None

    def full(self, i: int) -> np.ndarray:
        with self.heavy:
            if self._full is None or self._full[0] != i:
                self._full = None
                img = cv2.imread(str(self.images[i]), cv2.IMREAD_COLOR)
                if img is None:
                    raise FileNotFoundError(f"Bild nicht lesbar: {self.images[i].name}")
                self._full = (i, img)
            return self._full[1]

    def crop(
        self, i: int, x0: float, y0: float, x1: float, y1: float, out_w: int = 1600
    ) -> tuple[bytes, tuple[float, float, float, float]]:
        """Ausschnitt aus dem Original (Koordinaten wie die Vorschau, x darf umlaufen).

        Gibt (JPEG-Bytes, tatsaechliches Rechteck in Vorschau-Koordinaten) zurueck.
        """
        e = self.entry(i)
        ph, pw = e.preview.shape[:2]
        with self.heavy:
            full = self.full(i)
            fh, fw = full.shape[:2]
            kx, ky = fw / pw, fh / ph
            y0, y1 = max(0.0, y0), min(float(ph), y1)
            if not (x1 > x0 and y1 > y0):
                raise ValueError("leerer Ausschnitt")
            X0, X1 = int(np.floor(x0 * kx)), int(np.ceil(x1 * kx))
            Y0, Y1 = int(np.floor(y0 * ky)), min(fh, int(np.ceil(y1 * ky)))
            X1 = min(X1, X0 + fw)  # hoechstens ein voller Umlauf
            rows = full[Y0:Y1]
            cols = np.arange(X0, X1) % fw
            patch = rows[:, cols] if (X0 < 0 or X1 > fw) else rows[:, X0:X1]
            w = patch.shape[1]
            scale = min(1.0, out_w / w)
            if scale < 1.0:
                patch = cv2.resize(
                    patch,
                    (max(1, round(w * scale)), max(1, round(patch.shape[0] * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            ok, buf = cv2.imencode(".jpg", patch, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ok:
                raise RuntimeError("jpeg encode failed")
        return buf.tobytes(), (X0 / kx, Y0 / ky, X1 / kx, Y1 / ky)

    # ---- Export ------------------------------------------------------------------
    def export(
        self,
        i: int,
        rotation_fn,
        *,
        roll: float,
        pitch: float,
        yaw: float,
        n_lines: int,
        rms: float | None,
        rating: str | None,
        overwrite: bool = False,
    ) -> dict:
        """Rotiert das Original und schreibt nach OUT. ``rotation_fn(img) -> img``."""
        src = self.images[i]
        clash = self.name_collision(i)
        if clash:
            return {
                "ok": False,
                "reason": f"{clash} bekaeme denselben Dateinamen - Format/Zusatz aendern",
            }
        out = self.out_path(i)
        if out.exists() and not overwrite:
            return {
                "ok": False,
                "exists": True,
                "name": out.name,
                "reason": f"{out.name} existiert schon",
            }
        note = (
            f"PanoLevel: Ausrichtung roll={roll:.2f} pitch={pitch:.2f} yaw={yaw:.1f}, "
            f"Linien={n_lines}, {time.strftime('%Y-%m-%d')}"
        )
        with self.heavy:
            full = self.full(i)
            self._full = None  # Vollbild geht in die Drehung, danach frei
            rot = rotation_fn(full)
            del full
            warn = write_image(src, rot, out, self.settings, note=note, out_dir=self.out_dir)
            fsize = (rot.shape[1], rot.shape[0])
            del rot
        pts_name = None
        if self.settings["pts"] and self.template is not None:
            pts_name = self._write_pts(i, fsize, roll, pitch, yaw)
        self.status.set(
            src.name,
            status="done",
            roll=roll,
            pitch=pitch,
            yaw=yaw,
            lines=n_lines,
            rms_deg=None if rms is None else round(rms, 3),
            rating=rating,
            output=out.name,
            pts=pts_name,
            warning=warn,
        )
        self.write_report()
        return {"ok": True, "output": str(out), "name": out.name, "warning": warn}

    def _write_pts(self, i: int, size: tuple[int, int], roll, pitch, yaw) -> str:
        from .buildpts import OutputSettings, project_skeleton_from_template, set_orientation
        from .ptsio import load_pts, save_pts_copy

        src = self.images[i]
        pts_path = self.out_dir / (Path(self.out_name(i)).stem + ".pts")
        doc = project_skeleton_from_template(
            load_pts(self.template),
            filename=str(src),
            width=size[0],
            height=size[1],
            output=OutputSettings(),
        )
        OutputSettings().apply(doc["project"]["panoramaparams"])
        set_orientation(doc, roll=roll, pitch=pitch, yaw=yaw)
        save_pts_copy(doc, self.template, pts_path)
        return pts_path.name

    def skip(self, i: int) -> None:
        self.status.set(self.images[i].name, status="skipped")
        self.write_report()

    # ---- Bericht ------------------------------------------------------------------
    def write_report(self) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / REPORT_FILE
        label = {"done": "erledigt", "skipped": "uebersprungen", "open": "offen"}
        tmp = path.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(
                [
                    "Datei",
                    "Status",
                    "Ausgabedatei",
                    "Linien",
                    "Roll_Grad",
                    "Neigung_Grad",
                    "Drehung_Grad",
                    "Restfehler_Grad",
                    "Bewertung",
                    "Zeit",
                ]
            )
            for i, p in enumerate(self.images):
                rec = self.status.get(p.name) or {}
                w.writerow(
                    [
                        p.name,
                        label[self.state(i)],
                        rec.get("output", ""),
                        rec.get("lines", ""),
                        *(
                            f"{rec[k]:.2f}" if rec.get(k) is not None else ""
                            for k in ("roll", "pitch", "yaw")
                        ),
                        rec.get("rms_deg", "") if rec.get("rms_deg") is not None else "",
                        rec.get("rating") or "",
                        rec.get("time", ""),
                    ]
                )
        tmp.replace(path)
        return path

    def open_out(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        _open_dir(self.out_dir)
