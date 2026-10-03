"""Guete-Bewertung der Ausrichtung (reine Funktionen)."""

from __future__ import annotations

import math

import numpy as np

GOOD_RMS_DEG = 0.8
GOOD_MAX_DEG = 2.0
MIN_LINES = 3


def line_residuals_deg(normals: np.ndarray, zenith: np.ndarray) -> np.ndarray:
    """Winkelabweichung jeder Linie von der Senkrechten (Grad): asin(|n . z|)."""
    n = np.asarray(normals, dtype=float)
    z = np.asarray(zenith, dtype=float)
    z = z / np.linalg.norm(z)
    return np.degrees(np.arcsin(np.clip(np.abs(n @ z), 0.0, 1.0)))


def rate(residuals_deg, n_lines: int | None = None) -> tuple[str, float]:
    """('gut'|'pruefen', rms_grad). Weniger als 3 Linien oder grosse Widersprueche -> pruefen."""
    r = np.asarray(residuals_deg, dtype=float)
    if r.size == 0:
        return "pruefen", math.nan
    rms = float(np.sqrt(np.mean(r**2)))
    n = r.size if n_lines is None else n_lines
    ok = n >= MIN_LINES and rms <= GOOD_RMS_DEG and float(r.max()) <= GOOD_MAX_DEG
    return ("gut" if ok else "pruefen"), rms
