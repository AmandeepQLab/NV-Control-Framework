"""Main window layout after the GUI redesign (real MainWindow, --sim)."""

import sys
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QApplication, QComboBox, QLabel,
    QMessageBox, QPushButton,
)

_APP = QApplication.instance() or QApplication(sys.argv[:1])


class MainWindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gui.main_window import MainWindow
        cls.main = MainWindow(sim=True)

    @classmethod
    def tearDownClass(cls):
        main = cls.main
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            main.close()
        main.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _left_column(self):
        main = self.main
        return [main.camera_group, main.roi_section, main.experiment_group,
                main.hardware_group]

    def test_placeholders_and_empty_boxes_removed(self):
        texts = {b.text() for b in self.main.findChildren(QPushButton)}
        for removed in ("Rabi", "Ramsy", "T1", "T2", "Microwave", "Power Supplies"):
            self.assertNotIn(removed, texts)
        labels = {label.text() for label in self.main.findChildren(QLabel)}
        self.assertNotIn("Future tools", labels)
        for name in ("open_rabi_button", "open_ramsy_button", "open_t1_button",
                     "open_t2_button", "open_microwave_button",
                     "open_power_supply_button"):
            self.assertFalse(hasattr(self.main, name), name)

    def test_cards_sections_and_status_bar(self):
        main = self.main
        self.assertEqual([w.title() for w in self._left_column()],
                         ["Camera", "Acquisition ROI", "Experiments", "Hardware"])
        self.assertFalse(main.roi_section.is_expanded())
        self.assertTrue(main.roi_section.summary().startswith("Full sensor"))
        self.assertIs(main.state_label.parentWidget(), main.statusBar())
        self.assertEqual(main.state_label.text(), "State: IDLE")
        for button in (main.open_odmr_button, main.open_zero_field_button):
            self.assertTrue(main.experiment_group.isAncestorOf(button))
        self.assertTrue(main.hardware_group.isAncestorOf(main.open_magnet_button))

    def test_roi_summary_follows_the_roi(self):
        main = self.main
        main.roi_coordinate_spins["x"].setValue(4)
        main.roi_coordinate_spins["y"].setValue(6)
        main.roi_coordinate_spins["width"].setValue(20)
        main.roi_coordinate_spins["height"].setValue(10)
        main.apply_roi_coordinates()
        try:
            self.assertIn("x=4, y=6, w=20, h=10", main.roi_section.summary())
        finally:
            main.reset_acquisition_roi()
        self.assertTrue(main.roi_section.summary().startswith("Full sensor"))

    def test_units_in_suffixes_and_precision_kept(self):
        main = self.main
        self.assertEqual((main.exposure_spin.suffix(), main.exposure_spin.decimals()),
                         (" ms", 3))
        self.assertEqual((main.baseline_spin.suffix(), main.baseline_spin.decimals()),
                         (" counts", 1))
        for spin in main.roi_coordinate_spins.values():
            self.assertEqual(spin.suffix(), " px")

    def test_every_left_column_control_has_a_tooltip(self):
        controls = [c for box in self._left_column()
                    for kind in (QAbstractButton, QAbstractSpinBox, QComboBox)
                    for c in box.findChildren(kind)]
        controls += [self.main.roi_section.toggle_button, self.main.colormap_combo]
        self.assertGreater(len(controls), 10)
        missing = [getattr(c, "text", lambda: type(c).__name__)()
                   for c in controls if not c.toolTip().strip()]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
