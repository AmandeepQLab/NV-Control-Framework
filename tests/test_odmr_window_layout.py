"""ODMR window layout: the Start/Stop button fixed above a scrollable panel.

Runs real scans through ODMRWindow -> ODMRWorker -> ODMRExperiment on
SimCamera, SimMicrowave and SimPulseStreamer. No real hardware is touched.
"""

import sys
import time
import unittest
from unittest.mock import patch

# QApplication must exist before pyqtgraph (via the panel) is imported.
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from experiments.odmr_experiment import ODMRExperiment  # noqa: E402
from framework.field_direction import unit_direction  # noqa: E402
from gui.odmr_window import ODMRWindow  # noqa: E402
from hardware.camera.sim_camera import SimCamera  # noqa: E402
from tests.test_odmr_external_acquisition import _make_sim_hardware  # noqa: E402


def _pump_until(condition, timeout_s=30.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        _APP.processEvents()
        time.sleep(0.005)
    return True


class _AcquisitionState:
    acquisition_roi = None
    exposure_s = 0.001
    binning = 1
    baseline_counts = 0.0


class ODMRWindowLayoutTestCase(unittest.TestCase):
    def setUp(self):
        camera = SimCamera()
        camera.connect()
        self.hardware = _make_sim_hardware(camera)
        self.window = ODMRWindow(
            hardware=self.hardware,
            camera=camera,
            acquisition_state_getter=lambda: _AcquisitionState(),
        )
        self.panel = self.window.panel
        self.addCleanup(self._dispose)
        critical = patch.object(QMessageBox, "critical")
        self.critical = critical.start()
        self.addCleanup(critical.stop)

    def _dispose(self):
        window = self.window
        if window.odmr_running:
            window.request_stop()
        _pump_until(lambda: not window.odmr_running, 15)
        thread = window.worker_thread()
        _pump_until(lambda: thread is None or not thread.isRunning(), 10)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _fast_scan(self, steps):
        p = self.panel
        p.steps_spin.setValue(steps)
        p.delays_section.set_expanded(True)
        for spin, value in ((p.trigger_delay_spin, 0.001), (p.fire_delay_spin, 0.001),
                            (p.reset_delay_spin, 0.001), (p.pulse_lead_spin, 0.0005),
                            (p.pulse_tail_spin, 0.0005)):
            spin.setValue(value)

    def _assert_button(self, text, colour):
        button = self.panel.run_stop_button
        self.assertEqual(button.text(), text)
        self.assertIn(f"QPushButton {{ background-color: {colour};", button.styleSheet())
        self.assertIn("color: white", button.styleSheet())
        self.assertTrue(button.isEnabled())

    def _wait_idle(self):
        self.assertTrue(_pump_until(lambda: not self.window.odmr_running))
        thread = self.window.worker_thread()
        _pump_until(lambda: not thread.isRunning(), 10)


class LayoutTests(ODMRWindowLayoutTestCase):
    def test_run_button_is_fixed_above_the_scroll_area(self):
        p = self.panel
        scroll = p.control_scroll_area
        self.assertIs(scroll.widget(), p.control_box)
        self.assertFalse(scroll.isAncestorOf(p.run_stop_button))
        left = p.left_panel.layout()
        self.assertIs(left.itemAt(0).widget(), p.run_stop_button)
        self.assertIs(left.itemAt(1).widget(), p.progress_bar)
        self.assertIs(left.itemAt(2).widget(), p.freq_label)
        self.assertIs(left.itemAt(3).widget(), p.time_label)
        self.assertIs(left.itemAt(4).widget(), p.field_label)
        self.assertIs(left.itemAt(5).widget(), scroll)
        self.assertEqual(scroll.horizontalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(scroll.verticalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.assertTrue(scroll.widgetResizable())
        self.assertFalse(scroll.isAncestorOf(p.plot))

    def test_every_other_control_stays_in_the_scroll_area_in_order(self):
        p = self.panel
        layout = p.control_box.layout()
        widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
        widgets = [w for w in widgets if w is not None]
        # GUI redesign: one "Scan" card, then collapsible sections, then tools.
        self.assertEqual(widgets, [
            p.scan_group, p.dense_section, p.magnet_field_group,
            p.delays_section, p.stability_group, p.fit_section,
            p.statistics_group, p.tools_row,
        ])
        self.assertEqual(
            [w.title() for w in widgets[:-1]],
            ["Scan", "Dense points near ESR dips", "Magnetic field during scan",
             "Delays", "Stability check", "Plot & fit", "ODMR Statistics"])
        for control in (
            p.fstart_spin, p.fstop_spin, p.steps_spin, p.avg_spin, p.repeat_spin,
            p.mw_power_spin, p.save_check, p.num_dips_combo, p.dense2_steps_spin,
            p.trigger_delay_spin, p.pulse_tail_spin, p.plot_mode_combo,
            p.fit_type_combo, p.fit_button, p.initial_fit_parameters_button,
            p.fit_group, p.analysis_button, p.sequence_button,
        ):
            self.assertTrue(p.control_scroll_area.isAncestorOf(control), control)
        for control in (p.fstart_spin, p.steps_spin, p.mw_power_spin, p.save_check):
            self.assertTrue(p.scan_group.isAncestorOf(control), control)
        self.assertTrue(p.dense_section.body.isAncestorOf(p.dense1_center_spin))
        self.assertIs(p.dense_section.header_checkbox, p.dense_check)
        self.assertTrue(p.delays_section.body.isAncestorOf(p.pulse_tail_spin))
        self.assertTrue(p.fit_section.body.isAncestorOf(p.fit_button))

    def test_default_fold_states(self):
        p = self.panel
        self.assertTrue(p.fit_section.is_expanded())
        for section in (p.dense_section, p.magnet_field_group, p.delays_section,
                        p.stability_group, p.statistics_group):
            self.assertFalse(section.is_expanded(), section.title())
            self.assertTrue(section.summary(), section.title())
        self.assertEqual(p.dense_section.summary(), "Off")
        self.assertEqual(p.stability_group.summary(), "Not available (no live view)")
        self.assertEqual(p.delays_section.summary(),
                         "trigger 20 ms, fire 10 ms, reset 10 ms, settle 0 ms, "
                         "lead 2 ms, tail 2 ms")
        # This harness has no magnet.
        self.assertEqual(p.magnet_field_group.summary(),
                         "Field: no magnet available (not controlled)")

    def test_folding_never_changes_the_config(self):
        p = self.panel
        p.dense_check.setChecked(True)
        p.num_dips_combo.setCurrentText("2")
        p.trigger_delay_spin.setValue(0.0312)
        before = p.get_config()
        sections = (p.dense_section, p.magnet_field_group, p.delays_section,
                    p.stability_group, p.fit_section, p.statistics_group)
        for expanded in (True, False, True):
            for section in sections:
                section.set_expanded(expanded)
            self.assertEqual(p.get_config(), before)
        self.assertTrue(p.dense_check.isChecked())

    def test_dip2_rows_follow_the_number_of_dips_and_keep_values(self):
        p = self.panel
        form = p.dense_section.body_layout()
        self.assertFalse(form.isRowVisible(p.dense2_center_spin))
        p.dense2_center_spin.setValue(2.91)
        p.num_dips_combo.setCurrentText("2")
        self.assertTrue(form.isRowVisible(p.dense2_center_spin))
        p.num_dips_combo.setCurrentText("1")
        self.assertEqual(p.dense2_center_spin.value(), 2.91)

    def test_units_in_suffixes_and_precision_kept(self):
        p = self.panel
        expected = {
            "fstart_spin": (" GHz", 6), "fstop_spin": (" GHz", 6),
            "dense1_center_spin": (" GHz", 6), "dense2_center_spin": (" GHz", 6),
            "dense1_width_spin": (" MHz", 2), "dense2_width_spin": (" MHz", 2),
            "mw_power_spin": (" dBm", 1),
            "trigger_delay_spin": (" s", 4), "fire_delay_spin": (" s", 4),
            "reset_delay_spin": (" s", 4), "mw_settle_spin": (" s", 4),
            "pulse_lead_spin": (" s", 4), "pulse_tail_spin": (" s", 4),
        }
        for name, (suffix, decimals) in expected.items():
            spin = getattr(p, name)
            self.assertEqual((spin.suffix(), spin.decimals()), (suffix, decimals), name)
        for spin in (p.magnet_field_group.x_spin, p.magnet_field_group.magnitude_spin):
            self.assertEqual((spin.suffix(), spin.decimals()), (" G", 4))
        g = p.stability_group
        self.assertEqual([s.suffix() for s in (g.window_spin, g.max_std_spin,
                                               g.max_drift_spin, g.timeout_spin)],
                         [" s", " %", " %/min", " s"])
        from PyQt6.QtWidgets import QLabel
        for label in p.control_box.findChildren(QLabel):
            for unit in ("(GHz)", "(MHz)", "(dBm)", "(s)", "(G)", "(%)"):
                self.assertNotIn(unit, label.text())

    def test_every_control_has_a_tooltip(self):
        from PyQt6.QtWidgets import (QAbstractButton, QAbstractSpinBox,
                                     QComboBox, QProgressBar)
        p = self.panel
        controls = [w for kind in (QAbstractButton, QAbstractSpinBox, QComboBox,
                                   QProgressBar)
                    for w in p.left_panel.findChildren(kind)]
        self.assertGreater(len(controls), 40)
        missing = [w.text() if hasattr(w, "text") else type(w).__name__
                   for w in controls if not w.toolTip().strip()]
        self.assertEqual(missing, [])

    def test_get_config_defaults_unchanged(self):
        # The values get_config() returned before the layout change.
        self.assertEqual(self.panel.get_config(), {
            "f_start": 2.77e9, "f_stop": 2.97e9, "steps": 20, "averages": 1,
            "mw_power_dbm": -10.0, "trigger_delay_s": 0.02, "save_data": False,
            "repeats": 1, "use_dense_region": False, "num_dense_dips": 1,
            "dense1_center": 2.87e9, "dense1_width": 20e6, "dense1_steps": 30,
            "dense2_center": 2.90e9, "dense2_width": 20e6, "dense2_steps": 30,
            "fire_delay_s": 0.010, "reset_delay_s": 0.010, "mw_settle_s": 0.0,
            "pulse_lead_s": 0.002, "pulse_tail_s": 0.002,
            # Added with "Magnetic field during scan". This hardware has no
            # magnet, so the group is disabled and reports leave-as-is.
            "magnet_field_mode": "leave_as_is",
            "magnet_custom_vector_gauss": [0.0, 0.0, 0.0],
            # Added with the direction + magnitude entry.
            "magnet_vector_entry": "components",
            "magnet_field_direction": unit_direction([1.0, 1.0, 1.0]),
            "magnet_field_magnitude_gauss": 0.0,
        })

    def test_default_size_fits_1920x1080_with_taskbar(self):
        size = self.window.size()
        self.assertLessEqual(size.width(), 1920)
        self.assertLessEqual(size.height(), 1032 - 40)

    def test_expanded_controls_fit_the_column_width(self):
        p = self.panel
        width = p.control_scroll_area.minimumWidth()
        p.dense_check.setChecked(True)
        p.num_dips_combo.setCurrentText("2")
        for section in (p.dense_section, p.magnet_field_group, p.delays_section,
                        p.stability_group, p.statistics_group):
            section.set_expanded(True)
        p.magnet_field_group.set_vector_entry("direction")
        self.window.show()
        self.addCleanup(self.window.hide)
        _APP.processEvents()
        self.assertLessEqual(p.control_box.width(),
                             p.control_scroll_area.viewport().width())
        self.assertEqual(p.control_scroll_area.minimumWidth(), width)

    def test_button_stays_visible_at_a_short_window_height(self):
        p = self.panel
        self.window.show()
        self.addCleanup(self.window.hide)
        self.window.resize(self.window.width(), 400)
        _APP.processEvents()
        bar = p.control_scroll_area.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum())
        _APP.processEvents()
        self.assertTrue(p.run_stop_button.isVisible())
        self.assertFalse(p.run_stop_button.visibleRegion().isEmpty())


class RunButtonTests(ODMRWindowLayoutTestCase):
    def test_start_stop_start_on_completion(self):
        self._assert_button("Start", "green")
        self._fast_scan(steps=3)
        self.panel.run_stop_button.click()
        self._assert_button("Stop", "red")
        self._wait_idle()
        self._assert_button("Start", "green")
        self.critical.assert_not_called()

    def test_start_stop_start_on_stop(self):
        self._fast_scan(steps=500)
        stop_calls = []
        original_stop = self.window.stop_odmr
        with patch.object(self.window, "stop_odmr",
                          side_effect=lambda: (stop_calls.append(1), original_stop())):
            self.panel.run_stop_button.click()
            self._assert_button("Stop", "red")
            self.assertTrue(_pump_until(lambda: self.window.latest_x is not None))
            self.panel.run_stop_button.click()  # Stop, through the existing path
            self._wait_idle()
        self.assertEqual(stop_calls, [1])
        self.assertLess(len(self.window.latest_x), 500)  # really stopped early
        self._assert_button("Start", "green")

    def test_start_stop_start_on_error(self):
        self._fast_scan(steps=3)
        with patch.object(ODMRExperiment, "acquire_pixel_statistics",
                          side_effect=RuntimeError("simulated failure")):
            self.panel.run_stop_button.click()
            self._assert_button("Stop", "red")
            self._wait_idle()
        self._assert_button("Start", "green")
        self.critical.assert_called_once()


if __name__ == "__main__":
    unittest.main()
