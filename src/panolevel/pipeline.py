"""Single-panorama autolevel pipeline (Phase 3 core, extended for Weg A in 5).

One function turns a panorama into output + an overlay PNG and returns a report
dict. Three modes:
  - ``cp``     : detected verticals as PTGui control points in a .pts copy/project
                 (PTGui then optimizes). Default; the original Phase-3 behaviour.
  - ``angle``  : estimate roll/pitch (Weg A) and write them into the .pts
                 (PTGui renders straight without optimizing). PTGui sign-gate open.
  - ``rotate`` : estimate roll/pitch and straighten the PNG itself — no PTGui,
                 no .pts (5b). Single-pass remap.

No argument parsing, no printing — shared by the ``autolevel`` and ``batch`` CLI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2

from .buildpts import (
    OutputSettings,
    apply_alignment,
    build_pts_from_template,
    project_skeleton_from_template,
    set_orientation,
)
from .detect import detect_vertical_report
from .orient import estimate_zenith, roll_pitch_from_zenith, rotate_equirect, zenith_to_rotation
from .overlay import render_overlay
from .ptsio import load_pts, save_pts_copy

MODES = ("cp", "angle", "rotate")


def autolevel_one(
    image: str | Path,
    *,
    mode: str = "cp",
    pts: str | Path | None = None,
    template: str | Path | None = None,
    out: str | Path | None = None,
    overlay: str | Path | None = None,
    output: OutputSettings | None = None,
    overlay_reduce: int = 8,
    detect_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect verticals, produce output for ``mode``, return a report dict."""

    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; use one of {MODES}")

    image = Path(image)
    report = detect_vertical_report(image, **(detect_kwargs or {}))
    output_settings = output or OutputSettings()

    result: dict[str, Any] = {
        "image": report.image_path,
        "mode": mode,
        "lines": len(report.lines),
        "occupied_columns": report.occupied_columns,
        "column_coverage": report.column_coverage,
        "middle_coverage": report.middle_coverage,
        "confidence": report.confidence,
        "uncertain": report.uncertain,
        "roll": None,
        "pitch": None,
    }

    if mode in ("angle", "rotate"):
        zenith, _ = estimate_zenith(report.lines, *report.image_size)
        roll, pitch = roll_pitch_from_zenith(zenith)
        result["roll"] = roll
        result["pitch"] = pitch

    if mode == "rotate":
        img = cv2.imread(str(image), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"Cannot read panorama: {image}")
        rotated = rotate_equirect(img, zenith_to_rotation(zenith))
        out_png = Path(out) if out is not None else image.with_name(f"{image.stem}.autolevel.png")
        out_png.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(out_png), rotated):
            raise OSError(f"Failed to write rotated pano: {out_png}")
        overlay_path = (
            Path(overlay)
            if overlay is not None
            else out_png.with_name(f"{out_png.stem}.overlay.png")
        )
        render_overlay(image, report.lines, report.band, overlay_path, reduce=overlay_reduce)
        result["outputs"] = {"image": str(out_png), "overlay": str(overlay_path)}
        return result

    # cp / angle: produce a .pts (copy of an existing project, or from template)
    if pts is None and template is None:
        raise ValueError(f"mode {mode!r} needs either pts or template")

    if pts is not None:
        doc = load_pts(pts)
        if mode == "cp":
            apply_alignment(doc, report.lines, output=output_settings, clear_controlpoints=False)
        else:  # angle
            output_settings.apply(doc["project"]["panoramaparams"])
            set_orientation(doc, roll=result["roll"], pitch=result["pitch"])
        out_pts = save_pts_copy(doc, pts, out)
    else:
        width, height = report.image_size
        common = {
            "filename": image.name,
            "width": width,
            "height": height,
            "output": output_settings,
            "filesize": os.path.getsize(image),
            "filetimestamp": int(os.path.getmtime(image)),
        }
        if mode == "cp":
            doc = build_pts_from_template(load_pts(template), lines=report.lines, **common)
        else:  # angle
            doc = project_skeleton_from_template(load_pts(template), **common)
            set_orientation(doc, roll=result["roll"], pitch=result["pitch"])
        default_out = image.with_name(f"{image.stem}.autolevel.pts")
        out_pts = save_pts_copy(doc, image, out or default_out)

    overlay_path = (
        Path(overlay) if overlay is not None else out_pts.with_name(f"{out_pts.stem}.overlay.png")
    )
    render_overlay(image, report.lines, report.band, overlay_path, reduce=overlay_reduce)
    result["outputs"] = {"pts": str(out_pts), "overlay": str(overlay_path)}
    return result
