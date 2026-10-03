"""Local web UI backend for PanoLevel v2 (FastAPI). Binds 127.0.0.1 only."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .buildpts import OutputSettings, project_skeleton_from_template, set_orientation
from .detect import detect_vertical_report
from .orient import combined_rotation, estimate_zenith, roll_pitch_from_zenith, rotate_equirect
from .ptsio import load_pts, save_pts_copy

WEB_DIR = Path(__file__).resolve().parent / "web"
_REDUCE_FLAG = {
    1: cv2.IMREAD_COLOR,
    2: cv2.IMREAD_REDUCED_COLOR_2,
    4: cv2.IMREAD_REDUCED_COLOR_4,
    8: cv2.IMREAD_REDUCED_COLOR_8,
}


def _encode_png(bgr: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError("png encode failed")
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


class EstimateBody(BaseModel):
    lines: list[list[float]]
    yaw: float = 0.0


class ExportBody(BaseModel):
    lines: list[list[float]]
    yaw: float = 0.0
    mode: str = "png"  # "png" | "pts"
    out: str | None = None


def _is_valid_line(l) -> bool:
    """Return True iff l has at least 4 elements that are numeric (not bool)."""
    if len(l) < 4:
        return False
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in l[:4])


def _safe_out(out, default, allowed_dirs):
    if out is None:
        return default
    p = Path(out).resolve()
    for base in allowed_dirs:
        try:
            if p.is_relative_to(Path(base).resolve()):
                return p
        except (ValueError, OSError):
            continue
    return None


def _zenith_or_none(lines: list[list[float]], w: int, h: int):
    valid = [l for l in lines if _is_valid_line(l)]
    if len(valid) < 2:
        return None
    pairs = [((l[0], l[1]), (l[2], l[3])) for l in valid]
    try:
        z, _ = estimate_zenith(pairs, w, h)
        return z
    except ValueError:
        return None


def create_app(
    image_path, *, pts=None, template=None, preview_reduce=8, detect_kwargs=None
) -> FastAPI:
    image_path = Path(image_path)
    preview = cv2.imread(str(image_path), _REDUCE_FLAG[preview_reduce])
    if preview is None:
        raise FileNotFoundError(f"Cannot read panorama: {image_path}")
    ph, pw = preview.shape[:2]
    cfg = {"pts": pts, "template": template, "detect_kwargs": detect_kwargs or {}}
    app = FastAPI()
    from fastapi.staticfiles import StaticFiles

    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (WEB_DIR / "index.html").read_text(encoding="utf-8")

    @app.post("/detect")
    def detect() -> dict[str, Any]:
        rep = detect_vertical_report(image_path, **cfg["detect_kwargs"])
        fw, fh = rep.image_size
        sx, sy = pw / fw, ph / fh
        lines = [[a[0] * sx, a[1] * sy, b[0] * sx, b[1] * sy] for (a, b) in rep.lines]
        return {
            "ok": True,
            "preview": _encode_png(preview),
            "lines": lines,
            "width": pw,
            "height": ph,
        }

    @app.post("/estimate")
    def estimate(body: EstimateBody) -> dict[str, Any]:
        z = _zenith_or_none(body.lines, pw, ph)
        if z is None:
            return {"ok": False, "reason": "need >=2 non-degenerate lines"}
        roll, pitch = roll_pitch_from_zenith(z)
        rot = rotate_equirect(preview, combined_rotation(z, body.yaw))
        return {
            "ok": True,
            "roll": roll,
            "pitch": pitch,
            "yaw": body.yaw,
            "preview": _encode_png(rot),
        }

    @app.post("/export")
    def export(body: ExportBody) -> dict[str, Any]:
        z = _zenith_or_none(body.lines, pw, ph)
        if z is None:
            return {"ok": False, "reason": "need >=2 non-degenerate lines"}
        roll, pitch = roll_pitch_from_zenith(z)
        allowed = [image_path.parent, Path.cwd()]
        if body.mode == "png":
            full = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if full is None:
                return {"ok": False, "reason": "cannot read full-res pano"}
            rot = rotate_equirect(full, combined_rotation(z, body.yaw))
            default_png = image_path.with_name(f"{image_path.stem}.autolevel.png")
            out = _safe_out(body.out, default_png, allowed)
            if out is None:
                return {"ok": False, "reason": "out path outside allowed directories"}
            out.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out), rot)
            return {"ok": True, "mode": "png", "output": str(out)}
        if body.mode == "pts":
            if cfg["pts"] is not None:
                doc = load_pts(cfg["pts"])
                src = cfg["pts"]
            elif cfg["template"] is not None:
                rep = detect_vertical_report(image_path, **cfg["detect_kwargs"])
                fw, fh = rep.image_size
                doc = project_skeleton_from_template(
                    load_pts(cfg["template"]),
                    filename=image_path.name,
                    width=fw,
                    height=fh,
                    output=OutputSettings(),
                )
                src = image_path.with_name(f"{image_path.stem}.autolevel.pts")
            else:
                return {"ok": False, "reason": "mode pts needs --pts or --template"}
            from .ptsio import default_output_path

            default_pts = default_output_path(src)
            out_path = _safe_out(body.out, default_pts, allowed)
            if out_path is None:
                return {"ok": False, "reason": "out path outside allowed directories"}
            OutputSettings().apply(doc["project"]["panoramaparams"])
            set_orientation(doc, roll=roll, pitch=pitch, yaw=body.yaw)
            out_pts = save_pts_copy(doc, src, out_path)
            return {"ok": True, "mode": "pts", "output": str(out_pts)}
        return {"ok": False, "reason": f"unknown mode {body.mode!r}"}

    app.state.cfg = cfg
    app.state.image_path = image_path
    app.state.preview_size = (pw, ph)
    return app


MIN_LINES_FOLDER = 3


class FolderExportBody(BaseModel):
    lines: list[list[float]]
    yaw: float = 0.0
    overwrite: bool = False


class SettingsBody(BaseModel):
    format: str | None = None
    jpg_quality: int | None = None
    keep_metadata: bool | None = None
    suffix: bool | None = None
    pts: bool | None = None


def _zenith_normals(lines: list[list[float]], w: int, h: int):
    valid = [l for l in lines if _is_valid_line(l)]
    if len(valid) < 2:
        return None
    pairs = [((l[0], l[1]), (l[2], l[3])) for l in valid]
    try:
        z, N = estimate_zenith(pairs, w, h)
    except ValueError:
        return None
    return z, N, len(valid)


def create_folder_app(
    folder, out_dir=None, *, preview_reduce=8, detect_kwargs=None, allowed_hosts=None, template=None
) -> FastAPI:
    """Ordner-Modus: Bilder nur per Index aus der Ordnerliste, Export nur nach OUT."""
    from fastapi import HTTPException, Response

    from . import quality
    from .folder import FolderSession

    sess = FolderSession(
        folder,
        out_dir,
        preview_reduce=preview_reduce,
        detect_kwargs=detect_kwargs,
        template=template,
    )
    app = FastAPI()
    if allowed_hosts:
        from starlette.middleware.trustedhost import TrustedHostMiddleware

        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    from fastapi.staticfiles import StaticFiles

    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    def _idx(i: int) -> int:
        if not 0 <= i < len(sess):
            raise HTTPException(status_code=404, detail="Bild nicht in der Liste")
        return i

    def _entry(i: int):
        try:
            return sess.entry(_idx(i))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (WEB_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/folder")
    def listing() -> dict[str, Any]:
        return sess.listing()

    @app.post("/api/folder/{i}/load")
    def load(i: int) -> dict[str, Any]:
        e = _entry(i)
        sess.release_full(keep=i)
        sess.prefetch(i + 1)
        ph, pw = e.preview.shape[:2]
        rec = sess.status.get(sess.path(i).name) or {}
        return {
            "ok": True,
            "name": sess.path(i).name,
            "index": i,
            "status": sess.state(i),
            "preview": _encode_png(e.preview),
            "lines": e.lines,
            "width": pw,
            "height": ph,
            "full_width": e.full_size[0],
            "full_height": e.full_size[1],
            "saved": {k: rec.get(k) for k in ("roll", "pitch", "yaw", "lines")} if rec else None,
        }

    @app.post("/api/folder/{i}/estimate")
    def f_estimate(i: int, body: EstimateBody) -> dict[str, Any]:
        e = _entry(i)
        ph, pw = e.preview.shape[:2]
        zn = _zenith_normals(body.lines, pw, ph)
        if zn is None:
            return {"ok": False, "reason": "need >=2 non-degenerate lines"}
        z, N, n_valid = zn
        roll, pitch = roll_pitch_from_zenith(z)
        rating, rms = quality.rate(quality.line_residuals_deg(N, z), n_valid)
        rot = rotate_equirect(e.preview, combined_rotation(z, body.yaw))
        return {
            "ok": True,
            "roll": roll,
            "pitch": pitch,
            "yaw": body.yaw,
            "quality": rating,
            "preview": _encode_png(rot),
        }

    @app.get("/api/folder/{i}/crop")
    def f_crop(i: int, x0: float, y0: float, x1: float, y1: float, w: int = 1600) -> Response:
        _entry(i)
        try:
            data, rect = sess.crop(i, x0, y0, x1, y1, out_w=max(64, min(w, 4096)))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"X-Rect": ",".join(f"{v:.4f}" for v in rect), "Cache-Control": "no-store"},
        )

    @app.post("/api/folder/{i}/export")
    def f_export(i: int, body: FolderExportBody) -> dict[str, Any]:
        e = _entry(i)
        ph, pw = e.preview.shape[:2]
        n_valid = len([l for l in body.lines if _is_valid_line(l)])
        if n_valid < MIN_LINES_FOLDER:
            return {"ok": False, "reason": f"mindestens {MIN_LINES_FOLDER} Linien noetig"}
        zn = _zenith_normals(body.lines, pw, ph)
        if zn is None:
            return {"ok": False, "reason": "Linien nicht auswertbar"}
        z, N, _ = zn
        roll, pitch = roll_pitch_from_zenith(z)
        rating, rms = quality.rate(quality.line_residuals_deg(N, z), n_valid)
        rot = combined_rotation(z, body.yaw)
        try:
            return sess.export(
                i,
                lambda full: rotate_equirect(full, rot),
                roll=roll,
                pitch=pitch,
                yaw=body.yaw,
                n_lines=n_valid,
                rms=rms,
                rating=rating,
                overwrite=body.overwrite,
            )
        except FileNotFoundError as exc:
            return {"ok": False, "reason": str(exc)}
        except (ValueError, RuntimeError) as exc:
            return {"ok": False, "reason": str(exc)}

    @app.post("/api/folder/{i}/skip")
    def f_skip(i: int) -> dict[str, Any]:
        _idx(i)
        sess.skip(i)
        return {"ok": True, "name": sess.path(i).name}

    @app.post("/api/settings")
    def settings(body: SettingsBody) -> dict[str, Any]:
        raw = body.model_dump(exclude_none=True)
        if raw.get("pts") and sess.template is None:
            return {"ok": False, "reason": "PTGui-Projekt braucht --template beim Start"}
        try:
            return {"ok": True, "settings": sess.status.set_settings(raw)}
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}

    @app.post("/api/report")
    def report() -> dict[str, Any]:
        return {"ok": True, "path": str(sess.write_report())}

    @app.post("/api/open-out")
    def open_out() -> dict[str, Any]:
        sess.open_out()
        return {"ok": True, "path": str(sess.out_dir)}

    app.state.session = sess
    return app
