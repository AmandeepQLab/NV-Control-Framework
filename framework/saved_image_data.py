"""What a Zero Field run writes to disk: full images, binned images, or
only the mean over the image ("saved image data").

GUI-free; used by ZeroFieldExperiment (reducing each stored plane), the
Zero Field window and queue (settings, size estimate) and by the readers
(analysis/zero_field_averaging.py, the scripts) to tell whether a file holds
images at all.

Modes (``saved_image_mode``):

- ``"full"``: every plane as acquired (the default; the layout as before).
- ``"binned"``: each saved plane averaged over ``bin_factor`` x
  ``bin_factor`` blocks (mean, not sum, so counts/pixel stay comparable).
  Trailing rows/columns that do not fill a block are dropped.
- ``"roi_mean"``: no images. Each plane becomes one value, its mean over the
  whole image (= the acquisition ROI, the region behind the live "Mean
  Fluorescence"), stored as a 1 x 1 plane so the first axis is still the
  field point. Per-frame means are stored alongside (frame_means).

Only what is written changes: acquisition, timing, magnet commands and the
ratio (computed per pixel on the full planes) are the same in every mode.
"""

import math

import numpy as np

FULL = "full"
BINNED = "binned"
ROI_MEAN = "roi_mean"
MODES = (FULL, BINNED, ROI_MEAN)
BIN_FACTORS = (2, 4, 8)
DEFAULT_MODE = FULL
DEFAULT_BIN_FACTOR = 2

MODE_LABELS = {
    FULL: "Full images",
    BINNED: "Binned images",
    ROI_MEAN: "ROI mean only",
}

NO_IMAGES_MESSAGE = "No images saved in this file (ROI mean mode)."

# Per-point extra datasets holding the per-frame means of one scan, shape
# (field points, scans, frames per point); ROI mean mode only.
FRAME_MEANS = "frame_means"
REFERENCE_FRAME_MEANS = "reference_frame_means"


def validate(mode, bin_factor=None):
    """(mode, bin_factor) normalised; bin_factor is None unless binned.
    Raises ValueError for an unknown mode or factor."""
    if mode not in MODES:
        raise ValueError(f"saved_image_mode must be one of {MODES}, not {mode!r}")
    if mode != BINNED:
        return mode, None
    if isinstance(bin_factor, bool) or bin_factor not in BIN_FACTORS:
        raise ValueError(f"bin_factor must be one of {BIN_FACTORS}, not {bin_factor!r}")
    return mode, int(bin_factor)


def file_mode(metadata):
    """The saved_image_mode a file's metadata records ("full" if absent:
    every file written before the setting existed has full images)."""
    return (metadata or {}).get("saved_image_mode", FULL)


def has_images(metadata):
    return file_mode(metadata) != ROI_MEAN


def require_images(metadata, source="this file"):
    """Raise ValueError with NO_IMAGES_MESSAGE if *metadata* says the file
    holds no images (ROI mean mode)."""
    if not has_images(metadata):
        raise ValueError(f"{source}: {NO_IMAGES_MESSAGE}")


# ---- reducing one plane ---------------------------------------------------

def binned_shape(shape, factor):
    height, width = shape[:2]
    return (height // factor, width // factor)


def cropped_pixels(shape, factor):
    """(rows, columns) dropped because they do not fill a whole block."""
    height, width = shape[:2]
    return (height % factor, width % factor)


def bin_image(image, factor):
    """Mean over factor x factor blocks, float64. Trailing rows/columns that
    don't fill a block are dropped; an image smaller than one block is
    refused (there would be nothing to save)."""
    image = np.asarray(image, dtype=np.float64)
    height, width = binned_shape(image.shape, factor)
    if height < 1 or width < 1:
        raise ValueError(
            f"Image {image.shape[0]} x {image.shape[1]} px is smaller than one "
            f"{factor} x {factor} bin."
        )
    blocks = image[: height * factor, : width * factor]
    return blocks.reshape(height, factor, width, factor).mean(axis=(1, 3))


def reduce_plane(image, mode, bin_factor=None):
    """The plane to store for *image* in *mode*. Full: *image* itself
    (unchanged, no copy). Binned: bin_image(). ROI mean: a 1 x 1 plane
    holding np.mean(image) (NaN pixels, e.g. a ratio's, give NaN, as the
    mean of a full plane would)."""
    if mode == FULL:
        return image
    if mode == BINNED:
        return bin_image(image, bin_factor)
    if mode == ROI_MEAN:
        return np.full((1, 1), np.mean(np.asarray(image, dtype=np.float64)))
    raise ValueError(f"Unknown saved_image_mode {mode!r}")


def saved_shape(shape, mode, bin_factor=None):
    if mode == BINNED:
        return binned_shape(shape, bin_factor)
    if mode == ROI_MEAN:
        return (1, 1)
    return tuple(shape[:2])


def plane_metadata(mode, bin_factor, acquired_shape, acquisition_roi):
    """Metadata describing the stored planes, once the image shape is known."""
    height, width = (int(n) for n in acquired_shape[:2])
    metadata = {
        "saved_image_mode": mode,
        "bin_factor": bin_factor if mode == BINNED else None,
        "image_shape_acquired": [height, width],
        "image_shape_saved": list(saved_shape((height, width), mode, bin_factor)),
    }
    if mode == BINNED:
        metadata["binning_cropped_px"] = list(cropped_pixels((height, width), bin_factor))
    if mode == ROI_MEAN:
        # The region every value is the mean of: the whole acquired image,
        # which is the camera's acquisition ROI (full sensor if None).
        metadata["roi_mean_region_px"] = [0, 0, width, height]
        metadata["roi_mean_acquisition_roi"] = (
            None if acquisition_roi is None else list(acquisition_roi)
        )
    return metadata


DEFINITIONS = {
    BINNED: {
        "saved_image_mode": (
            "'binned': data, reference, ratio (and the direction averages) "
            "are the acquired planes averaged over bin_factor x bin_factor "
            "pixel blocks (mean, so counts/pixel are unchanged). Trailing "
            "rows/columns that did not fill a block (binning_cropped_px: "
            "[rows, columns]) are not saved. ratio is the bin mean of the "
            "per-pixel ratio. image_shape_acquired / image_shape_saved: "
            "[height, width] before / after binning."
        ),
    },
    ROI_MEAN: {
        "saved_image_mode": (
            "'roi_mean': no images saved. data, reference and ratio (and the "
            "direction averages) hold one value per point, shape (points, 1, "
            "1): the mean over the whole acquired image (roi_mean_region_px "
            "[x0, y0, x1, y1] in image pixels, i.e. the acquisition ROI "
            "roi_mean_acquisition_roi in sensor pixels; null = full sensor), "
            "the same region as the live 'Mean Fluorescence'. ratio is the "
            "image mean of the per-pixel signal/reference ratio, as a full "
            "file's ratio averaged over its pixels."
        ),
        FRAME_MEANS: (
            "Dataset (roi_mean only): image mean of every single camera frame "
            "of the signal half, shape (points, scans, frames per point), in "
            "the same point order as data; averaged over scans and frames it "
            "equals data. A multi-scan average keeps every completed scan "
            "(axis 1, scan order). reference_frame_means: the same for the "
            "reference half."
        ),
    },
}


# ---- estimated size on disk ----------------------------------------------

# gzip ratios (stored / raw) measured on the rig's files (2026-11-27, 41 x
# 450 x 516 float64, 20 frames per point): count planes ~0.40, ratio planes
# ~0.77. Binned planes are means of many more values and compress about as
# badly as the ratio.
_COUNTS_RATIO_FULL = 0.40
_COUNTS_RATIO_BINNED = 0.77
_RATIO_RATIO = 0.77
# Metadata JSON and HDF5 structure per file (measured in --sim; the
# per-point lists dominate).
_FILE_OVERHEAD_BYTES = 20_000
_FILE_OVERHEAD_PER_POINT = 1_000


def acquired_image_shape(acquisition_roi, binning, camera=None):
    """(height, width) of the frames a run would acquire, or None if not
    known yet. From the acquisition ROI and binning; for the full sensor,
    from the simulator's sensor shape or the camera's last frame. Reads
    cached attributes only (no camera I/O)."""
    try:
        b = max(1, int(binning or 1))
    except (TypeError, ValueError):
        b = 1
    if acquisition_roi is not None:
        try:
            x0, y0, x1, y1 = acquisition_roi
            return (max(1, round((y1 - y0) / b)), max(1, round((x1 - x0) / b)))
        except (TypeError, ValueError):
            return None
    sensor = getattr(camera, "image_shape", None)
    if isinstance(sensor, tuple) and len(sensor) >= 2:
        return (max(1, sensor[0] // b), max(1, sensor[1] // b))
    frame = getattr(camera, "latest_frame", None)
    if isinstance(frame, np.ndarray) and frame.ndim == 2:
        return tuple(int(n) for n in frame.shape)
    return None


def estimate_run_bytes(config, image_shape):
    """Approximate bytes a run with *config* (ZeroFieldWidget.get_config())
    writes, or None if the image shape is unknown (and needed). 0 when
    saving is off. Every file of the run: the single-scan file, or the
    average plus any kept individual scans."""
    if not config.get("save_data", True):
        return 0
    mode = config.get("saved_image_mode", FULL)
    factor = config.get("bin_factor", DEFAULT_BIN_FACTOR)
    points = int(config["field_points"])
    repeats = int(config["averages"])
    scans = int(config["num_scans"]) if config.get("averaging_enabled") else 1
    reference = bool(config.get("reference_enabled"))
    alternate = bool(config.get("alternate_direction")) and scans > 1

    if mode == ROI_MEAN:
        pixels = 1
        counts_ratio = ratio_ratio = 1.0
    else:
        if image_shape is None:
            return None
        shape = saved_shape(image_shape, mode, factor)
        pixels = max(1, shape[0] * shape[1])
        counts_ratio = _COUNTS_RATIO_FULL if mode == FULL else _COUNTS_RATIO_BINNED
        ratio_ratio = _RATIO_RATIO
    plane_bytes = points * pixels * 8

    def file_bytes(counts_planes, ratio_planes, frame_scans):
        size = plane_bytes * (counts_planes * counts_ratio + ratio_planes * ratio_ratio)
        if mode == ROI_MEAN:
            size += points * frame_scans * repeats * 8 * (2 if reference else 1)
        return size + _FILE_OVERHEAD_BYTES + _FILE_OVERHEAD_PER_POINT * points

    single = file_bytes(1 + reference, int(reference), 1)
    if scans == 1:
        return int(single)
    average = file_bytes(
        1 + reference + (2 if alternate else 0),
        int(reference) * (3 if alternate else 1),
        scans,
    )
    raw = scans * single if config.get("save_raw_scans") else 0
    return int(average + raw)


def format_bytes(size):
    """"≈ 287 MB", "≈ 1.2 GB", "≈ 45 kB"."""
    if size is None:
        return "size unknown"
    units = (("GB", 1e9), ("MB", 1e6), ("kB", 1e3))
    for unit, scale in units:
        if size >= scale:
            value = size / scale
            text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
            return f"≈ {text} {unit}"
    return f"≈ {int(math.ceil(size))} B"
