"""Pixel-wise fitting of the final mean ODMR image cube."""

from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from analysis.odmr_fit import (
    TwoLorentzianFitResult,
    TwoLorentzianInitialGuess,
    fit_two_lorentzian_spectrum,
)


class PixelFitStatus(IntEnum):
    """Machine-readable outcomes for a pixel spectrum fit."""

    SUCCESS = 0
    INSUFFICIENT_SAMPLES = 1
    UNUSABLE_SPECTRUM = 2
    OPTIMIZER_FAILURE = 3
    INVALID_RESULT = 4


PIXEL_FIT_STATUS_LABELS = {
    PixelFitStatus.SUCCESS: "success",
    PixelFitStatus.INSUFFICIENT_SAMPLES: "insufficient_samples",
    PixelFitStatus.UNUSABLE_SPECTRUM: "unusable_spectrum",
    PixelFitStatus.OPTIMIZER_FAILURE: "optimizer_failure",
    PixelFitStatus.INVALID_RESULT: "invalid_result",
}


@dataclass(frozen=True)
class ODMRPixelFitResult:
    """Per-pixel resonance, quality, uncertainty, and status maps.

    Frequency-valued maps and frequency uncertainties are in Hz. All map
    fields have shape ``(y, x)``. Failed pixels retain NaN fit quantities and
    a non-success status code.
    """

    baseline: np.ndarray
    amplitude_minus: np.ndarray
    amplitude_plus: np.ndarray
    f_minus_hz: np.ndarray
    f_plus_hz: np.ndarray
    splitting_hz: np.ndarray
    center_frequency_hz: np.ndarray
    linewidth_minus_hz: np.ndarray
    linewidth_plus_hz: np.ndarray
    rmse: np.ndarray
    r2: np.ndarray
    f_minus_uncertainty_hz: np.ndarray
    f_plus_uncertainty_hz: np.ndarray
    status: np.ndarray
    valid_spectrum_mask: np.ndarray
    successful_fit_mask: np.ndarray
    finite_sample_count: np.ndarray
    valid_pixel_count: int
    successful_fit_count: int
    failed_fit_count: int
    success_fraction: float
    frequency_hz: np.ndarray


def fit_odmr_pixels(mean_cube, global_fit, min_valid_samples=8):
    """Fit every pixel spectrum using a successful global fit as its template.

    ``mean_cube`` is the normalized final mean with shape ``(frequency, y,
    x)``. Frequencies are taken from ``global_fit.frequency_hz`` and must be
    in Hz, matching the statistics-cube convention. Non-finite samples are
    omitted independently per pixel; unusable spectra and fit failures are
    recorded in the status map and do not abort processing of other pixels.
    """
    mean_cube = np.asarray(mean_cube, dtype=np.float64)
    if mean_cube.ndim != 3:
        raise ValueError("mean_cube must have shape (frequency, y, x).")
    if not isinstance(global_fit, TwoLorentzianFitResult):
        raise TypeError("global_fit must be a TwoLorentzianFitResult.")
    if not global_fit.success:
        raise ValueError("global_fit must be successful before pixel fitting.")
    if isinstance(min_valid_samples, (bool, np.bool_)) or not isinstance(
        min_valid_samples, (int, np.integer)
    ) or min_valid_samples < 8:
        raise ValueError("min_valid_samples must be an integer of at least 8.")

    frequency_hz = np.asarray(global_fit.frequency_hz, dtype=np.float64)
    if frequency_hz.ndim != 1 or frequency_hz.size != mean_cube.shape[0]:
        raise ValueError("global_fit frequency axis must match mean_cube frequency length.")
    if (
        not np.all(np.isfinite(frequency_hz))
        or np.any(np.diff(frequency_hz) <= 0)
    ):
        raise ValueError("global_fit frequencies must be finite and strictly increasing.")

    global_template = global_fit.as_initial_guess()
    _validate_global_template(global_template, frequency_hz)

    _, height, width = mean_cube.shape
    map_shape = (height, width)
    float_maps = {
        name: np.full(map_shape, np.nan, dtype=np.float64)
        for name in (
            "baseline", "amplitude_minus", "amplitude_plus", "f_minus_hz",
            "f_plus_hz", "splitting_hz", "center_frequency_hz",
            "linewidth_minus_hz", "linewidth_plus_hz", "rmse", "r2",
            "f_minus_uncertainty_hz", "f_plus_uncertainty_hz",
        )
    }
    status = np.full(map_shape, PixelFitStatus.INSUFFICIENT_SAMPLES, dtype=np.uint8)
    finite_sample_count = np.zeros(map_shape, dtype=np.int32)
    valid_spectrum_mask = np.zeros(map_shape, dtype=bool)

    for y_index in range(height):
        for x_index in range(width):
            spectrum = mean_cube[:, y_index, x_index]
            finite = np.isfinite(spectrum)
            n_finite = int(finite.sum())
            finite_sample_count[y_index, x_index] = n_finite
            if n_finite < min_valid_samples:
                status[y_index, x_index] = PixelFitStatus.INSUFFICIENT_SAMPLES
                continue

            finite_values = spectrum[finite]
            local_range = float(np.ptp(finite_values))
            if not np.isfinite(local_range) or local_range <= 0:
                status[y_index, x_index] = PixelFitStatus.UNUSABLE_SPECTRUM
                continue
            valid_spectrum_mask[y_index, x_index] = True

            local_guess = _local_initial_guess(
                frequency_hz, spectrum, finite, global_template, local_range
            )
            try:
                fit = fit_two_lorentzian_spectrum(
                    frequency_hz, spectrum, initial_guess=local_guess
                )
            except (ValueError, RuntimeError, FloatingPointError):
                status[y_index, x_index] = PixelFitStatus.OPTIMIZER_FAILURE
                continue

            if not fit.success:
                status[y_index, x_index] = PixelFitStatus.OPTIMIZER_FAILURE
                continue
            if not _fit_result_is_physical(fit, frequency_hz):
                status[y_index, x_index] = PixelFitStatus.INVALID_RESULT
                continue

            status[y_index, x_index] = PixelFitStatus.SUCCESS
            values = {
                "baseline": fit.baseline,
                "amplitude_minus": fit.amplitude_minus,
                "amplitude_plus": fit.amplitude_plus,
                "f_minus_hz": fit.frequency_minus_hz,
                "f_plus_hz": fit.frequency_plus_hz,
                "splitting_hz": fit.frequency_plus_hz - fit.frequency_minus_hz,
                "center_frequency_hz": (
                    fit.frequency_plus_hz + fit.frequency_minus_hz
                ) / 2.0,
                "linewidth_minus_hz": fit.linewidth_minus_hz,
                "linewidth_plus_hz": fit.linewidth_plus_hz,
                "rmse": fit.rmse,
                "r2": fit.r2,
            }
            for name, value in values.items():
                float_maps[name][y_index, x_index] = value

            errors = fit.standard_errors
            if (
                fit.covariance is not None
                and errors is not None
                and np.shape(fit.covariance) == (7, 7)
                and np.all(np.isfinite(fit.covariance))
                and np.shape(errors) == (7,)
                and np.isfinite(errors[2])
                and np.isfinite(errors[5])
                and errors[2] >= 0
                and errors[5] >= 0
            ):
                float_maps["f_minus_uncertainty_hz"][y_index, x_index] = errors[2]
                float_maps["f_plus_uncertainty_hz"][y_index, x_index] = errors[5]

    success_mask = status == PixelFitStatus.SUCCESS
    valid_pixel_count = int(valid_spectrum_mask.sum())
    successful_fit_count = int(success_mask.sum())
    failed_fit_count = int(status.size - successful_fit_count)
    success_fraction = (
        successful_fit_count / valid_pixel_count if valid_pixel_count else 0.0
    )
    return ODMRPixelFitResult(
        **float_maps,
        status=status,
        valid_spectrum_mask=valid_spectrum_mask,
        successful_fit_mask=success_mask,
        finite_sample_count=finite_sample_count,
        valid_pixel_count=valid_pixel_count,
        successful_fit_count=successful_fit_count,
        failed_fit_count=failed_fit_count,
        success_fraction=float(success_fraction),
        frequency_hz=frequency_hz.copy(),
    )


def _validate_global_template(template, frequency_hz):
    values = template.as_array()
    if not np.all(np.isfinite(values)):
        raise ValueError("global_fit parameters must all be finite.")
    if template.amplitude_minus <= 0 or template.amplitude_plus <= 0:
        raise ValueError("global_fit amplitudes must be positive.")
    if template.linewidth_minus_hz <= 0 or template.linewidth_plus_hz <= 0:
        raise ValueError("global_fit linewidths must be positive.")
    if template.frequency_minus_hz >= template.frequency_plus_hz:
        raise ValueError("global_fit must satisfy f_minus < f_plus.")
    if not (
        frequency_hz[0] <= template.frequency_minus_hz
        < template.frequency_plus_hz <= frequency_hz[-1]
    ):
        raise ValueError("global_fit resonance centers must lie in the measured band.")


def _local_initial_guess(frequency_hz, spectrum, finite, global_template, local_range):
    valid_spectrum = spectrum[finite]
    baseline = float(np.percentile(valid_spectrum, 90.0))
    local_amplitudes = []
    for center_hz, global_amplitude in (
        (global_template.frequency_minus_hz, global_template.amplitude_minus),
        (global_template.frequency_plus_hz, global_template.amplitude_plus),
    ):
        nearest_index = int(np.argmin(np.abs(frequency_hz - center_hz)))
        if finite[nearest_index]:
            estimate = baseline - float(spectrum[nearest_index])
        else:
            estimate = float("nan")
        amplitude_floor = max(local_range * 1e-6, np.finfo(np.float64).eps)
        if not np.isfinite(estimate) or estimate <= amplitude_floor:
            estimate = global_amplitude
        local_amplitudes.append(float(estimate))

    return TwoLorentzianInitialGuess(
        baseline=baseline,
        amplitude_minus=local_amplitudes[0],
        frequency_minus_hz=global_template.frequency_minus_hz,
        linewidth_minus_hz=global_template.linewidth_minus_hz,
        amplitude_plus=local_amplitudes[1],
        frequency_plus_hz=global_template.frequency_plus_hz,
        linewidth_plus_hz=global_template.linewidth_plus_hz,
    )


def _fit_result_is_physical(fit, frequency_hz):
    parameters = fit.params
    return bool(
        np.all(np.isfinite(parameters))
        and fit.amplitude_minus > 0
        and fit.amplitude_plus > 0
        and fit.linewidth_minus_hz > 0
        and fit.linewidth_plus_hz > 0
        and frequency_hz[0] <= fit.frequency_minus_hz
        < fit.frequency_plus_hz <= frequency_hz[-1]
        and np.isfinite(fit.rmse)
        and np.isfinite(fit.r2)
    )
