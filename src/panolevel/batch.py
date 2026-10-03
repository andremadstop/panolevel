"""Batch folder run over panorama / .pts pairs (Phase 4).

Pairs ``X.png`` with ``X <suffix>.pts`` (or ``X.pts``), runs the single-pano
``autolevel_one`` pipeline over each, and is robust against single failures: a
broken panorama becomes a ``fehler`` entry instead of aborting the run. The
collective report separates ``sicher`` from ``unsicher`` (and ``fehler`` /
``uebersprungen``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .buildpts import OutputSettings
from .pipeline import autolevel_one

DEFAULT_PTS_SUFFIX = " Panorama"
_STATUSES = ("sicher", "unsicher", "fehler", "uebersprungen")


def pair_panos(
    folder: str | Path, *, pts_suffix: str = DEFAULT_PTS_SUFFIX
) -> list[tuple[Path, Path | None]]:
    """Pair every source ``*.png`` in ``folder`` with its ``.pts`` if present.

    Our own outputs (``*.overlay.png`` and ``*.autolevel.pts``) are never treated
    as source panoramas or source projects. A pano matches ``X<suffix>.pts`` first,
    then a plain ``X.pts``; otherwise it pairs with ``None`` (template path).
    """

    folder = Path(folder)
    pairs: list[tuple[Path, Path | None]] = []
    for png in sorted(folder.glob("*.png")):
        if png.name.endswith(".overlay.png"):
            continue
        stem = png.stem
        suffixed = folder / f"{stem}{pts_suffix}.pts"
        plain = folder / f"{stem}.pts"
        if suffixed.exists():
            pts: Path | None = suffixed
        elif plain.exists():
            pts = plain
        else:
            pts = None
        pairs.append((png, pts))
    return pairs


def run_batch(
    folder: str | Path,
    *,
    mode: str = "cp",
    template: str | Path | None = None,
    pts_suffix: str = DEFAULT_PTS_SUFFIX,
    output: OutputSettings | None = None,
    overlay_reduce: int = 8,
    detect_kwargs: dict[str, Any] | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run ``autolevel_one`` over every pano/.pts pair in ``folder``.

    ``mode`` is passed through to every pano (cp/angle/rotate). In ``rotate`` mode
    no ``.pts``/``template`` is needed, so panos are never skipped for lack of one.
    """

    folder = Path(folder)
    out_dir_p = Path(out_dir) if out_dir is not None else None
    if out_dir_p is not None:
        out_dir_p.mkdir(parents=True, exist_ok=True)
    out_suffix = ".autolevel.png" if mode == "rotate" else ".autolevel.pts"

    results: list[dict[str, Any]] = []
    for image, pts in pair_panos(folder, pts_suffix=pts_suffix):
        source = "pts" if pts is not None else "template"
        entry: dict[str, Any] = {
            "image": str(image),
            "mode": mode,
            "status": None,
            "source": source,
            "lines": None,
            "confidence": None,
            "uncertain": None,
            "roll": None,
            "pitch": None,
            "pts": None,
            "output": None,
            "overlay": None,
            "error": None,
        }

        if mode in ("cp", "angle") and pts is None and template is None:
            entry["status"] = "uebersprungen"
            entry["source"] = None
            entry["error"] = "kein .pts und kein Template"
            results.append(entry)
            continue

        try:
            out = overlay = None
            if out_dir_p is not None:
                out = out_dir_p / f"{image.stem}{out_suffix}"
                overlay = out_dir_p / f"{image.stem}.autolevel.overlay.png"
            report = autolevel_one(
                image,
                mode=mode,
                pts=pts,
                template=template,
                out=out,
                overlay=overlay,
                output=output,
                overlay_reduce=overlay_reduce,
                detect_kwargs=detect_kwargs,
            )
            entry["status"] = "unsicher" if report["uncertain"] else "sicher"
            entry["lines"] = report["lines"]
            entry["confidence"] = report["confidence"]
            entry["uncertain"] = report["uncertain"]
            entry["roll"] = report["roll"]
            entry["pitch"] = report["pitch"]
            outputs = report["outputs"]
            entry["pts"] = outputs.get("pts")
            entry["output"] = outputs.get("image") or outputs.get("pts")
            entry["overlay"] = outputs["overlay"]
        except Exception as exc:  # robust: one bad pano must not abort the batch
            entry["status"] = "fehler"
            entry["error"] = f"{type(exc).__name__}: {exc}"
        results.append(entry)

    summary = {status: 0 for status in _STATUSES}
    for entry in results:
        summary[entry["status"]] = summary.get(entry["status"], 0) + 1

    return {
        "folder": str(folder),
        "total": len(results),
        "results": results,
        "summary": summary,
    }
