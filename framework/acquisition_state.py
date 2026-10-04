"""User-owned camera settings shared by live view and experiments."""

from dataclasses import dataclass


def sensor_roi_to_binned(roi, binning):
    """Convert a full-sensor, zero-based ROI to effective binned pixels."""
    if roi is None:
        return None
    if binning < 1:
        raise ValueError("Binning must be a positive integer")
    return tuple(round(coordinate / binning) for coordinate in roi)


def binned_roi_to_sensor(roi, binning):
    """Convert an effective-pixel ROI to full-sensor coordinates."""
    if roi is None:
        return None
    if binning < 1:
        raise ValueError("Binning must be a positive integer")
    return tuple(coordinate * binning for coordinate in roi)


@dataclass(frozen=True)
class AcquisitionState:
    """Camera settings expressed in the Main Window's acquisition context.

    ``acquisition_roi`` uses full-sensor coordinates, or ``None`` for the
    complete sensor.  Analysis ROIs, if ever needed, are intentionally not
    represented here because they use image-local coordinates.
    """

    acquisition_roi: tuple[int, int, int, int] | None = None
    exposure_s: float = 0.02
    binning: int = 1
    baseline_counts: float = 0.0

    @property
    def effective_roi(self):
        """Return the physical ROI expressed in acquired binned pixels."""
        return sensor_roi_to_binned(self.acquisition_roi, self.binning)

    def apply_to(self, camera):
        """Apply this complete state while the camera is idle."""
        camera.set_exposure(self.exposure_s)
        camera.set_binning(self.binning)
        camera.set_roi(self.acquisition_roi)
