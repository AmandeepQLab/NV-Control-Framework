"""Console and session-log output (framework/logging_setup.py).

- Console: short "HH:MM:SS  Source  message" lines at INFO+ (DEBUG with
  verbose); third-party libraries only from WARNING; summary blocks without
  prefix; no duplicate lines; warnings and errors always shown.
- Session log file: DEBUG+, rotation keeps the newest 30.
- Startup summary (OK / FAILED / SIM) and shutdown summary (all OK, one
  FAILED, one TIMEOUT, magnet-failure SKIPPED).
- Pulse Streamer: the client's connect notice is captured and summarised;
  "sequence streamed" is DEBUG only; the client's own error prints are not
  captured.
- ODMR console lines: start line and 25 % progress.
- Tests write no logs into data/ or logs/.

Mocks and simulators only.
"""

import contextlib
import io
import logging
import os
import re
import tempfile
import threading
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import hardware.hardware_manager as hardware_manager_module
import hardware.pulse_streamer.swabian_pulse_streamer as swabian_module
from framework import logging_setup
from framework.logging_setup import (
    format_block,
    format_duration,
    format_status_line,
    log_block,
    prune_session_logs,
    setup_logging,
    teardown_logging,
)
from hardware.hardware_manager import HardwareManager
from hardware.pulse_streamer.swabian_pulse_streamer import (
    SwabianPulseStreamer,
    parse_client_notice,
)

REPO = Path(__file__).resolve().parents[1]
LINE = re.compile(r"^\d\d:\d\d:\d\d  (.{15}) (.*)$")

CLIENT_NOTICE = (
    "Connect to Pulse Streamer via JSON-RPC.\n"
    "IP / Hostname: 132.64.56.138\n"
    "Pulse Streamer 8/2 firmware: v1.7.2\n"
    "Client software: v1.6.0\n"
    "The Pulse Streamer 8/2 firmware is more up to date than your client "
    "software. We recommend updating your client software.\n"
    "For detailed information visit https://www.swabianinstruments.com/\n"
)


class LoggingTestCase(unittest.TestCase):
    """setup_logging() into a StringIO console and a temp log directory."""

    verbose = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.console = io.StringIO()
        self.log_path = setup_logging(verbose=self.verbose, log_dir=self.tmp.name,
                                      stream=self.console)
        self.addCleanup(teardown_logging)

    def console_lines(self):
        return [line for line in self.console.getvalue().splitlines() if line]

    def file_text(self):
        for handler in logging.getLogger().handlers:
            handler.flush()
        return Path(self.log_path).read_text(encoding="utf-8")


class ConsoleAndFileTests(LoggingTestCase):
    def test_console_format_and_levels(self):
        logging.getLogger("hardware.microwave.sg386").info("Connected (s/n 002306)")
        logging.getLogger("hardware.microwave.sg386").debug("IDN raw text")
        lines = self.console_lines()
        self.assertEqual(len(lines), 1)
        match = LINE.match(lines[0])
        self.assertIsNotNone(match, lines[0])
        self.assertEqual(match.group(1).strip(), "SG386")
        self.assertEqual(match.group(2), "Connected (s/n 002306)")
        text = self.file_text()
        self.assertIn("DEBUG   hardware.microwave.sg386", text)
        self.assertIn("IDN raw text", text)

    def test_third_party_info_hidden_but_warnings_and_errors_shown(self):
        logging.getLogger("pyqtgraph.something").info("library chatter")
        logging.getLogger("h5py._conv").warning("library warning")
        logging.getLogger("tinyrpc").error("library error")
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("a deprecation", DeprecationWarning)
        console = self.console.getvalue()
        self.assertNotIn("library chatter", console)
        self.assertIn("WARNING: library warning", console)
        self.assertIn("ERROR: library error", console)
        self.assertIn("a deprecation", console)
        self.assertIn("library chatter", self.file_text())

    def test_error_with_traceback_is_short_on_console_full_in_file(self):
        try:
            raise OSError("VISA timeout")
        except OSError:
            logging.getLogger("hardware.hardware_manager").exception("SG386 close failed")
        lines = self.console_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("ERROR: SG386 close failed (OSError: VISA timeout)", lines[0])
        self.assertIn("Traceback (most recent call last)", self.file_text())

    def test_summary_block_has_no_prefix(self):
        log_block(format_block("Title", [("SG386", "OK", "fine")], "footer"))
        lines = self.console_lines()
        self.assertEqual(lines, ["Title", "-" * 60,
                                 "SG386           OK      fine", "-" * 60, "footer"])

    def test_no_duplicate_lines_when_set_up_twice(self):
        setup_logging(log_dir=self.tmp.name, stream=self.console)
        logging.getLogger("gui.main_window").info("once")
        self.assertEqual(self.console.getvalue().count("once"), 1)

    def test_log_file_name(self):
        self.assertRegex(Path(self.log_path).name,
                         r"^nv_control_\d{4}-\d\d-\d\d_\d{6}(_\d+)?\.log$")


class VerboseTests(LoggingTestCase):
    verbose = True

    def test_debug_on_console(self):
        logging.getLogger("hardware.pulse_streamer.swabian_pulse_streamer").debug(
            "Sequence streamed to hardware")
        self.assertIn("Sequence streamed to hardware", self.console.getvalue())


class RotationTests(unittest.TestCase):
    def test_keeps_newest_30(self):
        with tempfile.TemporaryDirectory() as tmp:
            for day in range(1, 36):
                Path(tmp, f"nv_control_2026-10-{day:02d}_080000.log").write_text("x")
            Path(tmp, "other.txt").write_text("kept")
            removed = prune_session_logs(tmp)
            remaining = sorted(p.name for p in Path(tmp).glob("nv_control_*.log"))
            self.assertEqual(len(removed), 5)
            self.assertEqual(len(remaining), 30)
            self.assertEqual(remaining[0], "nv_control_2026-10-06_080000.log")
            self.assertTrue(Path(tmp, "other.txt").exists())

    def test_setup_prunes_with_its_own_file_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            for day in range(1, 31):
                Path(tmp, f"nv_control_2026-10-{day:02d}_080000.log").write_text("x")
            path = setup_logging(log_dir=tmp, stream=io.StringIO())
            try:
                names = sorted(p.name for p in Path(tmp).glob("nv_control_*.log"))
                self.assertEqual(len(names), 30)
                self.assertIn(Path(path).name, names)
                self.assertNotIn("nv_control_2026-10-01_080000.log", names)
            finally:
                teardown_logging()


class FormatTests(unittest.TestCase):
    def test_status_line_and_duration(self):
        self.assertEqual(format_status_line("Pulse Streamer", "TIMEOUT", "no answer"),
                         "Pulse Streamer  TIMEOUT no answer")
        self.assertEqual(format_status_line("Channels", "", "laser 1"),
                         "Channels                laser 1")
        self.assertEqual(format_duration(3.24), "3.2 s")
        self.assertEqual(format_duration(810), "13 min 30 s")
        self.assertEqual(format_duration(7500), "2 h 05 min")


# =====================================================================
# Startup summary
# =====================================================================

class StartupSummaryTests(unittest.TestCase):
    def _manager(self, force_sim=False):
        manager = HardwareManager(SimpleNamespace(config_path="config/setupInfo.json"),
                                  force_sim=force_sim)
        manager._startup_rf_off = True
        manager._init_step = None
        return manager

    def test_real_devices_ok(self):
        manager = self._manager()
        sg386 = SimpleNamespace(idn="Stanford Research Systems,SG386,s/n002306,ver1.23.26")
        pulse = SimpleNamespace(ip_address="132.64.56.138", firmware_version="1.7.2",
                                client_version="1.6.0",
                                version_note="newer client software available, see log")
        E3632A = type("E3632A", (), {})
        axes = [SimpleNamespace(name=n, flip_channel=c) for n, c in (("X", 6), ("Y", 7), ("Z", 3))]
        manager.hardware = {
            "channels": {"greenLaser": 1, "MW": 2, "detector": 5, "flipMF_X": 6},
            "microwave": sg386, "camera": type("AndorNeoAndor3", (), {})(),
            "pulse_streamer": pulse,
            "power_supplies": {"X": E3632A(), "Y": E3632A(), "Z": E3632A()},
            "magnet": SimpleNamespace(axes=axes),
        }
        lines = [format_status_line(*row) for row in manager.startup_rows()]
        self.assertEqual(lines, [
            "SG386           OK      s/n 002306, fw 1.23.26, RF off",
            "Camera          OK      Andor Neo (andor3)",
            "Pulse Streamer  OK      132.64.56.138, fw v1.7.2 (client v1.6.0; newer "
            "client software available, see log)",
            "Power supply X  OK      E3632A",
            "Power supply Y  OK      E3632A",
            "Power supply Z  OK      E3632A",
            "Magnet          OK      relays X ch6, Y ch7, Z ch3",
            "Channels                laser 1, MW 2, detector 5",
        ])

    def test_failed_device_and_not_started(self):
        manager = self._manager()
        manager.hardware = {"channels": {"MW": 2},
                            "microwave": SimpleNamespace(idn="x,SG386,s/n1,ver2")}
        manager._init_step = "Camera"
        rows = manager.startup_rows(error=OSError("Andor SDK: no camera found"))
        self.assertEqual(rows[1], ("Camera", "FAILED",
                                   "OSError: Andor SDK: no camera found (see log)"))
        self.assertEqual(rows[2], ("Pulse Streamer", "-", "not started"))
        self.assertEqual(rows[6], ("Magnet", "-", "not started"))

    def test_rf_off_failure_at_startup_is_a_warning(self):
        manager = self._manager()
        manager._startup_rf_off = False
        manager.hardware = {"microwave": SimpleNamespace(idn="x,SG386,s/n1,ver2")}
        self.assertEqual(manager.startup_rows()[0],
                         ("SG386", "WARN", "s/n 1, fw 2, RF off failed (see log)"))

    def test_sim_initialize_logs_one_block(self):
        from config.config_manager import ConfigManager
        config = ConfigManager("config/setupInfo.json")
        config.load()
        manager = HardwareManager(config, force_sim=True)
        with self.assertLogs("nv_control.summary", level="INFO") as logs:
            manager.initialize()
        self.addCleanup(manager.shutdown)
        block = logs.output[0]
        self.assertIn("[--sim: simulated hardware]", block)
        for row in ("SG386           SIM     simulated, RF off",
                    "Camera          SIM     simulated",
                    "Pulse Streamer  SIM     simulated",
                    "Power supply X  SIM     simulated",
                    "Magnet          SIM     relays X ch6, Y ch7, Z ch3",
                    "Channels                laser 1, MW 2, detector 5"):
            self.assertIn(row, block)

    def test_initialize_failure_still_logs_the_summary(self):
        from config.config_manager import ConfigManager
        config = ConfigManager("config/setupInfo.json")
        config.load()
        manager = HardwareManager(config, force_sim=True)
        with patch("hardware.hardware_manager.SimCamera.connect",
                   side_effect=RuntimeError("camera exploded")), \
                self.assertLogs("nv_control.summary", level="WARNING") as logs, \
                self.assertRaises(RuntimeError):
            manager.initialize()
        self.addCleanup(manager.shutdown)
        self.assertIn("Camera          FAILED  RuntimeError: camera exploded (see log)",
                      logs.output[0])


# =====================================================================
# Shutdown summary
# =====================================================================

def _devices():
    return {
        "magnet": Mock(spec=["disable"]),
        "power_supplies": {axis: Mock(spec=["disconnect"]) for axis in "XYZ"},
        "pulse_streamer": Mock(spec=["close"]),
        "microwave": Mock(spec=["rf_off", "is_rf_on", "close"], **{"is_rf_on.return_value": False}),
        "camera": Mock(spec=["close"]),
    }


class ShutdownSummaryTests(unittest.TestCase):
    def _shutdown(self, hardware, level="INFO"):
        manager = HardwareManager(config_manager=None)
        manager.hardware = hardware
        with self.assertLogs("nv_control.summary", level=level) as logs:
            manager.shutdown()
        return manager.shutdown_rows, logs.output[0]

    def test_all_ok(self):
        rows, block = self._shutdown(_devices())
        self.assertEqual([format_status_line(*row) for row in rows], [
            "Magnet          OK      coils 0 A, outputs off",
            "Power supply X  OK      disconnected",
            "Power supply Y  OK      disconnected",
            "Power supply Z  OK      disconnected",
            "Pulse Streamer  OK      outputs LOW, sequence stopped, closed",
            "SG386           OK      RF off (read back), closed",
            "Camera          OK      acquisition stopped, closed",
        ])
        self.assertIn("NV Control shutdown", block)
        self.assertRegex(block, r"Shutdown complete in \d+\.\d s   \(log: ")
        self.assertTrue(block.startswith("INFO:"))

    def test_one_failed(self):
        hardware = _devices()
        hardware["camera"].close.side_effect = OSError("SDK error 13")
        rows, block = self._shutdown(hardware, level="WARNING")
        self.assertEqual(rows[-1], ("Camera", "FAILED", "close() failed: OSError: SDK error 13"))
        self.assertEqual(rows[-2][1], "OK")  # SG386 unaffected
        self.assertTrue(block.startswith("WARNING:"))

    def test_one_timeout(self):
        hardware = _devices()
        never = threading.Event()
        hardware["pulse_streamer"].close.side_effect = lambda: never.wait(30)
        with patch.object(hardware_manager_module, "PULSE_STREAMER_CLOSE_TIMEOUT_S", 0.2):
            rows, _block = self._shutdown(hardware, level="WARNING")
        never.set()
        self.assertEqual(rows[4], ("Pulse Streamer", "TIMEOUT",
                                   "no answer within 0.2 s (see log)"))
        self.assertEqual([r[0] for r in rows][-2:], ["SG386", "Camera"])

    def test_pulse_streamer_skipped_when_magnet_fails(self):
        hardware = _devices()
        hardware["magnet"].disable.side_effect = RuntimeError("serial timeout")
        rows, _block = self._shutdown(hardware, level="WARNING")
        self.assertEqual(rows[0], ("Magnet", "FAILED", "RuntimeError: serial timeout"))
        self.assertEqual(rows[4][:2], ("Pulse Streamer", "SKIPPED"))

    def test_rf_still_on_is_failed(self):
        hardware = _devices()
        hardware["microwave"].is_rf_on.return_value = True
        rows, _block = self._shutdown(hardware, level="WARNING")
        self.assertEqual(rows[5], ("SG386", "FAILED", "RF still ON (read back)"))

    def test_pulse_streamer_close_problems_reported(self):
        hardware = _devices()
        hardware["pulse_streamer"] = SimpleNamespace(
            close=lambda: None, close_problems=["stop failed: timeout"])
        rows, _block = self._shutdown(hardware, level="WARNING")
        self.assertEqual(rows[4], ("Pulse Streamer", "FAILED", "stop failed: timeout"))


# =====================================================================
# Pulse Streamer output
# =====================================================================

class _FakeClient:
    """Prints the real client's connect notice, like PulseStreamer()."""

    def __init__(self, ip):
        print(CLIENT_NOTICE, end="")

    def createSequence(self):
        from pulsestreamer import Sequence
        return Sequence()

    def stream(self, seq, **kwargs):
        pass


class PulseStreamerOutputTests(LoggingTestCase):
    def test_connect_notice_captured_and_summarised(self):
        streamer = SwabianPulseStreamer("132.64.56.138", channel_map={"MW": 2})
        stdout = io.StringIO()
        with patch.object(swabian_module, "PulseStreamer", _FakeClient), \
                contextlib.redirect_stdout(stdout):
            streamer.connect()
        self.assertEqual(stdout.getvalue(), "")  # nothing printed directly
        self.assertEqual(self.console.getvalue(), "")  # DEBUG only
        self.assertEqual((streamer.firmware_version, streamer.client_version),
                         ("1.7.2", "1.6.0"))
        self.assertEqual(streamer.version_note, "newer client software available, see log")
        self.assertIn("We recommend updating your client software", self.file_text())

    def test_firmware_update_note(self):
        text = ("Pulse Streamer 8/2 firmware: v1.5.0\nClient software: v1.7.0\n"
                "Your client software is more up to date than the Pulse Streamer 8/2 "
                "firmware. We recommend updating the firmware of your Pulse Streamer 8/2.\n")
        self.assertEqual(parse_client_notice(text),
                         ("1.5.0", "1.7.0", "firmware update available, see log"))

    def test_sequence_streamed_is_debug_only_and_client_errors_not_captured(self):
        from sequencing.pulse_sequence import PulseSequence
        streamer = SwabianPulseStreamer("ip", channel_map={"MW": 2})
        client = _FakeClient.__new__(_FakeClient)
        client.stream = lambda seq, **kwargs: print("Binary transfer failed with error code: 7")
        streamer.ps = client
        sequence = PulseSequence()
        sequence.add_pulse(channel=2, start_ns=0, duration_ns=1000)
        streamer.load_sequence(sequence)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            streamer.run(n_runs=1)
        self.assertNotIn("Sequence streamed", self.console.getvalue())
        self.assertIn("Sequence streamed to hardware", self.file_text())
        # The client's own error print is not swallowed.
        self.assertIn("Binary transfer failed", stdout.getvalue())


class _ProblemClient(_FakeClient):
    """Connect output with a warning and an error among the notice lines."""

    def __init__(self, ip):
        print(CLIENT_NOTICE, end="")
        print("Warning: trigger rearm not supported by this firmware")
        print("Binary transfer failed with error code: 7")


class CapturedThirdPartyProblemsTests(LoggingTestCase):
    def test_captured_warning_and_error_reach_the_console(self):
        streamer = SwabianPulseStreamer("132.64.56.138", channel_map={"MW": 2})
        with patch.object(swabian_module, "PulseStreamer", _ProblemClient), \
                contextlib.redirect_stdout(io.StringIO()):
            streamer.connect()
        console = self.console.getvalue()
        self.assertIn("WARNING: Pulse Streamer client reported at connect:", console)
        self.assertIn("Warning: trigger rearm not supported by this firmware", console)
        self.assertIn("Binary transfer failed with error code: 7", console)
        # The ordinary version notice is not repeated on the console.
        self.assertNotIn("We recommend updating your client software", console)
        self.assertIn("We recommend updating your client software", self.file_text())

    def test_plain_notice_is_not_a_problem(self):
        self.assertEqual(swabian_module.problem_lines(CLIENT_NOTICE), [])


# =====================================================================
# Qt's own messages
# =====================================================================

class QtMessageRoutingTests(LoggingTestCase):
    def test_levels_and_category(self):
        from PyQt6.QtCore import QtMsgType
        from framework.logging_setup import log_qt_message
        context = SimpleNamespace(category="qt.qpa.fonts")
        log_qt_message(QtMsgType.QtDebugMsg, context, "qt debug text")
        log_qt_message(QtMsgType.QtInfoMsg, None, "qt info text")
        log_qt_message(QtMsgType.QtWarningMsg, context, "font directory missing")
        log_qt_message(QtMsgType.QtCriticalMsg, None, "qt critical text")
        log_qt_message(QtMsgType.QtFatalMsg, None, "qt fatal text")
        console = self.console.getvalue()
        self.assertNotIn("qt debug text", console)
        self.assertNotIn("qt info text", console)
        self.assertRegex(console, r"\d\d:\d\d:\d\d  Qt\s+WARNING: font directory missing")
        self.assertIn("ERROR: qt critical text", console)
        self.assertIn("ERROR: qt fatal text", console)
        text = self.file_text()
        self.assertIn("DEBUG   qt.qpa.fonts", text)
        self.assertIn("qt info text", text)
        self.assertIn("WARNING qt.qpa.fonts", text)

    def test_real_qt_warning_end_to_end(self):
        from PyQt6.QtCore import qInstallMessageHandler, qWarning
        from framework.logging_setup import log_qt_message
        qInstallMessageHandler(log_qt_message)
        try:
            qWarning(b"real Qt warning")
        finally:
            qInstallMessageHandler(None)
        self.assertIn("WARNING: real Qt warning", self.console.getvalue())

    def test_main_window_hook_routes_and_still_releases_on_fatal(self):
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
        import gui.main_window as main_window_module
        window = Mock(_shut_down=False)
        handler = main_window_module.install_fatal_error_hook(lambda: window)
        try:
            handler(QtMsgType.QtWarningMsg, None, "a Qt warning")
            window.hw_manager.shutdown.assert_not_called()
            handler(QtMsgType.QtFatalMsg, None, "Traceback ...")
        finally:
            qInstallMessageHandler(None)
        window.hw_manager.shutdown.assert_called_once()
        self.assertIn("WARNING: a Qt warning", self.console.getvalue())
        self.assertIn("ERROR: Traceback ...", self.console.getvalue())


# =====================================================================
# ODMR console lines
# =====================================================================

class ODMRLinesTests(unittest.TestCase):
    def test_field_text(self):
        from gui.odmr_worker import field_text
        self.assertEqual(field_text({"magnet_field_mode": "field_zero",
                                     "magnet_commanded_vector_gauss": [0.0, 0.0, 0.0]}),
                         "calibrated zero (0, 0, 0) G")
        self.assertEqual(field_text({"magnet_field_mode": "custom_vector",
                                     "magnet_field_description": "4 G along [0.707, 0.707, 0]"}),
                         "4 G along [0.707, 0.707, 0]")
        self.assertEqual(field_text({"magnet_field_mode": "leave_as_is",
                                     "magnet_available": True, "magnet_enabled": False}),
                         "left as is (coils off)")
        self.assertIsNone(field_text(None))

    def test_progress_every_quarter(self):
        from gui.odmr_worker import ODMRWorker
        worker = SimpleNamespace(freqs=list(range(10)), n_avg=2, average_index=0, freq_index=0)
        with self.assertLogs("gui.odmr_worker", level="INFO") as logs:
            for average in range(2):
                for point in range(1, 11):
                    worker.average_index, worker.freq_index = average, point
                    ODMRWorker._log_progress(worker)
        self.assertEqual([line.split(":", 2)[2] for line in logs.output], [
            "ODMR scan 25 % (5 of 20 points)",
            "ODMR scan 50 % (10 of 20 points)",
            "ODMR scan 75 % (15 of 20 points)",
        ])


# =====================================================================
# Tests write no logs into data/ or logs/
# =====================================================================

class WriteGuardTests(unittest.TestCase):
    """tests/conftest.py fails the session if a test writes into data/ or logs/."""

    def setUp(self):
        import sys
        self.conftest = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
        if self.conftest is None or not hasattr(self.conftest, "guarded_write"):
            self.skipTest("conftest not loaded")

    def test_guard_classification(self):
        guarded = self.conftest.guarded_write
        data_file = str(REPO / "data" / "x.csv")
        self.assertIsNotNone(guarded("open", (data_file, "w", 0)))
        self.assertIsNotNone(guarded("open", (data_file, "a", 0)))
        self.assertIsNotNone(guarded("open", (data_file, None, os.O_WRONLY | os.O_CREAT)))
        self.assertIsNone(guarded("open", (data_file, "r", 0)))
        self.assertIsNone(guarded("os.mkdir", (str(REPO / "data"), 0o777, -1)))  # exists anyway
        self.assertIsNotNone(guarded("os.mkdir", (str(REPO / "logs"), 0o777, -1)))
        self.assertIsNotNone(guarded("open", (str(REPO / "logs" / "a.log"), "a", 0)))
        self.assertIsNone(guarded("open", (os.path.join(tempfile.gettempdir(), "x"), "w", 0)))

    def test_hook_records_a_real_attempt(self):
        # A folder that does not exist: open() fails, nothing is created,
        # but the audit event fires first.
        probe = REPO / "data" / "__write_guard_probe_missing_dir__" / "x.txt"
        self.assertFalse(probe.parent.exists())
        before = len(self.conftest.GUARDED_WRITES)
        with self.assertRaises(FileNotFoundError):
            open(probe, "w")
        recorded = self.conftest.GUARDED_WRITES[before:]
        del self.conftest.GUARDED_WRITES[before:]  # expected; not a real write
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][0], "open")

    def test_nothing_recorded_so_far(self):
        self.assertEqual(self.conftest.GUARDED_WRITES, [])


class TestLogLocationTests(unittest.TestCase):
    def test_conftest_routes_logs_to_a_temp_directory(self):
        log_dir = Path(os.environ["NV_CONTROL_LOG_DIR"]).resolve()
        diagnostics = Path(os.environ["NV_CONTROL_CAMERA_DIAGNOSTICS_LOG"]).resolve()
        for path in (log_dir, diagnostics):
            self.assertNotIn(REPO.resolve(), path.parents)
        self.assertEqual(logging_setup.log_directory().resolve(), log_dir)
        from utils import camera_diagnostics
        camera_diagnostics.log_event("test_event", source="test")
        handlers = logging.getLogger("nv_control.camera_diagnostics").handlers
        self.assertEqual(Path(handlers[0].baseFilename).resolve(), diagnostics)


if __name__ == "__main__":
    unittest.main()
