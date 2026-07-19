"""GUI-thread-safe ownership of the main live-view update timer."""

import threading

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot


class _TimerRequest:
    def __init__(self, was_running=None):
        self.was_running = was_running
        self.result = False
        self.complete = threading.Event()


class LiveViewTimerController(QObject):
    """Suspend and restore a QTimer from either the GUI or worker thread.

    The ownership framework treats this object only as a controller with
    ``suspend`` and ``resume`` methods.  Qt-specific thread marshalling stays
    in the GUI layer.
    """

    _suspend_requested = pyqtSignal(object)
    _resume_requested = pyqtSignal(object)

    def __init__(self, timer, interval_ms):
        super().__init__()
        self._timer = timer
        self._interval_ms = interval_ms
        self._suspend_requested.connect(self._suspend_on_gui_thread)
        self._resume_requested.connect(self._resume_on_gui_thread)

    def suspend(self):
        """Stop the timer and return whether it was active before suspension."""
        if QThread.currentThread() == self.thread():
            return self._suspend_timer()

        request = _TimerRequest()
        self._suspend_requested.emit(request)
        request.complete.wait()
        return request.result

    def resume(self, was_running):
        """Restart the timer only when it was active before suspension."""
        if not was_running:
            return

        if QThread.currentThread() == self.thread():
            self._resume_timer()
            return

        request = _TimerRequest(was_running=True)
        self._resume_requested.emit(request)
        request.complete.wait()

    @pyqtSlot(object)
    def _suspend_on_gui_thread(self, request):
        request.result = self._suspend_timer()
        request.complete.set()

    @pyqtSlot(object)
    def _resume_on_gui_thread(self, request):
        if request.was_running:
            self._resume_timer()
        request.complete.set()

    def _suspend_timer(self):
        was_running = self._timer.isActive()
        if was_running:
            self._timer.stop()
        return was_running

    def _resume_timer(self):
        self._timer.start(self._interval_ms)
