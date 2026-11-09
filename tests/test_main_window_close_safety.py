"""Hardware-free tests for MainWindow close safety while scans are running.

Workers are real QThreads blocked on an event, so "the worker finished"
arrives through the real cross-thread QThread.finished signal. MainWindow
is built without hardware (only QMainWindow.__init__ plus the close-state
it needs), as in test_magnet_experiment_lock.py.
"""

import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv)

import gui.main_window as main_window_module
from gui.main_window import MainWindow
from gui.odmr_window import ODMRWindow
from gui.zero_field_window import ZeroFieldWindow


class BlockingWorkerThread(QThread):
    """Stands in for an experiment worker thread until released."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def run(self):
        self.release.wait(10)


class FakeExperimentWindow:
    def __init__(self, thread):
        self.thread = thread
        self.stop_requests = 0

    def worker_thread(self):
        return self.thread

    def request_stop(self):
        self.stop_requests += 1


def _pump_until(condition, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        _APP.processEvents()
        time.sleep(0.01)
    return True


def _make_main_window():
    main = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(main)
    main._running_experiments = {}
    main.magnet_window = None
    main.odmr_window = None
    main.zero_field_window = None
    main._init_close_state()
    main.hw_manager = Mock()
    main.stop_stream = Mock()
    return main


class MainWindowCloseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.main = _make_main_window()
        self.threads = []

    def tearDown(self):
        for thread in self.threads:
            thread.release.set()
            thread.wait(5000)
        _pump_until(lambda: not self.main._worker_threads, timeout_s=2.0)
        self.main.deleteLater()
        _APP.processEvents()

    def _start_scan(self, name="Zero Field", swept=True):
        thread = BlockingWorkerThread()
        self.threads.append(thread)
        window = FakeExperimentWindow(thread)
        self.main._experiment_window_started(window, name, swept)
        thread.start()
        self.assertTrue(_pump_until(thread.isRunning))
        return window, thread

    def test_no_scan_running_closes_without_scan_dialog(self):
        with patch.object(self.main, "_ask_stop_and_close") as ask, \
             patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            closed = self.main.close()
        self.assertTrue(closed)
        ask.assert_not_called()
        self.main.hw_manager.shutdown.assert_called_once()

    def test_scan_running_cancel_keeps_app_open_and_scan_running(self):
        window, thread = self._start_scan()
        with patch.object(self.main, "_ask_stop_and_close", return_value=False), \
             patch.object(QMessageBox, "question") as question:
            closed = self.main.close()
        self.assertFalse(closed)
        question.assert_not_called()
        self.assertEqual(window.stop_requests, 0)
        self.main.hw_manager.shutdown.assert_not_called()
        self.assertTrue(thread.isRunning())
        self.assertFalse(self.main._closing_after_stop)

    def test_stop_and_close_defers_until_worker_thread_finishes(self):
        window, thread = self._start_scan()
        with patch.object(self.main, "_ask_stop_and_close", return_value=True), \
             patch.object(QMessageBox, "question") as question:
            closed = self.main.close()

            # Deferred: stop requested, nothing shut down yet.
            self.assertFalse(closed)
            self.assertEqual(window.stop_requests, 1)
            self.assertEqual(
                self.main.statusBar().currentMessage(), "Stopping scan — please wait…"
            )
            self.main.hw_manager.shutdown.assert_not_called()

            # The scan stops; once its thread exits, close proceeds.
            thread.release.set()
            self.assertTrue(
                _pump_until(lambda: self.main.hw_manager.shutdown.called)
            )
        self.main.hw_manager.shutdown.assert_called_once()
        question.assert_not_called()  # "Stop and Close" already confirmed
        self.assertFalse(thread.isRunning())
        self.assertEqual(self.main._worker_threads, {})
        self.assertFalse(self.main._close_timeout_timer.isActive())

    def test_close_attempts_while_stopping_do_not_reprompt(self):
        self._start_scan()
        with patch.object(self.main, "_ask_stop_and_close", return_value=True) as ask:
            self.main.close()
            self.assertFalse(self.main.close())
        ask.assert_called_once()

    def test_stops_every_running_experiment(self):
        zfe, _ = self._start_scan("Zero Field", True)
        odmr, _ = self._start_scan("ODMR", False)
        with patch.object(self.main, "_ask_stop_and_close", return_value=True):
            self.main.close()
        self.assertEqual((zfe.stop_requests, odmr.stop_requests), (1, 1))

    def test_timeout_closes_even_if_worker_never_finishes(self):
        _, thread = self._start_scan()
        with patch.object(main_window_module, "_CLOSE_WAIT_TIMEOUT_MS", 0), \
             patch.object(self.main, "_ask_stop_and_close", return_value=True), \
             patch.object(QMessageBox, "question") as question, \
             self.assertLogs("gui.main_window", level="WARNING"):
            self.main.close()
            self.assertTrue(
                _pump_until(lambda: self.main.hw_manager.shutdown.called)
            )
        self.assertTrue(thread.isRunning())  # closed anyway, as designed
        question.assert_not_called()

        # A worker that finishes after the forced close must not trigger a
        # second shutdown.
        thread.release.set()
        thread.wait(5000)
        _pump_until(lambda: not self.main._worker_threads, timeout_s=2.0)
        self.main.hw_manager.shutdown.assert_called_once()

    def test_registry_drops_thread_when_it_exits(self):
        _, thread = self._start_scan()
        self.assertIn(thread, self.main._worker_threads)
        thread.release.set()
        self.assertTrue(_pump_until(lambda: not self.main._worker_threads))


class ExperimentWindowCloseEventTests(unittest.TestCase):
    """closeEvent() on the real classes, with lightweight stand-ins for
    the window (constructing extra ZeroFieldWindows segfaults at shutdown
    in this environment -- see test_zero_field_window_save_toggle.py)."""

    def _close(self, window_class, running_attr, running):
        window = SimpleNamespace(**{running_attr: running}, request_stop=Mock())
        event = Mock()
        window_class.closeEvent(window, event)
        return window, event

    def test_running_scan_requests_stop_and_ignores_close(self):
        for window_class, attr in ((ZeroFieldWindow, "zero_field_running"),
                                   (ODMRWindow, "odmr_running")):
            with self.subTest(window=window_class.__name__):
                window, event = self._close(window_class, attr, True)
                window.request_stop.assert_called_once()
                event.ignore.assert_called_once()
                event.accept.assert_not_called()

    def test_idle_window_closes_without_dialog(self):
        for window_class, attr in ((ZeroFieldWindow, "zero_field_running"),
                                   (ODMRWindow, "odmr_running")):
            with self.subTest(window=window_class.__name__), \
                 patch.object(QMessageBox, "question") as question:
                window, event = self._close(window_class, attr, False)
                event.accept.assert_called_once()
                window.request_stop.assert_not_called()
                question.assert_not_called()


if __name__ == "__main__":
    unittest.main()
