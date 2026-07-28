"""Reusable validation and extraction helpers for image regions of interest."""

from numbers import Real

import numpy as np


def validate_roi(roi):
    """Return a normalized ``(x0, y0, x1, y1)`` ROI or ``None``.

    Coordinates must be finite, integer-valued numbers and define a
    non-empty region before image-boundary clipping is applied.
    """
    if roi is None:
        return None

    try:
        coordinates = tuple(roi)
    except TypeError as error:
        raise ValueError(
            "ROI must be None or an iterable of four coordinates "
            "(x0, y0, x1, y1)."
        ) from error

    if len(coordinates) != 4:
        raise ValueError(
            "ROI must contain exactly four coordinates (x0, y0, x1, y1)."
        )

    normalized = []
    for name, value in zip(("x0", "y0", "x1", "y1"), coordinates):
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
            raise ValueError(f"ROI coordinate {name} must be a numeric value.")
        if not np.isfinite(value) or int(value) != value:
            raise ValueError(
                f"ROI coordinate {name} must be a finite integer-valued number."
            )
        normalized.append(int(value))

    x0, y0, x1, y1 = normalized
    if x0 >= x1:
        raise ValueError("ROI must satisfy x0 < x1; zero-width or reversed ROI received.")
    if y0 >= y1:
        raise ValueError("ROI must satisfy y0 < y1; zero-height or reversed ROI received.")

    return x0, y0, x1, y1


def clip_roi(roi, image_shape):
    """Clip a valid ROI to image boundaries, rejecting no-overlap regions."""
    validated_roi = validate_roi(roi)
    if validated_roi is None:
        return None

    if len(image_shape) < 2:
        raise ValueError("ROI extraction requires an image with at least two dimensions.")

    height, width = image_shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("ROI extraction requires a non-empty image.")

    x0, y0, x1, y1 = validated_roi
    clipped_roi = (
        max(0, min(width, x0)),
        max(0, min(height, y0)),
        max(0, min(width, x1)),
        max(0, min(height, y1)),
    )

    clipped_x0, clipped_y0, clipped_x1, clipped_y1 = clipped_roi
    if clipped_x0 >= clipped_x1 or clipped_y0 >= clipped_y1:
        raise ValueError("ROI is completely outside the image boundaries.")

    return clipped_roi


def prepare_roi(image, roi):
    """Return ``(validated_roi, clipped_roi, region)`` for an image and ROI."""
    image = np.asarray(image)
    if image.ndim < 2:
        raise ValueError("ROI extraction requires an image with at least two dimensions.")

    validated_roi = validate_roi(roi)
    if validated_roi is None:
        return None, None, image

    clipped_roi = clip_roi(validated_roi, image.shape)
    x0, y0, x1, y1 = clipped_roi
    region = image[y0:y1, x0:x1]
    return validated_roi, clipped_roi, region


def extract_roi(image, roi):
    """Return the clipped ROI region, or the full image when ``roi`` is None."""
    return prepare_roi(image, roi)[2]
