"""Running count statistics for the main window's live view (GUI-free).

One sample per live-view frame: the mean counts over the whole frame. The
live-view frame *is* the acquisition ROI (the camera AOI), so this is the
mean over the current acquisition ROI. Used by
gui/widgets/count_statistics_panel.py; nothing here touches the camera.
"""

import csv
import math
from pathlib import Path

import numpy as np

# Pixels compared, besides the exact frame sum, to tell a new frame from
# the same frame returned again (see frame_sample()).
_FINGERPRINT_PIXELS = 64


def frame_sample(frame):
    """(mean counts, fingerprint) of one live-view frame, in one pass.

    The live-view timer polls camera.get_latest_frame(), which returns a
    copy of the newest frame and so can return the same frame on two
    ticks; the drivers keep no frame counter. The fingerprint -- shape,
    exact sum and a strided sample of pixels -- lets the caller drop such
    repeats instead of counting one frame twice. Two distinct noisy frames
    agreeing on all of it is not a practical concern.
    """
    array = np.asarray(frame)
    # Integer frames (the Andor's uint16): an exact int64 sum, ~20% faster
    # than float64 on a full 2560x2160 frame and far from overflow.
    integer = np.issubdtype(array.dtype, np.integer)
    total = float(array.sum(dtype=np.int64 if integer else np.float64))
    mean = total / array.size if array.size else math.nan
    flat = array.reshape(-1)
    step = max(1, flat.size // _FINGERPRINT_PIXELS)
    fingerprint = (array.shape, total, flat[::step][:_FINGERPRINT_PIXELS].tobytes())
    return mean, fingerprint


class RunningStatistics:
    """Count, mean, sample SD (ddof=1), min and max of a stream of values.

    Welford's algorithm: no samples are stored, and the result matches
    numpy's mean/std(ddof=1)/min/max over the same values.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0
        self.mean = math.nan
        self._m2 = 0.0
        self.minimum = math.nan
        self.maximum = math.nan

    def add(self, value):
        value = float(value)
        self.count += 1
        if self.count == 1:
            self.mean = value
            self._m2 = 0.0
            self.minimum = self.maximum = value
            return
        delta = value - self.mean
        self.mean += delta / self.count
        self._m2 += delta * (value - self.mean)
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)

    @property
    def std(self):
        """Sample standard deviation (ddof=1); NaN below two samples."""
        return math.sqrt(self._m2 / (self.count - 1)) if self.count >= 2 else math.nan

    @property
    def std_percent(self):
        """Standard deviation as a percentage of the mean."""
        if self.count < 2 or self.mean == 0:
            return math.nan
        return 100.0 * self.std / abs(self.mean)

    @property
    def ratio(self):
        """max / min; NaN with no samples or a non-positive minimum."""
        if self.count == 0 or self.minimum <= 0:
            return math.nan
        return self.maximum / self.minimum


LOG_COLUMNS = ["timestamp", "mean_counts", "exposure_ms", "binning", "roi"]


def format_roi(roi):
    """Sensor ROI as "x0 y0 x1 y1", or "full" for the whole sensor."""
    return "full" if roi is None else " ".join(str(int(v)) for v in roi)


class CountLog:
    """Append-only CSV of samples, flushed per row so a crash keeps them."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(LOG_COLUMNS)
        self._file.flush()

    def write(self, timestamp, value, exposure_s, binning, roi):
        self._writer.writerow([
            timestamp.isoformat(timespec="milliseconds"),
            f"{value:.6f}",
            f"{exposure_s * 1000.0:.6g}",
            binning,
            format_roi(roi),
        ])
        self._file.flush()

    def close(self):
        if not self._file.closed:
            self._file.close()
