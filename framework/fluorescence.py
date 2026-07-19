"""Utilities for reporting fluorescence from camera images."""

import numpy as np

from framework.roi import extract_roi


def mean_fluorescence(frame, roi=None):
    """Return the mean camera counts per pixel in *roi* or the full frame."""
    return np.mean(extract_roi(frame, roi))
