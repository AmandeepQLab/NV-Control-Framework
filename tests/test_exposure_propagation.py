"""Tests for exposure propagation: GUI display -> AcquisitionState ->
camera -> experiments.

Investigation found four independent "default exposure" values that had
never been reconciled (AcquisitionState's dataclass default, the Andor
driver's __init__ default, the GUI spinbox's initial value, and nothing
in setupInfo.json), and nothing pushed any of them to the real camera at
startup until the user first touched the exposure spinbox, started a
stream, or ran a scan. Fixed by: building MainWindow.acquisition_state
from the spinboxes (not the dataclass default) and pushing it to the
camera once, explicitly, at the end of __init__; genuine SDK readback in
AndorNeoAndor3 instead of trusting the requested value; and a drift-check
warning (not a raise) in ODMRExperiment.configure_acquisition() comparing
config["exposure_s"] against the camera's real exposure.

No real hardware -- AndorNeoAndor3 is constructed directly (skipping
.connect()) with a fake SDK handle standing in for the vendor `andor3`
Andor3 object, so set_exposure()/configure_camera()'s actual production
code runs against a double, not real hardware.
"""

import inspect
import logging
import unittest

from experiments.odmr_experiment import ODMRExperiment, LOGGER as ODMR_LOGGER
from framework.acquisition_state import AcquisitionState
from gui.main_window import MainWindow
from hardware.camera.andor_neo_andor3 import AndorNeoAndor3
from hardware.camera.sim_camera import SimCamera


def _collect_warnings(fn):
    records = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector()
    prev_level = ODMR_LOGGER.level
    ODMR_LOGGER.addHandler(handler)
    ODMR_LOGGER.setLevel(logging.WARNING)
    try:
        fn()
    finally:
        ODMR_LOGGER.removeHandler(handler)
        ODMR_LOGGER.setLevel(prev_level)
    return [r for r in records if r.levelno >= logging.WARNING]


class _FakeAndorHandle:
    """Stands in for andor3.Andor3 -- enough surface for configure_camera()
    and set_exposure() to run unmodified. ExposureTime is quantised to a
    fake 1ms row period so readback can genuinely differ from the request,
    the way real sCMOS row-period quantisation does."""

    ROW_PERIOD_S = 0.001

    def __init__(self):
        self._exposure_s = 0.0

    def setBool(self, name, value):
        pass

    def setEnumString(self, name, value):
        pass

    def setEnumIndex(self, name, value):
        pass

    def setFloat(self, name, value):
        if name == "ExposureTime":
            steps = round(value / self.ROW_PERIOD_S)
            self._exposure_s = steps * self.ROW_PERIOD_S

    def getFloat(self, name):
        if name == "ExposureTime":
            return self._exposure_s
        raise KeyError(name)


class AndorReadbackTests(unittest.TestCase):
    """set_exposure()/configure_camera() must store what the SDK actually
    accepted, not the raw request -- the readback call exists in the SDK
    (already used elsewhere in this file for AOIWidth etc.) and is real,
    not hypothetical."""

    def test_set_exposure_stores_readback_not_request(self):
        driver = AndorNeoAndor3()
        driver.cam = _FakeAndorHandle()

        driver.set_exposure(0.0123)  # not a whole multiple of the fake row period

        expected = round(0.0123 / _FakeAndorHandle.ROW_PERIOD_S) * _FakeAndorHandle.ROW_PERIOD_S
        self.assertNotEqual(driver.exposure_time, 0.0123)
        self.assertAlmostEqual(driver.exposure_time, expected, places=9)

    def test_set_exposure_readback_matches_request_when_exact(self):
        driver = AndorNeoAndor3()
        driver.cam = _FakeAndorHandle()

        driver.set_exposure(0.020)  # exact multiple of the fake row period

        self.assertAlmostEqual(driver.exposure_time, 0.020, places=9)

    def test_configure_camera_reads_back_initial_exposure(self):
        driver = AndorNeoAndor3()
        driver.exposure_time = 0.0207  # not a whole multiple of the fake row period
        driver.cam = _FakeAndorHandle()

        driver.configure_camera()

        expected = round(0.0207 / _FakeAndorHandle.ROW_PERIOD_S) * _FakeAndorHandle.ROW_PERIOD_S
        self.assertNotEqual(driver.exposure_time, 0.0207)
        self.assertAlmostEqual(driver.exposure_time, expected, places=9)

    def test_set_exposure_without_cam_falls_back_to_request(self):
        """Before connect() -- self.cam is None -- there's nothing to read
        back from, so the requested value is stored as-is."""
        driver = AndorNeoAndor3()
        self.assertIsNone(driver.cam)

        driver.set_exposure(0.05)

        self.assertEqual(driver.exposure_time, 0.05)


class ExposureDriftWarningTests(unittest.TestCase):
    """ODMRExperiment.configure_acquisition() must log loudly on drift
    between config["exposure_s"] (what camera_gate_s was sized from) and
    the camera's real exposure -- never raise, per the reasoning that
    quantisation will make small drift routine once real readback exists."""

    class _FakeCamera:
        def __init__(self, exposure_time):
            self.exposure_time = exposure_time
            self.roi_calls = []

        def set_roi(self, roi):
            self.roi_calls.append(roi)

    def test_warns_on_mismatch(self):
        camera = self._FakeCamera(exposure_time=0.021)
        experiment = ODMRExperiment(
            {"camera": camera}, {"exposure_s": 0.02}
        )

        warnings = _collect_warnings(experiment.configure_acquisition)

        self.assertEqual(len(warnings), 1)
        message = warnings[0].getMessage()
        self.assertIn("0.020000", message)
        self.assertIn("0.021000", message)

    def test_silent_when_matching(self):
        camera = self._FakeCamera(exposure_time=0.02)
        experiment = ODMRExperiment(
            {"camera": camera}, {"exposure_s": 0.02}
        )

        warnings = _collect_warnings(experiment.configure_acquisition)

        self.assertEqual(warnings, [])

    def test_never_raises_on_mismatch(self):
        camera = self._FakeCamera(exposure_time=0.5)  # wildly different
        experiment = ODMRExperiment(
            {"camera": camera}, {"exposure_s": 0.02}
        )

        experiment.configure_acquisition()  # must not raise

    def test_records_actual_exposure_s_for_metadata(self):
        camera = self._FakeCamera(exposure_time=0.0207)
        experiment = ODMRExperiment(
            {"camera": camera}, {"exposure_s": 0.02}
        )

        self.assertIsNone(experiment.actual_exposure_s)
        experiment.configure_acquisition()

        self.assertEqual(experiment.actual_exposure_s, 0.0207)


class MainWindowSingleSourceOfTruthTests(unittest.TestCase):
    """MainWindow is a QMainWindow with real widgets and can't cheaply be
    constructed headlessly in this test environment (no display) --
    matching this repo's existing convention (see
    AcquisitionStateReconstructionTests in test_baseline_correction.py) of
    testing MainWindow's structure via source inspection rather than live
    instantiation for exactly this reason."""

    def test_acquisition_state_built_from_spinboxes_not_dataclass_defaults(self):
        source = inspect.getsource(MainWindow.__init__)
        self.assertIn("exposure_s=self.exposure_spin.value()", source)
        self.assertIn("binning=self.binning_spin.value()", source)
        self.assertNotIn("self.acquisition_state = AcquisitionState()", source)

    def test_acquisition_state_constructed_after_spinboxes_exist(self):
        source = inspect.getsource(MainWindow.__init__)
        spinbox_pos = source.index("self.exposure_spin = QDoubleSpinBox()")
        state_pos = source.index("self.acquisition_state = AcquisitionState(")
        self.assertLess(
            spinbox_pos, state_pos,
            "exposure_spin must be constructed before acquisition_state "
            "reads its value"
        )

    def test_camera_pushed_explicitly_at_end_of_init(self):
        source = inspect.getsource(MainWindow.__init__)
        restorer_pos = source.index("register_camera_state_restorer(")
        push_pos = source.index("self.apply_acquisition_state_to_camera()")
        self.assertLess(restorer_pos, push_pos)


class SimCameraDefaultAlignedTests(unittest.TestCase):
    def test_sim_camera_default_matches_real_driver_default(self):
        sim = SimCamera()
        real = AndorNeoAndor3()
        self.assertEqual(sim.exposure_time, real.exposure_time)
        self.assertEqual(sim.exposure_time, 0.02)


if __name__ == "__main__":
    unittest.main()
