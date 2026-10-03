"""Direct orientation estimation (Phase 5, Weg A).

Estimate the camera tilt (roll/pitch) directly from the detected vertical lines,
without PTGui. Vertical world lines project to great-circle arcs in an equirect;
all of them pass through the zenith (world up). The zenith is therefore the unit
vector orthogonal to every great-circle normal — the smallest right singular
vector of the stacked normals (SVD).

Two consumers:
  - 5a: ``roll_pitch_from_zenith`` → write the angles into the ``.pts`` (PTGui
    renders straight without optimizing).
  - 5b: ``zenith_to_rotation`` + ``rotate_equirect`` → straighten the PNG itself.

Convention (= panolevel.synthetic / PTGui equirect):
  lon = (x/W - 0.5)*2π ; lat = (0.5 - y/H)*π
  d   = (cos(lat)·sin(lon), sin(lat), cos(lat)·cos(lon))   # x right, y up, z front
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .ptsio import Line


def pixel_to_dir(x: float, y: float, width: int, height: int) -> np.ndarray:
    lon = (x / width - 0.5) * 2.0 * math.pi
    lat = (0.5 - y / height) * math.pi
    cl = math.cos(lat)
    return np.array([cl * math.sin(lon), math.sin(lat), cl * math.cos(lon)])


def estimate_zenith(lines: list[Line], width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Return the zenith unit vector (world up in camera frame) and the normals.

    Needs at least two non-degenerate lines. Raises ValueError otherwise.
    """

    normals = []
    for a, b in lines:
        da = pixel_to_dir(a[0], a[1], width, height)
        db = pixel_to_dir(b[0], b[1], width, height)
        n = np.cross(da, db)
        norm = np.linalg.norm(n)
        if norm > 1e-9:
            normals.append(n / norm)
    if len(normals) < 2:
        raise ValueError("need at least two non-degenerate vertical lines")
    N = np.array(normals)
    _, _, vt = np.linalg.svd(N)
    z = vt[-1]
    if z[1] < 0:  # zenith points up (positive y)
        z = -z
    return z / np.linalg.norm(z), N


def roll_pitch_from_zenith(z: np.ndarray) -> tuple[float, float]:
    """Roll/pitch (degrees) of the zenith. Pure roll → z=(sin r, cos r, 0)."""

    roll = math.degrees(math.atan2(float(z[0]), float(z[1])))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, float(z[2])))))
    return roll, pitch


def zenith_to_rotation(z: np.ndarray) -> np.ndarray:
    """Rotation matrix R with R·z = (0,1,0) (shortest, yaw-neutral; Rodrigues)."""

    z = z / np.linalg.norm(z)
    up = np.array([0.0, 1.0, 0.0])
    v = np.cross(z, up)
    s = float(np.linalg.norm(v))
    c = float(np.dot(z, up))
    if s < 1e-9:
        return np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1.0 - c) / (s * s))


def yaw_rotation(yaw_deg: float) -> np.ndarray:
    """Rotation about the vertical (y) axis — moves the horizontal center."""
    a = math.radians(yaw_deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def combined_rotation(zenith: np.ndarray, yaw_deg: float = 0.0) -> np.ndarray:
    """Level the pano (zenith→up) then rotate horizontally by yaw."""
    return yaw_rotation(yaw_deg) @ zenith_to_rotation(zenith)


def rotate_equirect(image: np.ndarray, rotation: np.ndarray) -> np.ndarray:
    """Reproject an equirect by ``rotation`` (camera→level). Single-pass remap.

    Computed component-wise in float32 (no (h·w, 3) stack) to keep transient RAM
    moderate: a full 16384×8192 pano peaks around 3–4 GB instead of ~13 GB.
    """

    h, w = image.shape[:2]
    R = np.asarray(rotation, dtype=np.float32)
    lon = ((np.arange(w, dtype=np.float32) / w) - 0.5) * (2.0 * math.pi)
    lat = (0.5 - (np.arange(h, dtype=np.float32) / h)) * math.pi
    cl = np.cos(lat)[:, None]
    sl = np.sin(lat)[:, None]
    so = np.sin(lon)[None, :]
    co = np.cos(lon)[None, :]
    dx = cl * so  # (h, w)
    dz = cl * co
    # level → camera is R.T · d, i.e. cam_j = dx*R[0,j] + dy*R[1,j] + dz*R[2,j]
    # (dy = sl broadcasts (h,1) → (h,w)).
    cam_x = dx * R[0, 0] + sl * R[1, 0] + dz * R[2, 0]
    cam_y = dx * R[0, 1] + sl * R[1, 1] + dz * R[2, 1]
    cam_z = dx * R[0, 2] + sl * R[1, 2] + dz * R[2, 2]
    del dx, dz
    map_x = ((np.arctan2(cam_x, cam_z) / (2.0 * math.pi) + 0.5) * w).astype(np.float32)
    map_y = ((0.5 - np.arcsin(np.clip(cam_y, -1.0, 1.0)) / math.pi) * h).astype(np.float32)
    return cv2.remap(image, map_x, map_y, cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_WRAP)
