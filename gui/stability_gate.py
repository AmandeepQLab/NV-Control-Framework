"""Optional count stability check before an experiment starts (Qt side).

One StabilityMonitor per application, owned by MainWindow. It listens to
the main window's CountStatisticsPanel (sample_accepted /
samples_invalidated) -- the same per-frame mean counts the panel already
computes -- and never touches the camera. If the live view is off when a
check begins, the monitor starts it through the main window's normal
live-view start path and stops it again when the last check ends, before
the experiment is started (the experiment then takes the camera as usual).

Each experiment window drives a StabilityCheck through a
StabilityWaitController, which shows the wait line and the "Start anyway"
/ "Cancel" buttons. The decision rule is framework/stability.py.
"""

import logging
from collections import deque

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from framework import stability

LOGGER = logging.getLogger(__name__)

TICK_MS = 250  # evaluation and status-line rate


class StabilityCheck(QObject):
    """One wait for stable counts. Emits finished(start, record) once."""

    status_changed = pyqtSignal(str)
    finished = pyqtSignal(bool, object)

    def __init__(self, monitor, settings, clock):
        super().__init__()
        self.settings = settings
        self._monitor = monitor
        self._clock = clock
        self._started_at = clock()
        self._collecting_since = self._started_at
        self._samples = deque()
        self.evaluation = None
        self.done = False
        self.status = ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)
        self._timer.start(TICK_MS)

    def elapsed_s(self):
        return self._clock() - self._started_at

    def add_sample(self, t, value):
        self._samples.append((t, value))
        horizon = t - self.settings.window_s - stability.COVERAGE_SLACK_S
        while self._samples and self._samples[0][0] < horizon:
            self._samples.popleft()

    def invalidate(self, reason=""):
        """Settings changed: earlier samples no longer count."""
        self._samples.clear()
        self._collecting_since = self._clock()

    def _evaluate(self):
        times = [t for t, _ in self._samples]
        values = [v for _, v in self._samples]
        return stability.evaluate(
            times, values, self._clock(), self.settings, self._collecting_since
        )

    def tick(self):
        if self.done:
            return
        self.evaluation = self._evaluate()
        elapsed = self.elapsed_s()
        if self.evaluation.passed:
            self._finish(True, stability.OUTCOME_PASSED)
            return
        if elapsed >= self.settings.timeout_s:
            if self.settings.on_timeout == stability.ON_TIMEOUT_START:
                self._finish(True, stability.OUTCOME_TIMEOUT_STARTED)
            else:
                self._finish(False, stability.OUTCOME_TIMEOUT_CANCELLED)
            return
        self.status = stability.status_text(self.evaluation, self.settings, elapsed)
        self.status_changed.emit(self.status)

    def start_anyway(self):
        self._finish(True, stability.OUTCOME_USER_STARTED)

    def cancel(self, outcome=stability.OUTCOME_USER_CANCELLED):
        self._finish(False, outcome)

    def _finish(self, start, outcome):
        if self.done:
            return
        self.done = True
        self._timer.stop()
        self.evaluation = self._evaluate()
        result = stability.record(self.settings, outcome, self.evaluation, self.elapsed_s())
        LOGGER.info("Stability check finished: %s", result)
        # Release first: the live view is restored before the experiment
        # starts and takes the camera.
        self._monitor.release(self)
        self.finished.emit(bool(start), result)


class StabilityMonitor(QObject):
    """Shared sample source for every window's stability checks."""

    def __init__(self, count_panel, live_view_running, start_live_view, stop_live_view):
        super().__init__()
        self._clock = count_panel.clock
        self._live_view_running = live_view_running
        self._start_live_view = start_live_view
        self._stop_live_view = stop_live_view
        self._checks = []
        self._started_live_view = False
        count_panel.sample_accepted.connect(self._on_sample)
        count_panel.samples_invalidated.connect(self._on_invalidated)

    @property
    def started_live_view(self):
        """True while live view runs only because a check started it."""
        return self._started_live_view

    def begin(self, settings):
        """Start a check; live view is started if it was off."""
        if not self._checks and not self._live_view_running():
            self._start_live_view()
            self._started_live_view = bool(self._live_view_running())
        check = StabilityCheck(self, settings, self._clock)
        self._checks.append(check)
        return check

    def release(self, check):
        if check in self._checks:
            self._checks.remove(check)
        if not self._checks and self._started_live_view:
            self._started_live_view = False
            self._stop_live_view()

    def _on_sample(self, t, value):
        for check in list(self._checks):
            check.add_sample(t, value)

    def _on_invalidated(self, reason):
        for check in list(self._checks):
            check.invalidate(reason)


class StabilityWaitController(QObject):
    """Connects one window's wait bar to a StabilityCheck."""

    def __init__(self, monitor, wait_bar, parent=None):
        super().__init__(parent)
        self.monitor = monitor
        self.bar = wait_bar
        self.check = None
        self._on_start = None
        self._on_cancel = None
        self._on_status = None
        self.bar.start_anyway_button.clicked.connect(self.start_anyway)
        self.bar.cancel_button.clicked.connect(self.cancel)

    @property
    def active(self):
        return self.check is not None

    def begin(self, settings, on_start, on_cancel, on_status=None):
        """Wait for stable counts, then call on_start(record) or on_cancel(record)."""
        self._on_start, self._on_cancel = on_start, on_cancel
        self._on_status = on_status
        self.bar.show_waiting()
        check = self.monitor.begin(settings)
        self.check = check
        check.status_changed.connect(self._status)
        check.finished.connect(self._finished)
        check.tick()  # first status line at once, after the slots exist

    def _status(self, text):
        self.bar.set_status(text)
        if self._on_status is not None:
            self._on_status(text)

    def start_anyway(self):
        if self.check is not None:
            self.check.start_anyway()

    def cancel(self, outcome=stability.OUTCOME_USER_CANCELLED):
        if self.check is not None:
            self.check.cancel(outcome)

    def _finished(self, start, record):
        self.check = None
        self.bar.hide_waiting()
        callback = self._on_start if start else self._on_cancel
        self._on_start = self._on_cancel = self._on_status = None
        if callback is not None:
            callback(record)
