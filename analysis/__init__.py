import numpy as np
from scipy.optimize import curve_fit


def lorentzian_dip(f, baseline, amplitude, f0, fwhm):
    return baseline - amplitude / (1 + ((f - f0) / (fwhm / 2)) ** 2)


def fit_lorentzian(freq_ghz, signal_percent):

    x = np.array(freq_ghz, dtype=float)
    y = np.array(signal_percent, dtype=float)

    baseline0 = np.max(y)
    ymin = np.min(y)
    amplitude0 = baseline0 - ymin
    f0_0 = x[np.argmin(y)]
    fwhm0 = (np.max(x) - np.min(x)) / 10

    p0 = [baseline0, amplitude0, f0_0, fwhm0]

    popt, pcov = curve_fit(
        lorentzian_dip,
        x,
        y,
        p0=p0,
        maxfev=10000
    )

    yfit = lorentzian_dip(x, *popt)

    residuals = y - yfit
    rmse = np.sqrt(np.mean(residuals ** 2))

    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)

    r2 = 1 - ss_res / ss_tot if ss_tot != 0 else 0

    baseline, amplitude, f0, fwhm = popt

    noise = np.std(residuals)
    snr = amplitude / noise if noise != 0 else np.inf

    return {
        "params": popt,
        "fit_y": yfit,
        "baseline": baseline,
        "contrast": amplitude,
        "f0": f0,
        "fwhm": abs(fwhm),
        "rmse": rmse,
        "r2": r2,
        "snr": snr
    }
