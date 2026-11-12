"""Hardware-free tests for the ODMR statistics GUI."""

import sys
import unittest
from types import SimpleNamespace

import numpy as np
from PyQt6.QtWidgets import QApplication, QTextEdit

from gui.odmr_window import ODMRWindow
from framework.image_cube import ImageCube
from gui.panels.odmr_panel import ODMRPanel, ODMRStatisticsExplanationDialog


_APP = QApplication.instance() or QApplication(sys.argv)
_PANEL = ODMRPanel()


class ODMRStatisticsPanelTests(unittest.TestCase):
    def setUp(self):
        self.panel = _PANEL

    def test_panel_has_neutral_state_and_explanation_dialog(self):
        self.panel.set_statistics_status("Statistics: No completed acquisition")
        self.assertEqual(self.panel.statistics_group.title(), "ODMR Statistics")
        self.assertIn("No completed acquisition", self.panel.statistics_summary_label.text())
        self.assertEqual(
            self.panel.statistics_details_button.text(), "How are these calculated?"
        )
        self.panel.statistics_details_button.click()
        self.assertIsInstance(self.panel.statistics_dialog, ODMRStatisticsExplanationDialog)
        self.assertTrue(self.panel.statistics_dialog.isVisible())
        self.assertIn(
            "method-of-moments",
            self.panel.statistics_dialog.findChild(QTextEdit).toPlainText(),
        )
        self.panel.statistics_dialog.close()

    def test_completed_statistics_are_displayed_from_processor_result(self):
        image = np.array([[[1.0, 2.0]], [[3.0, np.nan]]])
        result = SimpleNamespace(
            n_averages=2,
            n_repeats_per_average=(20, 20),
            final_mean=image,
            fast_sd=np.ones_like(image),
            fast_sem=np.ones_like(image),
            slow_sd=np.ones_like(image),
            slow_sem=np.ones_like(image),
            combined_sem=np.ones_like(image),
            slow_noise_estimable=True,
            slow_noise_estimable_per_pixel=np.array([[[True, True]], [[True, False]]]),
        )
        self.panel.update_statistics(result)
        text = self.panel.statistics_summary_label.text()
        self.assertIn("Completed averages: 2", text)
        self.assertIn("Repeats / average: 20", text)
        self.assertIn("Final mean: Available", text)
        self.assertIn("Fast noise: Available", text)
        self.assertIn("Slow noise: Estimable", text)
        self.assertIn("Combined uncertainty: Available", text)
        self.assertIn("Valid data: 75.0% (3/4)", text)

    def test_slow_noise_unavailable_state_is_explicit(self):
        values = np.ones((1, 1, 2))
        result = SimpleNamespace(
            n_averages=1,
            n_repeats_per_average=(5,),
            final_mean=values,
            fast_sd=values,
            fast_sem=values,
            slow_sd=values,
            slow_sem=values,
            combined_sem=values,
            slow_noise_estimable=False,
            slow_noise_estimable_per_pixel=np.zeros_like(values, dtype=bool),
        )
        self.panel.update_statistics(result)
        self.assertIn(
            "Slow noise: Not estimable (<2 usable averages)",
            self.panel.statistics_summary_label.text(),
        )

    def test_statistics_update_does_not_change_scalar_plot_or_fit_controls(self):
        curve = self.panel.curve
        fit_button = self.panel.fit_button
        values = np.ones((1, 1, 1))
        result = SimpleNamespace(
            n_averages=1,
            n_repeats_per_average=(1,),
            final_mean=values,
            fast_sd=values,
            fast_sem=values,
            slow_sd=values,
            slow_sem=values,
            combined_sem=values,
            slow_noise_estimable=False,
            slow_noise_estimable_per_pixel=np.zeros_like(values, dtype=bool),
        )
        self.panel.update_statistics(result)
        self.assertIs(self.panel.curve, curve)
        self.assertIs(self.panel.fit_button, fit_button)

    def test_completed_window_acquisition_processes_in_memory_cube(self):
        frequencies = np.array([2.87e9])
        cube = ImageCube(
            data=np.array([[[[[1.0]]], [[[0.1]]]]]),
            axes={
                "axis_order": ["average", "statistic", "frequency", "y", "x"],
                "average": [0],
                "statistic": ["normalized_mean", "normalized_sd"],
                "frequency": frequencies.tolist(),
                "y": [0],
                "x": [0],
            },
            metadata={
                "completed_averages": 1,
                "configured_averages": 1,
                "n_repeats_per_average": [5],
            },
            experiment_type="ODMR",
            scan_axis_name="frequency",
            scan_axis_unit="Hz",
            scan_axis_values=frequencies,
        )
        captured = {}
        panel = SimpleNamespace(
            set_running_state=lambda *_: None,
            set_statistics_status=lambda value: captured.update(status=value),
            update_statistics=lambda value: captured.update(result=value),
            fit_button=SimpleNamespace(setEnabled=lambda *_: None),
            update_time_label=lambda **_: None,
        )
        fake_window = SimpleNamespace(
            odmr_running=True,
            panel=panel,
            odmr_worker=SimpleNamespace(statistics_cube=cube),
            refresh_plot=lambda: None,
            scan_start_time=None,
            current_config=None,
            run_finished=SimpleNamespace(emit=lambda: None),
        )
        ODMRWindow.odmr_finished(fake_window)
        self.assertEqual(captured["result"].n_averages, 1)
        self.assertFalse(captured["result"].slow_noise_estimable)


if __name__ == "__main__":
    unittest.main()
