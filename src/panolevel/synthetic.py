"""Deterministic synthetic panoramas for detector self-tests."""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np


def write_roll_fixture(
    path: str | Path,
    *,
    width: int = 2048,
    height: int = 1024,
    roll_deg: float = 7.0,
    outlier: bool = False,
) -> Path:
    """Write a simple equirect-like panorama with rolled vertical markers."""

    output = Path(path)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:] = (28, 30, 32)

    for y in range(0, height, 64):
        color = 38 if y % 128 else 48
        cv2.line(image, (0, y), (width - 1, y), (color, color, color), 1)

    azimuths = [-150, -110, -70, -30, 10, 45, 85, 125, 165]
    for idx, azimuth in enumerate(azimuths):
        points = _rolled_meridian_points(width, height, azimuth, roll_deg)
        color = (222, 222, 222) if idx % 2 else (190, 220, 240)
        cv2.polylines(image, [np.array(points, dtype=np.int32)], False, color, 5)

    if outlier:
        y0 = int(height * 0.23)
        y1 = int(height * 0.55)
        x0 = int(width * 0.50)
        x1 = x0 + int((y1 - y0) * math.tan(math.radians(15.0)))
        cv2.line(
            image,
            (x0, y0),
            (x1, y1),
            (255, 255, 255),
            7,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), image)
    return output


def write_texture_fixture(
    path: str | Path,
    *,
    width: int = 2048,
    height: int = 1024,
) -> Path:
    """Write deterministic low-contrast texture without reliable verticals."""

    output = Path(path)
    rng = np.random.default_rng(42)
    base = rng.normal(45, 4, (height, width, 1)).clip(0, 255).astype(np.uint8)
    image = np.repeat(base, 3, axis=2)
    for row in range(0, height, 97):
        cv2.line(image, (0, row), (width - 1, row + 11), (58, 58, 58), 2)
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), image)
    return output


def _rolled_meridian_points(
    width: int,
    height: int,
    azimuth_deg: float,
    roll_deg: float,
) -> list[tuple[int, int]]:
    azimuth = math.radians(azimuth_deg)
    roll = math.radians(roll_deg)
    cos_r = math.cos(-roll)
    sin_r = math.sin(-roll)
    points: list[tuple[int, int]] = []
    for lat_deg in np.linspace(-30, 34, 80):
        lat = math.radians(float(lat_deg))
        world_x = math.cos(lat) * math.sin(azimuth)
        world_y = math.sin(lat)
        world_z = math.cos(lat) * math.cos(azimuth)

        cam_x = cos_r * world_x - sin_r * world_y
        cam_y = sin_r * world_x + cos_r * world_y
        cam_z = world_z

        lon = math.atan2(cam_x, cam_z)
        lat_out = math.asin(max(-1.0, min(1.0, cam_y)))
        x = int(round((lon / (2.0 * math.pi) + 0.5) * width))
        y = int(round((0.5 - lat_out / math.pi) * height))
        if 0 <= x < width and 0 <= y < height:
            points.append((x, y))
    return points
