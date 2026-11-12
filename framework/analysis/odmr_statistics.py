"""Processing helpers for per-average wide-field ODMR statistics cubes.

The acquisition cube stores a normalized mean and repeat-level sample SD for
each independent frequency sweep. This module combines those completed sweep
statistics without coupling processing to acquisition or GUI code.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from framework.image_cube import ImageCube


_SUPPORTED_AXIS_ORDER = ["average", "statistic", "frequency", "y", "x"]
_SUPPORTED_STATISTICS = ["normalized_mean", "normalized_sd"]


@dataclass(frozen=True)
class ODMRStatisticsResult:
    """Pixel-wise combined ODMR mean and diagnostic uncertainty maps.

    ``fast_sd`` is the equal-average-weight RMS of the per-average repeat
    sample SDs. ``fast_sem`` propagates their variance-of-the-mean estimates
    through the equal-weight final average. ``slow_sd`` and ``slow_sem`` use a
    method-of-moments estimate of between-sweep variation after subtracting
    mean expected fast variance. These uncertainty maps are quality metrics;
    ``final_mean`` is the primary dataset for later ODMR fitting and magnetic
    image extraction.
    """

    final_mean: np.ndarray
    fast_sd: np.ndarray
    slow_sd: np.ndarray
    fast_sem: np.ndarray
    slow_sem: np.ndarray
    combined_sem: np.ndarray
    slow_variance: np.ndarray
    valid_average_count: np.ndarray
    slow_noise_estimable_per_pixel: np.ndarray
    n_averages: int
    n_repeats_per_average: tuple
    frequency_values: np.ndarray
    spatial_shape: tuple
    slow_noise_estimable: bool


def load_odmr_statistics(source):
    """Load and validate ODMR statistics from an ImageCube or HDF5 path."""
    if isinstance(source, ImageCube):
        cube = source
    elif isinstance(source, (str, Path)):
        cube = ImageCube.load(source)
    else:
        raise TypeError("source must be an ImageCube or an HDF5 path.")
    validate_odmr_statistics(cube)
    return cube


def validate_odmr_statistics(cube):
    """Validate the currently supported five-dimensional ODMR cube schema.

    NaN values in the statistic arrays are permitted for pixel-wise missing
    data. Frequency and axes metadata must describe the actual data dimensions.
    """
    if not isinstance(cube, ImageCube):
        raise TypeError("cube must be an ImageCube.")
    if cube.experiment_type != "ODMR":
        raise ValueError(
            "Expected an ODMR ImageCube (experiment_type='ODMR'); "
            f"got {cube.experiment_type!r}."
        )
    if cube.data is None:
        raise ValueError("ODMR statistics cube has no data.")
    data = np.asarray(cube.data)
    if data.ndim != 5:
        raise ValueError(
            "ODMR statistics data must have five dimensions in "
            "(average, statistic, frequency, y, x) order."
        )
    if not np.issubdtype(data.dtype, np.number):
        raise ValueError("ODMR statistics data must have a numeric dtype.")

    axes = cube.axes
    if not isinstance(axes, dict):
        raise ValueError("ODMR axes metadata must be a dictionary.")
    if axes.get("axis_order") != _SUPPORTED_AXIS_ORDER:
        raise ValueError(
            "Unsupported ODMR axis_order; expected "
            "['average', 'statistic', 'frequency', 'y', 'x']."
        )
    required_axes = {"average", "statistic", "frequency", "y", "x"}
    missing_axes = sorted(required_axes.difference(axes))
    if missing_axes:
        raise ValueError(f"ODMR axes metadata is missing: {missing_axes}.")
    if axes["statistic"] != _SUPPORTED_STATISTICS:
        raise ValueError(
            "Unsupported statistic labels; expected exactly "
            "['normalized_mean', 'normalized_sd']."
        )
    expected_lengths = {
        "average": data.shape[0],
        "statistic": data.shape[1],
        "frequency": data.shape[2],
        "y": data.shape[3],
        "x": data.shape[4],
    }
    for axis_name, expected_length in expected_lengths.items():
        values = np.asarray(axes[axis_name])
        if values.ndim != 1 or len(values) != expected_length:
            raise ValueError(
                f"ODMR {axis_name!r} axis must be one-dimensional with "
                f"length {expected_length}; got shape {values.shape}."
            )
    if data.shape[1] != len(_SUPPORTED_STATISTICS):
        raise ValueError("ODMR statistic dimension must have length two.")
    if data.shape[2] < 1 or data.shape[3] < 1 or data.shape[4] < 1:
        raise ValueError("ODMR frequency and spatial dimensions must be non-empty.")

    frequency_values = np.asarray(axes["frequency"], dtype=np.float64)
    scan_values = np.asarray(cube.scan_axis_values, dtype=np.float64)
    if frequency_values.ndim != 1:
        raise ValueError("ODMR frequency axis must be one-dimensional.")
    if scan_values.ndim != 1 or len(scan_values) != data.shape[2]:
        raise ValueError(
            "ImageCube scan_axis_values must contain one frequency per data plane."
        )
    if not np.all(np.isfinite(frequency_values)) or not np.all(np.isfinite(scan_values)):
        raise ValueError("ODMR frequency axis values must be finite.")
    if np.any(np.diff(frequency_values) <= 0):
        raise ValueError("ODMR analysis frequency values must be strictly increasing.")
    if not np.array_equal(frequency_values, scan_values):
        raise ValueError(
            "ODMR axes_json frequency values do not match ImageCube scan_axis_values."
        )
    if cube.scan_axis_name != "frequency":
        raise ValueError("ODMR ImageCube scan_axis_name must be 'frequency'.")
    if cube.scan_axis_unit != "Hz":
        raise ValueError("ODMR ImageCube scan_axis_unit must be 'Hz'.")

    metadata = cube.metadata
    if not isinstance(metadata, dict):
        raise ValueError("ODMR metadata must be a dictionary.")
    if "completed_averages" not in metadata:
        raise ValueError("ODMR metadata is missing 'completed_averages'.")
    completed = _positive_or_zero_integer(
        metadata["completed_averages"], "completed_averages", allow_zero=True
    )
    if completed != data.shape[0]:
        raise ValueError(
            "ODMR completed_averages metadata does not match the data's "
            f"average dimension ({completed} != {data.shape[0]})."
        )
    if "n_averages" in metadata and metadata["n_averages"] != completed:
        raise ValueError("ODMR n_averages metadata disagrees with completed_averages.")
    configured = metadata.get("configured_averages")
    if configured is not None:
        configured = _positive_or_zero_integer(
            configured, "configured_averages", allow_zero=False
        )
        if completed > configured:
            raise ValueError("completed_averages exceeds configured_averages.")
    if metadata.get("experiment_complete") is True and (
        configured is None or completed != configured
    ):
        raise ValueError(
            "ODMR metadata marks the experiment complete but configured and "
            "completed average counts disagree."
        )

    repeat_counts = metadata.get("n_repeats_per_average")
    if not isinstance(repeat_counts, (list, tuple)) or len(repeat_counts) != completed:
        raise ValueError(
            "ODMR n_repeats_per_average must contain exactly one count per "
            "completed average."
        )
    for index, count in enumerate(repeat_counts):
        _positive_or_zero_integer(
            count, f"n_repeats_per_average[{index}]", allow_zero=False
        )

    if "configured_repeats" in metadata:
        configured_repeats = _positive_or_zero_integer(
            metadata["configured_repeats"], "configured_repeats", allow_zero=False
        )
        if any(count > configured_repeats for count in repeat_counts):
            raise ValueError("An actual repeat count exceeds configured_repeats.")

    return True


def get_average_statistics(cube, average_index):
    """Return one average's frequency vector, mean, and SD without mutation."""
    validate_odmr_statistics(cube)
    average_index = _positive_or_zero_integer(
        average_index, "average_index", allow_zero=True
    )
    if average_index >= cube.data.shape[0]:
        raise IndexError(
            f"average_index {average_index} is out of range for "
            f"{cube.data.shape[0]} completed averages."
        )
    data = np.asarray(cube.data)
    return (
        np.asarray(cube.scan_axis_values, dtype=np.float64).copy(),
        data[average_index, 0].copy(),
        data[average_index, 1].copy(),
    )


def calculate_odmr_statistics(cube):
    """Combine completed averages into final mean and diagnostic noise maps.

    The final mean gives every completed average equal weight, independent of
    its repeat count. At each pixel/frequency, averages with a non-finite mean
    or SD (or a negative SD) are omitted; if no usable average remains, output
    maps at that location are NaN. Fast SD is the RMS of usable within-average
    sample SDs. Fast SEM propagates ``SD**2 / N`` through the equal-weight mean.

    Slow variance is estimated as sample variance across usable average means
    minus their mean expected fast variance, clipped at zero. This transparent
    method-of-moments estimate can be noisy with few independent averages. With
    fewer than two usable averages at a location, slow variance is reported as
    zero and the per-pixel estimability flag is false. Uncertainty maps are
    diagnostic/quality measures; ``final_mean`` is the primary dataset for
    subsequent ODMR fitting and magnetic-image extraction.
    """
    validate_odmr_statistics(cube)
    data = np.asarray(cube.data, dtype=np.float64)
    n_averages, _, n_frequencies, height, width = data.shape
    frequency_values = np.asarray(cube.scan_axis_values, dtype=np.float64).copy()
    repeat_counts = tuple(int(n) for n in cube.metadata["n_repeats_per_average"])
    spatial_shape = (height, width)
    output_shape = (n_frequencies, height, width)

    if n_averages == 0:
        empty_map = np.full(output_shape, np.nan, dtype=np.float64)
        zero_count = np.zeros(output_shape, dtype=np.int64)
        false_map = np.zeros(output_shape, dtype=bool)
        return ODMRStatisticsResult(
            final_mean=empty_map.copy(),
            fast_sd=empty_map.copy(),
            slow_sd=empty_map.copy(),
            fast_sem=empty_map.copy(),
            slow_sem=empty_map.copy(),
            combined_sem=empty_map.copy(),
            slow_variance=empty_map.copy(),
            valid_average_count=zero_count,
            slow_noise_estimable_per_pixel=false_map,
            n_averages=0,
            n_repeats_per_average=repeat_counts,
            frequency_values=frequency_values,
            spatial_shape=spatial_shape,
            slow_noise_estimable=False,
        )

    means = data[:, 0]
    sds = data[:, 1]
    usable = np.isfinite(means) & np.isfinite(sds) & (sds >= 0)
    count = usable.sum(axis=0)
    safe_count = np.maximum(count, 1)
    valid_means = np.where(usable, means, 0.0)
    final_mean = valid_means.sum(axis=0) / safe_count
    final_mean[count == 0] = np.nan

    repeats = np.asarray(repeat_counts, dtype=np.float64).reshape(
        (n_averages, 1, 1, 1)
    )
    fast_variance_per_average = np.square(sds) / repeats
    valid_fast_variance = np.where(usable, fast_variance_per_average, 0.0)
    fast_variance_final = valid_fast_variance.sum(axis=0) / np.square(safe_count)
    fast_sem = np.sqrt(fast_variance_final)
    fast_sd = np.sqrt(
        np.where(usable, np.square(sds), 0.0).sum(axis=0) / safe_count
    )

    centered = np.where(usable, means - final_mean[np.newaxis, ...], 0.0)
    observed_between_variance = np.zeros(output_shape, dtype=np.float64)
    enough_for_slow = count >= 2
    observed_between_variance[enough_for_slow] = (
        np.square(centered).sum(axis=0)[enough_for_slow]
        / (count[enough_for_slow] - 1)
    )
    mean_fast_variance = valid_fast_variance.sum(axis=0) / safe_count
    slow_variance = np.maximum(
        observed_between_variance - mean_fast_variance, 0.0
    )
    slow_variance[count == 0] = np.nan
    slow_sd = np.sqrt(slow_variance)
    slow_sem = slow_sd / np.sqrt(safe_count)
    combined_sem = np.sqrt(np.square(fast_sem) + np.square(slow_sem))
    no_valid_averages = count == 0
    for result_map in (fast_sd, fast_sem, slow_sd, slow_sem, combined_sem):
        result_map[no_valid_averages] = np.nan
    slow_estimable_per_pixel = enough_for_slow

    return ODMRStatisticsResult(
        final_mean=final_mean,
        fast_sd=fast_sd,
        slow_sd=slow_sd,
        fast_sem=fast_sem,
        slow_sem=slow_sem,
        combined_sem=combined_sem,
        slow_variance=slow_variance,
        valid_average_count=count,
        slow_noise_estimable_per_pixel=slow_estimable_per_pixel,
        n_averages=n_averages,
        n_repeats_per_average=repeat_counts,
        frequency_values=frequency_values,
        spatial_shape=spatial_shape,
        slow_noise_estimable=n_averages >= 2,
    )


def _positive_or_zero_integer(value, name, allow_zero):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer.")
    value = int(value)
    if value < (0 if allow_zero else 1):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {qualifier}.")
    return value
