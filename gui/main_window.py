import sys
import argparse
import logging
import pyqtgraph as pg # type: ignore

from PyQt6.QtWidgets import ( # type: ignore
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel,
    QDoubleSpinBox, QSpinBox, QGridLayout,
    QGroupBox, QMessageBox, QComboBox
)
from PyQt6.QtCore import QSignalBlocker, QTimer, pyqtSignal # type: ignore
from gui.magnet_window import MagnetControlWindow
from experiments.odmr_experiment import ODMRExperiment
from experiments.zero_field_experiment import ZeroFieldExperiment
from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager
from gui.odmr_window import ODMRWindow
from gui.odmr_analysis_window import ODMRAnalysisWindow
from gui.zero_field_window import ZeroFieldWindow
from gui.pyqtgraph_teardown import clear_view_registry
from gui import style
from gui.widgets.collapsible_section import enable_state_persistence
from framework.camera_ownership import (
    apply_live_acquisition_state,
    experiment_owns_camera,
    register_camera_state_restorer,
    register_live_view_controller,
    start_live_stream_if_available,
)
from framework.acquisition_state import (
    AcquisitionState,
    binned_roi_to_sensor,
    sensor_roi_to_binned,
)
from framework.roi import validate_roi
from gui.live_view_controller import LiveViewTimerController
from gui.widgets.count_statistics_panel import CountStatisticsPanel
from gui.widgets.collapsible_section import CollapsibleSection
from gui.widgets.image_normalization_section import ImageNormalizationSection
from gui.stability_gate import StabilityMonitor
from gui.display_settings import (
    COLORMAP_NAMES,
    LIVE_VIEW_COLORMAP_KEY,
    colormap_for,
    load_colormap,
    save_colormap,
    select_colormap,
)
from utils.camera_diagnostics import log_event


LOGGER = logging.getLogger(__name__)

# How long a "Stop and Close" waits for worker threads before closing anyway.
_CLOSE_WAIT_TIMEOUT_MS = 10_000


def _tipped(text, field):
    """A form label carrying its field's tooltip."""
    label = QLabel(text)
    label.setToolTip(field.toolTip())
    return label


def _refresh_roi_summary(window):
    """Refresh the ROI section's summary. getattr: tests call
    _sync_roi_controls() on lightweight stand-ins without the section."""
    section = getattr(window, "roi_section", None)
    if section is not None:
        section.refresh_summary()


class MainWindow(QMainWindow):

    # Experiment lifetime, relayed to MagnetControlWindow's command lock.
    # experiment_started carries (experiment_name, magnet_is_swept), where
    # magnet_is_swept is the experiment class's MAGNET_IS_SWEPT constant.
    # With several experiments running at once it is re-emitted with the
    # combined state, and experiment_finished fires only when none remain.
    experiment_started = pyqtSignal(str, bool)
    experiment_finished = pyqtSignal()

    def __init__(self, sim=False):
        super().__init__()

        # Running experiments, keyed by the window that started them:
        # window -> (experiment_name, magnet_is_swept).
        self._running_experiments = {}
        self.magnet_window = None
        self._init_close_state()

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

        self.hw_manager = HardwareManager(self.cfg, force_sim=sim)
        self.hw_manager.initialize()

        self.hardware = self.hw_manager.get_hardware()
        self.camera = self.hardware["camera"]

        # =====================================================
        # STATE VARIABLES
        # =====================================================

        self.state = "IDLE"
        self.odmr_window = None
        self.odmr_analysis_window = None
        # self.acquisition_state is constructed below, once the exposure/
        # binning/baseline spinboxes exist -- built from their displayed
        # values so the GUI display is the single source of truth for the
        # startup default, not an independent dataclass default. See the
        # explicit push to the camera at the end of __init__.
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
        # LEFT CONTROL PANEL (cards and sections, gui/style.py)
        # =====================================================

        left_panel = QVBoxLayout()
        left_panel.setSpacing(style.COLUMN_SPACING)
        main_layout.addLayout(left_panel, 2)

        # =====================================================
        # CAMERA
        # =====================================================

        camera_group, camera_form = style.card("Camera")
        self.camera_group = camera_group
        left_panel.addWidget(camera_group)

        self.start_button = QPushButton("Start Stream")
        self.start_button.setToolTip(
            "Start the live view: the camera streams and the image and count "
            "statistics update continuously. Experiments pause it while they "
            "own the camera.")
        self.start_button.clicked.connect(self.start_stream)
        self.stop_button = QPushButton("Stop Stream")
        self.stop_button.setToolTip("Stop the live view (the camera stops streaming).")
        self.stop_button.clicked.connect(self.stop_stream)
        camera_form.addRow(style.button_row(self.start_button, self.stop_button))

        try:
            exposure_min_s, exposure_max_s = self.camera.get_exposure_limits()
        except Exception as error:
            # get_exposure_limits() is an SDK call made during __init__ --
            # it must never prevent the app from starting. Fall back to
            # the previous hardcoded range and say so loudly.
            exposure_min_s, exposure_max_s = 0.001, 10.0
            print(
                f"[MainWindow] camera.get_exposure_limits() failed "
                f"({error!r}); falling back to the default "
                f"{exposure_min_s * 1000:.3f}-{exposure_max_s * 1000:.0f} ms "
                f"exposure range."
            )

        self.exposure_spin = QDoubleSpinBox()
        self.exposure_spin.setRange(exposure_min_s * 1000.0, exposure_max_s * 1000.0)
        self.exposure_spin.setDecimals(3)
        self.exposure_spin.setSuffix(" ms")
        self.exposure_spin.setValue(10.0)
        self.exposure_spin.setToolTip(
            "Camera exposure time, in milliseconds. Pushed to the camera "
            "immediately, and read back afterward -- the value shown may "
            "differ slightly from what you typed: the sensor quantises to "
            "the nearest row period, measured at ±3 µs (see CLAUDE.md). "
            f"Range ({exposure_min_s * 1000:.3f}-{exposure_max_s * 1000:.0f} "
            "ms) is queried live from the camera at startup."
        )
        self.exposure_spin.valueChanged.connect(self.set_exposure)
        camera_form.addRow(_tipped("Exposure:", self.exposure_spin), self.exposure_spin)

        self.binning_spin = QSpinBox()
        self.binning_spin.setRange(1, 8)
        self.binning_spin.setValue(1)
        self.binning_spin.setToolTip(
            "Camera pixel binning (NxN). Applied immediately to the "
            "camera; changes the acquired frame's pixel dimensions."
        )
        self.binning_spin.valueChanged.connect(self.set_binning)
        camera_form.addRow(_tipped("Binning:", self.binning_spin), self.binning_spin)

        self.baseline_spin = QDoubleSpinBox()
        self.baseline_spin.setRange(0.0, 65535.0)
        self.baseline_spin.setDecimals(1)
        self.baseline_spin.setSuffix(" counts")
        self.baseline_spin.setValue(0.0)
        self.baseline_spin.setToolTip(
            "Camera dark-count offset, subtracted from ROI means before ODMR "
            "contrast is computed.\n"
            "~120 counts measured on this rig (laser blocked, against "
            "~20,000 counts with laser on) -- re-measure if sample, ROI, "
            "or laser power changes.\n"
            "Measure by blocking the laser at your working exposure and "
            "entering the resulting ROI mean.\n"
            "0 disables the correction.\n"
            "The raw I_on/I_off traces are not baseline-corrected; only the "
            "contrast is."
        )
        self.baseline_spin.valueChanged.connect(self.set_baseline_counts)
        camera_form.addRow(_tipped("Camera baseline:", self.baseline_spin),
                           self.baseline_spin)

        # Full-sensor coordinates for camera acquisition.  Experiments
        # receive this value but never define a separate acquisition ROI
        # of their own. Built from the spinboxes just created above, not
        # AcquisitionState's own dataclass defaults, so what's displayed
        # is what this object holds from the moment it exists -- pushed
        # to the camera itself at the end of __init__.
        self.acquisition_state = AcquisitionState(
            exposure_s=self.exposure_spin.value() / 1000.0,
            binning=self.binning_spin.value(),
            baseline_counts=self.baseline_spin.value(),
        )

        # =====================================================
        # ROI (collapsible; the summary is the current ROI)
        # =====================================================

        self.roi_section = CollapsibleSection(
            "Acquisition ROI", settings_key="main/roi",
            tooltip="Show or hide the ROI coordinates. The line below the "
                    "title always shows the ROI in use.")
        left_panel.addWidget(self.roi_section)
        roi_form = self.roi_section.body_layout()

        self.roi_label = QLabel("Acquisition ROI: None")
        self.roi_label.setWordWrap(True)
        self.roi_label.setToolTip(
            "The acquisition ROI every experiment uses (the camera AOI), in "
            "effective binned pixels. Drag the red box on the image or edit "
            "the coordinates below.")
        roi_form.addRow(self.roi_label)

        coordinates_tooltip = (
            "Effective binned-pixel coordinates, 0-based. X/Y is the top-left "
            "corner; Width/Height extend from there. Applied on "
            "Enter/focus-out."
        )
        self.roi_coordinate_spins = {}
        for coordinate in ("X", "Y", "Width", "Height"):
            spin = QSpinBox()
            spin.setRange(
                -1_000_000 if coordinate in ("X", "Y") else 0,
                1_000_000,
            )
            spin.setSuffix(" px")
            spin.setKeyboardTracking(False)
            spin.setToolTip(f"ROI {coordinate.lower()}. {coordinates_tooltip}")
            spin.editingFinished.connect(self.apply_roi_coordinates)
            roi_form.addRow(_tipped(f"{coordinate}:", spin), spin)
            self.roi_coordinate_spins[coordinate.lower()] = spin

        self.reset_roi_button = QPushButton("Reset ROI")
        self.reset_roi_button.setToolTip(
            "Clear the acquisition ROI back to the full sensor."
        )
        self.reset_roi_button.clicked.connect(self.reset_acquisition_roi)
        roi_form.addRow(self.reset_roi_button)
        self.roi_section.set_summary_provider(lambda: self.roi_label.text())

        # =====================================================
        # EXPERIMENTS
        # =====================================================

        experiment_group, experiment_layout = style.card("Experiments", form=False)
        self.experiment_group = experiment_group
        left_panel.addWidget(experiment_group)

        self.open_odmr_button = QPushButton("ODMR")
        self.open_odmr_button.setToolTip(
            "Open the ODMR window: microwave frequency sweeps with the camera "
            "(ESR spectra, statistics, fits).")
        self.open_odmr_button.clicked.connect(self.open_odmr_window)
        self.open_odmr_button.setMinimumHeight(36)

        self.open_zero_field_button = QPushButton("Zero-Field Imaging")
        self.open_zero_field_button.setToolTip(
            "Open the Zero Field window: fluorescence images while sweeping "
            "the magnetic field, with multi-scan averaging and a run queue.")
        self.open_zero_field_button.clicked.connect(self.open_zero_field_window)
        self.open_zero_field_button.setMinimumHeight(36)
        experiment_layout.addLayout(style.button_row(
            self.open_odmr_button, self.open_zero_field_button))

        # =====================================================
        # HARDWARE
        # =====================================================

        hardware_group, hardware_layout = style.card("Hardware", form=False)
        self.hardware_group = hardware_group
        left_panel.addWidget(hardware_group)

        self.open_magnet_button = QPushButton("Magnet")
        self.open_magnet_button.setToolTip(
            "Open the magnet window: switch the coils on/off, set a field per "
            "axis, see the commanded/model field. Locked while an experiment "
            "runs.")
        self.open_magnet_button.setMinimumHeight(36)
        self.open_magnet_button.clicked.connect(
            self.open_magnet_window
        )
        hardware_layout.addWidget(self.open_magnet_button)

        left_panel.addStretch()

        # =====================================================
        # STATUS (in the window's status bar)
        # =====================================================

        self.state_label = QLabel("State: IDLE")
        self.state_label.setToolTip("Live-view state of the main window.")
        self.statusBar().addPermanentWidget(self.state_label)

        # =====================================================
        # RIGHT IMAGE PANEL
        # =====================================================

        right_panel = QVBoxLayout()
        right_panel.setSpacing(style.COLUMN_SPACING)
        main_layout.addLayout(right_panel, 4)

        # The live image and its colormap in one card.
        self.live_view_group, live_view_layout = style.card("Live view", form=False)
        right_panel.addWidget(self.live_view_group, 3)

        # Same selector as the Zero Field window's (gui/display_settings.py).
        colormap_row = QHBoxLayout()
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(COLORMAP_NAMES)
        self.colormap_combo.setToolTip(
            "Colour map of the live view. Display only; applied at once and "
            "remembered (setupInfo.json, display.live_view_colormap).")
        colormap_row.addWidget(_tipped("Colormap:", self.colormap_combo))
        colormap_row.addWidget(self.colormap_combo)
        colormap_row.addStretch()
        live_view_layout.addLayout(colormap_row)

        self.image_plot = pg.PlotItem()
        self.image_plot.showAxis("bottom", True)
        self.image_plot.showAxis("left", True)
        self._pixel_axes_binning = None
        # Own name, not pyqtgraph's shared default "ImageView" (see
        # gui/pyqtgraph_teardown.py).
        self.image_view = pg.ImageView(view=self.image_plot, name="LiveView")
        live_view_layout.addWidget(self.image_view, 1)
        self._init_live_view_colormap()

        # pyqtgraph's own "Normalization" box (Menu > Normalization), as a
        # folded section under the image; same widgets, same behaviour.
        self.normalization_section = ImageNormalizationSection(
            self.image_view, settings_key="main/normalization")
        live_view_layout.addWidget(self.normalization_section)

        # Statistics of the live-view mean counts. It only consumes frames
        # update_image() already received; it never touches the camera.
        self.count_statistics_panel = CountStatisticsPanel(
            settings_getter=self.get_acquisition_state,
            camera_owned=lambda: experiment_owns_camera(self.camera),
        )
        right_panel.addWidget(self.count_statistics_panel, 1)

        # Shared pre-experiment count stability check (gui/stability_gate.py):
        # it uses the panel's samples, and starts/stops the live view only
        # through start_stream()/stop_stream() below. Handed to every
        # experiment window.
        self.stability_monitor = StabilityMonitor(
            self.count_statistics_panel,
            live_view_running=lambda: self.timer.isActive(),
            start_live_view=self.start_stream,
            stop_live_view=self.stop_stream,
        )

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

        # Push the just-built acquisition_state (from the spinboxes'
        # displayed values) to the real camera now, once, deterministically
        # -- before any user interaction or experiment can run. Without
        # this, the camera keeps running at whatever the driver's own
        # __init__ hardcoded until the user happens to touch a spinbox,
        # start a stream, or run a scan, while the GUI already displays
        # different numbers. Called directly (not via apply_acquisition_state())
        # since no live view is running yet and no experiment can own the
        # camera this early in construction.
        self.apply_acquisition_state_to_camera()

    # =====================================================
    # OPEN MAGNET WINDOW
    # =====================================================

    def open_magnet_window(self):

        if self.magnet_window is not None:

            # Closing the magnet window only hides it (it is kept so the
            # post-experiment action still runs), so show it again here.
            self.magnet_window.show()

            self.magnet_window.raise_()

            self.magnet_window.activateWindow()

            return

        self.magnet_window = MagnetControlWindow(
            self.hardware
        )

        self.experiment_started.connect(self.magnet_window.on_experiment_started)
        self.experiment_finished.connect(self.magnet_window.on_experiment_finished)
        # Opened mid-experiment: start locked rather than waiting for the
        # next start/finish transition.
        if self._running_experiments:
            self.magnet_window.on_experiment_started(
                *self._combined_experiment_state()
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
    # EXPERIMENT LIFETIME (magnet control lock)
    # =========================================================

    def _combined_experiment_state(self):
        """(name, magnet_is_swept) summarizing every running experiment."""
        entries = list(self._running_experiments.values())
        name = " + ".join(entry_name for entry_name, _ in entries)
        return name, any(swept for _, swept in entries)

    def _experiment_window_started(self, window, name, magnet_is_swept):
        self._running_experiments[window] = (name, bool(magnet_is_swept))
        self._register_worker_thread(window)
        self.experiment_started.emit(*self._combined_experiment_state())

    def _experiment_window_finished(self, window):
        if self._running_experiments.pop(window, None) is None:
            return
        if self._running_experiments:
            # Another experiment is still running: stay locked, but update
            # the name/swept flag to what is still running.
            self.experiment_started.emit(*self._combined_experiment_state())
        else:
            self.experiment_finished.emit()

    def _relay_zero_field_vector(self, x_mT, y_mT, z_mT):
        if self.magnet_window is not None:
            self.magnet_window.update_field_display(x_mT, y_mT, z_mT)

    # =========================================================
    # WORKER REGISTRY + CLOSE SAFETY
    # =========================================================
    # closeEvent() must not shut hardware down while an experiment worker
    # thread may still be commanding it. Every experiment window exposes
    # worker_thread() and request_stop(); a thread is registered when its
    # window announces run_started and removed on QThread.finished -- the
    # thread really exiting, which is later than run_finished (the worker
    # can still be running after emitting its finished signal).

    def _init_close_state(self):
        # Live worker threads: QThread -> the experiment window that owns it.
        self._worker_threads = {}
        self._closing_after_stop = False  # user chose "Stop and Close"
        self._force_close = False         # wait timed out; close regardless
        self._shut_down = False           # hardware shutdown already done
        self._close_timeout_timer = None

    def _register_worker_thread(self, window):
        thread = window.worker_thread()
        if thread is None or thread in self._worker_threads:
            return
        self._worker_threads[thread] = window
        # Bound method (not a lambda), so this runs on the GUI thread even
        # though QThread.finished is emitted from the worker thread.
        thread.finished.connect(self._on_worker_thread_finished)

    def _on_worker_thread_finished(self):
        self._worker_threads.pop(self.sender(), None)

    def _active_queue_windows(self):
        """Experiment windows whose run queue is active, including between
        runs (no worker thread then). Windows without a queue are skipped."""
        return [
            window for window in self._running_experiments
            if getattr(window, "queue_active", lambda: False)()
        ]

    def _ask_stop_and_close(self):
        """True for "Stop and Close", False for "Cancel"."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Scan in progress")
        box.setText(
            "A scan is running. Stop it and close the application, "
            "or cancel and return to the scan?"
        )
        stop_button = box.addButton(
            "Stop and Close", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(stop_button)
        box.exec()
        return box.clickedButton() is stop_button

    def _begin_stop_and_close(self):
        """Stop every running scan and close once all workers have exited.

        Never blocks the GUI thread: the close is re-attempted from
        _on_worker_finished_for_close() or, at worst, _on_close_timeout().
        """
        for window in set(self._worker_threads.values()):
            try:
                window.request_stop()
            except Exception:
                LOGGER.exception("Failed to request stop for %r", window)
        self.statusBar().showMessage("Stopping scan — please wait…")
        for thread in list(self._worker_threads):
            # Connected after the registry slot, so it sees the registry
            # already updated for this thread.
            thread.finished.connect(self._on_worker_finished_for_close)
        self._close_timeout_timer = QTimer(self)
        self._close_timeout_timer.setSingleShot(True)
        self._close_timeout_timer.timeout.connect(self._on_close_timeout)
        self._close_timeout_timer.start(_CLOSE_WAIT_TIMEOUT_MS)

    def _on_worker_finished_for_close(self):
        if not self._closing_after_stop or self._shut_down:
            return
        if self._worker_threads:
            return
        if self._close_timeout_timer is not None:
            self._close_timeout_timer.stop()
        self.close()

    def _on_close_timeout(self):
        if self._shut_down:
            return
        if self._worker_threads:
            LOGGER.warning(
                "Closing after %.0f s with %d experiment worker thread(s) "
                "still running; hardware shutdown may race with them.",
                _CLOSE_WAIT_TIMEOUT_MS / 1000.0, len(self._worker_threads),
            )
        self._force_close = True
        self.close()

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
            self.count_statistics_panel.add_frame(frame)
            self._update_sensor_dimensions_from_full_frame(frame)
            log_event("gui_image_update", source="live stream", image_target="main live-view image")
            effective_roi = self.acquisition_state.effective_roi
            x0, y0 = (0, 0) if effective_roi is None else effective_roi[:2]
            height, width = frame.shape[:2]
            self.image_view.setImage(
                frame.T,
                autoRange=False,
                autoLevels=False,
                pos=(x0, y0),
            )
            binning = self.acquisition_state.binning
            self._update_pixel_axes(binning)
            self.image_view.getView().setRange(
                xRange=(x0, x0 + width),
                yRange=(y0, y0 + height),
                padding=0,
            )

    def _init_live_view_colormap(self):
        """Show and apply the saved live-view colormap, then track changes."""
        select_colormap(
            self.colormap_combo,
            load_colormap(getattr(self, "cfg", None), LIVE_VIEW_COLORMAP_KEY),
        )
        self._apply_live_view_colormap(self.colormap_combo.currentText())
        self.colormap_combo.currentTextChanged.connect(
            self._on_live_view_colormap_changed
        )

    def _apply_live_view_colormap(self, name):
        # Set on the ImageView's own LUT, so it recolours the frame already
        # shown (stream running or not) and persists across setImage() calls.
        self.image_view.setColorMap(colormap_for(name))

    def _on_live_view_colormap_changed(self, name):
        self._apply_live_view_colormap(name)
        save_colormap(getattr(self, "cfg", None), LIVE_VIEW_COLORMAP_KEY, name)

    def _update_pixel_axes(self, binning):
        """Label plot axes with effective-pixel units when binning changes."""
        if getattr(self, "_pixel_axes_binning", None) == binning:
            return
        self.image_plot.setLabel(
            "bottom", f"X ({binning}x{binning} effective binned pixels)"
        )
        self.image_plot.setLabel(
            "left", f"Y ({binning}x{binning} effective binned pixels)"
        )
        self._pixel_axes_binning = binning

    # =========================================================
    # CAMERA SETTINGS
    # =========================================================

    def set_exposure(self, value_ms):
        self.acquisition_state = AcquisitionState(
            acquisition_roi=self.acquisition_state.acquisition_roi,
            exposure_s=value_ms / 1000.0,
            binning=self.acquisition_state.binning,
            baseline_counts=self.acquisition_state.baseline_counts,
        )
        self.apply_acquisition_state()

    def set_binning(self, value):
        previous_state = self.acquisition_state
        candidate_state = AcquisitionState(
            acquisition_roi=self.acquisition_state.acquisition_roi,
            exposure_s=self.acquisition_state.exposure_s,
            binning=value,
            baseline_counts=self.acquisition_state.baseline_counts,
        )
        self.acquisition_state = candidate_state
        applied = self.apply_acquisition_state()
        if applied is False:
            self.acquisition_state = previous_state
            blocker = QSignalBlocker(self.binning_spin)
            self.binning_spin.setValue(previous_state.binning)
            del blocker
            return

        # The Andor driver may align an ROI to the active binning and
        # SDK-reported AOI limits. Keep the authoritative GUI state in
        # sync with the ROI that was actually programmed.
        actual_roi = getattr(
            getattr(self, "camera", None), "roi", candidate_state.acquisition_roi
        )
        if actual_roi != candidate_state.acquisition_roi:
            self.acquisition_state = AcquisitionState(
                acquisition_roi=actual_roi,
                exposure_s=candidate_state.exposure_s,
                binning=candidate_state.binning,
                baseline_counts=candidate_state.baseline_counts,
            )
        sync_roi_controls = getattr(self, "_sync_roi_controls", None)
        if sync_roi_controls is not None:
            sync_roi_controls()

    def set_baseline_counts(self, value):
        """Update the camera dark-count offset. Never touches the camera --
        this is a pure software correction applied later in ODMR's
        process_frame(), not a hardware setting, so unlike set_exposure()/
        set_binning() this does not call apply_acquisition_state()."""
        self.acquisition_state = AcquisitionState(
            acquisition_roi=self.acquisition_state.acquisition_roi,
            exposure_s=self.acquisition_state.exposure_s,
            binning=self.acquisition_state.binning,
            baseline_counts=value,
        )

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
        """Apply effective binned-pixel coordinates as a sensor AOI."""
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
        """Apply one effective-pixel ROI as the authoritative sensor ROI."""
        try:
            sensor_roi = binned_roi_to_sensor(roi, self.acquisition_state.binning)
            sensor_roi = self._validate_acquisition_roi(sensor_roi)
        except ValueError as error:
            self._sync_roi_controls()
            if show_validation_error:
                QMessageBox.warning(self, "Invalid ROI Coordinates", str(error))
            return False

        candidate_state = AcquisitionState(
            acquisition_roi=sensor_roi,
            exposure_s=self.acquisition_state.exposure_s,
            binning=self.acquisition_state.binning,
            baseline_counts=self.acquisition_state.baseline_counts,
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

        actual_roi = getattr(self.camera, "roi", sensor_roi)
        self.acquisition_state = AcquisitionState(
            acquisition_roi=actual_roi,
            exposure_s=candidate_state.exposure_s,
            binning=candidate_state.binning,
            baseline_counts=candidate_state.baseline_counts,
        )
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
        """Infer sensor size from a full-frame output if hardware size is unavailable."""
        if self._sensor_dimensions is not None or self.acquisition_state.acquisition_roi is not None:
            return
        if getattr(frame, "ndim", 0) >= 2:
            binning = self.acquisition_state.binning
            self._sensor_dimensions = (
                int(frame.shape[1]) * binning,
                int(frame.shape[0]) * binning,
            )
            self._sync_roi_controls()

    def _sync_roi_controls(self):
        """Show the sensor ROI in effective binned-pixel coordinates."""
        roi = self.acquisition_state.acquisition_roi
        binning = self.acquisition_state.binning
        if roi is None:
            sensor_dimensions = self._sensor_dimensions or self._read_sensor_dimensions()
            if sensor_dimensions is None:
                self.roi_label.setText("Acquisition ROI: Full sensor")
                _refresh_roi_summary(self)
                return
            width, height = sensor_dimensions
            width //= binning
            height //= binning
            coordinates = (0, 0, width, height)
            overlay_roi = (0, 0, width, height)
            self.roi_label.setText(
                f"Full sensor ({binning}x{binning} effective pixels): "
                f"x=0, y=0, w={width}, h={height}"
            )
        else:
            effective_roi = sensor_roi_to_binned(roi, binning)
            x0, y0, x1, y1 = effective_roi
            coordinates = (x0, y0, x1 - x0, y1 - y0)
            overlay_roi = effective_roi
            self.roi_label.setText(
                f"Acquisition ROI ({binning}x{binning} effective pixels): "
                f"x={x0}, y={y0}, "
                f"w={x1 - x0}, h={y1 - y0}"
            )

        for spin, value in zip(self.roi_coordinate_spins.values(), coordinates):
            blocker = QSignalBlocker(spin)
            spin.setValue(value)
            del blocker
        _refresh_roi_summary(self)

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
            stability_monitor=getattr(self, "stability_monitor", None),
        )
        self.odmr_window.analysis_requested.connect(
            self.open_odmr_analysis_window
        )
        window = self.odmr_window
        window.run_started.connect(
            lambda: self._experiment_window_started(
                window, "ODMR", ODMRExperiment.MAGNET_IS_SWEPT
            )
        )
        window.run_finished.connect(
            lambda: self._experiment_window_finished(window)
        )

        self.odmr_window.show()

    def open_odmr_analysis_window(self):
        """Show the reusable offline ODMR analysis window."""
        if self.odmr_analysis_window is None:
            self.odmr_analysis_window = ODMRAnalysisWindow(parent=self)
            self.odmr_analysis_window.destroyed.connect(
                self._odmr_analysis_window_destroyed
            )

        self.odmr_analysis_window.show()
        self.odmr_analysis_window.raise_()
        self.odmr_analysis_window.activateWindow()

    def _odmr_analysis_window_destroyed(self, _object=None):
        self.odmr_analysis_window = None
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

            stability_monitor=getattr(self, "stability_monitor", None),

        )
        window = self.zero_field_window
        window.run_started.connect(
            lambda: self._experiment_window_started(
                window, "Zero Field", ZeroFieldExperiment.MAGNET_IS_SWEPT
            )
        )
        window.run_finished.connect(
            lambda: self._experiment_window_finished(window)
        )
        window.field_updated.connect(self._relay_zero_field_vector)

        self.zero_field_window.show()
    # =========================================================
    # MAIN GUI CLOSE WARNING + CLEAN EXIT
    # =========================================================

    def closeEvent(self, event):

        if self._shut_down:
            event.accept()
            return

        # Gatekeeper: never shut hardware down under a live worker thread.
        # A Zero Field run queue between runs has no thread but is still a
        # running sequence, so it gets the same dialog.
        if (self._worker_threads or self._active_queue_windows()) and not self._force_close:
            if self._closing_after_stop:
                # Already stopping; the worker-finished slot or the
                # timeout will close again.
                event.ignore()
                return
            if not self._ask_stop_and_close():
                event.ignore()
                return
            self._closing_after_stop = True
            # A queue between runs ends synchronously here; one with a run
            # in progress is stopped by _begin_stop_and_close() below.
            for window in self._active_queue_windows():
                if window not in self._worker_threads.values():
                    window.request_stop()
            if self._worker_threads:
                # Defer: close is re-attempted once every worker exits.
                event.ignore()
                self._begin_stop_and_close()
                return
            # Every worker exited while the dialog was open: close now.

        # "Stop and Close" already confirmed the exit; don't ask twice.
        if not (self._closing_after_stop or self._force_close):
            reply = QMessageBox.question(
                self,
                "Exit NV Control",
                "Closing the NV Control GUI will stop all running experiments.\n\n"
                "Are you sure you want to continue?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._shutdown_hardware()
        event.accept()

    def _close_tool_windows(self):
        """Close the non-experiment windows on exit.

        Qt quits when the last *visible* top-level window closes, so a
        visible magnet window used to keep the process running after Exit
        (hardware was already shut down by then). The magnet window has no
        closeEvent, so close() only hides it; it is also deleted, which
        disconnects its experiment_started/finished slots -- a worker that
        finishes after a forced close then can't trigger its post-experiment
        magnet command. The analysis window touches no hardware; while a
        pixel fit runs its closeEvent defers the close until the fit ends,
        and the process quits then.
        """
        magnet_window = getattr(self, "magnet_window", None)
        if magnet_window is not None:
            try:
                magnet_window.close()
                magnet_window.deleteLater()
            except Exception:
                LOGGER.exception("Failed to close the magnet window")
            self.magnet_window = None
        analysis_window = getattr(self, "odmr_analysis_window", None)
        if analysis_window is not None:
            try:
                analysis_window.close()
            except Exception:
                LOGGER.exception("Failed to close the ODMR analysis window")

    def _shutdown_hardware(self):
        """Stop live view, close experiment and tool windows, shut hardware down."""

        self._shut_down = True
        if self._close_timeout_timer is not None:
            self._close_timeout_timer.stop()

        # Stop live view safely
        self.stop_stream()

        # Close the count-statistics CSV log, if one is open.
        panel = getattr(self, "count_statistics_panel", None)
        if panel is not None:
            panel.close_log()

        # Close experiment windows; no scan is running by now, so each
        # window accepts its close.
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

        # Tool windows next: a visible one would keep the process alive
        # after the main window closes. Before hardware shutdown, so the
        # magnet window can't command a magnet that is being shut down.
        self._close_tool_windows()

        # Shut down hardware if HardwareManager supports it
        try:
            self.hw_manager.shutdown()
        except Exception:
            pass

        # Worker threads have stopped (or the close was forced) and the
        # experiment windows are closed: no display will update again, so
        # empty pyqtgraph's view registry before Qt destroys the widgets.
        clear_view_registry()


def main():

    parser = argparse.ArgumentParser(description="NV Control Framework")
    parser.add_argument(
        "--sim",
        action="store_true",
        default=False,
        help="Launch with simulated hardware instead of connecting to real instruments.",
    )
    args, _ = parser.parse_known_args()

    app = QApplication(sys.argv)
    # Normal launch only: the shared look, and remembered open/closed
    # sections (tests and scripts that build windows directly get neither,
    # so they always see the documented defaults).
    style.apply_app_style(app)
    enable_state_persistence()

    win = MainWindow(sim=args.sim)
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
