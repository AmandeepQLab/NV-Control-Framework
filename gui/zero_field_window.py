"""Window that integrates the zero-field scan widget with laboratory hardware."""

from pathlib import Path
import logging
from datetime import datetime

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

        self.widget = ZeroFieldWidget()
        self.setCentralWidget(self.widget)
        self.widget.run_stop_button.clicked.connect(self.toggle_scan)
        self.widget.save_button.clicked.connect(self.save_data)
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

    def handle_live_update(self, field, signal, _image):
        log_event("gui_image_update", source="Zero Field experiment", image_target="Zero Field image")
        self.scan_fields.append(field)
        self.scan_signals.append(signal)

    def handle_progress(self, current, total):
        self.widget.update_live_data(
            self.scan_fields, self.scan_signals, current, total
        )

    def handle_scan_started(self, current, total):
        self.scan_fields = []
        self.scan_signals = []
        self.widget.update_scan_progress(current, total)

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
