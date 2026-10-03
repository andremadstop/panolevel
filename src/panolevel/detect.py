"""Vertical line detection for equirectangular panoramas.

The detector works on OpenCV's reduced image loads, then scales every accepted
line back to original pixel coordinates for ``ptsio.insert_vertical_lines``.
"""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

Point = tuple[int, int]
Line = tuple[Point, Point]


@dataclass(frozen=True)
class Segment:
    x1: float
    y1: float
    x2: float
    y2: float
    length: float
    angle_deg: float
    signed_angle_deg: float
    column: int
    score: float
    adaptive: bool = False

    def to_full_line(self, scale_x: float, scale_y: float, width: int, height: int) -> Line:
        return (
            (
                _clamp_int(round(self.x1 * scale_x), 0, width - 1),
                _clamp_int(round(self.y1 * scale_y), 0, height - 1),
            ),
            (
                _clamp_int(round(self.x2 * scale_x), 0, width - 1),
                _clamp_int(round(self.y2 * scale_y), 0, height - 1),
            ),
        )


@dataclass(frozen=True)
class DetectionReport:
    image_path: str
    method: str
    image_size: tuple[int, int]
    reduced_size: tuple[int, int]
    scale: tuple[float, float]
    band: tuple[int, int]
    raw_segments: int
    vertical_candidates: int
    distributed_candidates: int
    inlier_count: int
    outlier_count: int
    lines: list[Line]
    median_angle_deg: float | None
    occupied_columns: list[int]
    target_columns: int
    column_coverage: float
    middle_coverage: int
    confidence: float
    uncertain: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "image": self.image_path,
            "method": self.method,
            "image_size": list(self.image_size),
            "reduced_size": list(self.reduced_size),
            "scale": [self.scale[0], self.scale[1]],
            "band": {"y0": self.band[0], "y1": self.band[1]},
            "raw_segments": self.raw_segments,
            "vertical_candidates": self.vertical_candidates,
            "distributed_candidates": self.distributed_candidates,
            "inlier_count": self.inlier_count,
            "outlier_count": self.outlier_count,
            "lines": [
                {"0": [line[0][0], line[0][1]], "1": [line[1][0], line[1][1]]}
                for line in self.lines
            ],
            "median_angle_deg": self.median_angle_deg,
            "occupied_columns": self.occupied_columns,
            "target_columns": self.target_columns,
            "column_coverage": self.column_coverage,
            "middle_coverage": self.middle_coverage,
            "confidence": self.confidence,
            "uncertain": self.uncertain,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def detect_vertical_lines(
    image_path: str | Path,
    *,
    band_center: float = 0.40,
    band_height: float = 0.34,
    reduce: int = 4,
    angle_tol_deg: float = 20.0,
    min_len_frac: float = 0.30,
    target_columns: int = 16,
    max_lines: int = 12,
) -> list[Line]:
    """Return conservative vertical-line proposals in full pixel coordinates."""

    return detect_vertical_report(
        image_path,
        band_center=band_center,
        band_height=band_height,
        reduce=reduce,
        angle_tol_deg=angle_tol_deg,
        min_len_frac=min_len_frac,
        target_columns=target_columns,
        max_lines=max_lines,
    ).lines


def detect_vertical_report(
    image_path: str | Path,
    *,
    band_center: float = 0.40,
    band_height: float = 0.34,
    reduce: int = 4,
    angle_tol_deg: float = 20.0,
    min_len_frac: float = 0.30,
    target_columns: int = 16,
    max_lines: int = 12,
    adaptive: bool = True,
) -> DetectionReport:
    """Detect vertical world-line candidates and return a structured report."""

    path = Path(image_path)
    if target_columns < 4:
        raise ValueError("target_columns must be >= 4")
    if max_lines < 1:
        raise ValueError("max_lines must be >= 1")

    original_width, original_height = image_size(path)
    reduced = _read_reduced(path, reduce)
    reduced_height, reduced_width = reduced.shape[:2]
    scale_x = original_width / reduced_width
    scale_y = original_height / reduced_height

    gray = cv2.cvtColor(reduced, cv2.COLOR_BGR2GRAY)
    band_y0, band_y1 = _band_bounds(reduced_height, band_center, band_height)
    band = gray[band_y0:band_y1, :]
    raw, method = _detect_raw_segments(band)
    band_height_px = max(1, band_y1 - band_y0)
    strict_min = band_height_px * float(min_len_frac)
    adaptive_min = strict_min * 0.58

    candidates: list[Segment] = []
    for x1, y1, x2, y2 in raw:
        segment = _segment_from_raw(
            x1,
            y1 + band_y0,
            x2,
            y2 + band_y0,
            reduced_width,
            target_columns,
            angle_tol_deg,
        )
        if segment is None:
            continue
        strict = segment.length >= strict_min and segment.angle_deg <= angle_tol_deg
        relaxed = adaptive and segment.length >= adaptive_min and segment.angle_deg <= angle_tol_deg
        if strict or relaxed:
            candidates.append(
                Segment(
                    segment.x1,
                    segment.y1,
                    segment.x2,
                    segment.y2,
                    segment.length,
                    segment.angle_deg,
                    segment.signed_angle_deg,
                    segment.column,
                    segment.score * (1.0 if strict else 0.72),
                    adaptive=not strict,
                )
            )

    distributed = _select_by_columns(candidates, target_columns, max_lines, adaptive)
    inliers, outliers, median = _ransac_mad_filter(distributed)
    inliers = _select_spread(inliers, max_lines)
    lines = [
        segment.to_full_line(scale_x, scale_y, original_width, original_height)
        for segment in inliers
    ]
    occupied = sorted({segment.column for segment in inliers})
    confidence, uncertain = _confidence(inliers, outliers, target_columns)
    if uncertain:
        # Conservative output: keep no more than the strongest two hints when the
        # line set is too thin or inconsistent for automatic optimization.
        kept_uncertain = sorted(inliers, key=lambda item: item.score, reverse=True)[:2]
        lines = [
            segment.to_full_line(scale_x, scale_y, original_width, original_height)
            for segment in kept_uncertain
        ]
        occupied = sorted({segment.column for segment in kept_uncertain})

    return DetectionReport(
        image_path=str(path),
        method=method,
        image_size=(original_width, original_height),
        reduced_size=(reduced_width, reduced_height),
        scale=(scale_x, scale_y),
        band=(band_y0, band_y1),
        raw_segments=len(raw),
        vertical_candidates=len(candidates),
        distributed_candidates=len(distributed),
        inlier_count=len(inliers),
        outlier_count=len(outliers),
        lines=lines,
        median_angle_deg=median,
        occupied_columns=occupied,
        target_columns=target_columns,
        column_coverage=len(occupied) / target_columns,
        middle_coverage=sum(
            1 for col in occupied if target_columns // 3 <= col < (2 * target_columns) // 3
        ),
        confidence=confidence,
        uncertain=uncertain,
    )


def image_size(path: str | Path) -> tuple[int, int]:
    """Return image size without fully decoding large panorama files."""

    path = Path(path)
    with path.open("rb") as handle:
        head = handle.read(32)
        if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
            return struct.unpack(">II", head[16:24])
        if head[:2] == b"\xff\xd8":
            return _jpeg_size(handle)
        if head[:4] in {b"II*\x00", b"MM\x00*"}:
            return _tiff_size(handle, head[:4])

    probe = cv2.imread(str(path), cv2.IMREAD_REDUCED_COLOR_8)
    if probe is None:
        raise ValueError(f"Could not read image: {path}")
    height, width = probe.shape[:2]
    return width * 8, height * 8


def _jpeg_size(handle: Any) -> tuple[int, int]:
    handle.seek(2)
    while True:
        marker_prefix = handle.read(1)
        while marker_prefix and marker_prefix != b"\xff":
            marker_prefix = handle.read(1)
        if marker_prefix != b"\xff":
            raise ValueError("Invalid JPEG marker")
        marker = handle.read(1)
        while marker == b"\xff":
            marker = handle.read(1)
        if marker in {b"\xd8", b"\xd9"}:
            continue
        size_raw = handle.read(2)
        if len(size_raw) != 2:
            raise ValueError("Truncated JPEG segment")
        size = struct.unpack(">H", size_raw)[0]
        if marker in {b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7"}:
            payload = handle.read(size - 2)
            if len(payload) < 5:
                raise ValueError("Truncated JPEG SOF segment")
            height, width = struct.unpack(">HH", payload[1:5])
            return width, height
        handle.seek(size - 2, 1)


def _tiff_size(handle: Any, magic: bytes) -> tuple[int, int]:
    endian = "<" if magic.startswith(b"II") else ">"
    handle.seek(4)
    offset_raw = handle.read(4)
    if len(offset_raw) != 4:
        raise ValueError("Truncated TIFF header")
    ifd_offset = struct.unpack(f"{endian}I", offset_raw)[0]
    handle.seek(ifd_offset)
    count_raw = handle.read(2)
    if len(count_raw) != 2:
        raise ValueError("Truncated TIFF IFD")
    count = struct.unpack(f"{endian}H", count_raw)[0]
    width: int | None = None
    height: int | None = None
    for _ in range(count):
        entry = handle.read(12)
        if len(entry) != 12:
            raise ValueError("Truncated TIFF tag")
        tag, field_type, value_count = struct.unpack(f"{endian}HHI", entry[:8])
        value_raw = entry[8:12]
        if tag not in {256, 257} or value_count < 1:
            continue
        if field_type == 3:
            value = struct.unpack(f"{endian}H", value_raw[:2])[0]
        elif field_type == 4:
            value = struct.unpack(f"{endian}I", value_raw)[0]
        else:
            continue
        if tag == 256:
            width = value
        elif tag == 257:
            height = value
    if width is None or height is None:
        raise ValueError("TIFF width/height tags not found")
    return width, height


def _read_reduced(path: Path, reduce: int) -> np.ndarray:
    flags = {
        1: cv2.IMREAD_COLOR,
        2: cv2.IMREAD_REDUCED_COLOR_2,
        4: cv2.IMREAD_REDUCED_COLOR_4,
        8: cv2.IMREAD_REDUCED_COLOR_8,
    }
    if reduce not in flags:
        raise ValueError("reduce must be one of 1, 2, 4, 8")
    image = cv2.imread(str(path), flags[reduce])
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    if len(image.shape) != 3:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image


def _band_bounds(height: int, center: float, band_height: float) -> tuple[int, int]:
    center = min(max(float(center), 0.0), 1.0)
    band_height = min(max(float(band_height), 0.05), 1.0)
    y0 = int(round((center - band_height / 2.0) * height))
    y1 = int(round((center + band_height / 2.0) * height))
    y0 = _clamp_int(y0, 0, height - 2)
    y1 = _clamp_int(y1, y0 + 2, height)
    return y0, y1


def _detect_raw_segments(band: np.ndarray) -> tuple[list[tuple[float, float, float, float]], str]:
    segments: list[tuple[float, float, float, float]] = []
    try:
        lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
        lines = lsd.detect(band)[0]
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                segments.append((float(x1), float(y1), float(x2), float(y2)))
        return segments, "LSD"
    except Exception:
        edges = cv2.Canny(band, 50, 150)
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180,
            threshold=80,
            minLineLength=max(20, int(band.shape[0] * 0.25)),
            maxLineGap=10,
        )
        if lines is not None:
            for line in lines:
                segments.append(tuple(float(value) for value in line[0]))
        return segments, "HoughLinesP"


def _segment_from_raw(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: int,
    target_columns: int,
    angle_tol_deg: float,
) -> Segment | None:
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length <= 0:
        return None
    angle = math.degrees(math.atan2(abs(dx), abs(dy)))
    if angle > max(angle_tol_deg, 1.0):
        return None
    if dy < 0:
        dx = -dx
        dy = -dy
    signed = math.degrees(math.atan2(dx, dy if dy else 1e-9))
    center_x = (x1 + x2) / 2.0
    column = _clamp_int(int(center_x / max(1, width) * target_columns), 0, target_columns - 1)
    score = length * (1.0 - min(angle / max(angle_tol_deg, 1.0), 1.0) * 0.45)
    return Segment(x1, y1, x2, y2, length, angle, signed, column, score)


def _select_by_columns(
    candidates: Iterable[Segment],
    target_columns: int,
    max_lines: int,
    adaptive: bool,
) -> list[Segment]:
    by_column: dict[int, list[Segment]] = {column: [] for column in range(target_columns)}
    for segment in candidates:
        by_column[segment.column].append(segment)

    selected: list[Segment] = []
    for column in range(target_columns):
        segments = sorted(by_column[column], key=lambda item: item.score, reverse=True)
        strict = [segment for segment in segments if not segment.adaptive]
        if strict:
            selected.append(strict[0])
        elif adaptive and segments:
            selected.append(segments[0])
    return _select_spread(selected, max_lines)


def _select_spread(segments: list[Segment], max_lines: int) -> list[Segment]:
    if len(segments) <= max_lines:
        return sorted(segments, key=lambda item: item.column)
    ordered = sorted(segments, key=lambda item: item.score, reverse=True)
    selected: list[Segment] = []
    for segment in ordered:
        if len(selected) >= max_lines:
            break
        selected.append(segment)
    return sorted(selected, key=lambda item: item.column)


def _ransac_mad_filter(
    segments: list[Segment],
) -> tuple[list[Segment], list[Segment], float | None]:
    if not segments:
        return [], [], None
    angles = np.array([segment.signed_angle_deg for segment in segments], dtype=float)
    global_median = float(np.median(angles))
    mad = float(np.median(np.abs(angles - global_median)))
    base_tol = max(4.0, min(9.0, 1.4826 * mad * 3.0 if mad else 4.0))

    best_inliers: list[int] = []
    for angle in angles:
        residuals = np.abs(angles - angle)
        inliers = [i for i, residual in enumerate(residuals) if residual <= base_tol]
        if len(inliers) > len(best_inliers):
            best_inliers = inliers
        elif len(inliers) == len(best_inliers) and inliers:
            current_score = sum(segments[i].score for i in inliers)
            best_score = sum(segments[i].score for i in best_inliers)
            if current_score > best_score:
                best_inliers = inliers

    inlier_angles = angles[best_inliers] if best_inliers else angles
    refined_median = float(np.median(inlier_angles))
    refined_mad = float(np.median(np.abs(inlier_angles - refined_median)))
    refined_tol = max(4.0, min(8.0, 1.4826 * refined_mad * 3.0 if refined_mad else base_tol))
    final_inliers = [
        segment
        for segment, residual in zip(segments, np.abs(angles - refined_median), strict=True)
        if residual <= refined_tol
    ]
    final_outliers = [
        segment
        for segment, residual in zip(segments, np.abs(angles - refined_median), strict=True)
        if residual > refined_tol
    ]
    return final_inliers, final_outliers, refined_median


def _confidence(
    inliers: list[Segment], outliers: list[Segment], target_columns: int
) -> tuple[float, bool]:
    occupied = len({segment.column for segment in inliers})
    middle = sum(
        1
        for column in {segment.column for segment in inliers}
        if target_columns // 3 <= column < (2 * target_columns) // 3
    )
    inlier_score = min(len(inliers) / 8.0, 1.0) * 0.42
    spread_score = min(occupied / max(1, target_columns * 0.45), 1.0) * 0.38
    middle_score = min(middle / 2.0, 1.0) * 0.12
    outlier_penalty = min(len(outliers) / max(1, len(inliers) + len(outliers)), 1.0) * 0.18
    confidence = max(0.0, min(1.0, inlier_score + spread_score + middle_score - outlier_penalty))
    uncertain = len(inliers) < 4 or occupied < 4 or confidence < 0.42
    return round(confidence, 3), uncertain


def _clamp_int(value: float | int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))
