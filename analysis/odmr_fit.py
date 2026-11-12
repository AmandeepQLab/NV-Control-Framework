"""Scalar ODMR fitting models.

The original fitting helpers at the bottom of this module accept GHz and are
kept unchanged for compatibility. The canonical analysis API added here uses
Hz, matching the persisted ODMR statistics frequency axis.
"""

from dataclasses import dataclass
import warnings

import numpy as np
from scipy.optimize import curve_fit


def lorentzian_dip(f, baseline, amp, f0, fwhm):
    return baseline - amp / (1 + ((f - f0) / (fwhm / 2)) ** 2)


def double_lorentzian_dip(f, baseline, amp1, f01, fwhm1, amp2, f02, fwhm2):
    dip1 = amp1 / (1 + ((f - f01) / (fwhm1 / 2)) ** 2)
    dip2 = amp2 / (1 + ((f - f02) / (fwhm2 / 2)) ** 2)
    return baseline - dip1 - dip2


def _fit_quality(y, yfit, n_params):
    residuals = y - yfit
    rmse = np.sqrt(np.mean(residuals ** 2))
    noise = np.std(residuals)

    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot != 0 else 0

    return residuals, rmse, noise, r2


def fit_single_lorentzian(freq_ghz, signal_percent, initial_guess=None):
    x = np.array(freq_ghz, dtype=float)
    y = np.array(signal_percent, dtype=float)

    baseline0 = np.max(y)
    amp0 = baseline0 - np.min(y)
    f0_0 = x[np.argmin(y)]
    fwhm0 = (np.max(x) - np.min(x)) / 10

    p0 = [baseline0, amp0, f0_0, fwhm0]
    if initial_guess is not None:
        if len(initial_guess) != 4:
            raise ValueError("Single Lorentzian initial_guess must contain 4 parameters.")
        p0 = [automatic if value is None else float(value)
              for automatic, value in zip(p0, initial_guess)]

    popt, pcov = curve_fit(
        lorentzian_dip,
        x,
        y,
        p0=p0,
        maxfev=20000
    )

    yfit = lorentzian_dip(x, *popt)

    residuals, rmse, noise, r2 = _fit_quality(y, yfit, len(popt))

    baseline, amp, f0, fwhm = popt

    snr = amp / noise if noise != 0 else np.inf

    return {
        "fit_type": "single",
        "params": popt,
        "fit_y": yfit,
        "baseline": baseline,
        "contrast": amp,
        "f0": f0,
        "fwhm": abs(fwhm),
        "rmse": rmse,
        "r2": r2,
        "snr": snr
    }


def fit_double_lorentzian(freq_ghz, signal_percent, initial_guess=None):
    x = np.array(freq_ghz, dtype=float)
    y = np.array(signal_percent, dtype=float)

    baseline0 = np.max(y)

    idx_sorted = np.argsort(y)
    f01_0 = x[idx_sorted[0]]
    f02_0 = x[idx_sorted[min(5, len(idx_sorted) - 1)]]

    amp0 = baseline0 - np.min(y)
    fwhm0 = (np.max(x) - np.min(x)) / 20

    p0 = [
        baseline0,
        amp0 / 2, f01_0, fwhm0,
        amp0 / 2, f02_0, fwhm0
    ]
    if initial_guess is not None:
        if len(initial_guess) != 7:
            raise ValueError("Double Lorentzian initial_guess must contain 7 parameters.")
        p0 = [automatic if value is None else float(value)
              for automatic, value in zip(p0, initial_guess)]

    popt, pcov = curve_fit(
        double_lorentzian_dip,
        x,
        y,
        p0=p0,
        maxfev=50000
    )

    yfit = double_lorentzian_dip(x, *popt)

    residuals, rmse, noise, r2 = _fit_quality(y, yfit, len(popt))

    baseline, amp1, f01, fwhm1, amp2, f02, fwhm2 = popt

    snr1 = amp1 / noise if noise != 0 else np.inf
    snr2 = amp2 / noise if noise != 0 else np.inf

    return {
        "fit_type": "double",
        "params": popt,
        "fit_y": yfit,
        "baseline": baseline,
        "contrast1": amp1,
        "contrast2": amp2,
        "f01": f01,
        "f02": f02,
        "fwhm1": abs(fwhm1),
        "fwhm2": abs(fwhm2),
        "rmse": rmse,
        "r2": r2,
        "snr1": snr1,
        "snr2": snr2
    }


@dataclass(frozen=True)
class TwoLorentzianInitialGuess:
    """Reusable ordered initial parameters; frequencies and widths are Hz."""

    baseline: float
    amplitude_minus: float
    frequency_minus_hz: float
    linewidth_minus_hz: float
    amplitude_plus: float
    frequency_plus_hz: float
    linewidth_plus_hz: float

    def as_array(self):
        """Return parameters in the canonical fitter order."""
        return np.asarray(
            [
                self.baseline,
                self.amplitude_minus,
                self.frequency_minus_hz,
                self.linewidth_minus_hz,
                self.amplitude_plus,
                self.frequency_plus_hz,
                self.linewidth_plus_hz,
            ],
            dtype=np.float64,
        )


@dataclass(frozen=True)
class TwoLorentzianFitResult:
    """Fit products for one spectrum, with all frequency quantities in Hz."""

    baseline: float
    amplitude_minus: float
    frequency_minus_hz: float
    linewidth_minus_hz: float
    amplitude_plus: float
    frequency_plus_hz: float
    linewidth_plus_hz: float
    frequency_hz: np.ndarray
    spectrum: np.ndarray
    fitted_spectrum: np.ndarray
    residuals: np.ndarray
    rmse: float
    r2: float
    success: bool
    status: str
    message: str
    covariance: np.ndarray | None
    standard_errors: np.ndarray | None
    initial_guess: TwoLorentzianInitialGuess
    n_valid_points: int
    n_valid_pixels_per_frequency: np.ndarray | None = None

    @property
    def params(self):
        """Canonical seven-element parameter vector."""
        return TwoLorentzianInitialGuess(
            self.baseline,
            self.amplitude_minus,
            self.frequency_minus_hz,
            self.linewidth_minus_hz,
            self.amplitude_plus,
            self.frequency_plus_hz,
            self.linewidth_plus_hz,
        ).as_array()

    @property
    def fitted_y(self):
        """Compatibility-style short name for the fitted spectrum."""
        return self.fitted_spectrum

    def as_initial_guess(self):
        """Return the fitted parameters as a reusable initialization template."""
        return TwoLorentzianInitialGuess(
            self.baseline,
            self.amplitude_minus,
            self.frequency_minus_hz,
            self.linewidth_minus_hz,
            self.amplitude_plus,
            self.frequency_plus_hz,
            self.linewidth_plus_hz,
        )


def two_lorentzian_odmr_model(
    frequency_hz,
    baseline,
    amplitude_minus,
    frequency_minus_hz,
    linewidth_minus_hz,
    amplitude_plus,
    frequency_plus_hz,
    linewidth_plus_hz,
):
    """Evaluate two positive Lorentzian dips with a shared baseline.

    ``linewidth_*_hz`` denotes the full width at half maximum (FWHM).
    The ordered branches correspond to the ``ms=0 -> ms=-1`` and
    ``ms=0 -> ms=+1`` transitions, respectively.
    """
    if amplitude_minus <= 0 or amplitude_plus <= 0:
        raise ValueError("Lorentzian dip amplitudes must be positive.")
    if linewidth_minus_hz <= 0 or linewidth_plus_hz <= 0:
        raise ValueError("Lorentzian linewidths must be positive.")
    if frequency_minus_hz >= frequency_plus_hz:
        raise ValueError("The ordered resonances must satisfy f_minus < f_plus.")
    frequency_hz = np.asarray(frequency_hz, dtype=np.float64)
    minus = amplitude_minus / (
        1.0 + 4.0 * np.square(frequency_hz - frequency_minus_hz)
        / np.square(linewidth_minus_hz)
    )
    plus = amplitude_plus / (
        1.0 + 4.0 * np.square(frequency_hz - frequency_plus_hz)
        / np.square(linewidth_plus_hz)
    )
    return baseline - minus - plus


def fit_two_lorentzian_spectrum(
    frequency_hz,
    spectrum,
    initial_guess=None,
    *,
    maxfev=50000,
):
    """Fit one spectrum with bounded, ordered two-dip Lorentzians.

    Frequency inputs, fitted centers, and linewidths are all in Hz. Non-finite
    signal samples are omitted from the fit and retained as NaN residuals.
    Invalid input raises ``ValueError``; optimizer failures return an explicit
    unsuccessful result rather than fabricated fit values.
    """
    frequency_hz = np.asarray(frequency_hz, dtype=np.float64)
    spectrum = np.asarray(spectrum, dtype=np.float64)
    if frequency_hz.ndim != 1 or spectrum.ndim != 1:
        raise ValueError("frequency_hz and spectrum must be one-dimensional.")
    if frequency_hz.shape != spectrum.shape:
        raise ValueError("frequency_hz and spectrum must have the same shape.")
    if not np.all(np.isfinite(frequency_hz)):
        raise ValueError("frequency_hz must contain only finite values.")
    if frequency_hz.size < 8 or np.any(np.diff(frequency_hz) <= 0):
        raise ValueError("At least 8 strictly increasing frequency samples are required.")

    valid = np.isfinite(spectrum)
    x = frequency_hz[valid]
    y = spectrum[valid]
    if x.size < 8:
        raise ValueError("At least 8 finite spectrum samples are required for fitting.")
    signal_range = float(np.ptp(y))
    if not np.isfinite(signal_range) or signal_range <= 0:
        raise ValueError("Spectrum must have a finite, non-zero signal range.")

    template = (
        _default_two_lorentzian_guess(x, y)
        if initial_guess is None
        else _coerce_initial_guess(initial_guess)
    )
    _validate_initial_guess(template)

    x_min = float(x[0])
    x_max = float(x[-1])
    span = x_max - x_min
    positive_floor = max(np.finfo(np.float64).eps * signal_range, 1e-15)
    width_floor = max(float(np.min(np.diff(x))) * 0.1, span * 1e-8)
    width_ceiling = span * 2.0
    amplitude_ceiling = signal_range * 2.0

    # Logistic frequency coordinates guarantee both centers stay in the
    # measured band and f_minus < f_plus without sorting or relabeling dips.
    p0 = _physical_to_internal(template.as_array(), x_min, x_max)
    p0[0] = np.clip(p0[0], np.min(y) - signal_range, np.max(y) + 2 * signal_range)
    p0[1] = np.clip(p0[1], positive_floor * 2, amplitude_ceiling * 0.9)
    p0[3] = np.clip(p0[3], width_floor * 1.1, width_ceiling * 0.9)
    p0[4] = np.clip(p0[4], positive_floor * 2, amplitude_ceiling * 0.9)
    p0[6] = np.clip(p0[6], width_floor * 1.1, width_ceiling * 0.9)
    lower = np.array(
        [np.min(y) - signal_range, positive_floor, -12.0, width_floor,
         positive_floor, -12.0, width_floor]
    )
    upper = np.array(
        [np.max(y) + 2 * signal_range, amplitude_ceiling, 12.0, width_ceiling,
         amplitude_ceiling, 12.0, width_ceiling]
    )

    initial_physical = _internal_to_physical(p0, x_min, x_max)
    initial_template = TwoLorentzianInitialGuess(*initial_physical)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            popt_internal, pcov_internal = curve_fit(
                lambda f, *p: two_lorentzian_odmr_model(
                    f, *_internal_to_physical(np.asarray(p), x_min, x_max)
                ),
                x,
                y,
                p0=p0,
                bounds=(lower, upper),
                maxfev=maxfev,
            )
        covariance = _transform_covariance(
            pcov_internal, popt_internal, x_min, x_max
        )
        warning_messages = [str(item.message) for item in caught]
        if warning_messages or not np.all(np.isfinite(covariance)):
            covariance = None
            standard_errors = None
            status = "success_covariance_unavailable"
            message = "; ".join(warning_messages) or "Non-finite parameter covariance."
        else:
            standard_errors = np.sqrt(np.maximum(np.diag(covariance), 0.0))
            status = "success"
            message = "Fit converged."

        params = _internal_to_physical(popt_internal, x_min, x_max)
        fitted = two_lorentzian_odmr_model(frequency_hz, *params)
        residuals = spectrum - fitted
        residuals[~valid] = np.nan
        rmse = float(np.sqrt(np.mean(np.square(y - fitted[valid]))))
        ss_res = float(np.sum(np.square(y - fitted[valid])))
        ss_tot = float(np.sum(np.square(y - np.mean(y))))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        success = True
    except (RuntimeError, ValueError, FloatingPointError) as error:
        params = initial_template.as_array()
        fitted = np.full_like(frequency_hz, np.nan)
        residuals = np.full_like(spectrum, np.nan)
        rmse = float("nan")
        r2 = float("nan")
        covariance = None
        standard_errors = None
        success = False
        status = "optimizer_failed"
        message = str(error)

    return TwoLorentzianFitResult(
        baseline=float(params[0]),
        amplitude_minus=float(params[1]),
        frequency_minus_hz=float(params[2]),
        linewidth_minus_hz=float(params[3]),
        amplitude_plus=float(params[4]),
        frequency_plus_hz=float(params[5]),
        linewidth_plus_hz=float(params[6]),
        frequency_hz=frequency_hz.copy(),
        spectrum=spectrum.copy(),
        fitted_spectrum=fitted,
        residuals=residuals,
        rmse=rmse,
        r2=float(r2),
        success=success,
        status=status,
        message=message,
        covariance=covariance,
        standard_errors=standard_errors,
        initial_guess=initial_template,
        n_valid_points=int(valid.sum()),
    )


def fit_global_odmr_spectrum(final_mean, frequency_hz, initial_guess=None):
    """Average finite pixels at each frequency and fit the global spectrum."""
    final_mean = np.asarray(final_mean, dtype=np.float64)
    frequency_hz = np.asarray(frequency_hz, dtype=np.float64)
    if final_mean.ndim != 3:
        raise ValueError("final_mean must have shape (frequency, y, x).")
    if frequency_hz.ndim != 1 or frequency_hz.size != final_mean.shape[0]:
        raise ValueError("frequency_hz must have one value per frequency plane.")
    if not np.all(np.isfinite(frequency_hz)) or np.any(np.diff(frequency_hz) <= 0):
        raise ValueError("frequency_hz must be finite and strictly increasing.")
    global_spectrum = global_mean_odmr_spectrum(final_mean)
    n_valid_pixels = np.isfinite(final_mean).sum(axis=(1, 2), dtype=np.int64)

    result = fit_two_lorentzian_spectrum(
        frequency_hz, global_spectrum, initial_guess=initial_guess
    )
    return TwoLorentzianFitResult(
        **{
            **result.__dict__,
            "n_valid_pixels_per_frequency": n_valid_pixels,
        }
    )


def global_mean_odmr_spectrum(final_mean):
    """Return the finite-pixel spatial mean at every frequency plane.

    ``final_mean`` must have shape ``(frequency, y, x)``. Frequency planes
    with no finite pixels are represented by NaN. This is the same reduction
    used by :func:`fit_global_odmr_spectrum`, exposed for pre-fit display.
    """
    final_mean = np.asarray(final_mean, dtype=np.float64)
    if final_mean.ndim != 3:
        raise ValueError("final_mean must have shape (frequency, y, x).")
    valid = np.isfinite(final_mean)
    counts = valid.sum(axis=(1, 2), dtype=np.int64)
    sums = np.where(valid, final_mean, 0.0).sum(axis=(1, 2))
    spectrum = np.full(final_mean.shape[0], np.nan, dtype=np.float64)
    has_pixels = counts > 0
    spectrum[has_pixels] = sums[has_pixels] / counts[has_pixels]
    return spectrum


def _default_two_lorentzian_guess(frequency_hz, spectrum):
    order = np.argsort(spectrum)
    first = int(order[0])
    min_separation = max(2, frequency_hz.size // 20)
    candidates = [
        int(index) for index in order
        if abs(int(index) - first) >= min_separation
    ]
    second = candidates[0] if candidates else int(order[min(1, len(order) - 1)])
    minus_index, plus_index = sorted((first, second))
    span = float(frequency_hz[-1] - frequency_hz[0])
    depth = max(float(np.max(spectrum) - np.min(spectrum)), np.finfo(float).eps)
    linewidth = max(span / 20.0, float(np.min(np.diff(frequency_hz))) * 2.0)
    return TwoLorentzianInitialGuess(
        baseline=float(np.max(spectrum)),
        amplitude_minus=depth / 2.0,
        frequency_minus_hz=float(frequency_hz[minus_index]),
        linewidth_minus_hz=linewidth,
        amplitude_plus=depth / 2.0,
        frequency_plus_hz=float(frequency_hz[plus_index]),
        linewidth_plus_hz=linewidth,
    )


def _coerce_initial_guess(initial_guess):
    if isinstance(initial_guess, TwoLorentzianInitialGuess):
        return initial_guess
    values = np.asarray(initial_guess, dtype=np.float64)
    if values.shape != (7,):
        raise ValueError("initial_guess must be a template or seven parameters.")
    return TwoLorentzianInitialGuess(*values)


def _validate_initial_guess(guess):
    values = guess.as_array()
    if not np.all(np.isfinite(values)):
        raise ValueError("initial_guess parameters must all be finite.")
    if guess.amplitude_minus <= 0 or guess.amplitude_plus <= 0:
        raise ValueError("initial_guess amplitudes must be positive.")
    if guess.linewidth_minus_hz <= 0 or guess.linewidth_plus_hz <= 0:
        raise ValueError("initial_guess linewidths must be positive.")
    if guess.frequency_minus_hz >= guess.frequency_plus_hz:
        raise ValueError("initial_guess must satisfy f_minus < f_plus.")


def _physical_to_internal(params, x_min, x_max):
    baseline, amp_minus, f_minus, width_minus, amp_plus, f_plus, width_plus = params
    span = x_max - x_min
    u = np.clip((f_minus - x_min) / span, 1e-5, 1 - 1e-5)
    remaining = x_max - f_minus
    v = np.clip((f_plus - f_minus) / remaining, 1e-5, 1 - 1e-5)
    return np.asarray(
        [baseline, amp_minus, _logit(u), width_minus,
         amp_plus, _logit(v), width_plus],
        dtype=np.float64,
    )


def _internal_to_physical(params, x_min, x_max):
    baseline, amp_minus, u, width_minus, amp_plus, v, width_plus = params
    u_fraction = _sigmoid(u)
    v_fraction = _sigmoid(v)
    f_minus = x_min + (x_max - x_min) * u_fraction
    f_plus = f_minus + (x_max - f_minus) * v_fraction
    return np.asarray(
        [baseline, amp_minus, f_minus, width_minus,
         amp_plus, f_plus, width_plus],
        dtype=np.float64,
    )


def _transform_covariance(covariance, params, x_min, x_max):
    if not np.all(np.isfinite(covariance)):
        return covariance
    _, _, u, _, _, v, _ = params
    du = _sigmoid(u) * (1.0 - _sigmoid(u))
    dv = _sigmoid(v) * (1.0 - _sigmoid(v))
    span = x_max - x_min
    f_minus = x_min + span * _sigmoid(u)
    jacobian = np.eye(7, dtype=np.float64)
    jacobian[2, 2] = span * du
    jacobian[5, 2] = (1.0 - _sigmoid(v)) * span * du
    jacobian[5, 5] = (x_max - f_minus) * dv
    return jacobian @ covariance @ jacobian.T


def _sigmoid(value):
    return 1.0 / (1.0 + np.exp(-value))


def _logit(value):
    return np.log(value / (1.0 - value))
