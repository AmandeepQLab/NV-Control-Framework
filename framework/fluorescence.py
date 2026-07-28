"""Utilities for reporting fluorescence from camera images."""

import numpy as np

from framework.roi import extract_roi


def mean_fluorescence(frame, roi=None):
    """Return mean camera counts per pixel in an image-local ROI.

    ``roi`` is an optional software-analysis ROI.  A camera acquisition ROI
    must not be passed here: hardware-cropped frames are already local.
    """
    return np.mean(extract_roi(frame, roi))
