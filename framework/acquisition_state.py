"""User-owned camera settings shared by live view and experiments."""

from dataclasses import dataclass


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

    def apply_to(self, camera):
        """Apply this complete state while the camera is idle."""
        camera.set_exposure(self.exposure_s)
        camera.set_binning(self.binning)
        camera.set_roi(self.acquisition_roi)
