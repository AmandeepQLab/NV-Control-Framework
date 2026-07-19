import sys
import pyqtgraph as pg # type: ignore

from PyQt6.QtWidgets import ( # type: ignore
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel,
    QDoubleSpinBox, QSpinBox,
    QGroupBox, QMessageBox
)
from PyQt6.QtCore import QTimer # type: ignore
from gui.magnet_window import MagnetControlWindow
from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager
from gui.odmr_window import ODMRWindow
from gui.zero_field_window import ZeroFieldWindow
from framework.camera_ownership import (
    register_live_view_controller,
    start_live_stream_if_available,
)
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

        self.roi_label = QLabel("ROI: None")
        roi_layout.addWidget(self.roi_label)

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

        # =====================================================
        # LIVE VIEW TIMER
        # =====================================================

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_image)
        self.live_view_timer = LiveViewTimerController(self.timer, 30)
        register_live_view_controller(self.camera, self.live_view_timer)

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
            log_event("gui_image_update", source="live stream", image_target="main live-view image")
            self.image_view.setImage(
                frame.T,
                autoLevels=False
            )

    # =========================================================
    # CAMERA SETTINGS
    # =========================================================

    def set_exposure(self, value):

        self.camera.set_exposure(value)

    def set_binning(self, value):

        self.camera.set_binning(value)

    # =========================================================
    # ROI
    # =========================================================

    def update_roi_info(self):

        pos = self.roi.pos()
        size = self.roi.size()

        text = (
            f"x={int(pos.x())}, "
            f"y={int(pos.y())}, "
            f"w={int(size.x())}, "
            f"h={int(size.y())}"
        )

        self.roi_label.setText(text)

    def get_current_roi(self):

        pos = self.roi.pos()
        size = self.roi.size()

        return (
            int(pos.x()),
            int(pos.y()),
            int(pos.x() + size.x()),
            int(pos.y() + size.y())
        )

    def get_current_exposure(self):

        return self.exposure_spin.value()

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
            roi_getter=self.get_current_roi,
            exposure_getter=self.get_current_exposure
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

            roi_getter=self.get_current_roi,

            exposure_getter=self.get_current_exposure,

            binning_getter=lambda: self.binning_spin.value(),

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
