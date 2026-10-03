"""PTGui JSON .pts I/O helpers for Phase 1.

Phase 1 only reads and writes vertical control points. It does not compute
orientation and never mutates an input file in place.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

Point = tuple[int, int]
Line = tuple[Point, Point]


def load_pts(path: str | Path) -> dict[str, Any]:
    """Load a PTGui v11+ JSON .pts file."""

    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("PTGui .pts root must be a JSON object")
    return data


def image_size(doc: dict[str, Any]) -> list[int] | None:
    """Return the first imagegroup size as ``[width, height]`` if present."""

    try:
        size = doc["project"]["imagegroups"][0]["size"]
    except (KeyError, IndexError, TypeError):
        return None
    if isinstance(size, list) and len(size) == 2 and all(isinstance(value, int) for value in size):
        return [size[0], size[1]]
    return None


def position_params(doc: dict[str, Any]) -> dict[str, Any]:
    """Return first imagegroup position params, or an empty dict."""

    try:
        params = doc["project"]["imagegroups"][0]["position"]["params"]
    except (KeyError, IndexError, TypeError):
        return {}
    if isinstance(params, dict):
        return dict(params)
    return {}


def list_vertical_lines(doc: dict[str, Any]) -> list[Line]:
    """Return all ``t:1`` vertical control points as endpoint pairs."""

    lines: list[Line] = []
    for cp in _controlpoints(doc):
        if not isinstance(cp, dict) or cp.get("t") != 1:
            continue
        parsed = _line_from_cp(cp)
        if parsed is not None:
            lines.append(parsed)
    return lines


def insert_vertical_lines(
    doc: dict[str, Any], lines: Iterable[Line], *, imagegroup: int = 0, image: int = 0
) -> int:
    """Append new vertical lines, deduplicating by exact endpoint coordinates.

    PTGui tolerance for custom keys is not assumed, so generated control points
    are plain PTGui objects. Idempotence is therefore coordinate-based.
    """

    controlpoints = _ensure_controlpoints(doc)
    existing = {
        _line_signature(parsed)
        for cp in controlpoints
        if isinstance(cp, dict) and cp.get("t") == 1
        for parsed in [_line_from_cp(cp)]
        if parsed is not None
    }

    inserted = 0
    for raw_line in lines:
        line = _normalize_line(raw_line)
        signature = _line_signature(line)
        if signature in existing:
            continue
        controlpoints.append(
            {
                "t": 1,
                "0": [int(imagegroup), int(image), line[0][0], line[0][1]],
                "1": [int(imagegroup), int(image), line[1][0], line[1][1]],
            }
        )
        existing.add(signature)
        inserted += 1
    return inserted


def save_pts_copy(
    doc: dict[str, Any], src_path: str | Path, out_path: str | Path | None = None
) -> Path:
    """Write ``doc`` to ``<stem>.autolevel.pts`` or ``out_path``.

    The source path is only used to derive the default output name. Writing to
    the source path is rejected.
    """

    source = Path(src_path)
    output = Path(out_path) if out_path is not None else default_output_path(source)
    if output.resolve() == source.resolve():
        raise ValueError("Refusing to overwrite the input .pts file")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return output


def default_output_path(src_path: str | Path) -> Path:
    source = Path(src_path)
    suffix = source.suffix or ".pts"
    return source.with_name(f"{source.stem}.autolevel{suffix}")


def _controlpoints(doc: dict[str, Any]) -> list[Any]:
    try:
        controlpoints = doc["project"].get("controlpoints", [])
    except (KeyError, TypeError):
        return []
    if isinstance(controlpoints, list):
        return controlpoints
    return []


def _ensure_controlpoints(doc: dict[str, Any]) -> list[Any]:
    project = doc.setdefault("project", {})
    if not isinstance(project, dict):
        raise ValueError("PTGui .pts project must be a JSON object")
    controlpoints = project.setdefault("controlpoints", [])
    if not isinstance(controlpoints, list):
        raise ValueError("project.controlpoints must be a JSON list")
    return controlpoints


def _line_from_cp(cp: dict[str, Any]) -> Line | None:
    try:
        a = cp["0"]
        b = cp["1"]
        return ((int(a[2]), int(a[3])), (int(b[2]), int(b[3])))
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _normalize_line(line: Line) -> Line:
    try:
        (x1, y1), (x2, y2) = line
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid line {line!r}") from exc
    return ((_to_int_pixel(x1), _to_int_pixel(y1)), (_to_int_pixel(x2), _to_int_pixel(y2)))


def _to_int_pixel(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Pixel coordinates must be numeric")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    return int(str(value))


def _line_signature(line: Line) -> tuple[int, int, int, int]:
    return (line[0][0], line[0][1], line[1][0], line[1][1])
