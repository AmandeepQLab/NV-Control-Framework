"""Hardware-free regression tests for Andor SDK3 binning and AOI limits."""

import unittest
from unittest.mock import patch

import numpy as np

from framework.acquisition_state import AcquisitionState
from framework.acquisition_state import binned_roi_to_sensor, sensor_roi_to_binned
from gui.main_window import MainWindow
from hardware.camera.andor_neo_andor3 import AndorNeoAndor3
from hardware.camera.sim_camera import SimCamera


class FakeAndorHandle:
    """Model SDK3's output-pixel AOI sizes and sensor-pixel positions."""

    def __init__(self, sensor_width=2048, sensor_height=2048):
        self.sensor_width = sensor_width
        self.sensor_height = sensor_height
        self.binning = 1
        self.exposure = 0.02
        self.values = {
            "AOILeft": 1,
            "AOITop": 1,
            "AOIWidth": sensor_width,
            "AOIHeight": sensor_height,
        }
        self.calls = []

    def command(self, name):
        self.calls.append(("command", name))

    def setBool(self, name, value):
        self.calls.append(("bool", name, value))

    def getInt(self, name):
        if name == "SensorWidth":
            return self.sensor_width
        if name == "SensorHeight":
            return self.sensor_height
        return self.values[name]

    def setFloat(self, name, value):
        if name == "ExposureTime":
            self.exposure = value

    def getFloat(self, name):
        if name == "ExposureTime":
            return self.exposure
        raise KeyError(name)

    def getIntMin(self, name):
        return 1

    def getIntMax(self, name):
        if name == "AOIWidth":
            return self.sensor_width // self.binning
        if name == "AOIHeight":
            return self.sensor_height // self.binning
        if name == "AOILeft":
            return self.sensor_width - self.values["AOIWidth"] * self.binning + 1
        if name == "AOITop":
            return self.sensor_height - self.values["AOIHeight"] * self.binning + 1
        raise KeyError(name)

    def setInt(self, name, value):
        self.calls.append(("int", name, value))
        if name in ("AOILeft", "AOITop"):
            maximum = self.getIntMax(name)
            if not 1 <= value <= maximum or (value - 1) % self.binning:
                raise ValueError(f"{name}={value} outside SDK AOI limits 1..{maximum}")
        elif name in ("AOIWidth", "AOIHeight"):
            maximum = self.getIntMax(name)
            if not 1 <= value <= maximum:
                raise ValueError(f"{name}={value} outside SDK AOI limits 1..{maximum}")
        else:
            raise KeyError(name)
        self.values[name] = value

    def setEnumIndex(self, name, index):
        self.calls.append(("enum", name, index))
        if name == "AOIBinning":
            self.binning = {0: 1, 1: 2, 2: 3, 3: 4, 4: 8}[index]
            # SDK3 changes dimension limits with binning.
            self.values["AOIWidth"] = min(
                self.values["AOIWidth"], self.sensor_width // self.binning
            )
            self.values["AOIHeight"] = min(
                self.values["AOIHeight"], self.sensor_height // self.binning
            )


class AndorBinningTests(unittest.TestCase):
    def setUp(self):
        self.driver = AndorNeoAndor3()
        self.handle = FakeAndorHandle()
        self.driver.cam = self.handle

    def apply(self, binning, roi):
        self.driver.set_binning(binning)
        self.driver.set_roi(roi)

    def assert_camera_roi_valid(self):
        binning = self.driver.binning
        left = self.handle.values["AOILeft"]
        top = self.handle.values["AOITop"]
        width = self.handle.values["AOIWidth"]
        height = self.handle.values["AOIHeight"]
        self.assertGreaterEqual(left, 1)
        self.assertGreaterEqual(top, 1)
        self.assertLessEqual(left, self.handle.getIntMax("AOILeft"))
        self.assertLessEqual(top, self.handle.getIntMax("AOITop"))
        self.assertEqual((left - 1) % binning, 0)
        self.assertEqual((top - 1) % binning, 0)
        self.assertLessEqual((left - 1) + width * binning, self.handle.sensor_width)
        self.assertLessEqual((top - 1) + height * binning, self.handle.sensor_height)

    def test_full_frame_roi_changes_binning(self):
        self.apply(2, None)
        self.assertEqual(self.handle.values["AOIWidth"], 1024)
        self.assertEqual(self.handle.values["AOIHeight"], 1024)
        self.assertIsNone(self.driver.roi)
        self.assert_camera_roi_valid()

    def test_non_full_frame_roi_uses_output_dimensions_and_sensor_positions(self):
        self.apply(2, (100, 200, 500, 600))
        self.assertEqual(self.driver.roi, (100, 200, 500, 600))
        self.assertEqual(self.handle.values["AOIWidth"], 200)
        self.assertEqual(self.handle.values["AOIHeight"], 200)
        self.assertEqual(self.handle.values["AOILeft"], 101)
        self.assertEqual(self.handle.values["AOITop"], 201)
        self.assert_camera_roi_valid()

    def test_roi_incompatible_with_new_binning_is_aligned_to_nearest_valid(self):
        self.apply(2, (3, 1799, 1004, 2048))
        self.assertEqual(self.driver.roi, (4, 1800, 1004, 2048))
        self.assert_camera_roi_valid()

    def test_transition_moves_origin_before_changing_binning(self):
        self.driver.set_roi((0, 1500, 2048, 2048))
        self.handle.calls.clear()
        self.driver.set_binning(2)

        calls = self.handle.calls
        self.assertLess(calls.index(("int", "AOITop", 1)), calls.index(("enum", "AOIBinning", 1)))
        self.assertEqual(self.handle.values["AOITop"], 1)
        self.driver.set_roi((0, 1500, 2048, 2048))
        self.assert_camera_roi_valid()

    def test_repeated_binning_changes_do_not_accumulate_invalid_roi(self):
        state = AcquisitionState((103, 207, 1004, 1205), 0.02, 1)
        for binning in (1, 2, 4, 1):
            state = AcquisitionState(state.acquisition_roi, state.exposure_s, binning)
            state.apply_to(self.driver)
            state = AcquisitionState(self.driver.roi, state.exposure_s, binning)
            self.assertEqual(self.driver.binning, state.binning)
            self.assertEqual(self.driver.roi, state.acquisition_roi)
            self.assert_camera_roi_valid()

    def test_main_window_binning_updates_state_to_programmed_roi(self):
        class WindowHarness:
            def __init__(self, camera):
                self.camera = camera
                self.acquisition_state = AcquisitionState(
                    (3, 1799, 1004, 2048), 0.02, 1
                )

            def apply_acquisition_state(self):
                self.acquisition_state.apply_to(self.camera)
                return True

            def _sync_roi_controls(self):
                pass

        window = WindowHarness(self.driver)
        MainWindow.set_binning(window, 2)
        self.assertEqual(window.acquisition_state.binning, 2)
        self.assertEqual(window.acquisition_state.acquisition_roi, self.driver.roi)
        self.assertEqual(self.driver.roi, (4, 1800, 1004, 2048))

    def test_roi_is_preserved_through_1_2_1_and_1_2_3_1(self):
        original_roi = (402, 300, 1206, 900)
        for sequence in ((1, 2, 1), (1, 2, 3, 1)):
            state = AcquisitionState(original_roi, 0.02, 1)
            for binning in sequence:
                state = AcquisitionState(state.acquisition_roi, state.exposure_s, binning)
                state.apply_to(self.driver)
                state = AcquisitionState(self.driver.roi, state.exposure_s, binning)
                self.assertEqual(self.driver.roi, original_roi)
            self.assertEqual(state.acquisition_roi, original_roi)


class BinnedImagePresentationTests(unittest.TestCase):
    def test_effective_roi_conversion_preserves_sensor_roi(self):
        physical_roi = (402, 300, 1206, 900)
        for binning, expected in (
            (1, (402, 300, 1206, 900)),
            (2, (201, 150, 603, 450)),
            (3, (134, 100, 402, 300)),
        ):
            effective = sensor_roi_to_binned(physical_roi, binning)
            self.assertEqual(effective, expected)
            self.assertEqual(binned_roi_to_sensor(effective, binning), physical_roi)

    def test_simulated_full_frame_has_true_binned_dimensions(self):
        camera = SimCamera(image_shape=(12, 15))
        expected_shapes = {1: (12, 15), 2: (6, 7), 3: (4, 5)}
        for binning, expected_shape in expected_shapes.items():
            camera.set_binning(binning)
            frame = camera._apply_acquisition_state(
                np.ones(camera.image_shape, dtype=float)
            )
            self.assertEqual(frame.shape, expected_shape)

    def test_simulated_binning_sums_without_averaging_or_interpolation(self):
        source = np.arange(1, 17, dtype=float).reshape(4, 4)
        original = source.copy()
        camera = SimCamera(image_shape=(4, 4))
        camera.set_binning(2)

        result = camera._apply_acquisition_state(source)

        np.testing.assert_array_equal(result, [[14, 22], [46, 54]])
        np.testing.assert_array_equal(source, original)
        self.assertEqual(result.shape, (2, 2))

        camera3 = SimCamera(image_shape=(3, 3))
        camera3.set_binning(3)
        result3 = camera3._apply_acquisition_state(np.arange(1, 10).reshape(3, 3))
        np.testing.assert_array_equal(result3, [[45]])
        self.assertEqual(result3.shape, (1, 1))

    def test_main_window_controls_show_effective_roi_coordinates(self):
        class ValueControl:
            def __init__(self):
                self.value = None

            def setValue(self, value):
                self.value = value

        class Label:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = text

        class Overlay:
            def setPos(self, pos, finish=False):
                self.pos = pos

            def setSize(self, size, finish=False):
                self.size = size

        harness = type("WindowHarness", (), {})()
        harness.acquisition_state = AcquisitionState((402, 300, 1206, 900), 0.02, 2)
        harness._sensor_dimensions = (2048, 2048)
        harness.roi_coordinate_spins = {
            name: ValueControl() for name in ("x", "y", "width", "height")
        }
        harness.roi_label = Label()
        harness.roi = Overlay()

        with patch("gui.main_window.QSignalBlocker", lambda control: None):
            MainWindow._sync_roi_controls(harness)

        self.assertEqual(
            tuple(control.value for control in harness.roi_coordinate_spins.values()),
            (201, 150, 402, 300),
        )
        self.assertEqual(harness.roi.pos, (201, 150))
        self.assertEqual(harness.roi.size, (402, 300))
        self.assertIn("2x2 effective pixels", harness.roi_label.text)

    def test_image_display_scales_viewport_without_resizing_frame(self):
        class FakeView:
            def __init__(self):
                self.ranges = []

            def setRange(self, **kwargs):
                self.ranges.append(kwargs)

        class FakeImageView:
            def __init__(self):
                self.images = []
                self.view = FakeView()

            def setImage(self, image, **kwargs):
                self.images.append((image, kwargs))

            def getView(self):
                return self.view

        class FakePlot:
            def __init__(self):
                self.labels = []

            def setLabel(self, axis, label):
                self.labels.append((axis, label))

        harness = type("WindowHarness", (), {})()
        harness.camera = type("Camera", (), {"get_latest_frame": lambda self: frame})()
        harness.acquisition_state = AcquisitionState((402, 300, 1206, 900), 0.02, 2)
        harness.image_view = FakeImageView()
        harness.image_plot = FakePlot()
        harness._update_pixel_axes = lambda binning: MainWindow._update_pixel_axes(
            harness, binning
        )
        harness._update_sensor_dimensions_from_full_frame = lambda frame: None
        frame = np.arange(12, dtype=np.uint16).reshape(2, 6)

        MainWindow.update_image(harness)

        displayed, options = harness.image_view.images[0]
        self.assertEqual(displayed.shape, (6, 2))
        np.testing.assert_array_equal(displayed, frame.T)
        self.assertEqual(options["pos"], (201, 150))
        self.assertFalse(options["autoRange"])
        self.assertEqual(
            harness.image_view.view.ranges[0],
            {"xRange": (201, 207), "yRange": (150, 152), "padding": 0},
        )
        self.assertIn("2x2", harness.image_plot.labels[0][1])

    def test_full_frame_axes_and_viewport_follow_1_2_and_3_binning(self):
        class FakeView:
            def __init__(self):
                self.ranges = []

            def setRange(self, **kwargs):
                self.ranges.append(kwargs)

        class FakeImageView:
            def __init__(self):
                self.images = []
                self.view = FakeView()

            def setImage(self, image, **kwargs):
                self.images.append((image, kwargs))

            def getView(self):
                return self.view

        class FakePlot:
            def __init__(self):
                self.labels = []

            def setLabel(self, axis, label):
                self.labels.append((axis, label))

        harness = type("WindowHarness", (), {})()
        harness.image_view = FakeImageView()
        harness.image_plot = FakePlot()
        harness._update_pixel_axes = lambda binning: MainWindow._update_pixel_axes(
            harness, binning
        )
        harness._update_sensor_dimensions_from_full_frame = lambda frame: None
        for binning, shape in ((1, (12, 15)), (2, (6, 7)), (3, (4, 5))):
            frame = np.ones(shape, dtype=np.uint16)
            harness.camera = type(
                "Camera", (), {"get_latest_frame": lambda self: frame}
            )()
            harness.acquisition_state = AcquisitionState(None, 0.02, binning)

            MainWindow.update_image(harness)

            displayed, options = harness.image_view.images[-1]
            self.assertEqual(displayed.shape, (shape[1], shape[0]))
            self.assertEqual(options["pos"], (0, 0))
            self.assertFalse(options["autoRange"])
            self.assertEqual(
                harness.image_view.view.ranges[-1],
                {"xRange": (0, shape[1]), "yRange": (0, shape[0]), "padding": 0},
            )
            self.assertIn(f"{binning}x{binning}", harness.image_plot.labels[-2][1])
            self.assertIn(f"{binning}x{binning}", harness.image_plot.labels[-1][1])

    def test_roi_edit_coordinates_convert_back_to_physical_sensor_pixels(self):
        camera = SimCamera(image_shape=(2048, 2048))
        harness = type("WindowHarness", (), {})()
        harness.camera = camera
        harness.acquisition_state = AcquisitionState(None, 0.02, 2)
        harness._sensor_dimensions = camera.image_shape[::-1]
        harness._sync_roi_controls = lambda: None
        harness._validate_acquisition_roi = lambda roi: MainWindow._validate_acquisition_roi(
            harness, roi
        )

        applied = MainWindow._set_acquisition_roi(harness, (200, 150, 600, 450))

        self.assertTrue(applied)
        self.assertEqual(harness.acquisition_state.acquisition_roi, (400, 300, 1200, 900))
        self.assertEqual(camera.roi, harness.acquisition_state.acquisition_roi)


if __name__ == "__main__":
    unittest.main()
