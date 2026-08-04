"""Pure helpers for inspecting images already held by visualization widgets."""

import numpy as np


def pixel_value(image, x, y):
    """Return the displayed image value at integer ``(x, y)``, or ``None``."""
    image = np.asarray(image)
    if image.ndim < 2:
        return None
    if not (0 <= x < image.shape[1] and 0 <= y < image.shape[0]):
        return None
    return image[y, x]


def line_profile(image, start, end):
    """Sample a nearest-neighbor intensity profile and its pixel distance axis."""
    image = np.asarray(image)
    if image.ndim < 2:
        raise ValueError("Line profiles require a two-dimensional image.")

    x0, y0 = start
    x1, y1 = end
    distance = float(np.hypot(x1 - x0, y1 - y0))
    sample_count = max(int(np.ceil(distance)) + 1, 1)
    x_coordinates = np.rint(np.linspace(x0, x1, sample_count)).astype(int)
    y_coordinates = np.rint(np.linspace(y0, y1, sample_count)).astype(int)
    x_coordinates = np.clip(x_coordinates, 0, image.shape[1] - 1)
    y_coordinates = np.clip(y_coordinates, 0, image.shape[0] - 1)
    distances = np.linspace(0.0, distance, sample_count)
    return distances, image[y_coordinates, x_coordinates]
