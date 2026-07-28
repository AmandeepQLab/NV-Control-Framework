"""Tests for ODMR hardware acquisition-ROI lifecycle."""

import unittest

import numpy as np

from experiments.odmr_experiment import ODMRExperiment
from framework.camera_ownership import register_camera_state_restorer


class FakeCamera:
    def __init__(self):
        self.roi_calls = []

    def set_roi(self, roi):
        self.roi_calls.append(roi)


class PointODMRExperiment(ODMRExperiment):
    def set_scan_point(self, _frequency):
        pass

    def acquire_frame(self):
        return np.ones((2, 2))


class FailingPointODMRExperiment(PointODMRExperiment):
    def acquire_frame(self):
        raise RuntimeError("camera acquisition failed")


class ODMRAcquisitionRoiTests(unittest.TestCase):
    def test_acquire_point_restores_registered_acquisition_state(self):
        camera = FakeCamera()
        experiment = PointODMRExperiment(
            {"camera": camera}, {}, acquisition_roi=(10, 20, 30, 40)
        )

        register_camera_state_restorer(camera, lambda: camera.set_roi((1, 2, 3, 4)))
        experiment.acquire_point(2.87e9)

        self.assertEqual(camera.roi_calls, [(10, 20, 30, 40), (1, 2, 3, 4)])

    def test_acquire_point_restores_registered_state_after_exception(self):
        camera = FakeCamera()
        experiment = FailingPointODMRExperiment(
            {"camera": camera}, {}, acquisition_roi=(10, 20, 30, 40)
        )

        register_camera_state_restorer(camera, lambda: camera.set_roi((1, 2, 3, 4)))
        with self.assertRaisesRegex(RuntimeError, "camera acquisition failed"):
            experiment.acquire_point(2.87e9)

        self.assertEqual(camera.roi_calls, [(10, 20, 30, 40), (1, 2, 3, 4)])


if __name__ == "__main__":
    unittest.main()
