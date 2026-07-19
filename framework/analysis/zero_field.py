"""Analysis routines for Zero Field ImageCube datasets."""

import numpy as np

from framework.fluorescence import mean_fluorescence


def mean_fluorescence_vs_field(image_cube, roi=None):
    """Return magnetic-field values and mean fluorescence for each image.

    The magnetic-field vector is read from the ImageCube scan-axis metadata,
    so the same analysis works for an in-memory acquisition or a cube loaded
    from disk.  ROI validation and clipping are delegated to the shared
    fluorescence and ROI utilities.
    """
    data = image_cube.data
    if data is None:
        raise ValueError("ImageCube contains no acquired images to analyze.")

    data = np.asarray(data)
    if data.ndim < 3:
        raise ValueError(
            "Zero Field analysis requires ImageCube data shaped "
            "(field_point, image_y, image_x)."
        )

    fields = np.asarray(image_cube.scan_axis_values)
    if fields.ndim != 1 or len(fields) != len(data):
        raise ValueError(
            "ImageCube scan-axis metadata must contain one magnetic-field "
            "value for each acquired image."
        )

    signals = np.asarray([mean_fluorescence(frame, roi) for frame in data])
    return fields, signals
