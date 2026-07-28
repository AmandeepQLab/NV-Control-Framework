"""Tests for Main-Window-owned camera acquisition settings."""

import unittest

import numpy as np

from framework.acquisition_state import AcquisitionState
from hardware.camera.sim_camera import SimCamera


class FakeCamera:
    def __init__(self):
        self.calls = []

    def set_exposure(self, value):
        self.calls.append(("exposure", value))

    def set_binning(self, value):
        self.calls.append(("binning", value))

    def set_roi(self, value):
        self.calls.append(("roi", value))


class AcquisitionStateTests(unittest.TestCase):
    def test_applies_the_complete_camera_configuration(self):
        camera = FakeCamera()
        state = AcquisitionState((2, 3, 10, 11), 0.05, 2)

        state.apply_to(camera)

        self.assertEqual(
            camera.calls,
            [
                ("exposure", 0.05),
                ("binning", 2),
                ("roi", (2, 3, 10, 11)),
            ],
        )

    def test_simulated_camera_returns_roi_and_binning_shape(self):
        camera = SimCamera(image_shape=(8, 10))
        camera.set_roi((2, 2, 10, 8))
        camera.set_binning(2)

        frame = camera.snap()

        self.assertEqual(frame.shape, (3, 4))
        self.assertTrue(np.isfinite(frame).all())


if __name__ == "__main__":
    unittest.main()
