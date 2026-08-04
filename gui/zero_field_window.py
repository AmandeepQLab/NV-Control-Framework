"""Window that integrates the zero-field scan widget with laboratory hardware."""

from pathlib import Path
import logging
from datetime import datetime

import numpy as np

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

from gui.widgets.zero_field_widget import ZeroFieldWidget
from gui.zero_field_worker import ZeroFieldWorker
from framework.analysis.zero_field import mean_fluorescence_vs_field
from utils.camera_diagnostics import log_event


LOGGER = logging.getLogger(__name__)


class ZeroFieldWindow(QMainWindow):
    """Configure, run, monitor, and save a ZeroFieldExperiment."""

    def __init__(
        self,
        hardware_manager,
        hardware,
        camera,
        acquisition_state_getter,
    ):
        super().__init__()
        self.setWindowTitle("Zero-Field Imaging")
        self.resize(1000, 650)

        self.hardware_manager = hardware_manager
        self.hardware = hardware
        self.camera = camera
        self.magnet = hardware["magnet"]
        self.acquisition_state_getter = acquisition_state_getter

        self.worker = None
        self.thread = None
        self.zero_field_running = False
        self.image_cube = None
        self.scan_fields = []
        self.scan_signals = []
        self.field_point_count = 0
        self.total_scans = 1
        self.output_directory = Path(__file__).resolve().parents[1] / "data"
        self.output_path = None
        self._reference_image = None
        self._latest_averaged_image = None
        self._current_display_image = None

        self.widget = ZeroFieldWidget()
        self.setCentralWidget(self.widget)
        self.widget.run_stop_button.clicked.connect(self.toggle_scan)
        self.widget.save_button.clicked.connect(self.save_data)
        self.widget.display_mode_combo.currentTextChanged.connect(
            self.handle_display_mode_changed
        )
        self.widget.display_scaling_combo.currentTextChanged.connect(
            self.handle_display_settings_changed
        )
        self.widget.display_min_spin.valueChanged.connect(
            self.handle_display_settings_changed
        )
        self.widget.display_max_spin.valueChanged.connect(
            self.handle_display_settings_changed
        )
        self.widget.colormap_combo.currentTextChanged.connect(
            self.handle_display_settings_changed
        )
        # Before acquisition this existing control selects an optional output
        # directory.  The automatic timestamped filename remains unchanged.
        self.widget.save_button.setEnabled(True)

    def toggle_scan(self):
        if self.zero_field_running:
            self.stop_scan()
        else:
            self.start_scan()

    def start_scan(self):
        config = self.widget.get_config()
        acquisition_state = self.acquisition_state_getter()
        acquisition_roi = acquisition_state.acquisition_roi
        LOGGER.info("Using acquisition ROI from Main Window: %s", acquisition_roi)

        self.image_cube = None
        self.scan_fields = []
        self.scan_signals = []
        self._reference_image = None
        self._latest_averaged_image = None
        self._current_display_image = None
        self.field_point_count = config["field_points"]
        self.total_scans = (
            config["num_scans"] if config["averaging_enabled"] else 1
        )
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.output_path = self.output_directory / f"zero_field_{timestamp}_average.npz"
        self.widget.save_button.setEnabled(False)
        self.widget.reset_scan(config["field_points"], self.total_scans)
        self.widget.set_running_state(True)
        self.zero_field_running = True

        metadata = {
            "scan_parameters": dict(config),
            "camera_parameters": {
                "exposure_s": acquisition_state.exposure_s,
                "binning": acquisition_state.binning,
                "acquisition_roi": acquisition_roi,
            },
        }

        self.thread = QThread(self)
        self.worker = ZeroFieldWorker(
            self.hardware_manager,
            self.camera,
            self.magnet,
            config,
            acquisition_roi,
            metadata,
            self.output_path,
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.live_update_signal.connect(self.handle_live_update)
        self.worker.fluorescence_update_signal.connect(
            self.handle_fluorescence_update
        )
        self.worker.progress_signal.connect(self.handle_progress)
        self.worker.scan_started_signal.connect(self.handle_scan_started)
        self.worker.scan_completed_signal.connect(self.handle_scan_completed)
        self.worker.finished_signal.connect(self.scan_finished)
        self.worker.finished_signal.connect(self.thread.quit)
        self.worker.error_signal.connect(self.show_error)
        self.worker.error_signal.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def stop_scan(self):
        if self.worker is not None:
            self.widget.status_label.setText("Status: Stopping")
            self.worker.stop()

    def handle_live_update(self, field, signal, image):
        log_event("gui_image_update", source="Zero Field experiment", image_target="Zero Field image")
        self._latest_averaged_image = np.array(image, copy=True)
        self._reference_image = self._capture_reference_image(
            self._reference_image, self._latest_averaged_image
        )
        self._update_displayed_image()

    def handle_display_mode_changed(self, _mode):
        """Redraw the latest acquired image in the newly selected mode."""
        self._update_displayed_image()

    def handle_display_settings_changed(self, _value):
        """Re-render the retained display image after a visualization change."""
        self._render_displayed_image()

    def handle_fluorescence_update(self, fields, signals):
        """Display the cumulative raw fluorescence curve for this scan."""
        self.scan_fields = fields.tolist()
        self.scan_signals = signals.tolist()
        self.widget.update_fluorescence_curve(self.scan_fields, self.scan_signals)

    def handle_progress(self, current, total):
        self.widget.update_progress(current, total)

    def handle_scan_started(self, current, total):
        self.scan_fields = []
        self.scan_signals = []
        self.widget.update_scan_progress(current, total)

    def _update_displayed_image(self):
        if self._latest_averaged_image is None:
            return
        self._current_display_image = self._display_image_for_mode(
            self.widget.display_mode_combo.currentText(),
            self._latest_averaged_image,
            self._reference_image,
        )
        self._render_displayed_image()

    def _render_displayed_image(self):
        """Render the existing display image with the selected visualization."""
        if self._current_display_image is None:
            return
        try:
            parameters = self._prepare_display_parameters(
                self._current_display_image,
                self.widget.display_scaling_combo.currentText(),
                self.widget.display_min_spin.value(),
                self.widget.display_max_spin.value(),
                self.widget.colormap_combo.currentText(),
            )
        except ValueError as error:
            self.widget.status_label.setText(f"Display settings: {error}")
            return
        self.widget.update_image(self._current_display_image, **parameters)

    @staticmethod
    def _prepare_display_parameters(
        image, scaling_mode, manual_minimum, manual_maximum, colormap_name
    ):
        """Select rendering limits and colormap without altering image data."""
        if scaling_mode == "Manual":
            if manual_minimum >= manual_maximum:
                raise ValueError("minimum intensity must be less than maximum intensity")
            levels = (float(manual_minimum), float(manual_maximum))
        else:
            finite_values = np.asarray(image)[np.isfinite(image)]
            if finite_values.size == 0:
                levels = (0.0, 1.0)
            else:
                minimum = float(np.min(finite_values))
                maximum = float(np.max(finite_values))
                if minimum == maximum:
                    padding = max(abs(minimum) * 0.01, 1.0)
                    levels = (minimum - padding, maximum + padding)
                else:
                    levels = (minimum, maximum)
        return {"levels": levels, "colormap_name": colormap_name}

    @staticmethod
    def _capture_reference_image(reference_image, averaged_image):
        """Keep the first averaged image as the one immutable sweep reference."""
        if reference_image is None:
            return np.array(averaged_image, copy=True)
        return reference_image

    @staticmethod
    def _display_image_for_mode(mode, averaged_image, reference_image):
        """Build a display-only representation from already acquired images."""
        if mode == "Difference Image":
            return (
                np.asarray(averaged_image, dtype=np.float64)
                - np.asarray(reference_image, dtype=np.float64)
            )
        return averaged_image

    def handle_scan_completed(self, image_cube, current, total):
        fields, signals = mean_fluorescence_vs_field(image_cube)
        self.scan_fields = fields.tolist()
        self.scan_signals = signals.tolist()
        self.widget.update_live_data(
            self.scan_fields,
            self.scan_signals,
            self.field_point_count,
            self.field_point_count,
        )
        self.widget.update_scan_progress(current, total)

    def scan_finished(self, image_cube, _fields, _signals, stopped):
        self.image_cube = image_cube
        self.zero_field_running = False
        self.widget.set_running_state(False)
        if image_cube is None:
            self.widget.status_label.setText("Status: Error")
            self.widget.save_button.setEnabled(False)
            return

        if image_cube.data is not None:
            fields, signals = mean_fluorescence_vs_field(image_cube)
            self.scan_fields = fields.tolist()
            self.scan_signals = signals.tolist()
            self.widget.update_live_data(
                self.scan_fields,
                self.scan_signals,
                len(self.scan_fields),
                self.field_point_count,
            )

        self.widget.status_label.setText(
            "Status: Stopped" if stopped else "Status: Complete"
        )
        self.widget.save_button.setEnabled(False)
        if self.output_path is not None:
            self.widget.status_label.setText(
                f"Status: {'Stopped' if stopped else 'Complete'} — saved {self.output_path.name}"
            )

    def show_error(self, message):
        self.zero_field_running = False
        self.widget.set_running_state(False)
        self.widget.status_label.setText("Status: Error")
        QMessageBox.critical(self, "Zero Field Error", message)

    def save_data(self):
        if self.image_cube is not None or self.zero_field_running:
            return

        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose Zero Field Data Directory",
            str(self.output_directory),
        )
        if directory:
            self.output_directory = Path(directory)
            self.widget.status_label.setText(
                f"Status: Output directory set to {self.output_directory}"
            )

    def closeEvent(self, event):
        if self.zero_field_running:
            reply = QMessageBox.question(
                self,
                "Close Zero Field",
                "A scan is running. Stop it and close this window?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stop_scan()
        event.accept()
