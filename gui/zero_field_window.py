"""Window that integrates the zero-field scan widget with laboratory hardware."""

from pathlib import Path
import logging
import shutil
from datetime import datetime

import numpy as np

from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QDockWidget, QFileDialog, QMainWindow, QMessageBox

from gui.display_settings import (
    ZFE_COLORMAP_KEY,
    load_colormap,
    save_colormap,
    select_colormap,
)
from gui import zero_field_queue as queue_model
from gui.widgets import run_button
from gui.widgets.zero_field_queue_widget import ZeroFieldQueueWidget
from gui.widgets.zero_field_widget import ZeroFieldWidget
from gui.zero_field_queue import QueueEntry, QueueLog
from gui.zero_field_worker import ZeroFieldWorker, ratio_curve
from gui.stability_gate import StabilityWaitController
from framework import stability as stability_model
from framework.stability import StabilitySettings
from experiments.zero_field_experiment import (
    VECTOR_AXIS,
    ZeroFieldExperiment,
    unit_direction,
)
from framework.analysis.zero_field import mean_fluorescence_vs_field
from framework.fluorescence import mean_fluorescence
from framework.paths import ensure_writable_directory
from utils.camera_diagnostics import log_event


LOGGER = logging.getLogger(__name__)

# ImageCube.save() dispatches on this suffix; ZeroFieldWorker derives raw
# per-scan filenames from output_path.suffix, so changing this one constant
# is sufficient to switch both the averaged cube and raw scans to HDF5.
_OUTPUT_EXTENSION = ".h5"

# Default zero-field output directory when config/setupInfo.json has no
# dataOutput.zeroFieldDirectory key (older configs) -- the same directory
# this window always used before that key existed.
_DEFAULT_OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / "data"

_DEFAULT_WINDOW_SIZE = (1100, 960)
_TITLE_BAR_ALLOWANCE_PX = 40

# Output file names carry this timestamp (one-second resolution), so the
# run queue never starts two runs within the same second.
_TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"


def field_range_problem(magnet, config, axis_limit_note):
    """(title, text) if the sweep in *config* can't be reached, else None.

    See ZeroFieldWindow._preflight_field_range() for what is checked and
    why only ValueError counts. Pure calculation: no I/O, no dialog.

    A vector sweep (field_axis "vector") commands s * n_hat on every axis.
    Each axis's target is linear in s, so the endpoints still cover every
    point; all three axes are checked at each endpoint (and the reference).
    """
    swept = config["field_axis"].lower()
    if swept == VECTOR_AXIS:
        try:
            direction = unit_direction(config.get("field_direction"))
        except ValueError as error:
            return (
                "Invalid field direction",
                f"{error}\n\nRequested direction: {config.get('field_direction')}.",
            )
        components = dict(zip(("x", "y", "z"), direction))
        where = "along n = (" + ", ".join(f"{n:.3g}" for n in direction) + ")"
    else:
        components = {name: 1.0 if name == swept else 0.0 for name in ("x", "y", "z")}
        where = f"on {swept.upper()}"
    targets = [(config["field_start"], False), (config["field_stop"], False)]
    # With reference normalization on, the reference vector (the swept
    # axis at reference_field_gauss, the others zeroed as for the sweep;
    # for a vector sweep, reference_field_gauss * n_hat) is commanded at
    # every point and is checked the same way.
    if config.get("reference_enabled", False):
        targets.append((config["reference_field_gauss"], True))
    for target_gauss, is_reference in targets:
        for name in ("x", "y", "z"):
            target_mT = target_gauss * 0.1 * components[name] + 0.0  # no -0.0
            try:
                getattr(magnet, name).plan_field(target_mT)
            except ValueError as error:
                requested = (
                    f"Reference field: {target_gauss:g} G {where}."
                    if is_reference else
                    f"Requested sweep: {config['field_start']:g} G to "
                    f"{config['field_stop']:g} G {where}."
                )
                return (
                    "Field range out of bounds",
                    f"{error}\n\n{requested}\n{axis_limit_note(name)}",
                )
            except RuntimeError:
                # Relay-state precondition, not a range problem.
                continue
    return None


class ZeroFieldWindow(QMainWindow):
    """Configure, run, monitor, and save a ZeroFieldExperiment."""

    # Announce a run's lifetime to MainWindow, which relays it to the magnet
    # control lock. run_finished fires exactly once per run_started, from
    # scan_finished() -- the worker's finished_signal reaches it on every exit
    # path (completion, stop, error).
    run_started = pyqtSignal()
    run_finished = pyqtSignal()
    # Relayed from ZeroFieldWorker.field_updated: (x_mT, y_mT, z_mT).
    field_updated = pyqtSignal(float, float, float)

    def __init__(
        self,
        hardware_manager,
        hardware,
        camera,
        acquisition_state_getter,
        stability_monitor=None,
    ):
        super().__init__()
        self.setWindowTitle("Zero-Field Imaging")
        # 1100 x 960 fits a 1920 x 1080 screen with the taskbar visible
        # (1032 px available; 991 px with the title bar). Clamped to a
        # smaller screen; below that the controls scroll (minimum height
        # is about 540 px, set by the plot/queue areas, not the controls).
        available = self.screen().availableGeometry() if self.screen() else None
        width, height = _DEFAULT_WINDOW_SIZE
        if available is not None:
            width = min(width, available.width())
            height = min(height, available.height() - _TITLE_BAR_ALLOWANCE_PX)
        self.resize(width, height)

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
        self._alternate_direction = False
        self._reference_enabled = False
        self.output_directory = self._resolve_default_output_directory()
        self.output_path = None
        self._scan_timestamp = None
        self._save_off_warning_shown = False
        self._reference_image = None
        self._latest_averaged_image = None
        self._current_display_image = None

        self.widget = ZeroFieldWidget()
        self.setCentralWidget(self.widget)
        self.widget.set_output_directory(self.output_directory)
        self.widget.run_stop_button.clicked.connect(self.toggle_scan)
        self.widget.save_button.clicked.connect(self.save_data)
        self.widget.browse_output_button.clicked.connect(self.browse_output_directory)
        self.widget.save_data_check.toggled.connect(self._handle_save_data_toggled)
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
        # Persisted colormap (config display.zfe_colormap, default Gray).
        # Selected without emitting, so startup neither renders nor saves.
        self._display_cfg = getattr(hardware_manager, "cfg", None)
        select_colormap(
            self.widget.colormap_combo,
            load_colormap(self._display_cfg, ZFE_COLORMAP_KEY),
        )
        self.widget.colormap_combo.currentTextChanged.connect(
            self._save_colormap
        )
        # "Save Data" copies the already-written result file to a
        # user-chosen location; there's nothing to copy until a scan has
        # produced one.
        self.widget.save_button.setEnabled(False)

        # The run/stop button follows the real run state: run_started and
        # run_finished, which every exit path (completion, stop, error)
        # reaches -- not the click.
        self._run_in_progress = False
        self.run_started.connect(self._on_run_started_for_button)
        self.run_finished.connect(self._on_run_finished_for_button)

        # Optional count stability check before a run (gui/stability_gate.py).
        # Unavailable without the main window's live view (monitor None).
        self.stability_monitor = stability_monitor
        self.stability = None
        if stability_monitor is not None:
            self.stability = StabilityWaitController(
                stability_monitor, self.widget.stability_wait_bar, self
            )
        self.widget.stability_group.set_available(stability_monitor is not None)
        self._single_stability_wait = False  # a Run-button wait (not a queue's)

        self._init_queue()

    def _resolve_default_output_directory(self):
        """Read the configured default zero-field output directory.

        Falls back to the directory this window always used before the
        dataOutput.zeroFieldDirectory config key existed, for configs that
        predate it -- a missing key is not an error, just an old config.
        """
        cfg = getattr(self.hardware_manager, "cfg", None)
        if cfg is not None:
            try:
                configured = cfg.get("dataOutput", "zeroFieldDirectory")
            except (KeyError, TypeError):
                configured = None
            if configured:
                path = Path(configured)
                if not path.is_absolute():
                    path = Path(__file__).resolve().parents[1] / path
                return path
        return _DEFAULT_OUTPUT_DIRECTORY

    def toggle_scan(self):
        if self.zero_field_running:
            self.stop_scan()
        elif not self.stability_waiting():
            self.start_scan()

    def start_scan(self):
        """Run button. With the stability check on, the form is snapshotted
        now, the field range is checked, and the run starts once the counts
        are stable (or per "On timeout"). With it off: exactly _start_run()."""
        config = self.widget.get_config()
        output_directory = self.output_directory
        settings = self.widget.stability_group.get_settings()
        if not settings.enabled or self.stability is None:
            self._start_run(config, output_directory)
            return
        # Refuse an unreachable sweep before waiting, not after.
        if not self._preflight_field_range(config):
            return
        self._single_stability_wait = True
        self._update_run_button()
        self.widget.status_label.setText("Status: Waiting for stable counts")
        self.stability.begin(
            settings,
            on_start=lambda record: self._single_stability_done(
                config, output_directory, record),
            on_cancel=self._single_stability_cancelled,
        )

    # ---- stability check -------------------------------------------------

    def stability_waiting(self):
        """True while a stability check (single run or queue entry) waits."""
        return self.stability is not None and self.stability.active

    def _single_stability_done(self, config, output_directory, record):
        self._single_stability_wait = False
        self._update_run_button()
        self._start_run(config, output_directory, stability_record=record)

    def _single_stability_cancelled(self, record):
        self._single_stability_wait = False
        self._update_run_button()
        self.widget.status_label.setText(
            f"Status: Not started — stability check {record['stability_outcome']}"
        )

    def _start_run(self, config, output_directory, interactive=True,
                   stability_record=None):
        """Validate *config* and start its worker; the one start path for
        both the Run button and the run queue.

        Returns None once the worker thread has been started, otherwise the
        reason it was not. interactive=True (the Run button) reports a
        refusal in a dialog, exactly as before the queue existed; the queue
        passes False so an unattended run is never blocked by a modal
        dialog, and records the returned reason instead.
        """
        output_directory = Path(output_directory)

        # Reject an unreachable sweep before any state changes, any thread
        # exists, or any hardware is touched.
        if interactive:
            if not self._preflight_field_range(config):
                return "Field range out of bounds"
        else:
            problem = field_range_problem(self.magnet, config, self._axis_limit_note)
            if problem is not None:
                return " ".join(problem[1].split())

        # Checked before anything else -- no hardware is touched and no
        # worker/thread is created until the destination is confirmed usable.
        # Skipped entirely when saving is off: there is no destination.
        if config["save_data"]:
            try:
                ensure_writable_directory(output_directory)
            except OSError as error:
                message = f"Output directory is not usable: {output_directory}\n{error}"
                if interactive:
                    QMessageBox.critical(self, "Zero Field Error", message)
                return " ".join(message.split())

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
        self._alternate_direction = bool(
            config.get("alternate_direction", False) and config["averaging_enabled"]
        )
        self._reference_enabled = bool(config.get("reference_enabled", False))
        self.widget.set_signal_mode(self._reference_enabled)
        timestamp = datetime.now().strftime(_TIMESTAMP_FORMAT)
        # Stored unconditionally (even when saving is off) so "Save Data"
        # can later synthesize a filename matching what this run would have
        # used had saving been on.
        self._scan_timestamp = timestamp
        if not config["save_data"]:
            self.output_path = None
        elif self.total_scans == 1:
            # Nothing to average -- the single scan streams directly into
            # this path, so it's the only file this run produces, not a
            # companion to a separate per-scan file.
            self.output_path = (
                output_directory / f"zero_field_{timestamp}{_OUTPUT_EXTENSION}"
            )
        else:
            self.output_path = (
                output_directory / f"zero_field_{timestamp}_average{_OUTPUT_EXTENSION}"
            )
        self.widget.save_button.setEnabled(False)
        self.widget.reset_scan(config["field_points"], self.total_scans)
        self.widget.set_running_state(True)
        self.zero_field_running = True
        self._update_queue_controls()

        metadata = {
            "scan_parameters": dict(config),
            "camera_parameters": {
                "exposure_s": acquisition_state.exposure_s,
                "binning": acquisition_state.binning,
                "acquisition_roi": acquisition_roi,
            },
        }
        # Top-level keys of every saved file's metadata. With the check off
        # the only addition is stability_check_enabled = false.
        metadata.update(stability_record or {"stability_check_enabled": False})

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
        self.worker.field_updated.connect(self.field_updated)
        self.thread.finished.connect(self.thread.deleteLater)
        if not interactive:
            # Queue runs only: the next entry starts once this thread has
            # really exited, not when its finished_signal arrives.
            self.thread.finished.connect(self._queue_run_thread_finished)
        # Before the thread starts, so the magnet lock is in place before
        # the worker can send any magnet command.
        self._lock_held = True
        self.run_started.emit()
        self.thread.start()
        return None

    def _preflight_field_range(self, config):
        """Return False (after telling the user) if the sweep can't be reached.

        Runs MagnetAxis.plan_field() -- pure calculation, no I/O -- for the
        vector at each sweep endpoint: the swept axis at field_start/field_stop
        and the other axes at 0.0 mT (this window always zeroes them). This is
        the same check Magnet.set_vector() makes per point mid-scan. Required
        current on a branch is |target - intercept| / ratio, so over a linear
        sweep its maximum is at an endpoint; checking both endpoints covers
        every point in between. A vector sweep commands s * n_hat, so every
        axis is linear in s and is checked at both endpoints the same way.

        Only ValueError blocks the scan: that is what plan_field() raises for
        an out-of-range or non-finite target. Its RuntimeErrors concern relay
        state, notably "unsynchronized relay" before the magnet's first
        enable(), which the scan's own setup_scan() resolves -- blocking on
        those would refuse every first scan after launch.

        The check itself is field_range_problem(), shared with the run
        queue, which reports a refusal without a dialog.
        """
        problem = field_range_problem(self.magnet, config, self._axis_limit_note)
        if problem is None:
            return True
        QMessageBox.critical(self, *problem)
        return False

    def _axis_limit_note(self, name):
        """Approximate reachable range of one axis, in Gauss, from its config."""
        note = f"Check your calibration limits in setupInfo.json (Helmholtz.{name.upper()})."
        try:
            axis = getattr(self.magnet, name)
            positive = axis.calibration["positive"]
            negative = axis.calibration["negative"]
            max_current = axis.max_current
            upper_gauss = 10.0 * (
                positive["magnetic_field_offset"]
                + positive["magnetic_field_ratio"] * max_current
            )
            lower_gauss = 10.0 * (
                negative["magnetic_field_offset"]
                - negative["magnetic_field_ratio"] * max_current
            )
        except (AttributeError, KeyError, TypeError):
            return note
        return (
            f"{name.upper()}-axis reachable range is about {lower_gauss:.1f} G to "
            f"{upper_gauss:.1f} G (max_current {max_current:g} A). {note}"
        )

    def stop_scan(self):
        if self.worker is not None:
            self.widget.status_label.setText("Status: Stopping")
            self.worker.stop()

    # Experiment-window interface used by MainWindow's worker registry and
    # close-safety path (see CLAUDE.md). ODMRWindow implements the same two.
    def worker_thread(self):
        """The QThread running the current scan's worker, or None."""
        return self.thread

    def request_stop(self):
        """Ask the running scan to stop; it finishes asynchronously.

        While a queue runs this stops the whole queue (the running entry
        and every remaining one), which is what MainWindow's "Stop and
        Close" needs."""
        if self._queue_active:
            self.stop_queue()
        elif self.stability_waiting():
            self.stability.cancel()  # nothing started yet
        else:
            self.stop_scan()

    def queue_active(self):
        """True from "Run queue" until the queue has ended, including the
        pauses between runs when no worker thread exists. MainWindow's close
        gate treats such a window as busy."""
        return self._queue_active

    def handle_live_update(self, field, signal, image):
        entry = self._queue_current
        if entry is not None and entry.first_frame_mean is None:
            # Mean counts of the first stored point's image (scan 1, point 1;
            # averaged over that point's repeats), for the queue log.
            entry.first_frame_mean = float(mean_fluorescence(image))
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

    def _save_colormap(self, name):
        save_colormap(self._display_cfg, ZFE_COLORMAP_KEY, name)

    def handle_fluorescence_update(self, fields, signals):
        """Display the cumulative raw fluorescence curve for this scan."""
        self.scan_fields = fields.tolist()
        self.scan_signals = signals.tolist()
        self.widget.update_fluorescence_curve(self.scan_fields, self.scan_signals)

    def handle_progress(self, current, total):
        self.widget.update_progress(current, total)

    def _scan_direction_label(self, scan_index):
        """"start→stop"/"stop→start" while alternating, else None."""
        if not self._alternate_direction:
            return None
        reversed_sweep = ZeroFieldExperiment.scan_is_reversed(scan_index, True)
        return "stop→start" if reversed_sweep else "start→stop"

    def handle_scan_started(self, current, total):
        self.scan_fields = []
        self.scan_signals = []
        # A reverse scan's live curve is plotted against its real field
        # values, so it fills in from the stop end.
        self.widget.update_scan_progress(
            current, total, self._scan_direction_label(current)
        )

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

    def _curve_from_cube(self, image_cube):
        """Per-point curve of a cube: mean fluorescence, or with the
        reference on, the mean signal/reference ratio."""
        if self._reference_enabled and "ratio" in image_cube.extra_datasets:
            return ratio_curve(image_cube)
        return mean_fluorescence_vs_field(image_cube)

    def handle_scan_completed(self, image_cube, current, total):
        # image_cube is the running average, already aligned by field
        # (start -> stop) whatever direction the scans ran.
        fields, signals = self._curve_from_cube(image_cube)
        self.scan_fields = fields.tolist()
        self.scan_signals = signals.tolist()
        self.widget.update_live_data(
            self.scan_fields,
            self.scan_signals,
            self.field_point_count,
            self.field_point_count,
        )
        self.widget.update_scan_progress(
            current, total, self._scan_direction_label(current)
        )

    def scan_finished(self, image_cube, _fields, _signals, stopped):
        self.image_cube = image_cube
        self.zero_field_running = False
        # Emitted first so nothing below can prevent the magnet lock from
        # being released. The worker has already finished cleanup_scan()
        # (magnet.disable()) by the time finished_signal is sent. A queue
        # that goes on to another entry keeps the lock instead: run_finished
        # is then emitted once, when the queue ends.
        if not self._queue_record_finished_run(image_cube, stopped):
            self._lock_held = False
            self.run_finished.emit()
        self.widget.set_running_state(False)
        self._update_queue_controls()
        if image_cube is None:
            self.widget.status_label.setText("Status: Error")
            self.widget.save_button.setEnabled(False)
            return

        if image_cube.data is not None:
            fields, signals = self._curve_from_cube(image_cube)
            self.scan_fields = fields.tolist()
            self.scan_signals = signals.tolist()
            self.widget.update_live_data(
                self.scan_fields,
                self.scan_signals,
                len(self.scan_fields),
                self.field_point_count,
            )

        status_prefix = "Status: Stopped" if stopped else "Status: Complete"
        self.widget.status_label.setText(status_prefix)
        # Enabled exactly when there's an ImageCube to hand to "Save Data" --
        # either it's already on disk (copy) or it isn't yet (serialize from
        # memory); either way there's something to save.
        self.widget.save_button.setEnabled(image_cube.data is not None)
        if self.output_path is not None:
            self.widget.status_label.setText(
                f"{status_prefix} — saved {self.output_path.name}"
            )
        elif image_cube.data is not None:
            # Saving was off for this run -- say so explicitly rather than
            # leaving "was this saved?" implied by an easy-to-forget
            # checkbox state, so a scan can't be mistaken for one that
            # produced a file just because a file usually does exist.
            self.widget.status_label.setText(
                f"{status_prefix} — not saved (use Save Data to keep it)"
            )

    def _handle_save_data_toggled(self, checked):
        """Warn once per window instance when the user opts out of saving.

        A crash or forced quit with saving off loses the run's data
        entirely, since nothing is written incrementally -- worth a
        one-time heads-up, not a dialog on every scan.
        """
        if checked or self._save_off_warning_shown:
            return
        QMessageBox.warning(
            self,
            "Zero Field",
            "Saving is off: this run's data will exist only in memory. "
            "A crash or forced quit before you use \"Save Data\" will lose it.",
        )
        self._save_off_warning_shown = True

    def show_error(self, message):
        self.zero_field_running = False
        self.widget.set_running_state(False)
        self.widget.status_label.setText("Status: Error")
        if self._queue_current is not None:
            # Unattended: no modal dialog. The message goes to the entry's
            # status and the queue log when finished_signal arrives.
            self._queue_current.message = " ".join(str(message).split())
            self.widget.status_label.setText(f"Status: Error — {message}")
            self._update_queue_controls()
            return
        QMessageBox.critical(self, "Zero Field Error", message)

    def browse_output_directory(self):
        """Pick the destination for future scans; rejects unusable choices
        immediately rather than deferring the error to scan start."""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose Zero Field Output Directory",
            str(self.output_directory),
        )
        if not directory:
            return
        try:
            ensure_writable_directory(directory)
        except OSError as error:
            QMessageBox.critical(
                self, "Zero Field Error", f"Directory is not usable: {directory}\n{error}"
            )
            return
        self.output_directory = Path(directory)
        self.widget.set_output_directory(self.output_directory)
        self.widget.status_label.setText(
            f"Status: Output directory set to {self.output_directory}"
        )

    def save_data(self):
        """Save this run's result, however it currently exists.

        If saving was on, the result is already durably written to
        self.output_path -- this is then a plain file copy, not a re-save.
        If saving was off, there is no file yet; the in-memory ImageCube is
        serialized directly to the chosen destination instead.
        """
        if self.zero_field_running:
            return
        if self.output_path is not None and self.output_path.exists():
            self._save_copy_of_output_file()
        elif self.image_cube is not None and self.image_cube.data is not None:
            self._save_in_memory_cube()
        else:
            QMessageBox.critical(self, "Zero Field Error", "Nothing to save.")

    def _save_copy_of_output_file(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose Destination for a Copy of the Zero Field Data",
            str(self.output_directory),
        )
        if not directory:
            return

        destination = Path(directory) / self.output_path.name
        try:
            shutil.copy2(self.output_path, destination)
        except OSError as error:
            QMessageBox.critical(
                self, "Zero Field Error", f"Could not save a copy: {error}"
            )
            return
        self.widget.status_label.setText(f"Status: Saved a copy to {destination}")

    def _save_in_memory_cube(self):
        """Serialize the acquired-but-never-written ImageCube on request.

        Mirrors _save_copy_of_output_file's interaction (pick a folder,
        report the same way) but writes fresh via ImageCube.save() since
        there's no already-written file to copy.
        """
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose Destination for the Zero Field Data",
            str(self.output_directory),
        )
        if not directory:
            return

        suffix = "_average" if self.total_scans > 1 else ""
        filename = f"zero_field_{self._scan_timestamp}{suffix}{_OUTPUT_EXTENSION}"
        destination = Path(directory) / filename
        try:
            self.image_cube.save(destination)
        except OSError as error:
            QMessageBox.critical(
                self, "Zero Field Error", f"Could not save data: {error}"
            )
            return
        self.widget.status_label.setText(f"Status: Saved data to {destination}")

    # =========================================================
    # RUN QUEUE
    # =========================================================
    # Each entry runs through _start_run() exactly as the Run button would
    # (interactive=False only replaces dialogs with a recorded reason).
    # run_started fires per run, so MainWindow registers every worker
    # thread; run_finished fires once, when the queue ends, so the magnet
    # lock is never released between runs.

    def _init_queue(self):
        self.queue_entries = []
        self._queue_active = False
        self._queue_stop_requested = False
        self._queue_current = None       # entry whose worker is running
        self._queue_started = []         # entries started by this execution
        self._queue_log = None
        self._lock_held = False          # run_started sent, run_finished not yet
        self._queue_timer = QTimer(self)
        self._queue_timer.setSingleShot(True)
        self._queue_timer.timeout.connect(self._start_next_queue_entry)

        self.queue_widget = ZeroFieldQueueWidget()
        self.queue_dock = QDockWidget("Run Queue", self)
        self.queue_dock.setObjectName("ZeroFieldRunQueue")
        # Not closable: there would be no way to get it back.
        self.queue_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.queue_dock.setWidget(self.queue_widget)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.queue_dock)

        q = self.queue_widget
        q.add_button.clicked.connect(self.add_to_queue)
        q.remove_button.clicked.connect(self.remove_queue_entry)
        q.move_up_button.clicked.connect(lambda: self.move_queue_entry(-1))
        q.move_down_button.clicked.connect(lambda: self.move_queue_entry(+1))
        q.duplicate_button.clicked.connect(self.duplicate_queue_entry)
        q.clear_button.clicked.connect(self.clear_pending_entries)
        q.load_into_form_button.clicked.connect(self.load_entry_into_form)
        q.save_queue_button.clicked.connect(self.save_queue)
        q.load_queue_button.clicked.connect(self.load_queue)
        q.run_queue_button.clicked.connect(self.run_queue)
        q.stop_current_button.clicked.connect(self.stop_current_run)
        q.stop_queue_button.clicked.connect(self.stop_queue)
        q.table.itemSelectionChanged.connect(self._update_queue_controls)
        self._refresh_queue()

    # ---- editing -------------------------------------------------------

    def add_to_queue(self):
        """Snapshot the form (and output directory) as a pending entry.

        The same field-range check as the Run button, with its dialog; an
        unreachable entry is not added. Returns True if it was added."""
        config = self.widget.get_config()
        if not self._preflight_field_range(config):
            return False
        self.queue_entries.append(QueueEntry.snapshot(
            config, self.output_directory, self.widget.stability_group.get_settings()
        ))
        self._refresh_queue(select=len(self.queue_entries) - 1)
        return True

    def _selected_entry(self):
        row = self.queue_widget.selected_row()
        if row is None or row >= len(self.queue_entries):
            return None, None
        return row, self.queue_entries[row]

    def remove_queue_entry(self):
        row, entry = self._selected_entry()
        if entry is None or entry.status == queue_model.RUNNING:
            return
        del self.queue_entries[row]
        self._refresh_queue(select=min(row, len(self.queue_entries) - 1))

    def move_queue_entry(self, step):
        """Move the selected entry up (-1) or down (+1). The queue runs the
        first pending entry in list order, so reordering pending entries
        during a queue changes what runs next."""
        row, entry = self._selected_entry()
        target = None if row is None else row + step
        if entry is None or not 0 <= target < len(self.queue_entries):
            return
        entries = self.queue_entries
        entries[row], entries[target] = entries[target], entries[row]
        self._refresh_queue(select=target)

    def duplicate_queue_entry(self):
        row, entry = self._selected_entry()
        if entry is None:
            return
        self.queue_entries.insert(row + 1, entry.duplicate())
        self._refresh_queue(select=row + 1)

    def clear_pending_entries(self):
        self.queue_entries = [
            entry for entry in self.queue_entries
            if entry.status != queue_model.PENDING
        ]
        self._refresh_queue()

    def load_entry_into_form(self):
        """Copy the selected entry's settings back into the form."""
        _row, entry = self._selected_entry()
        if entry is None:
            return
        self.widget.set_config(entry.config)
        self.widget.stability_group.set_settings(entry.stability)
        self.output_directory = Path(entry.output_directory)
        self.widget.set_output_directory(self.output_directory)

    def save_queue(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Zero Field Queue",
            str(self.output_directory / "zero_field_queue.json"),
            "Queue files (*.json)",
        )
        if path:
            self.save_queue_to(path)

    def save_queue_to(self, path):
        try:
            queue_model.save_queue_file(
                path, self.queue_entries,
                self.queue_widget.pause_spin.value(),
                self.queue_widget.on_failure_combo.currentText(),
            )
        except OSError as error:
            QMessageBox.critical(self, "Zero Field Queue", f"Could not save the queue: {error}")
            return False
        self.queue_widget.queue_status_label.setText(f"Queue: saved {Path(path).name}")
        return True

    def load_queue(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Zero Field Queue", str(self.output_directory),
            "Queue files (*.json)",
        )
        if path:
            self.load_queue_from(path)

    def load_queue_from(self, path):
        """Replace the list with a queue file's entries, all pending.

        Each entry gets the same field-range check as "Add to queue"; if
        any is unreachable, or the file is invalid, nothing is loaded."""
        if self._queue_active:
            return False
        try:
            entries, pause_s, on_failure = queue_model.load_queue_file(path)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Zero Field Queue", f"Could not load {path}:\n{error}")
            return False
        refused = []
        for number, entry in enumerate(entries, start=1):
            problem = field_range_problem(self.magnet, entry.config, self._axis_limit_note)
            if problem is not None:
                refused.append(f"Entry {number}: {problem[1]}")
        if refused:
            QMessageBox.critical(
                self, "Field range out of bounds",
                "The queue was not loaded.\n\n" + "\n\n".join(refused),
            )
            return False
        self.queue_entries = entries
        self.queue_widget.pause_spin.setValue(pause_s)
        self.queue_widget.on_failure_combo.setCurrentText(on_failure)
        self._refresh_queue()
        self.queue_widget.queue_status_label.setText(f"Queue: loaded {Path(path).name}")
        return True

    # ---- execution -----------------------------------------------------

    def run_queue(self):
        """Run every pending entry in list order."""
        if self._queue_active or self.zero_field_running:
            return False
        pending = [e for e in self.queue_entries if e.status == queue_model.PENDING]
        if not pending:
            self.queue_widget.queue_status_label.setText("Queue: nothing pending")
            return False
        self._queue_active = True
        self._queue_stop_requested = False
        self._queue_started = []
        self._queue_log = self._open_queue_log(Path(pending[0].output_directory))
        self._update_queue_controls()
        self._start_next_queue_entry()
        return True

    def _open_queue_log(self, directory):
        """zero_field_queue_<timestamp>.csv in the first entry's output
        directory; None (logged) if it can't be created."""
        timestamp = datetime.now().strftime(_TIMESTAMP_FORMAT)
        try:
            ensure_writable_directory(directory)
            log = QueueLog(directory / f"zero_field_queue_{timestamp}.csv")
        except OSError:
            LOGGER.exception("Could not create the Zero Field queue log in %s", directory)
            return None
        LOGGER.info("Zero Field queue log: %s", log.path)
        return log

    def _start_next_queue_entry(self):
        if not self._queue_active:
            return
        if self._queue_stop_requested:
            self._end_queue()
            return
        entry = next(
            (e for e in self.queue_entries if e.status == queue_model.PENDING), None
        )
        if entry is None:
            self._end_queue()
            return
        now = datetime.now()
        if now.strftime(_TIMESTAMP_FORMAT) == self._scan_timestamp:
            # The previous run used this second's timestamp; starting now
            # would give this run the same file name. Wait for the next second.
            self._queue_timer.start(1000 - now.microsecond // 1000 + 10)
            return

        entry.status = queue_model.RUNNING
        entry.message = ""
        entry.output_path = ""
        entry.first_frame_mean = None
        entry.start_time = now
        entry.end_time = None
        entry.stability_result = None
        entry.log_index = self.queue_entries.index(entry) + 1
        self._queue_started.append(entry)
        self._queue_current = entry
        self._refresh_queue()

        if entry.stability.enabled and self.stability is not None:
            # The entry's own settings, checked now: after the pause between
            # runs, just before it starts. No dialogs; the wait line goes to
            # the window's status, the queue status and the wait bar.
            entry.message = "waiting for stable counts"
            self._refresh_queue()
            self.stability.begin(
                entry.stability,
                on_start=lambda record: self._launch_queue_entry(entry, record),
                on_cancel=lambda record: self._queue_stability_cancelled(entry, record),
                on_status=self._queue_stability_status,
            )
            return
        self._launch_queue_entry(entry, None)

    def _queue_stability_status(self, text):
        self.queue_widget.queue_status_label.setText(f"Queue: {text}")
        self.widget.status_label.setText(f"Status: {text}")

    def _queue_stability_cancelled(self, entry, record):
        """The entry's stability wait ended without starting it."""
        entry.stability_result = record
        self._queue_current = None
        outcome = record["stability_outcome"]
        if self._queue_stop_requested:
            status, message = queue_model.STOPPED, "queue stopped during stability check"
        elif outcome == stability_model.OUTCOME_TIMEOUT_CANCELLED:
            std, drift = record["stability_std_percent"], record["stability_drift_percent_per_min"]
            status = queue_model.SKIPPED_UNSTABLE
            message = (
                f"not stable within {record['stability_timeout_s']:g} s "
                f"(std {'--' if std is None else f'{std:.2f}'}%, "
                f"drift {'--' if drift is None else f'{drift:+.2f}'}%/min)"
            )
        else:
            status, message = queue_model.STOPPED, "cancelled during stability check"
        self.widget.status_label.setText(f"Status: Not started — {status}")
        self._finish_queue_entry(entry, status, message)
        if self._queue_should_continue(entry):
            self._queue_timer.start(0)
        else:
            self._end_queue()

    def _launch_queue_entry(self, entry, stability_record):
        entry.message = ""
        entry.stability_result = stability_record
        if self._queue_stop_requested:
            # Stopped while the check was finishing; never start.
            self._queue_current = None
            self._finish_queue_entry(entry, queue_model.STOPPED, "queue stopped")
            self._end_queue()
            return
        # Field range re-checked here, immediately before the start, as
        # well as when the entry was added.
        # With the check off this is the same call as before it existed.
        extra = {} if stability_record is None else {"stability_record": stability_record}
        problem = self._start_run(entry.config, entry.output_directory,
                                  interactive=False, **extra)
        if problem is not None:
            # Refused before any worker existed: nothing to wait for.
            self._queue_current = None
            self._finish_queue_entry(entry, queue_model.FAILED, problem)
            if self._queue_should_continue(entry):
                self._queue_timer.start(0)
            else:
                self._end_queue()
            return
        entry.output_path = "" if self.output_path is None else str(self.output_path)
        self._refresh_queue()

    def _queue_record_finished_run(self, image_cube, stopped):
        """Called from scan_finished(). Records the queued run's outcome and
        returns True if the queue goes on to another entry (the magnet lock
        is then kept); False for a run outside a queue, or the last one."""
        entry = self._queue_current
        if not self._queue_active or entry is None:
            return False
        self._queue_current = None
        try:
            if image_cube is None:
                status = queue_model.FAILED
                message = entry.message or "the run raised an error"
            elif stopped:
                status = queue_model.STOPPED
                message = "queue stopped" if self._queue_stop_requested else "stopped by user"
            else:
                status, message = queue_model.DONE, ""
            self._finish_queue_entry(entry, status, message)
            if self._queue_should_continue(entry):
                # The next entry starts from _queue_run_thread_finished().
                return True
        except Exception:
            LOGGER.exception("Zero Field queue bookkeeping failed; ending the queue")
        # The caller (scan_finished) releases the magnet lock.
        self._end_queue(release_lock=False)
        return False

    def _queue_run_thread_finished(self):
        """A queued run's worker thread has exited: after the pause, start
        the next entry (if the queue is still going)."""
        if not self._queue_active or self._queue_current is not None:
            return
        pause_ms = int(round(self.queue_widget.pause_spin.value() * 1000))
        self._queue_timer.start(pause_ms)
        if pause_ms:
            self.queue_widget.queue_status_label.setText(
                f"Queue: pausing {pause_ms / 1000:g} s before the next run"
            )

    def _queue_should_continue(self, finished_entry):
        if self._queue_stop_requested:
            return False
        if (
            finished_entry.status == queue_model.FAILED
            and self.queue_widget.on_failure_combo.currentText() == queue_model.ON_FAILURE_STOP
        ):
            return False
        return any(e.status == queue_model.PENDING for e in self.queue_entries)

    def _finish_queue_entry(self, entry, status, message):
        entry.status = status
        entry.message = message
        entry.end_time = datetime.now()
        if self._queue_log is not None:
            try:
                self._queue_log.append(entry)
            except OSError:
                LOGGER.exception("Could not append to queue log %s", self._queue_log.path)
        self._refresh_queue()

    def stop_current_run(self):
        """Stop the running entry; the queue continues with the next one."""
        if self._queue_active and self.zero_field_running:
            self.stop_scan()
        elif self._queue_active and self.stability_waiting():
            self.stability.cancel()  # entry marked stopped; queue continues

    def stop_queue(self):
        """Stop the running entry and start no more."""
        if not self._queue_active:
            return
        self._queue_stop_requested = True
        self._queue_timer.stop()
        if self.zero_field_running:
            self.stop_scan()  # scan_finished() then ends the queue
        elif self.stability_waiting():
            self.stability.cancel()  # its callback ends the queue
        else:
            self._end_queue()

    def _end_queue(self, release_lock=True):
        self._queue_active = False
        self._queue_timer.stop()
        self._queue_current = None
        counts = {
            status: sum(e.status == status for e in self._queue_started)
            for status in (queue_model.DONE, queue_model.STOPPED, queue_model.FAILED)
        }
        text = (
            f"Queue: finished — {counts[queue_model.DONE]} done, "
            f"{counts[queue_model.STOPPED]} stopped, {counts[queue_model.FAILED]} failed"
        )
        skipped = sum(e.status == queue_model.SKIPPED_UNSTABLE for e in self._queue_started)
        if skipped:
            text += f", {skipped} skipped (unstable)"
        if self._queue_stop_requested:
            text += " (queue stopped)"
        self.queue_widget.queue_status_label.setText(text)
        if self._queue_log is not None:
            self.queue_widget.queue_status_label.setToolTip(f"Log: {self._queue_log.path}")
        if release_lock and self._lock_held:
            self._lock_held = False
            self.run_finished.emit()
        self._refresh_queue()

    # ---- display -------------------------------------------------------

    def _refresh_queue(self, select=None):
        self.queue_widget.set_entries(self.queue_entries)
        if select is not None:
            self.queue_widget.select_row(select)
        if self._queue_active and self._queue_current is not None:
            total = len(self._queue_started) + sum(
                e.status == queue_model.PENDING for e in self.queue_entries
            )
            self.queue_widget.queue_status_label.setText(
                f"Queue: run {len(self._queue_started)} of {total}"
            )
        self._update_queue_controls()

    def _update_queue_controls(self):
        q = self.queue_widget
        active = self._queue_active
        row, entry = self._selected_entry()
        count = len(self.queue_entries)
        has_pending = any(e.status == queue_model.PENDING for e in self.queue_entries)
        self._update_run_button()
        q.run_queue_button.setEnabled(not active and not self.zero_field_running and has_pending)
        q.stop_current_button.setEnabled(
            active and (self.zero_field_running or self.stability_waiting())
        )
        q.stop_queue_button.setEnabled(active)
        q.remove_button.setEnabled(entry is not None and entry.status != queue_model.RUNNING)
        q.move_up_button.setEnabled(row is not None and row > 0)
        q.move_down_button.setEnabled(row is not None and row < count - 1)
        q.duplicate_button.setEnabled(entry is not None)
        q.clear_button.setEnabled(has_pending)
        q.load_into_form_button.setEnabled(entry is not None)
        q.save_queue_button.setEnabled(count > 0)
        q.load_queue_button.setEnabled(not active)

    # ---- run/stop button ------------------------------------------------

    def _on_run_started_for_button(self):
        self._run_in_progress = True
        self._update_run_button()

    def _on_run_finished_for_button(self):
        self._run_in_progress = False
        self._update_run_button()

    def _update_run_button(self):
        """The Run button is the single-run path; the queue owns the window
        while it runs, so the button is then disabled ("Queue running")."""
        if self._queue_active:
            state = run_button.BLOCKED
        elif getattr(self, "_single_stability_wait", False):
            self.widget.set_run_button_waiting()
            return
        elif self._run_in_progress:
            state = run_button.RUNNING
        else:
            state = run_button.IDLE
        self.widget.set_run_button_state(state)

    def closeEvent(self, event):
        # MainWindow is the close gatekeeper (it waits for worker threads
        # before shutting hardware down). Here, a running scan is asked to
        # stop and the window stays open until it has finished; closing it
        # afterwards is accepted normally.
        if self.zero_field_running:
            self.request_stop()
            event.ignore()
            return
        if getattr(self, "_queue_active", False):
            # Between queued runs: nothing is commanding hardware, so end
            # the queue (releasing the magnet lock) and close.
            self.stop_queue()
        elif getattr(self, "stability", None) is not None and self.stability_waiting():
            # Waiting for stable counts: nothing started; cancel the wait
            # (restoring the live view) and close.
            self.stability.cancel()
        event.accept()
