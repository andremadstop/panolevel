"""Overlay rendering for visual confirmation (Phase 3, OUT-01).

Draws a downscaled copy of the panorama with the proposed vertical lines and the
detection search band, so the suggestions can be eyeballed in seconds before
pruning/optimising in PTGui.
"""

from __future__ import annotations

from pathlib import Path

import cv2

from .ptsio import Line

_REDUCE_FLAG = {
    1: cv2.IMREAD_COLOR,
    2: cv2.IMREAD_REDUCED_COLOR_2,
    4: cv2.IMREAD_REDUCED_COLOR_4,
    8: cv2.IMREAD_REDUCED_COLOR_8,
}

LINE_COLOR = (0, 0, 255)  # red (BGR)
BAND_COLOR = (0, 255, 0)  # green (BGR)


def render_overlay(
    image_path: str | Path,
    lines: list[Line],
    band: tuple[int, int],
    out_path: str | Path,
    *,
    reduce: int = 8,
) -> Path:
    """Render ``image_path`` reduced by ``reduce`` with lines + band, write PNG."""

    if reduce not in _REDUCE_FLAG:
        raise ValueError(f"Unsupported reduce factor {reduce!r}; use 1, 2, 4 or 8")

    image = cv2.imread(str(image_path), _REDUCE_FLAG[reduce])
    if image is None:
        raise FileNotFoundError(f"Cannot read panorama: {image_path}")

    height, width = image.shape[:2]
    scale = 1.0 / reduce

    def _x(value: float) -> int:
        return max(0, min(width - 1, int(round(value * scale))))

    def _y(value: float) -> int:
        return max(0, min(height - 1, int(round(value * scale))))

    # Search band first, then lines on top.
    y0, y1 = sorted(band)
    cv2.line(image, (0, _y(y0)), (width - 1, _y(y0)), BAND_COLOR, 2)
    cv2.line(image, (0, _y(y1)), (width - 1, _y(y1)), BAND_COLOR, 2)

    for a, b in lines:
        cv2.line(image, (_x(a[0]), _y(a[1])), (_x(b[0]), _y(b[1])), LINE_COLOR, 2)

    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise OSError(f"Failed to write overlay: {output}")
    return output
