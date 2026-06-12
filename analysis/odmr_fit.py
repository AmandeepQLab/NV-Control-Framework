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


def fit_single_lorentzian(freq_ghz, signal_percent):
    x = np.array(freq_ghz, dtype=float)
    y = np.array(signal_percent, dtype=float)

    baseline0 = np.max(y)
    amp0 = baseline0 - np.min(y)
    f0_0 = x[np.argmin(y)]
    fwhm0 = (np.max(x) - np.min(x)) / 10

    p0 = [baseline0, amp0, f0_0, fwhm0]

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


def fit_double_lorentzian(freq_ghz, signal_percent):
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
