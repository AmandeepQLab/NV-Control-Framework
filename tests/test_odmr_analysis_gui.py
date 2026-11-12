"""Hardware-free tests for Stage 1 of the offline ODMR analysis GUI."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QCoreApplication, QEvent, QThread, Qt
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

from analysis.odmr_fit import two_lorentzian_odmr_model
from analysis.odmr_pixel_fit import (
    ODMRPixelFitResult,
    PixelFitStatus,
)
from framework.image_cube import ImageCube
from gui.odmr_analysis_window import (
    ODMRAnalysisState,
    ODMRAnalysisWindow,
    display_position_to_pixel,
)
from gui.odmr_analysis_worker import ODMRPixelFitWorker
from gui.main_window import MainWindow


_APP = QApplication.instance() or QApplication(sys.argv)
_APP.setQuitOnLastWindowClosed(False)


def _write_statistics_file(path, baseline_offset=0.0):
    frequency_hz = np.linspace(2.82e9, 2.92e9, 41)
    spectrum = two_lorentzian_odmr_model(
        frequency_hz,
        100.0 + baseline_offset,
        2.0,
        2.86e9,
        4.0e6,
        1.4,
        2.88e9,
        5.0e6,
    )
    spatial_values = np.empty((len(frequency_hz), 2, 2), dtype=np.float64)
    spatial_values[:, 0, 0] = spectrum
    spatial_values[:, 0, 1] = spectrum + 2.0
    spatial_values[:, 1, 0] = spectrum + 4.0
    spatial_values[:, 1, 1] = np.nan

    # Two identical completed averages make the final mean deterministic.
    data = np.empty((2, 2, len(frequency_hz), 2, 2), dtype=np.float64)
    data[:, 0] = spatial_values
    data[:, 1] = 0.2
    data[:, 1, :, 1, 1] = np.nan
    cube = ImageCube(
        data=data,
        axes={
            "axis_order": ["average", "statistic", "frequency", "y", "x"],
            "average": [0, 1],
            "statistic": ["normalized_mean", "normalized_sd"],
            "frequency": frequency_hz.tolist(),
            "y": [0, 1],
            "x": [0, 1],
        },
        metadata={
            "configured_averages": 2,
            "completed_averages": 2,
            "n_averages": 2,
            "configured_repeats": 5,
            "n_repeats_per_average": [5, 5],
            "experiment_complete": True,
            "interrupted": False,
        },
        experiment_type="ODMR",
        scan_axis_name="frequency",
        scan_axis_unit="Hz",
        scan_axis_values=frequency_hz,
    )
    cube.save(path)
    expected_global_spectrum = spectrum + 2.0
    return frequency_hz, expected_global_spectrum


def _fit_result(frequency_hz, spectrum):
    return SimpleNamespace(
        success=True,
        status="success",
        message="Fit converged.",
        frequency_hz=frequency_hz.copy(),
        spectrum=spectrum.copy(),
        fitted_spectrum=spectrum.copy(),
        baseline=101.0,
        frequency_minus_hz=2.86e9,
        frequency_plus_hz=2.88e9,
        linewidth_minus_hz=4.0e6,
        linewidth_plus_hz=5.0e6,
        amplitude_minus=2.0,
        amplitude_plus=1.4,
        rmse=0.02,
        r2=0.99,
    )


def _write_asymmetric_statistics_file(path):
    frequency_hz = np.linspace(2.82e9, 2.92e9, 41)
    base = two_lorentzian_odmr_model(
        frequency_hz, 100.0, 2.0, 2.86e9, 4.0e6, 1.4, 2.88e9, 5.0e6
    )
    final_mean = np.empty((len(frequency_hz), 2, 3), dtype=np.float64)
    for y in range(2):
        for x in range(3):
            final_mean[:, y, x] = base + 10.0 * y + x
    data = np.empty((1, 2, len(frequency_hz), 2, 3), dtype=np.float64)
    data[0, 0] = final_mean
    data[0, 1] = 0.2
    cube = ImageCube(
        data=data,
        axes={
            "axis_order": ["average", "statistic", "frequency", "y", "x"],
            "average": [0],
            "statistic": ["normalized_mean", "normalized_sd"],
            "frequency": frequency_hz.tolist(),
            "y": [0, 1],
            "x": [0, 1, 2],
        },
        metadata={
            "configured_averages": 1,
            "completed_averages": 1,
            "n_averages": 1,
            "configured_repeats": 5,
            "n_repeats_per_average": [5],
            "experiment_complete": True,
        },
        experiment_type="ODMR",
        scan_axis_name="frequency",
        scan_axis_unit="Hz",
        scan_axis_values=frequency_hz,
    )
    cube.save(path)
    return frequency_hz, final_mean


def _pixel_result(frequency_hz, status_codes=None):
    if status_codes is None:
        status_codes = np.zeros((2, 3), dtype=np.uint8)
    status = np.asarray(status_codes, dtype=np.uint8)
    height, width = status.shape
    yy, xx = np.indices(status.shape)
    success = status == PixelFitStatus.SUCCESS

    f_minus = 2.85e9 + (yy * width + xx) * 1.0e6
    f_plus = f_minus + 20.0e6 + (xx * 0.5e6)
    baseline = 100.0 + yy + xx
    amplitude_minus = np.full(status.shape, 2.0)
    amplitude_plus = np.full(status.shape, 1.5)
    linewidth_minus = np.full(status.shape, 4.0e6)
    linewidth_plus = np.full(status.shape, 5.0e6)
    rmse = np.full(status.shape, 0.03)
    r2 = np.full(status.shape, 0.98)
    f_minus_uncertainty = np.full(status.shape, 0.2e6)
    f_plus_uncertainty = np.full(status.shape, 0.3e6)
    for values in (
        f_minus,
        f_plus,
        baseline,
        amplitude_minus,
        amplitude_plus,
        linewidth_minus,
        linewidth_plus,
        rmse,
        r2,
        f_minus_uncertainty,
        f_plus_uncertainty,
    ):
        values[~success] = np.nan

    valid_spectrum = np.isin(
        status,
        [
            PixelFitStatus.SUCCESS,
            PixelFitStatus.OPTIMIZER_FAILURE,
            PixelFitStatus.INVALID_RESULT,
        ],
    )
    successful_count = int(success.sum())
    valid_count = int(valid_spectrum.sum())
    return ODMRPixelFitResult(
        baseline=baseline,
        amplitude_minus=amplitude_minus,
        amplitude_plus=amplitude_plus,
        f_minus_hz=f_minus,
        f_plus_hz=f_plus,
        splitting_hz=f_plus - f_minus,
        center_frequency_hz=(f_plus + f_minus) / 2.0,
        linewidth_minus_hz=linewidth_minus,
        linewidth_plus_hz=linewidth_plus,
        rmse=rmse,
        r2=r2,
        f_minus_uncertainty_hz=f_minus_uncertainty,
        f_plus_uncertainty_hz=f_plus_uncertainty,
        status=status,
        valid_spectrum_mask=valid_spectrum,
        successful_fit_mask=success,
        finite_sample_count=np.full(status.shape, len(frequency_hz), dtype=np.int32),
        valid_pixel_count=valid_count,
        successful_fit_count=successful_count,
        failed_fit_count=int(status.size - successful_count),
        success_fraction=successful_count / valid_count if valid_count else 0.0,
        frequency_hz=frequency_hz.copy(),
    )


def _wait_for_signal(spy, timeout_ms=5000):
    return len(spy) > 0 or spy.wait(timeout_ms)


class ODMRAnalysisGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.window = ODMRAnalysisWindow()
        self.addCleanup(self._close_window)

    def _close_window(self):
        self.window.close()
        self.window.deleteLater()
        _APP.processEvents()

    def _file(self, name, baseline_offset=0.0):
        path = Path(self.temp_dir.name) / name
        return path, _write_statistics_file(path, baseline_offset)

    def test_window_constructs_without_hardware_and_starts_empty(self):
        from hardware.hardware_manager import HardwareManager

        self.window.close()
        with patch.object(
            HardwareManager,
            "__init__",
            side_effect=AssertionError("hardware must not be constructed"),
        ) as hardware_init:
            self.window = ODMRAnalysisWindow()
        hardware_init.assert_not_called()
        self.assertEqual(self.window.state, ODMRAnalysisState.EMPTY)
        self.assertIsNone(self.window.loaded_odmr_cube)
        self.assertIsNone(self.window.global_spectrum)
        self.assertFalse(self.window.panel.fit_button.isEnabled())

    def test_load_populates_metadata_spectrum_and_loaded_state(self):
        path, (frequency_hz, expected_spectrum) = self._file("first.h5")

        self.assertTrue(self.window.load_file(path))

        self.assertEqual(self.window.state, ODMRAnalysisState.LOADED)
        self.assertEqual(self.window.selected_source_path, str(path))
        self.assertIsNotNone(self.window.loaded_odmr_cube)
        self.assertEqual(self.window.statistics_result.n_averages, 2)
        self.assertIn("Configured averages: 2", self.window.panel.metadata_label.text())
        self.assertIn("Completed averages: 2", self.window.panel.metadata_label.text())
        self.assertIn("Repeats per average: 5, 5", self.window.panel.metadata_label.text())
        self.assertIn("Image dimensions: 2 × 2", self.window.panel.metadata_label.text())
        self.assertIn("Frequency points: 41", self.window.panel.metadata_label.text())
        self.assertIn("Acquisition status: Complete", self.window.panel.metadata_label.text())
        self.assertTrue(self.window.panel.fit_button.isEnabled())

        np.testing.assert_array_equal(self.window.statistics_result.frequency_values, frequency_hz)
        np.testing.assert_allclose(self.window.global_spectrum, expected_spectrum)
        x, y = self.window.panel.measured_curve.getData()
        np.testing.assert_allclose(x, frequency_hz / 1.0e9)
        np.testing.assert_allclose(y, expected_spectrum)
        self.assertEqual(self.window.panel.plot.getAxis("bottom").labelText, "Frequency (GHz)")
        self.assertEqual(
            self.window.panel.plot.getAxis("left").labelText,
            "Normalized fluorescence (%)",
        )

    def test_global_fit_calls_backend_stores_result_and_updates_display(self):
        path, (frequency_hz, _) = self._file("fit.h5")
        self.window.load_file(path)
        expected = _fit_result(frequency_hz, self.window.global_spectrum)

        with patch("gui.odmr_analysis_window.fit_global_odmr_spectrum", return_value=expected) as fit:
            result = self.window.fit_global_spectrum()

        fit.assert_called_once()
        args, kwargs = fit.call_args
        np.testing.assert_array_equal(args[0], self.window.statistics_result.final_mean)
        np.testing.assert_array_equal(args[1], frequency_hz)
        self.assertEqual(kwargs, {})
        self.assertIs(result, expected)
        self.assertIs(self.window.global_fit_result, expected)
        self.assertEqual(self.window.state, ODMRAnalysisState.GLOBAL_FIT_AVAILABLE)
        x, y = self.window.panel.fitted_curve.getData()
        np.testing.assert_allclose(x, frequency_hz / 1.0e9)
        np.testing.assert_array_equal(y, expected.fitted_spectrum)
        fit_text = self.window.panel.fit_info_label.text()
        self.assertIn("f_low: 2.86 GHz", fit_text)
        self.assertIn("Splitting: 0.02 GHz", fit_text)
        self.assertIn("RMSE: 0.02%", fit_text)
        self.assertIn("R²: 0.99", fit_text)

    def test_loading_another_file_clears_previous_fit(self):
        first_path, (frequency_hz, _) = self._file("first.h5")
        second_path, _ = self._file("second.h5", baseline_offset=0.5)
        self.window.load_file(first_path)
        result = _fit_result(frequency_hz, self.window.global_spectrum)
        with patch("gui.odmr_analysis_window.fit_global_odmr_spectrum", return_value=result):
            self.window.fit_global_spectrum()
        self.assertEqual(self.window.state, ODMRAnalysisState.GLOBAL_FIT_AVAILABLE)

        self.assertTrue(self.window.load_file(second_path))

        self.assertEqual(self.window.state, ODMRAnalysisState.LOADED)
        self.assertIsNone(self.window.global_fit_result)
        x, y = self.window.panel.fitted_curve.getData()
        self.assertIsNone(x)
        self.assertIsNone(y)
        self.assertIn("No global fit", self.window.panel.fit_info_label.text())

    def test_fit_failure_keeps_loaded_spectrum_and_does_not_raise(self):
        path, _ = self._file("failure.h5")
        self.window.load_file(path)
        spectrum_before = self.window.global_spectrum.copy()

        with patch(
            "gui.odmr_analysis_window.fit_global_odmr_spectrum",
            side_effect=ValueError("At least 8 finite samples are required."),
        ), patch.object(QMessageBox, "warning") as warning:
            result = self.window.fit_global_spectrum()

        self.assertIsNone(result)
        self.assertEqual(self.window.state, ODMRAnalysisState.LOADED)
        self.assertIsNone(self.window.global_fit_result)
        np.testing.assert_array_equal(self.window.global_spectrum, spectrum_before)
        self.assertIn("fit failed", self.window.panel.status_label.text().lower())
        x, y = self.window.panel.measured_curve.getData()
        self.assertEqual(len(x), len(spectrum_before))
        self.assertEqual(len(y), len(spectrum_before))
        warning.assert_called_once()

    def test_cancelled_file_selection_leaves_current_state_unchanged(self):
        path, _ = self._file("cancel.h5")
        self.window.load_file(path)
        previous_state = self.window.state
        previous_cube = self.window.loaded_odmr_cube
        previous_spectrum = self.window.global_spectrum.copy()

        with patch(
            "gui.odmr_analysis_window.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ):
            self.window.open_file()

        self.assertEqual(self.window.state, previous_state)
        self.assertIs(self.window.loaded_odmr_cube, previous_cube)
        np.testing.assert_array_equal(self.window.global_spectrum, previous_spectrum)

    def test_failed_load_clears_old_downstream_results(self):
        good_path, _ = self._file("good.h5")
        bad_path = Path(self.temp_dir.name) / "invalid.h5"
        bad_path.write_text("not an HDF5 file", encoding="utf-8")
        self.window.load_file(good_path)

        with patch.object(QMessageBox, "warning"):
            success = self.window.load_file(bad_path)

        self.assertFalse(success)
        self.assertEqual(self.window.state, ODMRAnalysisState.EMPTY)
        self.assertIsNone(self.window.loaded_odmr_cube)
        self.assertIsNone(self.window.statistics_result)
        self.assertIsNone(self.window.global_spectrum)
        self.assertIsNone(self.window.global_fit_result)
        self.assertFalse(self.window.panel.fit_button.isEnabled())


class ODMRAnalysisStage2GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared_window = ODMRAnalysisWindow()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.window = self.shared_window
        self.window._clear_session()

    @classmethod
    def tearDownClass(cls):
        if cls.shared_window.pixel_thread is not None:
            cls.shared_window.pixel_thread.wait(5000)
        cls.shared_window.close()
        cls.shared_window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _APP.processEvents()

    def _prepare_analysis(self):
        path = Path(self.temp_dir.name) / "asymmetric.h5"
        frequency_hz, final_mean = _write_asymmetric_statistics_file(path)
        self.assertTrue(self.window.load_file(path))
        global_fit = _fit_result(frequency_hz, self.window.global_spectrum)
        self.window.global_fit_result = global_fit
        self.window.state = ODMRAnalysisState.GLOBAL_FIT_AVAILABLE
        self.window.panel.set_fit_result(global_fit)
        self.window._update_actions()
        return path, frequency_hz, final_mean, global_fit

    def _install_pixel_result(self, frequency_hz, status_codes=None):
        result = _pixel_result(frequency_hz, status_codes)
        self.window.pixel_fit_result = result
        self.window.state = ODMRAnalysisState.PIXEL_FIT_AVAILABLE
        self.window.panel.set_pixel_fit_summary(result)
        self.window.panel.set_map_enabled(True)
        self.window.show_selected_map(self.window.panel.map_combo.currentText())
        self.window._update_actions()
        return result

    def _start_delayed_window_fit(self, result=None, error=None):
        entered_backend = threading.Event()
        release_backend = threading.Event()
        self.addCleanup(release_backend.set)
        executing_threads = []

        def fake_fit(mean_cube, global_fit):
            executing_threads.append(QThread.currentThread())
            entered_backend.set()
            if not release_backend.wait(5.0):
                raise TimeoutError("test did not release fake fitter")
            if error is not None:
                raise error
            return result

        patcher = patch("gui.odmr_analysis_worker.fit_odmr_pixels", side_effect=fake_fit)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertTrue(self.window.fit_pixels())
        self.assertTrue(entered_backend.wait(2.0))
        thread = self.window.pixel_thread
        thread_finished = QSignalSpy(thread.finished)
        return release_backend, executing_threads, thread, thread_finished

    def test_pixel_worker_success_runs_off_gui_thread_and_emits_result(self):
        cube = np.zeros((8, 1, 1))
        global_fit = object()
        expected = object()
        observed = {}

        def fake_fit(received_cube, received_fit):
            observed["thread"] = QThread.currentThread()
            observed["cube"] = received_cube
            observed["fit"] = received_fit
            return expected

        worker = ODMRPixelFitWorker(cube, global_fit)
        thread = QThread()
        worker.moveToThread(thread)
        started = QSignalSpy(worker.started)
        finished = QSignalSpy(worker.finished)
        failed = QSignalSpy(worker.failed)
        thread_finished = QSignalSpy(thread.finished)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        # No deleteLater wiring: this test owns the thread's lifetime. Qt
        # emits QThread.finished from inside the thread *before* it has fully
        # exited, so deleting the QThread right after that signal (via
        # deleteLater, or by dropping the last Python reference when the test
        # returns) runs ~QThread, which waits for the exit while holding the
        # GIL -- and the exiting thread needs the GIL for PyQt's teardown.
        # That deadlock intermittently hung the failure-path test below,
        # which has the same structure.

        with patch("gui.odmr_analysis_worker.fit_odmr_pixels", side_effect=fake_fit):
            thread.start()
            self.assertTrue(_wait_for_signal(thread_finished))
            # Wait, bounded, until the thread has really exited. wait()
            # releases the GIL, so the thread can finish its teardown.
            self.assertTrue(thread.wait(5000))
        self.assertTrue(thread.isFinished())

        self.assertEqual(len(started), 1)
        self.assertEqual(len(finished), 1)
        self.assertEqual(len(failed), 0)
        self.assertIs(finished[0][0], expected)
        self.assertIs(observed["cube"], cube)
        self.assertIs(observed["fit"], global_fit)
        self.assertNotEqual(observed["thread"], _APP.thread())

    def test_pixel_worker_failure_emits_error_and_stops_thread(self):
        worker = ODMRPixelFitWorker(np.zeros((8, 1, 1)), object())
        thread = QThread()
        worker.moveToThread(thread)
        failed = QSignalSpy(worker.failed)
        finished = QSignalSpy(worker.finished)
        thread_finished = QSignalSpy(thread.finished)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        # No deleteLater wiring: this test owns the thread's lifetime. Qt
        # emits QThread.finished from inside the thread *before* it has fully
        # exited, so deleting the QThread right after that signal (via
        # deleteLater, or by dropping the last Python reference when the test
        # returns) runs ~QThread, which waits for the exit while holding the
        # GIL -- and the exiting thread needs the GIL for PyQt's teardown.
        # That deadlock hung this test intermittently.

        with patch(
            "gui.odmr_analysis_worker.fit_odmr_pixels",
            side_effect=RuntimeError("synthetic worker failure"),
        ):
            thread.start()
            self.assertTrue(_wait_for_signal(thread_finished))
            # Wait, bounded, until the thread has really exited. wait()
            # releases the GIL, so the thread can finish its teardown.
            self.assertTrue(thread.wait(5000))
        self.assertTrue(thread.isFinished())

        self.assertEqual(len(failed), 1)
        self.assertEqual(len(finished), 0)
        self.assertIn("synthetic worker failure", failed[0][0])

    def test_controls_disable_during_fit_and_restore_with_partial_result(self):
        _path, frequency_hz, _mean, global_fit = self._prepare_analysis()
        status = np.array(
            [
                [PixelFitStatus.SUCCESS, PixelFitStatus.INSUFFICIENT_SAMPLES,
                 PixelFitStatus.UNUSABLE_SPECTRUM],
                [PixelFitStatus.OPTIMIZER_FAILURE, PixelFitStatus.INVALID_RESULT,
                 PixelFitStatus.SUCCESS],
            ],
            dtype=np.uint8,
        )
        result = _pixel_result(frequency_hz, status)
        release, worker_threads, thread, thread_finished = self._start_delayed_window_fit(
            result=result
        )

        self.assertFalse(self.window.panel.open_button.isEnabled())
        self.assertFalse(self.window.panel.fit_button.isEnabled())
        self.assertFalse(self.window.panel.fit_pixels_button.isEnabled())
        self.assertFalse(self.window.panel.reconstruct_bz_button.isEnabled())
        self.assertIn("Fitting pixels", self.window.panel.status_label.text())
        self.assertFalse(self.window.load_file(Path(self.temp_dir.name) / "ignored.h5"))
        self.assertEqual(self.window.global_fit_result, global_fit)

        release.set()
        self.assertTrue(_wait_for_signal(thread_finished))
        _APP.processEvents()

        self.assertIsNone(self.window.pixel_thread)
        self.assertIsNone(self.window.pixel_worker)
        self.assertEqual(self.window.state, ODMRAnalysisState.PIXEL_FIT_AVAILABLE)
        self.assertIs(self.window.pixel_fit_result, result)
        self.assertIs(self.window.global_fit_result, global_fit)
        self.assertEqual(self.window.pixel_fit_result.successful_fit_count, 2)
        self.assertEqual(self.window.pixel_fit_result.failed_fit_count, 4)
        self.assertTrue(self.window.panel.open_button.isEnabled())
        self.assertTrue(self.window.panel.fit_button.isEnabled())
        self.assertTrue(self.window.panel.fit_pixels_button.isEnabled())
        self.assertTrue(self.window.panel.reconstruct_bz_button.isEnabled())
        self.assertNotEqual(worker_threads[0], _APP.thread())

    def test_worker_failure_retains_global_fit_and_restores_controls(self):
        self._prepare_analysis()
        release, _threads, _thread, thread_finished = self._start_delayed_window_fit(
            error=RuntimeError("synthetic fitting error")
        )
        with patch.object(QMessageBox, "warning") as warning:
            release.set()
            self.assertTrue(_wait_for_signal(thread_finished))
            _APP.processEvents()

        self.assertIsNone(self.window.pixel_thread)
        self.assertIsNone(self.window.pixel_worker)
        self.assertEqual(self.window.state, ODMRAnalysisState.GLOBAL_FIT_AVAILABLE)
        self.assertIsNotNone(self.window.global_fit_result)
        self.assertIsNone(self.window.pixel_fit_result)
        self.assertTrue(self.window.panel.fit_pixels_button.isEnabled())
        self.assertFalse(self.window.panel.reconstruct_bz_button.isEnabled())
        self.assertIn("synthetic fitting error", self.window.panel.status_label.text())
        warning.assert_called_once()

    def test_zero_success_result_keeps_diagnostics_and_disables_bz(self):
        _path, frequency_hz, _mean, _fit = self._prepare_analysis()
        statuses = np.array(
            [
                [PixelFitStatus.INSUFFICIENT_SAMPLES, PixelFitStatus.UNUSABLE_SPECTRUM,
                 PixelFitStatus.OPTIMIZER_FAILURE],
                [PixelFitStatus.INVALID_RESULT, PixelFitStatus.INSUFFICIENT_SAMPLES,
                 PixelFitStatus.UNUSABLE_SPECTRUM],
            ],
            dtype=np.uint8,
        )
        result = _pixel_result(frequency_hz, statuses)
        release, _threads, _thread, thread_finished = self._start_delayed_window_fit(
            result=result
        )
        release.set()
        self.assertTrue(_wait_for_signal(thread_finished))
        _APP.processEvents()

        self.assertEqual(self.window.state, ODMRAnalysisState.PIXEL_FIT_AVAILABLE)
        self.assertIs(self.window.pixel_fit_result, result)
        self.assertEqual(result.successful_fit_count, 0)
        self.assertFalse(self.window.panel.reconstruct_bz_button.isEnabled())
        self.assertEqual(self.window.panel.map_combo.currentText(), "fit_status")
        self.assertEqual(
            self.window.panel.image_item.image.shape,
            (3, 2),
        )
        self.assertIn("Successful fits: 0", self.window.panel.pixel_fit_summary_label.text())
        self.assertIn("no pixels fitted successfully", self.window.panel.status_label.text())

    def test_map_transpose_click_mapping_pixel_spectrum_and_fit_curve(self):
        _path, frequency_hz, final_mean, _fit = self._prepare_analysis()
        statuses = np.array(
            [
                [PixelFitStatus.SUCCESS, PixelFitStatus.INSUFFICIENT_SAMPLES,
                 PixelFitStatus.UNUSABLE_SPECTRUM],
                [PixelFitStatus.OPTIMIZER_FAILURE, PixelFitStatus.INVALID_RESULT,
                 PixelFitStatus.SUCCESS],
            ],
            dtype=np.uint8,
        )
        result = self._install_pixel_result(frequency_hz, statuses)

        displayed = self.window.panel.image_item.image
        np.testing.assert_allclose(displayed, result.f_minus_hz.T / 1.0e9, equal_nan=True)
        self.assertEqual(display_position_to_pixel(2.25, 1.75, (2, 3)), (2, 1))
        self.assertIsNone(display_position_to_pixel(-0.01, 0.5, (2, 3)))
        self.assertIsNone(display_position_to_pixel(3.0, 0.5, (2, 3)))
        self.assertIsNone(display_position_to_pixel(float("nan"), 0.5, (2, 3)))

        self.assertEqual(self.window.select_pixel_from_image(2.25, 1.75), (2, 1))
        self.assertIn("Pixel: x=2, y=1", self.window.panel.selected_pixel_label.text())
        measured_x, measured_y = self.window.panel.pixel_measured_curve.getData()
        np.testing.assert_allclose(measured_x, frequency_hz / 1.0e9)
        np.testing.assert_array_equal(measured_y, final_mean[:, 1, 2])
        fit_x, fit_y = self.window.panel.pixel_fitted_curve.getData()
        expected_fit = two_lorentzian_odmr_model(
            frequency_hz,
            result.baseline[1, 2],
            result.amplitude_minus[1, 2],
            result.f_minus_hz[1, 2],
            result.linewidth_minus_hz[1, 2],
            result.amplitude_plus[1, 2],
            result.f_plus_hz[1, 2],
            result.linewidth_plus_hz[1, 2],
        )
        np.testing.assert_allclose(fit_x, frequency_hz / 1.0e9)
        np.testing.assert_allclose(fit_y, expected_fit)

        self.window.panel.map_combo.setCurrentText("f_plus")
        _APP.processEvents()
        self.assertEqual(self.window.selected_pixel, (2, 1))
        np.testing.assert_allclose(
            self.window.panel.image_item.image,
            result.f_plus_hz.T / 1.0e9,
            equal_nan=True,
        )
        self.assertEqual(
            self.window.panel.color_bar.values,
            (
                float(np.nanmin(result.f_plus_hz) / 1.0e9),
                float(np.nanmax(result.f_plus_hz) / 1.0e9),
            ),
        )

        previous_pixel = self.window.selected_pixel
        self.assertIsNone(self.window.select_pixel_from_image(-0.01, 0.5))
        self.assertEqual(self.window.selected_pixel, previous_pixel)
        self.assertEqual(self.window.select_pixel_from_image(1.2, 0.2), (1, 0))
        self.assertIn("insufficient_samples", self.window.panel.selected_pixel_label.text())
        self.assertIsNone(self.window.panel.pixel_fitted_curve.getData()[0])
        failed_x, failed_y = self.window.panel.pixel_measured_curve.getData()
        self.assertEqual(len(failed_x), len(frequency_hz))
        self.assertEqual(len(failed_y), len(frequency_hz))

    def test_map_selector_displays_categorical_status_labels(self):
        _path, frequency_hz, _mean, _fit = self._prepare_analysis()
        statuses = np.array(
            [
                [PixelFitStatus.SUCCESS, PixelFitStatus.INSUFFICIENT_SAMPLES,
                 PixelFitStatus.UNUSABLE_SPECTRUM],
                [PixelFitStatus.OPTIMIZER_FAILURE, PixelFitStatus.INVALID_RESULT,
                 PixelFitStatus.SUCCESS],
            ],
            dtype=np.uint8,
        )
        self._install_pixel_result(frequency_hz, statuses)
        self.window.panel.map_combo.setCurrentText("fit_status")
        _APP.processEvents()

        shown = self.window.panel.image_item.image
        np.testing.assert_array_equal(shown, statuses.T)
        self.assertEqual(self.window.panel.color_bar.values, (-0.5, 4.5))
        self.assertFalse(self.window.panel.scale_min_spin.isEnabled())
        self.assertFalse(self.window.panel.colormap_combo.isEnabled())
        self.assertIn("insufficient_samples", self.window.panel.status_legend_label.text())
        self.assertIn("invalid_result", self.window.panel.status_legend_label.text())
        self.assertIn("category", self.window.panel.map_title_label.text())

    def test_colorbar_tracks_selected_map_and_displays_units(self):
        _path, frequency_hz, _mean, _fit = self._prepare_analysis()
        result = self._install_pixel_result(frequency_hz)
        panel = self.window.panel

        self.assertTrue(panel.color_bar.isVisible())
        self.assertEqual(
            panel.color_bar.values,
            (
                float(np.nanmin(result.f_minus_hz) / 1.0e9),
                float(np.nanmax(result.f_minus_hz) / 1.0e9),
            ),
        )
        self.assertEqual(panel.color_bar.getAxis("left").labelText, "GHz")

        panel.map_combo.setCurrentText("RMSE")
        _APP.processEvents()

        rmse_value = float(np.nanmin(result.rmse))
        padding = max(abs(rmse_value) * 1.0e-6, 1.0e-12)
        self.assertEqual(
            panel.color_bar.values,
            (rmse_value - padding, rmse_value + padding),
        )
        self.assertEqual(panel.color_bar.getAxis("left").labelText, "%")
        self.assertEqual(panel.map_combo.count(), 6)

    def test_manual_scale_and_auto_restore_only_change_display_levels(self):
        _path, frequency_hz, _mean, _fit = self._prepare_analysis()
        result = self._install_pixel_result(frequency_hz)
        panel = self.window.panel
        map_before = panel.image_item.image.copy()
        requested_levels = (2.851, 2.854)

        panel.scale_min_spin.setValue(requested_levels[0])
        panel.scale_max_spin.setValue(requested_levels[1])

        self.assertEqual(panel.color_bar.values, requested_levels)
        np.testing.assert_allclose(panel.image_item.getLevels(), requested_levels)
        np.testing.assert_array_equal(panel.image_item.image, map_before)
        np.testing.assert_array_equal(result.f_minus_hz, 2.85e9 + np.arange(6).reshape(2, 3) * 1.0e6)

        panel.auto_scale_button.click()

        automatic = (
            float(np.nanmin(result.f_minus_hz) / 1.0e9),
            float(np.nanmax(result.f_minus_hz) / 1.0e9),
        )
        self.assertEqual(panel.color_bar.values, automatic)
        np.testing.assert_allclose(panel.image_item.getLevels(), automatic)
        self.assertEqual(panel.scale_min_spin.value(), automatic[0])
        self.assertEqual(panel.scale_max_spin.value(), automatic[1])

    def test_colormap_selection_updates_image_and_colorbar(self):
        _path, frequency_hz, _mean, _fit = self._prepare_analysis()
        self._install_pixel_result(frequency_hz)
        panel = self.window.panel
        self.assertEqual(panel.colormap_combo.currentText(), "Viridis")

        panel.colormap_combo.setCurrentText("Magma")
        _APP.processEvents()

        expected = pg.colormap.get("magma", source="matplotlib")
        image_map = panel.image_item.getColorMap()
        self.assertIsNotNone(image_map)
        np.testing.assert_array_equal(
            image_map.getLookupTable(nPts=256, alpha=True),
            expected.getLookupTable(nPts=256, alpha=True),
        )
        np.testing.assert_array_equal(
            panel.color_bar._colorMap.getLookupTable(nPts=256, alpha=True),
            expected.getLookupTable(nPts=256, alpha=True),
        )

    def test_reconstruct_bz_calls_backend_and_preserves_invalid_pixels(self):
        _path, frequency_hz, _mean, _fit = self._prepare_analysis()
        statuses = np.array(
            [
                [PixelFitStatus.SUCCESS, PixelFitStatus.INSUFFICIENT_SAMPLES,
                 PixelFitStatus.UNUSABLE_SPECTRUM],
                [PixelFitStatus.OPTIMIZER_FAILURE, PixelFitStatus.INVALID_RESULT,
                 PixelFitStatus.SUCCESS],
            ],
            dtype=np.uint8,
        )
        pixel_result = self._install_pixel_result(frequency_hz, statuses)
        valid = pixel_result.successful_fit_mask.copy()
        bz_map = np.full(statuses.shape, np.nan)
        bz_map[valid] = [12.0, 15.0]
        magnetic = SimpleNamespace(
            bz_magnitude=bz_map,
            valid_mask=valid,
            field_units="G",
        )

        with patch("gui.odmr_analysis_window.reconstruct_bz", return_value=magnetic) as reconstruct:
            result = self.window.reconstruct_magnetic_map()

        reconstruct.assert_called_once_with(pixel_result)
        self.assertIs(result, magnetic)
        self.assertIs(self.window.magnetic_result, magnetic)
        self.assertEqual(self.window.state, ODMRAnalysisState.MAGNETIC_MAP_AVAILABLE)
        self.assertEqual(self.window.panel.map_combo.currentText(), "Bz magnitude")
        np.testing.assert_allclose(
            self.window.panel.image_item.image,
            bz_map.T,
            equal_nan=True,
        )
        self.assertIn("[G]", self.window.panel.map_title_label.text())
        self.assertTrue(np.isnan(self.window.panel.image_item.image[1, 0]))
        self.assertFalse(self.window.panel.reconstruct_bz_button.isEnabled())

    def test_reconstruction_error_preserves_pixel_maps(self):
        _path, frequency_hz, _mean, _fit = self._prepare_analysis()
        pixel_result = self._install_pixel_result(frequency_hz)
        before = self.window.panel.image_item.image.copy()

        with patch(
            "gui.odmr_analysis_window.reconstruct_bz",
            side_effect=ValueError("synthetic reconstruction error"),
        ), patch.object(QMessageBox, "warning") as warning:
            result = self.window.reconstruct_magnetic_map()

        self.assertIsNone(result)
        self.assertIs(self.window.pixel_fit_result, pixel_result)
        self.assertIsNone(self.window.magnetic_result)
        self.assertEqual(self.window.state, ODMRAnalysisState.PIXEL_FIT_AVAILABLE)
        np.testing.assert_array_equal(
            self.window.panel.image_item.image, before
        )
        warning.assert_called_once()

    def test_loading_new_file_invalidates_pixel_magnetic_and_selection(self):
        first = Path(self.temp_dir.name) / "first-asym.h5"
        second = Path(self.temp_dir.name) / "second-asym.h5"
        frequency_hz, _mean = _write_asymmetric_statistics_file(first)
        _write_asymmetric_statistics_file(second)
        self.assertTrue(self.window.load_file(first))
        self.window.global_fit_result = _fit_result(
            frequency_hz, self.window.global_spectrum
        )
        self.window.state = ODMRAnalysisState.GLOBAL_FIT_AVAILABLE
        pixel = self._install_pixel_result(frequency_hz)
        self.window.select_pixel_from_image(1.2, 1.2)
        magnetic = SimpleNamespace(
            bz_magnitude=np.ones((2, 3)),
            valid_mask=np.ones((2, 3), dtype=bool),
            field_units="G",
        )
        self.window.magnetic_result = magnetic
        self.window.state = ODMRAnalysisState.MAGNETIC_MAP_AVAILABLE

        self.assertTrue(self.window.load_file(second))

        self.assertIsNone(self.window.global_fit_result)
        self.assertIsNone(self.window.pixel_fit_result)
        self.assertIsNone(self.window.magnetic_result)
        self.assertIsNone(self.window.selected_pixel)
        self.assertEqual(self.window.state, ODMRAnalysisState.LOADED)
        self.assertFalse(self.window.panel.fit_pixels_button.isEnabled())
        self.assertFalse(self.window.panel.map_combo.isEnabled())

    def test_global_refit_invalidates_pixel_magnetic_and_selection(self):
        _path, frequency_hz, _mean, _fit = self._prepare_analysis()
        pixel_result = self._install_pixel_result(frequency_hz)
        self.window.select_pixel_from_image(1.2, 1.2)
        self.window.magnetic_result = SimpleNamespace(
            bz_magnitude=np.ones((2, 3)),
            valid_mask=np.ones((2, 3), dtype=bool),
            field_units="G",
        )
        self.window.state = ODMRAnalysisState.MAGNETIC_MAP_AVAILABLE
        replacement_fit = _fit_result(frequency_hz, self.window.global_spectrum)

        with patch(
            "gui.odmr_analysis_window.fit_global_odmr_spectrum",
            return_value=replacement_fit,
        ):
            self.window.fit_global_spectrum()

        self.assertIsNone(self.window.pixel_fit_result)
        self.assertIsNone(self.window.magnetic_result)
        self.assertIsNone(self.window.selected_pixel)
        self.assertIsNot(self.window.global_fit_result, _fit)
        self.assertIsNotNone(pixel_result)
        self.assertEqual(self.window.state, ODMRAnalysisState.GLOBAL_FIT_AVAILABLE)
        self.assertFalse(self.window.panel.map_combo.isEnabled())

    def test_close_is_deferred_until_pixel_worker_stops(self):
        self._prepare_analysis()
        release, _threads, thread, thread_finished = self._start_delayed_window_fit(
            result=_pixel_result(np.linspace(2.82e9, 2.92e9, 41))
        )
        self.window.show()
        _APP.processEvents()

        self.window.close()

        self.assertTrue(self.window._close_after_pixel_fit)
        self.assertTrue(thread.isRunning())
        self.assertTrue(self.window.isVisible())
        release.set()
        self.assertTrue(_wait_for_signal(thread_finished))
        _APP.processEvents()

        self.assertIsNone(self.window.pixel_thread)
        self.assertFalse(self.window._close_after_pixel_fit)
        self.assertFalse(self.window.isVisible())


class _ODMRLaunchMainWindowHarness(QMainWindow):
    """Use MainWindow launch methods without constructing or touching hardware."""

    open_odmr_window = MainWindow.open_odmr_window
    open_odmr_analysis_window = MainWindow.open_odmr_analysis_window
    _odmr_analysis_window_destroyed = MainWindow._odmr_analysis_window_destroyed

    def __init__(self):
        super().__init__()
        self.odmr_window = None
        self.odmr_analysis_window = None
        self.hardware = {}
        self.camera = None

    @staticmethod
    def get_acquisition_state():
        return None


class ODMRAcquisitionGuiIntegrationTests(unittest.TestCase):
    """Hardware-free tests for the analysis launch action and control scroll."""

    @classmethod
    def setUpClass(cls):
        cls.main = _ODMRLaunchMainWindowHarness()
        cls.main.open_odmr_window()
        cls.window = cls.main.odmr_window
        cls.panel = cls.window.panel

    @classmethod
    def tearDownClass(cls):
        if cls.main.odmr_analysis_window is not None:
            cls.main.odmr_analysis_window.hide()
        if cls.window is not None:
            cls.window.hide()
        cls.main.hide()

    def test_analysis_button_routes_through_odmr_to_main_window(self):
        self.assertEqual(self.panel.analysis_button.text(), "ODMR Analysis")

        self.panel.analysis_button.click()

        analysis = self.main.odmr_analysis_window
        self.assertIsNotNone(analysis)
        self.assertTrue(analysis.isVisible())
        self.assertTrue(analysis.isWindow())
        self.assertIs(analysis.parentWidget(), self.main)

    def test_repeated_requests_reuse_the_same_analysis_window(self):
        self.panel.analysis_button.click()
        analysis = self.main.odmr_analysis_window
        self.panel.analysis_button.click()

        self.assertIs(self.main.odmr_analysis_window, analysis)
        self.assertTrue(analysis.isVisible())

    def test_closed_analysis_window_can_be_shown_again(self):
        self.panel.analysis_button.click()
        analysis = self.main.odmr_analysis_window
        analysis.close()
        self.assertFalse(analysis.isVisible())

        self.panel.analysis_button.click()

        self.assertIs(self.main.odmr_analysis_window, analysis)
        self.assertTrue(analysis.isVisible())

    def test_control_box_is_resizable_vertical_scroll_contents(self):
        scroll_area = self.panel.control_scroll_area

        self.assertTrue(scroll_area.widgetResizable())
        self.assertEqual(
            scroll_area.verticalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
        self.assertIs(scroll_area.widget(), self.panel.control_box)
        self.assertFalse(scroll_area.widget().isAncestorOf(self.panel.plot))

        main_layout = self.panel.layout()
        self.assertIs(main_layout.itemAt(0).widget(), scroll_area)
        self.assertIs(main_layout.itemAt(1).widget(), self.panel.plot.parentWidget())

    def test_existing_acquisition_controls_remain_available(self):
        for widget in (
            self.panel.fstart_spin,
            self.panel.fstop_spin,
            self.panel.steps_spin,
            self.panel.avg_spin,
            self.panel.repeat_spin,
            self.panel.fit_button,
            self.panel.statistics_details_button,
            self.panel.run_stop_button,
            self.panel.sequence_button,
            self.panel.progress_bar,
            self.panel.plot,
        ):
            self.assertIsNotNone(widget)


if __name__ == "__main__":
    unittest.main()
