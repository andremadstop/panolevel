"""Build a PTGui-loadable single-image .pts from a settings template (Phase 3).

For panoramas without their own ``.pts`` (PTS-06): take an already well-configured
project as the settings basis, set image name/size/orientation/lines, and inherit
the rest. Output settings (PTS-07) are applied explicitly, never silently inherited
from whatever template happened to be picked.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .ptsio import Line, insert_vertical_lines


@dataclass(frozen=True)
class OutputSettings:
    """Explicit output configuration. Default = TIFF 16-bit (Andre 2026-06-17)."""

    fileformat: str = "tiff"
    datatype: str = "u16"

    def apply(self, panoramaparams: dict[str, Any]) -> None:
        panoramaparams["fileformat"] = self.fileformat
        if self.fileformat == "tiff":
            panoramaparams.setdefault("tiffparams", {})["datatype"] = self.datatype


def _position_slots(ig: dict[str, Any]) -> list[dict[str, Any]]:
    slots = [ig.get("position")]
    linkable = ig.get("linkable")
    if isinstance(linkable, dict):
        slots.append(linkable.get("position"))
    return [slot for slot in slots if isinstance(slot, dict)]


def apply_alignment(
    doc: dict[str, Any],
    lines: Iterable[Line],
    *,
    output: OutputSettings | None = None,
    neutralize_orientation: bool = False,
    clear_controlpoints: bool = True,
) -> dict[str, Any]:
    """Turn ``doc`` (in place) into a vertical-alignment project.

    Frees pitch/roll for the optimizer, applies output settings explicitly, and
    adds the detected ``lines`` as vertical control points (dedup by coordinates).

    With ``neutralize_orientation`` the yaw/pitch/roll are zeroed first (for
    template-built projects); without it, an existing project keeps its yaw and
    only re-solves pitch/roll. With ``clear_controlpoints`` the prior control
    points are dropped first — correct when rebuilding a fresh project from a
    template (its CPs belong to the template's image), but it must stay False for
    an existing ``.pts`` so the user's own manual/optimized CPs survive.
    """

    project = doc["project"]
    ig = project["imagegroups"][0]
    for slot in _position_slots(ig):
        params = slot.setdefault("params", {})
        if neutralize_orientation:
            params["yaw"] = 0.0
            params["pitch"] = 0.0
            params["roll"] = 0.0
        flags = slot.setdefault("optimizerflags", {})
        flags["pitch"] = True
        flags["roll"] = True

    (output or OutputSettings()).apply(project["panoramaparams"])
    if clear_controlpoints:
        project["controlpoints"] = []
    insert_vertical_lines(doc, lines)
    return doc


def set_orientation(
    doc: dict[str, Any],
    *,
    roll: float,
    pitch: float,
    yaw: float | None = None,
) -> dict[str, Any]:
    """5a: write estimated roll/pitch into position.params, freeze the optimizer.

    The pano then renders straight in PTGui without optimizing. ``yaw`` is kept
    unless given. The exact PTGui sign/order convention is the open gate — verify
    once in PTGui (like the Phase-1 round-trip); a flip is a one-line change.
    """

    ig = doc["project"]["imagegroups"][0]
    for slot in _position_slots(ig):
        params = slot.setdefault("params", {})
        params["roll"] = float(roll)
        params["pitch"] = float(pitch)
        if yaw is not None:
            params["yaw"] = float(yaw)
        flags = slot.setdefault("optimizerflags", {})
        flags["pitch"] = False
        flags["roll"] = False
    return doc


def build_pts_from_template(
    template: dict[str, Any],
    *,
    filename: str,
    width: int,
    height: int,
    lines: Iterable[Line],
    output: OutputSettings | None = None,
    filesize: int | None = None,
    filetimestamp: int | None = None,
) -> dict[str, Any]:
    """Return a fresh single-image project doc built from ``template``.

    The template is deep-copied and never mutated. Orientation is neutralised and
    pitch/roll are freed for the optimizer, control points are reset to exactly the
    supplied vertical lines, and output settings are applied explicitly.
    """

    doc = project_skeleton_from_template(
        template,
        filename=filename,
        width=width,
        height=height,
        output=output,
        filesize=filesize,
        filetimestamp=filetimestamp,
    )
    return apply_alignment(doc, lines, output=output, neutralize_orientation=True)


def project_skeleton_from_template(
    template: dict[str, Any],
    *,
    filename: str,
    width: int,
    height: int,
    output: OutputSettings | None = None,
    filesize: int | None = None,
    filetimestamp: int | None = None,
) -> dict[str, Any]:
    """Deep-copy ``template`` and set image name/size/metadata + output settings.

    No orientation or control-point changes — that is the caller's job (CPs for
    mode cp via ``apply_alignment``, angles for mode angle via ``set_orientation``).
    """

    doc = copy.deepcopy(template)
    ig = doc["project"]["imagegroups"][0]
    ig["size"] = [int(width), int(height)]

    image = ig["images"][0]
    image["filename"] = filename
    if filesize is not None or filetimestamp is not None:
        meta = image.setdefault("metadata", {})
        if filesize is not None:
            meta["filesize"] = int(filesize)
        if filetimestamp is not None:
            meta["filetimestamp"] = int(filetimestamp)

    (output or OutputSettings()).apply(doc["project"]["panoramaparams"])
    return doc
