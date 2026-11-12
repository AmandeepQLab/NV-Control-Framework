"""Magnetic-field magnitude reconstruction from fitted ODMR resonances."""

from dataclasses import dataclass

import numpy as np

from analysis.odmr_pixel_fit import ODMRPixelFitResult, PixelFitStatus


DEFAULT_GAMMA_E_HZ_PER_GAUSS = 2.8024951e6
DEFAULT_NV_PROJECTION_ANGLE_DEG = 54.7356


@dataclass(frozen=True)
class ODMRMagneticReconstruction:
    """Positive field-magnitude maps reconstructed from ODMR splitting.

    ``bz_nv_projection`` and ``bz_magnitude`` are in Gauss. Uncertainties are
    first-order propagated standard errors in Gauss. This result does not
    encode field sign or perform background subtraction.
    """

    bz_magnitude: np.ndarray
    bz_nv_projection: np.ndarray
    splitting_hz: np.ndarray
    bz_uncertainty: np.ndarray
    valid_mask: np.ndarray
    gamma_e_hz_per_gauss: float
    gamma_e_units: str
    nv_projection_angle_deg: float
    projection_factor: float
    convention: str
    field_units: str

    @property
    def metadata(self):
        """Return reconstruction constants and convention as plain metadata."""
        return {
            "gamma_e": self.gamma_e_hz_per_gauss,
            "gamma_e_units": self.gamma_e_units,
            "nv_projection_angle_deg": self.nv_projection_angle_deg,
            "projection_factor": self.projection_factor,
            "convention": self.convention,
            "field_units": self.field_units,
        }


def reconstruct_bz(
    pixel_fit_result,
    gamma_e=DEFAULT_GAMMA_E_HZ_PER_GAUSS,
    nv_projection_angle_deg=DEFAULT_NV_PROJECTION_ANGLE_DEG,
):
    """Reconstruct non-negative Bz magnitude maps from per-pixel ODMR fits.

    The resonance-frequency unit is Hz and ``gamma_e`` is in Hz/G. Frequency
    ordering is used only to form the positive splitting; it is not used to
    infer magnetic-field sign. The projection factor is ``cos(theta)`` and
    must be positive and non-zero.
    """
    if not isinstance(pixel_fit_result, ODMRPixelFitResult):
        raise TypeError("pixel_fit_result must be an ODMRPixelFitResult.")
    if isinstance(gamma_e, (bool, np.bool_)):
        raise ValueError("gamma_e must be a positive finite value in Hz/G.")
    gamma_e = float(gamma_e)
    if not np.isfinite(gamma_e) or gamma_e <= 0:
        raise ValueError("gamma_e must be a positive finite value in Hz/G.")
    if isinstance(nv_projection_angle_deg, (bool, np.bool_)):
        raise ValueError("nv_projection_angle_deg must be finite and in [0, 90).")
    angle = float(nv_projection_angle_deg)
    if not np.isfinite(angle) or not 0.0 <= angle < 90.0:
        raise ValueError("nv_projection_angle_deg must be finite and in [0, 90).")

    projection_factor = float(np.cos(np.deg2rad(angle)))
    if not np.isfinite(projection_factor) or projection_factor <= 1e-12:
        raise ValueError("NV projection factor must be positive and non-zero.")

    f_minus = np.asarray(pixel_fit_result.f_minus_hz, dtype=np.float64)
    f_plus = np.asarray(pixel_fit_result.f_plus_hz, dtype=np.float64)
    status = np.asarray(pixel_fit_result.status)
    successful = np.asarray(pixel_fit_result.successful_fit_mask, dtype=bool)
    if f_minus.ndim != 2 or f_plus.shape != f_minus.shape:
        raise ValueError("Fitted resonance maps must have matching (y, x) shapes.")
    if status.shape != f_minus.shape or successful.shape != f_minus.shape:
        raise ValueError("Pixel fit status and success masks must match resonance map shape.")

    splitting_hz = f_plus - f_minus
    valid = (
        successful
        & (status == PixelFitStatus.SUCCESS)
        & np.isfinite(f_minus)
        & np.isfinite(f_plus)
        & np.isfinite(splitting_hz)
        & (splitting_hz > 0.0)
    )
    bz_nv = np.full(f_minus.shape, np.nan, dtype=np.float64)
    bz_magnitude = np.full(f_minus.shape, np.nan, dtype=np.float64)
    bz_nv[valid] = splitting_hz[valid] / (2.0 * gamma_e)
    bz_magnitude[valid] = bz_nv[valid] / projection_factor

    sigma_minus = np.asarray(
        pixel_fit_result.f_minus_uncertainty_hz, dtype=np.float64
    )
    sigma_plus = np.asarray(
        pixel_fit_result.f_plus_uncertainty_hz, dtype=np.float64
    )
    if sigma_minus.shape != f_minus.shape or sigma_plus.shape != f_minus.shape:
        raise ValueError("Frequency uncertainty maps must match resonance map shape.")
    uncertainty_available = (
        valid
        & np.isfinite(sigma_minus)
        & np.isfinite(sigma_plus)
        & (sigma_minus >= 0.0)
        & (sigma_plus >= 0.0)
    )
    bz_uncertainty = np.full(f_minus.shape, np.nan, dtype=np.float64)
    sigma_splitting = np.hypot(
        sigma_minus[uncertainty_available],
        sigma_plus[uncertainty_available],
    )
    bz_uncertainty[uncertainty_available] = (
        sigma_splitting / (2.0 * gamma_e * projection_factor)
    )

    return ODMRMagneticReconstruction(
        bz_magnitude=bz_magnitude,
        bz_nv_projection=bz_nv,
        splitting_hz=splitting_hz,
        bz_uncertainty=bz_uncertainty,
        valid_mask=valid,
        gamma_e_hz_per_gauss=gamma_e,
        gamma_e_units="Hz/G",
        nv_projection_angle_deg=angle,
        projection_factor=projection_factor,
        convention="non_negative_magnitude",
        field_units="G",
    )
