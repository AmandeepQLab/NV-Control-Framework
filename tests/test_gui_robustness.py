"""Robustness (2026-11-26).

- Close right after a run: a finished run's exiting thread is not a scan.
- Hardware release: Pulse Streamer closed on exit, bounded, also after a
  failed start or a fatal error (simulator and mocks only).
- Window reuse: ODMR / Zero Field / Magnet are shown again, never doubled.
- Post-experiment magnet action without the Magnet window ever opened.
- Exit after an ODMR scan / a Zero Field queue, closing as the run ends
  (the real app with --sim, in a subprocess).

No real hardware: every device is simulated or a Mock.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication, QEvent, QtMsgType
from PyQt6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication(sys.argv[:1])

import gui.main_window as main_window_module  # noqa: E402
from gui.main_window import MainWindow, release_hardware  # noqa: E402
from gui.odmr_window import ODMRWindow  # noqa: E402
from gui.post_experiment_action import (  # noqa: E402
    POST_EXPERIMENT_ELECTRICAL_OFF,
    POST_EXPERIMENT_FIELD_ZERO,
)
from gui.widgets import collapsible_section  # noqa: E402
from gui import close_confirmation  # noqa: E402
import hardware.hardware_manager as hardware_manager_module  # noqa: E402
from hardware.hardware_manager import HardwareManager, call_with_timeout  # noqa: E402
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer  # noqa: E402
from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer  # noqa: E402
from tests.test_main_window_close_safety import (  # noqa: E402
    BlockingWorkerThread,
    FakeExperimentWindow,
    _make_main_window,
)

REPO = Path(__file__).resolve().parents[1]


def _pump_until(condition, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        _APP.processEvents()
        time.sleep(0.005)
    return True


def _pump_for(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)


def _dispose(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _running(thread):
    try:
        return thread.isRunning()
    except RuntimeError:
        return False


# =====================================================================
# 5. Close right after a run
# =====================================================================

class CloseRightAfterRunTests(unittest.TestCase):
    def setUp(self):
        self.main = _make_main_window()
        self.threads = []
        grace = patch.object(main_window_module, "_FINISHING_WORKER_GRACE_MS", 300)
        grace.start()
        self.addCleanup(grace.stop)

    def tearDown(self):
        for thread in self.threads:
            thread.release.set()
            thread.wait(5000)
        _pump_until(lambda: not self.main._worker_threads, 2.0)
        self.main._shut_down = True
        self.main.deleteLater()
        _APP.processEvents()

    def _run(self, finished):
        thread = BlockingWorkerThread()
        self.threads.append(thread)
        window = FakeExperimentWindow(thread)
        self.main._experiment_window_started(window, "Zero Field", True)
        thread.start()
        self.assertTrue(_pump_until(thread.isRunning))
        if finished:
            # The worker reported finished (run_finished), but its thread
            # is still exiting.
            self.main._experiment_window_finished(window)
        return window, thread

    def test_finished_run_waits_for_its_thread_then_closes_without_scan_dialog(self):
        _, thread = self._run(finished=True)
        with patch.object(self.main, "_ask_stop_and_close") as ask, \
             close_confirmation.auto_answer(True) as history:
            asked = len(history)
            self.assertFalse(self.main.close())  # deferred, not refused
            self.assertFalse(self.main.close())  # a second click: still waiting
            self.main.hw_manager.shutdown.assert_not_called()
            thread.release.set()
            self.assertTrue(_pump_until(lambda: self.main._shut_down, 3.0))
            titles = [c.title for c in history[asked:]]
        ask.assert_not_called()
        self.assertEqual(titles, ["Exit NV Control"])  # the normal Exit question, once
        self.main.hw_manager.shutdown.assert_called_once()

    def test_thread_still_alive_after_the_grace_counts_as_running(self):
        self._run(finished=True)
        with patch.object(self.main, "_ask_stop_and_close",
                          return_value=False) as ask:
            self.assertFalse(self.main.close())
            ask.assert_not_called()
            self.assertTrue(_pump_until(lambda: ask.called, 3.0))
        self.assertFalse(self.main._shut_down)  # cancelled: app stays open
        self.main.hw_manager.shutdown.assert_not_called()

    def test_a_running_scan_still_shows_the_dialog_at_once(self):
        self._run(finished=False)
        with patch.object(self.main, "_ask_stop_and_close",
                          return_value=False) as ask:
            self.assertFalse(self.main.close())
        ask.assert_called_once()

    def test_running_and_finishing_runs_together_show_the_dialog(self):
        self._run(finished=True)
        self._run(finished=False)
        with patch.object(self.main, "_ask_stop_and_close",
                          return_value=False) as ask:
            self.assertFalse(self.main.close())
        ask.assert_called_once()

    def test_already_exited_thread_is_pruned(self):
        _, thread = self._run(finished=True)
        thread.release.set()
        thread.wait(5000)  # exited; finished signal not yet delivered
        with patch.object(self.main, "_ask_stop_and_close") as ask, \
             patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.main.close())
        ask.assert_not_called()


# =====================================================================
# 6. Hardware release on exit (mocks and the simulator)
# =====================================================================

def _mock_hardware(order):
    def device(name, method):
        mock = Mock(name=name)
        getattr(mock, method).side_effect = lambda *a, **k: order.append(name)
        return mock
    return {
        "magnet": device("magnet.disable", "disable"),
        "power_supplies": {"X": device("ps.disconnect", "disconnect")},
        "pulse_streamer": device("pulse.close", "close"),
        "microwave": device("microwave.disconnect", "disconnect"),
        "camera": device("camera.disconnect", "disconnect"),
    }


class HardwareShutdownTests(unittest.TestCase):
    def _manager(self, hardware):
        manager = HardwareManager(config_manager=None)
        manager.hardware = hardware
        return manager

    def test_order_and_pulse_streamer_closed_once(self):
        order = []
        manager = self._manager(_mock_hardware(order))
        manager.shutdown()
        manager.shutdown()  # idempotent
        self.assertEqual(order, ["magnet.disable", "ps.disconnect", "pulse.close",
                                 "microwave.disconnect", "camera.disconnect"])

    def test_unreachable_pulse_streamer_does_not_hang(self):
        order = []
        hardware = _mock_hardware(order)
        never = threading.Event()
        hardware["pulse_streamer"].close.side_effect = lambda: never.wait(30)
        manager = self._manager(hardware)
        with patch.object(hardware_manager_module,
                          "PULSE_STREAMER_CLOSE_TIMEOUT_S", 0.3):
            start = time.monotonic()
            manager.shutdown()
            elapsed = time.monotonic() - start
        never.set()
        self.assertLess(elapsed, 2.0)
        self.assertIn("camera.disconnect", order)  # later steps still ran

    def test_one_failing_device_does_not_skip_the_rest(self):
        order = []
        hardware = _mock_hardware(order)
        hardware["pulse_streamer"].close.side_effect = RuntimeError("no route")
        hardware["microwave"].disconnect.side_effect = OSError("VISA gone")
        manager = self._manager(hardware)
        manager.shutdown()
        self.assertEqual(order, ["magnet.disable", "ps.disconnect",
                                 "camera.disconnect"])

    def test_relays_not_switched_if_the_magnet_could_not_be_switched_off(self):
        order = []
        hardware = _mock_hardware(order)
        hardware["magnet"].disable.side_effect = RuntimeError("serial timeout")
        manager = self._manager(hardware)
        manager.shutdown()
        hardware["pulse_streamer"].close.assert_not_called()
        self.assertEqual(order, ["ps.disconnect", "microwave.disconnect",
                                 "camera.disconnect"])

    def test_call_with_timeout_reports(self):
        self.assertTrue(call_with_timeout(lambda: None, 1.0))
        self.assertTrue(call_with_timeout(Mock(side_effect=ValueError), 1.0))
        event = threading.Event()
        self.assertFalse(call_with_timeout(lambda: event.wait(5), 0.1))
        event.set()

    def test_swabian_close_stops_streaming_and_releases_even_if_unreachable(self):
        streamer = SwabianPulseStreamer("fake", channel_map={"flipMF_X": 6})
        fake = Mock()
        fake.stream.side_effect = OSError("unreachable")
        fake.constant.side_effect = OSError("unreachable")
        streamer.ps = fake
        streamer.close()
        fake.constant.assert_called_once()
        self.assertIsNone(streamer.ps)
        streamer.close()  # second call: nothing sent
        fake.constant.assert_called_once()

    def test_sim_close_is_idempotent(self):
        streamer = SimPulseStreamer(channel_map={"flipMF_X": 6})
        streamer.connect()
        streamer.set_digital_output(6, True)
        streamer.close()
        self.assertFalse(streamer.connected)
        self.assertEqual(streamer.last_reset_channels, {6: 0})
        streamer.close()

    def test_release_hardware_only_once(self):
        window = Mock(_shut_down=False)
        release_hardware(window)
        release_hardware(window)
        window.hw_manager.shutdown.assert_called_once()

    def test_fatal_qt_message_releases_hardware(self):
        window = Mock(_shut_down=False)
        handler = main_window_module.install_fatal_error_hook(lambda: window)
        try:
            with patch.object(sys, "stderr"):
                handler(QtMsgType.QtWarningMsg, None, "just a warning")
                window.hw_manager.shutdown.assert_not_called()
                handler(QtMsgType.QtFatalMsg, None, "Traceback ...")
            window.hw_manager.shutdown.assert_called_once()
        finally:
            from PyQt6.QtCore import qInstallMessageHandler
            qInstallMessageHandler(None)

    def test_failed_construction_releases_hardware(self):
        with patch.object(MainWindow, "_build", side_effect=RuntimeError("boom")), \
             patch.object(HardwareManager, "shutdown") as shutdown:
            with self.assertRaises(RuntimeError):
                MainWindow(sim=True)
        shutdown.assert_called_once()


# =====================================================================
# 7 + 8. Real MainWindow (sim): window reuse, post-experiment action
# =====================================================================

class SimMainWindowTests(unittest.TestCase):
    def setUp(self):
        collapsible_section.disable_state_persistence()
        self.main = MainWindow(sim=True)
        self.magnet = self.main.hardware["magnet"]
        self.addCleanup(self._close_main)

    def _close_main(self):
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.main.close()
        _dispose(self.main)

    def test_odmr_window_is_reused_hidden_or_minimised(self):
        self.main.open_odmr_window()
        window = self.main.odmr_window
        window.close()  # idle: hides
        self.assertFalse(window.isVisible())
        self.main.open_odmr_window()
        self.assertIs(self.main.odmr_window, window)
        self.assertTrue(window.isVisible())
        window.showMinimized()
        _APP.processEvents()
        self.main.open_odmr_window()
        self.assertIs(self.main.odmr_window, window)
        self.assertFalse(window.isMinimized())

    def test_zero_field_and_magnet_windows_are_reused(self):
        self.main.open_zero_field_window()
        zero_field = self.main.zero_field_window
        zero_field.close()
        self.main.open_zero_field_window()
        self.assertIs(self.main.zero_field_window, zero_field)
        self.assertTrue(zero_field.isVisible())
        self.main.open_magnet_window()
        magnet_window = self.main.magnet_window
        magnet_window.showMinimized()
        _APP.processEvents()
        self.main.open_magnet_window()
        self.assertIs(self.main.magnet_window, magnet_window)
        self.assertFalse(magnet_window.isMinimized())

    def test_no_second_instance_while_a_scan_runs(self):
        self.main.open_odmr_window()
        window = self.main.odmr_window
        window.run_started.emit()  # registered as running
        self.assertIn(window, self.main._running_experiments)
        window.showMinimized()
        _APP.processEvents()
        for _ in range(3):
            self.main.open_odmr_window()
        self.assertIs(self.main.odmr_window, window)
        # Shown ones only: hidden windows of earlier tests' apps may linger.
        odmr_windows = [w for w in QApplication.topLevelWidgets()
                        if isinstance(w, ODMRWindow) and not w.isHidden()]
        self.assertEqual(odmr_windows, [window])
        window.run_finished.emit()

    def test_post_experiment_action_without_magnet_window(self):
        self.assertIsNone(self.main.magnet_window)
        self.magnet.enable()
        with patch.object(self.magnet, "set_vector") as set_vector:
            self.main.experiment_started.emit("ODMR", False)
            self.main.experiment_finished.emit()
        set_vector.assert_called_once_with(bx=0.0, by=0.0, bz=0.0)
        self.assertIsNone(self.main.magnet_window)

    def test_electrical_off_without_magnet_window(self):
        self.main.post_experiment_action.set_action(POST_EXPERIMENT_ELECTRICAL_OFF)
        self.magnet.enable()
        self.main.experiment_finished.emit()
        self.assertFalse(self.magnet.is_enabled())

    def test_action_runs_once_after_overlapping_runs_and_a_queue(self):
        self.magnet.enable()
        first, second = object(), object()
        with patch.object(self.magnet, "set_vector") as set_vector:
            self.main._running_experiments[first] = ("ODMR", False)
            self.main._running_experiments[second] = ("Zero Field", True)
            self.main.experiment_started.emit("ODMR + Zero Field", True)
            self.main._experiment_window_finished(first)
            set_vector.assert_not_called()  # one still running (a queue)
            self.main._experiment_window_finished(second)
        set_vector.assert_called_once()

    def test_magnet_window_shows_and_changes_the_same_setting(self):
        self.main.post_experiment_action.set_action(POST_EXPERIMENT_ELECTRICAL_OFF)
        self.main.open_magnet_window()
        combo = self.main.magnet_window.post_experiment_combo
        self.assertEqual(combo.currentText(), POST_EXPERIMENT_ELECTRICAL_OFF)
        combo.setCurrentText(POST_EXPERIMENT_FIELD_ZERO)
        self.assertEqual(self.main.post_experiment_action.action(),
                         POST_EXPERIMENT_FIELD_ZERO)
        # Applied once (by MainWindow), not again by the open window.
        self.magnet.enable()
        with patch.object(self.magnet, "set_vector") as set_vector:
            self.main.experiment_started.emit("ODMR", False)
            self.main.experiment_finished.emit()
        set_vector.assert_called_once()
        self.assertIn("calibrated 0 G",
                      self.main.magnet_window.status_label.text())

    def test_no_action_after_shutdown(self):
        self.magnet.enable()
        self.main._shut_down = True
        with patch.object(self.magnet, "set_vector") as set_vector:
            self.main.experiment_finished.emit()
        set_vector.assert_not_called()
        self.main._shut_down = False

    def test_exit_closes_the_sim_pulse_streamer(self):
        pulse = self.main.hardware["pulse_streamer"]
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.main.close())
        self.assertTrue(pulse.closed)
        self.assertFalse(pulse.connected)


# =====================================================================
# 9. The real app (--sim) exits cleanly right after a run
# =====================================================================

_DRIVER = textwrap.dedent(
    """
    import os, sys
    sys.argv = ["main.py", "--sim"]
    sys.path.insert(0, os.getcwd())
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox
    import gui.main_window as mw

    MODE = os.environ["EXIT_TEST_MODE"]
    OUT = os.environ["EXIT_TEST_OUT"]
    original_show = mw.MainWindow.show
    dialogs = []

    def say(*args):
        print("EXIT-TEST:", *args, flush=True)

    def show(self):
        original_show(self)
        QTimer.singleShot(300, lambda: setup(self))

    def close_now(win):
        say("run ended; live worker threads", len(win._worker_threads))
        QTimer.singleShot(50, answer)
        win.close()

    def answer():
        box = QApplication.activeModalWidget()
        if isinstance(box, QMessageBox):
            dialogs.append(box.windowTitle())
            say("dialog", box.windowTitle())
            yes = box.button(QMessageBox.StandardButton.Yes)
            (yes or box.buttons()[0]).click()
        QTimer.singleShot(50, answer)

    def setup(win):
        QTimer.singleShot(60000, watchdog)
        if MODE == "odmr":
            win.open_odmr_button.click()
            w = win.odmr_window
            p = w.panel
            p.steps_spin.setValue(5)
            p.avg_spin.setValue(2)
            p.repeat_spin.setValue(1)
            w.run_finished.connect(lambda: close_now(win))
            p.run_stop_button.click()
        else:
            win.open_zero_field_button.click()
            z = win.zero_field_window
            z.output_directory = OUT
            z.widget.set_output_directory(OUT)
            f = z.widget
            f.field_start_spin.setValue(-1.0)
            f.field_stop_spin.setValue(1.0)
            f.field_points_spin.setValue(3)
            f.settling_time_spin.setValue(0)
            f.post_flip_settling_spin.setValue(0)
            z.add_to_queue()
            z.add_to_queue()
            z.run_finished.connect(lambda: close_now(win))
            z.run_queue()

    def watchdog():
        say("still running")
        os._exit(3)

    mw.MainWindow.show = show
    try:
        mw.main()
    finally:
        # Close confirmations are auto-answered "close" (environment, see
        # tests/conftest.py); history lists every one asked.
        from gui import close_confirmation
        say("dialogs", dialogs)
        say("confirmations", [c.title for c in close_confirmation.history])
    """
)


def _run_app(mode, out_dir, ui_state):
    env = dict(os.environ, EXIT_TEST_MODE=mode, EXIT_TEST_OUT=out_dir,
               NV_CONTROL_AUTO_CONFIRM_CLOSE="yes",
               QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8",
               NV_CONTROL_UI_STATE_FILE=ui_state)
    env.pop("NV_ODMR_TIMING", None)
    env.pop("NV_ZFE_TIMING", None)
    return subprocess.run(
        [sys.executable, "-c", _DRIVER], cwd=REPO, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )


class ExitRightAfterRunTests(unittest.TestCase):
    def _check(self, mode):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_app(mode, tmp, os.path.join(tmp, "ui.ini"))
        markers = [line for line in result.stdout.splitlines()
                   if line.startswith("EXIT-TEST:")]
        detail = "\n".join(markers) + "\n" + result.stderr[-3000:]
        self.assertEqual(result.returncode, 0, detail)
        self.assertIn("Hardware shutdown complete.", result.stdout, detail)
        self.assertIn("EXIT-TEST: dialogs []", markers, detail)  # nothing blocked
        self.assertIn("EXIT-TEST: confirmations ['Exit NV Control']", markers, detail)
        self.assertNotIn("Scan in progress", result.stdout, detail)

    def test_exit_right_after_an_odmr_scan(self):
        self._check("odmr")

    def test_exit_right_after_a_zero_field_queue(self):
        self._check("zfe_queue")


if __name__ == "__main__":
    unittest.main()
