import sys
import pyqtgraph as pg # type: ignore

from PyQt6.QtWidgets import ( # type: ignore
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel,
    QDoubleSpinBox, QSpinBox, QGridLayout,
    QGroupBox, QMessageBox
)
from PyQt6.QtCore import QSignalBlocker, QTimer # type: ignore
from gui.magnet_window import MagnetControlWindow
from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager
from gui.odmr_window import ODMRWindow
from gui.zero_field_window import ZeroFieldWindow
from framework.camera_ownership import (
    apply_live_acquisition_state,
    register_camera_state_restorer,
    register_live_view_controller,
    start_live_stream_if_available,
)
from framework.acquisition_state import AcquisitionState
from framework.roi import validate_roi
from gui.live_view_controller import LiveViewTimerController
from utils.camera_diagnostics import log_event


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("NV Control")
        self.resize(1600, 800)

        # =====================================================
        # LOAD CONFIG
        # =====================================================

        self.cfg = ConfigManager("config/setupInfo.json")
        self.cfg.load()

        # =====================================================
        # INITIALIZE HARDWARE
        # =====================================================

        self.hw_manager = HardwareManager(self.cfg)
        self.hw_manager.initialize()

        self.hardware = self.hw_manager.get_hardware()
        self.camera = self.hardware["camera"]

        # =====================================================
        # STATE VARIABLES
        # =====================================================

        self.state = "IDLE"
        self.odmr_window = None
        # Full-sensor coordinates for camera acquisition.  Experiments receive
        # this value but never define a separate acquisition ROI of their own.
        self.acquisition_state = AcquisitionState()
        self._sensor_dimensions = self._read_sensor_dimensions()
        self._syncing_roi_controls = False

        # =====================================================
        # MAIN LAYOUT
        # =====================================================

        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout()
        central.setLayout(main_layout)

        # =====================================================
        # LEFT CONTROL PANEL
        # =====================================================

        left_panel = QVBoxLayout()
        main_layout.addLayout(left_panel, 2)

        # =====================================================
        # CAMERA
        # =====================================================

        camera_group = QGroupBox("Camera")

        camera_layout = QVBoxLayout()

        camera_group.setLayout(camera_layout)

        left_panel.addWidget(camera_group)

        # =====================================================
        # CAMERA CONTROLS
        # =====================================================

        # -----------------------------
        # Stream buttons
        # -----------------------------

        self.start_button = QPushButton("Start Stream")
        self.start_button.clicked.connect(self.start_stream)
        camera_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop Stream")
        self.stop_button.clicked.connect(self.stop_stream)
        camera_layout.addWidget(self.stop_button)

        # -----------------------------
        # Exposure
        # -----------------------------

        exposure_layout = QHBoxLayout()
        exposure_layout.addWidget(QLabel("Exposure (s):"))

        self.exposure_spin = QDoubleSpinBox()
        self.exposure_spin.setRange(0.001, 10)
        self.exposure_spin.setDecimals(4)
        self.exposure_spin.setValue(0.02)
        self.exposure_spin.valueChanged.connect(self.set_exposure)

        exposure_layout.addWidget(self.exposure_spin)
        camera_layout.addLayout(exposure_layout)

        # -----------------------------
        # Binning
        # -----------------------------

        binning_layout = QHBoxLayout()
        binning_layout.addWidget(QLabel("Binning:"))

        self.binning_spin = QSpinBox()
        self.binning_spin.setRange(1, 8)
        self.binning_spin.setValue(1)
        self.binning_spin.valueChanged.connect(self.set_binning)

        binning_layout.addWidget(self.binning_spin)
        camera_layout.addLayout(binning_layout)

        # -----------------------------
        # ROI
        # -----------------------------

        roi_box = QGroupBox("ROI")
        camera_layout.addWidget(roi_box)

        roi_layout = QVBoxLayout()
        roi_box.setLayout(roi_layout)

        self.roi_label = QLabel("Acquisition ROI: None")
        roi_layout.addWidget(self.roi_label)

        coordinates_box = QGroupBox("ROI Coordinates")
        coordinates_layout = QGridLayout(coordinates_box)
        self.roi_coordinate_spins = {}
        for row, coordinate in enumerate(("X", "Y", "Width", "Height")):
            coordinates_layout.addWidget(QLabel(f"{coordinate}:"), row, 0)
            spin = QSpinBox()
            spin.setRange(
                -1_000_000 if coordinate in ("X", "Y") else 0,
                1_000_000,
            )
            spin.setKeyboardTracking(False)
            spin.editingFinished.connect(self.apply_roi_coordinates)
            coordinates_layout.addWidget(spin, row, 1)
            self.roi_coordinate_spins[coordinate.lower()] = spin
        roi_layout.addWidget(coordinates_box)

        self.reset_roi_button = QPushButton("Reset ROI")
        self.reset_roi_button.clicked.connect(self.reset_acquisition_roi)
        roi_layout.addWidget(self.reset_roi_button)

        # =====================================================
        # EXPERIMENTS
        # =====================================================

        experiment_group = QGroupBox("Experiments")
        experiment_layout = QVBoxLayout()
        experiment_group.setLayout(experiment_layout)
        left_panel.addWidget(experiment_group)
        experiment_layout.setSpacing(8)
        experiment_layout.setContentsMargins(10, 10, 10, 10)

        # =====================================================
        # HARDWARE
        # =====================================================

        hardware_group = QGroupBox("Hardware")

        hardware_layout = QVBoxLayout()

        hardware_group.setLayout(hardware_layout)

        left_panel.addWidget(hardware_group)

        # -----------------------------------------------------
        # Magnet Control
        # -----------------------------------------------------

        self.open_magnet_button = QPushButton("Magnet")

        self.open_magnet_button.setMinimumHeight(36)

        self.open_magnet_button.clicked.connect(
            self.open_magnet_window
        )

        hardware_layout.addWidget(
            self.open_magnet_button
        )

        # -----------------------------------------------------
        # Future Hardware
        # -----------------------------------------------------

        self.open_microwave_button = QPushButton("Microwave")

        self.open_microwave_button.setEnabled(False)

        hardware_layout.addWidget(
            self.open_microwave_button
        )

        self.open_power_supply_button = QPushButton("Power Supplies")

        self.open_power_supply_button.setEnabled(False)

        hardware_layout.addWidget(
            self.open_power_supply_button
        )

        # =====================================================
        # OPEN ODMR WINDOW
        # =====================================================
        self.open_odmr_button = QPushButton("ODMR")
        self.open_odmr_button.clicked.connect(self.open_odmr_window)
        experiment_layout.addWidget(self.open_odmr_button)
        self.open_odmr_button.setMinimumHeight(36)
        
        # =====================================================
        # Zero-Field IMAGING
        # =====================================================
        
        self.open_zero_field_button = QPushButton("Zero-Field Imaging")
        self.open_zero_field_button.clicked.connect(self.open_zero_field_window)
        experiment_layout.addWidget(self.open_zero_field_button)
        self.open_zero_field_button.setMinimumHeight(36)

        # =====================================================
        # upcoming Experiments (disabled for now)
        # =====================================================
        
        #Rabi Button
        self.open_rabi_button = QPushButton("Rabi")
        self.open_rabi_button.setEnabled(False)
        experiment_layout.addWidget(self.open_rabi_button)

        #Ramsy Button
        self.open_ramsy_button = QPushButton("Ramsy")
        self.open_ramsy_button.setEnabled(False)
        experiment_layout.addWidget(self.open_ramsy_button)

        #T1 Button
        self.open_t1_button = QPushButton("T1")
        self.open_t1_button.setEnabled(False)
        experiment_layout.addWidget(self.open_t1_button)

        #T2 Button
        self.open_t2_button = QPushButton("T2")
        self.open_t2_button.setEnabled(False)
        experiment_layout.addWidget(self.open_t2_button)

        # =====================================================
        # STATUS
        # =====================================================
        status_box = QGroupBox("Status")
        left_panel.addWidget(status_box)
        status_layout = QVBoxLayout()
        status_box.setLayout(status_layout)

        self.state_label = QLabel("State: IDLE")
        status_layout.addWidget(self.state_label)

        left_panel.addStretch()
        # =====================================================
        # UTILITIES
        # =====================================================

        utility_group = QGroupBox("Utilities")
        utility_layout = QVBoxLayout()
        utility_group.setLayout(utility_layout)
        left_panel.addWidget(utility_group)
        utility_layout.addWidget(QLabel("Future tools"))

        # =====================================================
        # RIGHT IMAGE PANEL
        # =====================================================

        right_panel = QVBoxLayout()
        main_layout.addLayout(right_panel, 4)

        self.image_view = pg.ImageView()
        right_panel.addWidget(self.image_view, 3)

        # ROI overlay
        self.roi = pg.RectROI(
            [50, 50],
            [80, 80],
            pen="r"
        )

        self.image_view.addItem(self.roi)
        self.roi.sigRegionChanged.connect(self.update_roi_info)
        self._sync_roi_controls()

        # =====================================================
        # LIVE VIEW TIMER
        # =====================================================

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_image)
        self.live_view_timer = LiveViewTimerController(self.timer, 30)
        register_live_view_controller(self.camera, self.live_view_timer)
        register_camera_state_restorer(
            self.camera, self.apply_acquisition_state_to_camera
        )

    # =====================================================
    # OPEN MAGNET WINDOW
    # =====================================================

    def open_magnet_window(self):

        if hasattr(self, "magnet_window"):

            self.magnet_window.raise_()

            self.magnet_window.activateWindow()

            return

        self.magnet_window = MagnetControlWindow(
            self.hardware
        )

        self.magnet_window.show()

        self.magnet_window.destroyed.connect(
            lambda: setattr(
                self,
                "magnet_window",
                None
            )
        )

    # =========================================================
    # STATE
    # =========================================================

    def set_state(self, state):

        self.state = state
        self.state_label.setText(f"State: {state}")

    # =========================================================
    # STREAMING
    # =========================================================

    def start_stream(self):

        if self.state == "ODMR_RUNNING":
            return

        if not self.apply_acquisition_state():
            return

        if not start_live_stream_if_available(self.camera):
            return

        self.timer.start(30)

        self.set_state("LIVE_VIEW")

    def stop_stream(self):

        try:
            self.timer.stop()
        except Exception:
            pass

        try:
            self.camera.stop_stream()
        except Exception:
            pass

        self.set_state("IDLE")

    # =========================================================
    # IMAGE UPDATE
    # =========================================================

    def update_image(self):

        frame = self.camera.get_latest_frame()

        if frame is not None:
            self._update_sensor_dimensions_from_full_frame(frame)
            log_event("gui_image_update", source="live stream", image_target="main live-view image")
            self.image_view.setImage(
                frame.T,
                autoLevels=False
            )

    # =========================================================
    # CAMERA SETTINGS
    # =========================================================

    def set_exposure(self, value):
        self.acquisition_state = AcquisitionState(
            acquisition_roi=self.acquisition_state.acquisition_roi,
            exposure_s=value,
            binning=self.acquisition_state.binning,
        )
        self.apply_acquisition_state()

    def set_binning(self, value):
        self.acquisition_state = AcquisitionState(
            acquisition_roi=self.acquisition_state.acquisition_roi,
            exposure_s=self.acquisition_state.exposure_s,
            binning=value,
        )
        self.apply_acquisition_state()

    # =========================================================
    # ROI
    # =========================================================

    def update_roi_info(self):
        """Apply a graphical ROI edit through the Main Window acquisition state."""
        if self._syncing_roi_controls:
            return
        self._set_acquisition_roi(
            self._roi_overlay_coordinates(), show_validation_error=False
        )

    def _roi_overlay_coordinates(self):

        pos = self.roi.pos()
        size = self.roi.size()

        return (
            int(pos.x()),
            int(pos.y()),
            int(pos.x() + size.x()),
            int(pos.y() + size.y())
        )

    def apply_roi_coordinates(self):
        """Convert the coordinate editor's origin and size into an AOI."""
        x = self.roi_coordinate_spins["x"].value()
        y = self.roi_coordinate_spins["y"].value()
        width = self.roi_coordinate_spins["width"].value()
        height = self.roi_coordinate_spins["height"].value()
        if width <= 0 or height <= 0:
            self._sync_roi_controls()
            QMessageBox.warning(
                self,
                "Invalid ROI Coordinates",
                "ROI Width and Height must both be greater than zero.",
            )
            return
        self._set_acquisition_roi((x, y, x + width, y + height))

    def _set_acquisition_roi(self, roi, show_validation_error=True):
        """Apply one validated ROI without creating a second ROI state."""
        try:
            roi = self._validate_acquisition_roi(roi)
        except ValueError as error:
            self._sync_roi_controls()
            if show_validation_error:
                QMessageBox.warning(self, "Invalid ROI Coordinates", str(error))
            return False

        candidate_state = AcquisitionState(
            acquisition_roi=roi,
            exposure_s=self.acquisition_state.exposure_s,
            binning=self.acquisition_state.binning,
        )
        try:
            applied = apply_live_acquisition_state(
                self.camera, lambda: candidate_state.apply_to(self.camera)
            )
        except Exception as error:
            # Restore the current settings if the camera rejects an AOI rule.
            try:
                apply_live_acquisition_state(
                    self.camera,
                    lambda: self.acquisition_state.apply_to(self.camera),
                )
            except Exception:
                pass
            self._sync_roi_controls()
            if show_validation_error:
                QMessageBox.warning(
                    self, "Invalid ROI Coordinates", f"Camera rejected ROI: {error}"
                )
            return False

        if not applied:
            self._sync_roi_controls()
            if show_validation_error:
                QMessageBox.warning(
                    self,
                    "ROI Not Applied",
                    "The camera is currently owned by an experiment."
                )
            return False

        self.acquisition_state = candidate_state
        self._sync_roi_controls()
        return True

    def _validate_acquisition_roi(self, roi):
        """Validate AOI coordinates against the sensor without clipping them."""
        normalized_roi = validate_roi(roi)
        if normalized_roi is None:
            return None

        x0, y0, x1, y1 = normalized_roi
        sensor_dimensions = self._sensor_dimensions or self._read_sensor_dimensions()
        if sensor_dimensions is None:
            raise ValueError("Unable to determine sensor dimensions for ROI validation.")

        sensor_width, sensor_height = sensor_dimensions
        if x0 < 0 or y0 < 0:
            raise ValueError("ROI X and Y must be greater than or equal to zero.")
        if x1 > sensor_width or y1 > sensor_height:
            raise ValueError(
                "ROI must lie completely inside the sensor "
                f"({sensor_width} x {sensor_height} pixels)."
            )
        return normalized_roi

    def _read_sensor_dimensions(self):
        """Read dimensions only from already exposed camera properties."""
        image_shape = getattr(self.camera, "image_shape", None)
        if image_shape is not None and len(image_shape) >= 2:
            return int(image_shape[1]), int(image_shape[0])

        sdk_camera = getattr(self.camera, "cam", None)
        if sdk_camera is not None:
            try:
                return (
                    int(sdk_camera.getInt("SensorWidth")),
                    int(sdk_camera.getInt("SensorHeight")),
                )
            except Exception:
                pass
        return None

    def _update_sensor_dimensions_from_full_frame(self, frame):
        """Use a full-sensor live frame only when hardware dimensions are unavailable."""
        if self._sensor_dimensions is not None or self.acquisition_state.acquisition_roi is not None:
            return
        if getattr(frame, "ndim", 0) >= 2:
            self._sensor_dimensions = (int(frame.shape[1]), int(frame.shape[0]))
            self._sync_roi_controls()

    def _sync_roi_controls(self):
        """Reflect the authoritative acquisition ROI in both editing interfaces."""
        roi = self.acquisition_state.acquisition_roi
        if roi is None:
            sensor_dimensions = self._sensor_dimensions or self._read_sensor_dimensions()
            if sensor_dimensions is None:
                self.roi_label.setText("Acquisition ROI: Full sensor")
                return
            width, height = sensor_dimensions
            coordinates = (0, 0, width, height)
            overlay_roi = (0, 0, width, height)
            self.roi_label.setText(
                f"Acquisition ROI: x=0, y=0, w={width}, h={height}"
            )
        else:
            x0, y0, x1, y1 = roi
            coordinates = (x0, y0, x1 - x0, y1 - y0)
            overlay_roi = roi
            self.roi_label.setText(
                f"Acquisition ROI: x={x0}, y={y0}, "
                f"w={x1 - x0}, h={y1 - y0}"
            )

        for spin, value in zip(self.roi_coordinate_spins.values(), coordinates):
            blocker = QSignalBlocker(spin)
            spin.setValue(value)
            del blocker

        x0, y0, x1, y1 = overlay_roi
        self._syncing_roi_controls = True
        try:
            self.roi.setPos((x0, y0), finish=False)
            self.roi.setSize((x1 - x0, y1 - y0), finish=False)
        finally:
            self._syncing_roi_controls = False

    def get_acquisition_roi(self):
        """Return the Main Window acquisition ROI in full-sensor coordinates."""
        return self.acquisition_state.acquisition_roi

    def get_acquisition_state(self):
        """Return the immutable Main-Window acquisition settings snapshot."""
        return self.acquisition_state

    def apply_acquisition_state_to_camera(self):
        """Apply state to an idle camera; ownership is managed by the caller."""
        self.acquisition_state.apply_to(self.camera)

    def apply_acquisition_state(self):
        """Safely update camera settings while preserving live-view state."""
        return apply_live_acquisition_state(
            self.camera, self.apply_acquisition_state_to_camera
        )

    def reset_acquisition_roi(self):
        """Return the user's acquisition configuration to full-sensor AOI."""
        self._set_acquisition_roi(None)

    def get_current_roi(self):
        """Deprecated compatibility alias for :meth:`get_acquisition_roi`."""
        return self.get_acquisition_roi()

    def get_current_exposure(self):
        """Deprecated compatibility accessor for Main-Window exposure."""
        return self.acquisition_state.exposure_s

    # =========================================================
    # ODMR WINDOW
    # =========================================================

    def open_odmr_window(self):

        # If ODMR window is already open, bring it to front
        if (
            self.odmr_window is not None
            and self.odmr_window.isVisible()
        ):
            self.odmr_window.raise_()
            self.odmr_window.activateWindow()
            return

        self.odmr_window = ODMRWindow(
            hardware=self.hardware,
            camera=self.camera,
            acquisition_state_getter=self.get_acquisition_state,
        )

        self.odmr_window.show()
    # =====================================================
    # ZERO-FIELD IMAGING WINDOW
    # =====================================================

    def open_zero_field_window(self):

        if (
            hasattr(self, "zero_field_window")
            and self.zero_field_window is not None
            and self.zero_field_window.isVisible()
        ):
            self.zero_field_window.raise_()
            self.zero_field_window.activateWindow()
            return

        self.zero_field_window = ZeroFieldWindow(

            hardware_manager=self.hw_manager,

            hardware=self.hardware,

            camera=self.camera,

            acquisition_state_getter=self.get_acquisition_state,

        )

        self.zero_field_window.show()
    # =========================================================
    # MAIN GUI CLOSE WARNING + CLEAN EXIT
    # =========================================================

    def closeEvent(self, event):

        running_odmr = (
            self.odmr_window is not None
            and getattr(self.odmr_window, "odmr_running", False)
        )

        if running_odmr:
            msg = (
                "An ODMR scan is currently running.\n\n"
                "Closing the NV Control GUI will stop all running experiments.\n\n"
                "Are you sure you want to continue?"
            )
        else:
            msg = (
                "Closing the NV Control GUI will stop all running experiments.\n\n"
                "Are you sure you want to continue?"
            )

        reply = QMessageBox.question(
            self,
            "Exit NV Control",
            msg,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:

            # Stop live view safely
            self.stop_stream()

            # Close ODMR window if open
            try:
                if self.odmr_window is not None:
                    self.odmr_window.close()
            except Exception:
                pass

            try:
                if getattr(self, "zero_field_window", None) is not None:
                    self.zero_field_window.close()
            except Exception:
                pass

            # Shut down hardware if HardwareManager supports it
            try:
                self.hw_manager.shutdown()
            except Exception:
                pass

            event.accept()

        else:

            event.ignore()


def main():

    app = QApplication(sys.argv)

    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
