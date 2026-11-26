"""One logging setup for the app: a short console, a detailed session file.

setup_logging() is called once by the app entry points (gui/main_window.py
main(), odmr_analysis_main.py). Tests and scripts that don't call it keep
Python's defaults (warnings and errors still reach stderr).

- Console (stdout): INFO and above from our packages, WARNING and above
  from third-party libraries (their warnings and errors are never hidden).
  Format "08:01:12  SG386           Connected ...": time, a short source
  label (from the logger name, or extra={"source": ...}), the message.
  --verbose shows DEBUG on the console too.
- Session file: everything at DEBUG and above, with timestamps, level,
  module and thread, in logs/nv_control_YYYY-MM-DD_HHMMSS.log (directory
  overridable with NV_CONTROL_LOG_DIR; tests point it at a temp dir). Only
  the newest KEEP_SESSION_LOGS files are kept.
- Summary blocks (startup / shutdown): log_block() writes them to the
  console without the time prefix, and to the file as one record.
"""

import logging
import os
import sys
import time
from pathlib import Path

LOG_DIR_ENV = "NV_CONTROL_LOG_DIR"
DEFAULT_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOG_PREFIX = "nv_control_"
KEEP_SESSION_LOGS = 30
SUMMARY_LOGGER = "nv_control.summary"
RULE = "-" * 60

# Our top-level packages: INFO reaches the console. Anything else (pyqtgraph,
# h5py, the Pulse Streamer client, ...) only from WARNING up.
OWN_PACKAGES = (
    "gui", "framework", "hardware", "experiments", "analysis", "data",
    "config", "sequencing", "utils", "controller", "nv_control", "__main__",
)

# Logger-name prefix -> console label (first match wins).
SOURCE_LABELS = (
    ("hardware.microwave", "SG386"),
    ("hardware.camera", "Camera"),
    ("hardware.pulse_streamer", "Pulse Streamer"),
    ("hardware.power_supply", "Power supply"),
    ("hardware.interfaces", "Serial"),
    ("hardware.magnet", "Magnet"),
    ("hardware.hardware_manager", "Hardware"),
    ("experiments.odmr", "ODMR"),
    ("gui.odmr_analysis", "ODMR Analysis"),
    ("gui.odmr", "ODMR"),
    ("experiments.zero_field", "Zero Field"),
    ("gui.zero_field", "Zero Field"),
    ("gui.stability_gate", "Stability"),
    ("gui.magnet", "Magnet"),
    ("gui.main_window", "NV Control"),
    ("framework.microwave_safety", "SG386"),
    ("data", "Data"),
    ("config", "Config"),
    ("py.warnings", "Warning"),
    ("qt", "Qt"),
)

_state = {"handlers": [], "log_path": None}


def source_label(name):
    for prefix, label in SOURCE_LABELS:
        if name == prefix or name.startswith(prefix + ".") or name.startswith(prefix + "_"):
            return label
    top = name.split(".")[0]
    return top if top in OWN_PACKAGES else name


def _is_own(name):
    return name.split(".")[0] in OWN_PACKAGES


class ConsoleFilter(logging.Filter):
    """INFO+ from our code (DEBUG+ when verbose); WARNING+ from libraries."""

    def __init__(self, verbose=False):
        super().__init__()
        self.verbose = verbose

    def filter(self, record):
        if _is_own(record.name):
            return record.levelno >= (logging.DEBUG if self.verbose else logging.INFO)
        return record.levelno >= (logging.DEBUG if self.verbose else logging.WARNING)


class ConsoleFormatter(logging.Formatter):
    """"08:01:12  Source          message"; summary blocks unprefixed."""

    def format(self, record):
        message = record.getMessage()
        if record.exc_info and record.levelno >= logging.ERROR:
            # The traceback goes to the file; the console gets the reason.
            exc = record.exc_info[1]
            message = f"{message} ({type(exc).__name__}: {exc})"
        if record.name == SUMMARY_LOGGER:
            return message
        label = getattr(record, "source", None) or source_label(record.name)
        level = "" if record.levelno == logging.INFO or record.levelno == logging.DEBUG \
            else f"{record.levelname}: "
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        return f"{stamp}  {label[:15]:<15} {level}{message}"


FILE_FORMAT = ("%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s "
               "[%(threadName)s] %(message)s")


def log_directory():
    return Path(os.environ.get(LOG_DIR_ENV) or DEFAULT_LOG_DIR)


def prune_session_logs(directory, keep=KEEP_SESSION_LOGS):
    """Delete all but the newest *keep* session logs (by name = start time)."""
    logs = sorted(Path(directory).glob(f"{LOG_PREFIX}*.log"))
    removed = []
    for path in logs[:-keep] if keep > 0 else logs:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            pass
    return removed


def setup_logging(verbose=False, log_dir=None, stream=None):
    """Configure the root logger once (calling again replaces our handlers).
    Returns the session log file path, or None if it could not be created."""
    root = logging.getLogger()
    for handler in _state["handlers"]:
        root.removeHandler(handler)
        handler.close()
    _state["handlers"] = []
    root.setLevel(logging.DEBUG)

    stream = stream or sys.stdout
    if stream is sys.stdout and hasattr(stream, "reconfigure"):
        # "→", "×", "…" must never break the console: a terminal keeps its
        # encoding with unencodable characters replaced; redirected output
        # (a file, a pipe) is written as UTF-8 instead of the ANSI codepage.
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    console = logging.StreamHandler(stream)
    console.setLevel(logging.DEBUG)
    console.addFilter(ConsoleFilter(verbose))
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)
    _state["handlers"].append(console)

    directory = Path(log_dir) if log_dir is not None else log_directory()
    log_path = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        log_path = directory / f"{LOG_PREFIX}{stamp}.log"
        counter = 1
        while log_path.exists():  # two sessions in the same second
            counter += 1
            log_path = directory / f"{LOG_PREFIX}{stamp}_{counter}.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT, "%Y-%m-%d %H:%M:%S"))
        root.addHandler(file_handler)
        _state["handlers"].append(file_handler)
        prune_session_logs(directory)
    except OSError as error:
        log_path = None
        logging.getLogger(__name__).warning("Session log file not created: %s", error)

    logging.captureWarnings(True)  # warnings.warn() -> "py.warnings" logger
    _state["log_path"] = log_path
    logging.getLogger(__name__).debug(
        "Logging started (console %s, file %s)", "DEBUG" if verbose else "INFO", log_path)
    return log_path


def teardown_logging():
    """Remove the handlers setup_logging() added (tests)."""
    root = logging.getLogger()
    for handler in _state["handlers"]:
        root.removeHandler(handler)
        handler.close()
    _state["handlers"] = []
    _state["log_path"] = None
    logging.captureWarnings(False)


def current_log_path():
    return _state["log_path"]


def log_path_text():
    """The session log path relative to the working directory if possible."""
    path = _state["log_path"]
    if path is None:
        return "no log file"
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


# ---- summary blocks -------------------------------------------------------

LABEL_WIDTH = 15
STATE_WIDTH = 7


def format_status_line(label, state, detail=""):
    """"SG386           OK      s/n 002306, RF off"."""
    line = f"{label[:LABEL_WIDTH]:<{LABEL_WIDTH}} {state:<{STATE_WIDTH}} {detail}"
    return line.rstrip()


def format_block(title, rows, footer=None):
    """title, rule, one status line per (label, state, detail), rule, footer."""
    lines = [title, RULE]
    lines += [format_status_line(*row) for row in rows]
    lines.append(RULE)
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def log_block(text, level=logging.INFO):
    """A summary block: console unprefixed, file as one record."""
    logging.getLogger(SUMMARY_LOGGER).log(level, "\n" + text)


def format_duration(seconds):
    """"3.2 s", "13 min 30 s", "2 h 05 min" for console lines."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes} min {secs} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


# ---- Qt's own messages ----------------------------------------------------

QT_LOGGER = "qt"


def log_qt_message(mode, context, message):
    """Route one Qt message into logging: debug/info -> DEBUG (log file
    only), warning -> WARNING (console + log), critical/fatal -> ERROR.
    The logger is "qt" or "qt.<category>" (e.g. qt.qpa.fonts)."""
    from PyQt6.QtCore import QtMsgType

    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.DEBUG,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.ERROR,
    }
    category = getattr(context, "category", None) if context is not None else None
    if not category or category == "default":
        name = QT_LOGGER
    elif category == QT_LOGGER or category.startswith(QT_LOGGER + "."):
        name = category  # Qt's own categories already read "qt.qpa.fonts"
    else:
        name = f"{QT_LOGGER}.{category}"
    logging.getLogger(name).log(levels.get(mode, logging.WARNING), "%s", message)
