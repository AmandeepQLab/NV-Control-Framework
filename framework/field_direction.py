"""Field direction helpers shared by the Zero Field and ODMR experiments.

GUI-free and hardware-free: pure arithmetic on 3-component vectors.
"""

import math

import numpy as np


def unit_direction(direction):
    """Normalise a 3-component direction to a unit vector (list of floats).

    Raises ValueError for anything that is not three finite numbers, and
    for a zero vector, which has no direction.
    """
    if direction is None or isinstance(direction, (str, bytes)):
        raise ValueError("field_direction must be three numbers [nx, ny, nz].")
    values = list(direction)
    if len(values) != 3 or any(
        isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating))
        for v in values
    ):
        raise ValueError("field_direction must be three numbers [nx, ny, nz].")
    vector = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(vector)):
        raise ValueError("field_direction must be finite.")
    norm = float(np.linalg.norm(vector))
    if norm < 1e-12:
        raise ValueError("field_direction is a zero vector; it has no direction.")
    if abs(norm - 1.0) < 1e-12:
        # Already a unit vector: returned unchanged, so normalising twice
        # (widget, queue file, experiment) never alters the last digit.
        return [float(v) for v in vector]
    return [float(v) / norm for v in vector]


def vector_along(magnitude, direction):
    """[x, y, z] = magnitude * n̂, with n̂ = unit_direction(direction).

    The magnitude is signed: a negative value points opposite to the
    direction. Units are those of *magnitude*. ``+ 0.0`` keeps a zero
    component from becoming -0.0. Raises ValueError for a non-finite
    magnitude or an invalid direction.
    """
    if isinstance(magnitude, bool) or not isinstance(
        magnitude, (int, float, np.integer, np.floating)
    ) or not math.isfinite(float(magnitude)):
        raise ValueError("field magnitude must be a finite number.")
    unit = unit_direction(direction)
    return [float(magnitude) * n + 0.0 for n in unit]
