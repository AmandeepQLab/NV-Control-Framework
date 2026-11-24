"""Live count statistics for the main window, like a power meter's
statistics screen.

The panel never touches the camera. MainWindow.update_image() hands it
every frame the live view already received (add_frame()); one sample is
the mean counts over that frame, i.e. over the current acquisition ROI.
See framework/count_statistics.py for the maths and the CSV format.
"""

import logging
import math
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from framework.count_statistics import CountLog, RunningStatistics, frame_sample
from gui.widgets import run_button
from gui.widgets.run_button import style_run_button

LOGGER = logging.getLogger(__name__)

DISPLAY_INTERVAL_MS = 250        # labels and trend refresh at 4 Hz
TREND_WINDOW_S = 300.0           # trend plot shows the last 5 minutes
NO_FRAMES_AFTER_S = 1.5          # "waiting for live-view frames" after this
MAX_PANEL_HEIGHT_PX = 280        # values + buttons fit; the image keeps the rest
_DEFAULT_LOG_DIRECTORY = Path(__file__).resolve().parents[2] / "data"

PAUSED_TEXT = "Paused — experiment running"


def _settings_key(state):
    """The settings that make samples comparable: ROI, exposure, binning."""
    return (state.acquisition_roi, state.exposure_s, state.binning)


def _describe_change(old, new):
    changes = []
    if old[0] != new[0]:
        changes.append("ROI changed")
    if old[1] != new[1]:
        changes.append(f"exposure {old[1] * 1000:g} → {new[1] * 1000:g} ms")
    if old[2] != new[2]:
        changes.append(f"binning {old[2]} → {new[2]}")
    return ", ".join(changes)


def _fmt(value, spec):
    return "--" if value is None or not math.isfinite(value) else format(value, spec)


def _fmt_elapsed(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class CountStatisticsPanel(QGroupBox):
    """Statistics of the live-view mean counts: values, trend and CSV log.

    settings_getter() returns the main window's AcquisitionState; a change
    of its ROI, exposure or binning resets the statistics. camera_owned()
    is True while an experiment holds the camera; accumulation pauses then
    and resumes with the live view.

    Every accepted sample is also emitted as sample_accepted(t, value), t
    on this panel's clock, whether or not statistics are accumulating; the
    pre-experiment stability check (gui/stability_gate.py) listens to it.
    samples_invalidated(reason) fires when a settings change makes earlier
    samples incomparable.
    """

    sample_accepted = pyqtSignal(float, float)
    samples_invalidated = pyqtSignal(str)

    def __init__(self, settings_getter, camera_owned, log_directory=None,
                 clock=time.monotonic, now=datetime.now, parent=None):
        super().__init__("Count statistics", parent)
        self._settings_getter = settings_getter
        self._camera_owned = camera_owned
        self._log_directory = Path(log_directory or _DEFAULT_LOG_DIRECTORY)
        self._clock = clock
        self._now = now

        self.statistics = RunningStatistics()
        self.accumulating = False
        self.latest_sample = None          # "Actual": newest accepted frame
        self._last_fingerprint = None
        self._last_frame_time = None
        self._settings = _settings_key(settings_getter())
        self._skip_next_frame = False      # first frame after a settings change
        self._gap_pending = False          # break the trend line after a pause
        self._elapsed_before = 0.0         # accumulated time of earlier runs
        self._run_started_at = None
        self._trend_origin = None
        self._trend = deque()              # (t, value); NaN value = gap
        self.log = None

        self._build_ui()
        self.refresh()

        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self.refresh)
        self._display_timer.start(DISPLAY_INTERVAL_MS)

    # ---- layout ---------------------------------------------------------

    def _build_ui(self):
        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        layout.addLayout(left, 0)

        grid = QGridLayout()
        self.value_labels = {}
        rows = [
            ("actual", "Actual"), ("mean", "Mean"), ("minimum", "Min"),
            ("maximum", "Max"), ("std", "Std deviation"),
            ("ratio", "Ratio max/min"), ("count", "Samples"),
            ("elapsed", "Elapsed"),
        ]
        # Two columns of four, so the panel stays short under the image.
        for index, (key, title) in enumerate(rows):
            row, column = index % 4, 2 * (index // 4)
            grid.addWidget(QLabel(f"{title}:"), row, column)
            label = QLabel("--")
            label.setMinimumWidth(150)
            grid.addWidget(label, row, column + 1)
            self.value_labels[key] = label
        left.addLayout(grid)

        buttons = QHBoxLayout()
        self.start_stop_button = QPushButton()
        self.start_stop_button.clicked.connect(self.toggle_accumulation)
        buttons.addWidget(self.start_stop_button)
        self.reset_button = QPushButton("Reset")
        self.reset_button.clicked.connect(lambda: self.reset("Statistics reset"))
        buttons.addWidget(self.reset_button)
        left.addLayout(buttons)

        self.log_check = QCheckBox("Log to CSV")
        self.log_check.setToolTip(
            "While accumulating, append each sample (timestamp, mean counts, "
            "exposure, binning, ROI) to data/count_log_<timestamp>.csv. "
            "Every row is flushed as it is written."
        )
        self.log_check.toggled.connect(self._on_log_toggled)
        left.addWidget(self.log_check)

        self.status_label = QLabel()
        self.note_label = QLabel("")
        self.note_label.setWordWrap(True)
        self.log_label = QLabel("")
        self.log_label.setWordWrap(True)
        for label in (self.status_label, self.note_label, self.log_label):
            left.addWidget(label)
        left.addStretch()

        self.trend_plot = pg.PlotWidget()
        self.trend_plot.setLabel("left", "Mean counts")
        self.trend_plot.setLabel("bottom", "Time (s)")
        self.trend_plot.showGrid(x=True, y=True)
        self.trend_curve = self.trend_plot.plot([], [], pen="y")
        layout.addWidget(self.trend_plot, 1)

        # Keep the live image the main thing in the column above it.
        self.setMaximumHeight(MAX_PANEL_HEIGHT_PX)

        self._update_button()

    def _update_button(self):
        if self.accumulating:
            style_run_button(self.start_stop_button, run_button.RUNNING, "Stop")
        else:
            style_run_button(self.start_stop_button, run_button.IDLE, "Start")

    # ---- frames ---------------------------------------------------------

    def add_frame(self, frame):
        """Take one live-view frame. Cheap: one pass over the pixels."""
        if self._camera_owned():
            # Suspended for an experiment; ignore a frame that slipped in.
            self._gap_pending = self.accumulating or self._gap_pending
            return
        self._check_settings()
        value, fingerprint = frame_sample(frame)
        if fingerprint == self._last_fingerprint:
            return  # the same frame again (the timer polls faster than frames)
        self._last_fingerprint = fingerprint
        if self._skip_next_frame:
            # Possibly taken with the old settings just before the change.
            self._skip_next_frame = False
            return
        self.latest_sample = value
        self._last_frame_time = self._clock()
        self.sample_accepted.emit(self._last_frame_time, value)
        if not self.accumulating:
            return
        self.statistics.add(value)
        t = self._clock() - self._trend_origin
        if self._gap_pending:
            self._trend.append((t, math.nan))
            self._gap_pending = False
        self._trend.append((t, value))
        while self._trend and self._trend[0][0] < t - TREND_WINDOW_S:
            self._trend.popleft()
        if self.log is not None:
            state = self._settings_getter()
            try:
                self.log.write(self._now(), value, state.exposure_s,
                               state.binning, state.acquisition_roi)
            except OSError as error:
                LOGGER.exception("Count log write failed")
                self._close_log()
                self.log_check.setChecked(False)
                self.note_label.setText(f"CSV logging stopped: {error}")

    def _check_settings(self):
        key = _settings_key(self._settings_getter())
        if key == self._settings:
            return
        change = _describe_change(self._settings, key)
        self._settings = key
        self._skip_next_frame = True
        self.latest_sample = None
        self.reset(f"Statistics reset: {change}")
        self.samples_invalidated.emit(change)

    @property
    def clock(self):
        """The clock sample_accepted times are on."""
        return self._clock

    # ---- accumulation ---------------------------------------------------

    def toggle_accumulation(self):
        if self.accumulating:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.accumulating:
            return
        now = self._clock()
        if self._trend_origin is None:
            self._trend_origin = now
        elif self._trend:
            self._gap_pending = True
        self._run_started_at = now
        self.accumulating = True
        if self.log_check.isChecked():
            self._open_log()
        self._update_button()
        self.refresh()

    def stop(self):
        if not self.accumulating:
            return
        self._elapsed_before += self._clock() - self._run_started_at
        self._run_started_at = None
        self.accumulating = False
        self._close_log()
        self._update_button()
        self.refresh()

    def reset(self, note="Statistics reset"):
        """Clear the statistics and trend; accumulation keeps its state."""
        had_data = self.statistics.count > 0 or self.accumulating
        self.statistics.reset()
        self._trend.clear()
        self._gap_pending = False
        self._elapsed_before = 0.0
        now = self._clock()
        self._trend_origin = now if self.accumulating else None
        self._run_started_at = now if self.accumulating else None
        if had_data:
            self.note_label.setText(f"{note} at {self._now():%H:%M:%S}")
        self.refresh()

    def elapsed_s(self):
        running = self._clock() - self._run_started_at if self.accumulating else 0.0
        return self._elapsed_before + running

    # ---- CSV log --------------------------------------------------------

    def _on_log_toggled(self, checked):
        if checked and self.accumulating:
            self._open_log()
        elif not checked:
            self._close_log()

    def _open_log(self):
        if self.log is not None:
            return
        path = self._log_directory / f"count_log_{self._now():%Y-%m-%d_%H-%M-%S}.csv"
        try:
            self.log = CountLog(path)
        except OSError as error:
            LOGGER.exception("Could not create count log %s", path)
            self.log_check.setChecked(False)
            self.note_label.setText(f"Could not create CSV log: {error}")
            return
        self.log_label.setText(f"Logging to {path.name}")

    def _close_log(self):
        if self.log is None:
            return
        self.log.close()
        self.log_label.setText(f"Log saved: {self.log.path.name}")
        self.log = None

    def close_log(self):
        """Close the CSV file (application shutdown)."""
        self._close_log()

    # ---- display --------------------------------------------------------

    def status_text(self):
        if self._camera_owned():
            return PAUSED_TEXT
        if not self.accumulating:
            return "Stopped"
        if (self._last_frame_time is None
                or self._clock() - self._last_frame_time > NO_FRAMES_AFTER_S):
            return "Accumulating — waiting for live-view frames"
        return "Accumulating"

    def refresh(self):
        """Update labels and trend (4 Hz timer, not per frame)."""
        if self._camera_owned():
            self._gap_pending = self.accumulating or self._gap_pending
        else:
            self._check_settings()
        s = self.statistics
        v = self.value_labels
        # Significant digits, not fixed decimals: ~20000 counts at the rig
        # reads "20012.3"; a dim or simulated signal keeps its variation.
        v["actual"].setText(_fmt(self.latest_sample, ".6g"))
        v["mean"].setText(_fmt(s.mean, ".7g"))
        v["minimum"].setText(_fmt(s.minimum, ".6g"))
        v["maximum"].setText(_fmt(s.maximum, ".6g"))
        v["std"].setText(
            "--" if not math.isfinite(s.std)
            else f"{s.std:.4g}  ({s.std_percent:.3f} %)"
        )
        v["ratio"].setText(_fmt(s.ratio, ".5f"))
        v["count"].setText(str(s.count))
        v["elapsed"].setText(_fmt_elapsed(self.elapsed_s()))
        self.status_label.setText(f"Status: {self.status_text()}")

        if self._trend:
            t, y = (np.array(c, dtype=float) for c in zip(*self._trend))
            self.trend_curve.setData(t, y, connect="finite")
            last = t[-1]
            self.trend_plot.setXRange(max(0.0, last - TREND_WINDOW_S),
                                      max(last, 1.0), padding=0)
        else:
            self.trend_curve.setData([], [])
