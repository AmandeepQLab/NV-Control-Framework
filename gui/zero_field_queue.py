"""Run queue for the Zero Field window: entries, queue files and the run log.

GUI-free on purpose: the window (gui/zero_field_window.py) owns execution,
and this module only holds what an entry is, how it is summarised, saved,
loaded and logged. Each entry is a frozen copy of ZeroFieldWidget.get_config()
plus the output directory; nothing in it refers back to the widgets.
"""

import copy
import csv
import json
from dataclasses import dataclass
from pathlib import Path

from experiments.zero_field_experiment import VECTOR_AXIS, unit_direction


PENDING = "pending"
RUNNING = "running"
DONE = "done"
STOPPED = "stopped"
FAILED = "failed"

ON_FAILURE_STOP = "Stop queue"
ON_FAILURE_SKIP = "Skip to next"
ON_FAILURE_CHOICES = (ON_FAILURE_STOP, ON_FAILURE_SKIP)

QUEUE_FILE_FORMAT = "nv_control.zero_field_queue"
QUEUE_FILE_VERSION = 1

# Every key ZeroFieldWidget.get_config() returns, with the type a loaded
# value must have. A queue file missing any of them is refused rather than
# filled with defaults, so a loaded entry always runs exactly as saved.
# A vector sweep (field_axis "vector") also requires field_direction, a list
# of three numbers; see validate_config().
CONFIG_TYPES = {
    "field_start": float,
    "field_stop": float,
    "field_points": int,
    "field_axis": str,
    "settling_time_ms": int,
    "post_flip_settling_ms": int,
    "averages": int,
    "averaging_enabled": bool,
    "num_scans": int,
    "save_raw_scans": bool,
    "alternate_direction": bool,
    "reference_enabled": bool,
    "reference_field_gauss": float,
    "save_data": bool,
}

LOG_COLUMNS = (
    "index",
    "status",
    "start_time",
    "end_time",
    "duration_s",
    "output_file",
    "first_frame_mean_counts",
    "summary",
    "message",
)


@dataclass
class QueueEntry:
    """One queued run. ``config`` and ``output_directory`` are the saved
    settings; the remaining fields describe this session's execution only
    and are not written to queue files."""

    config: dict
    output_directory: str
    status: str = PENDING
    message: str = ""
    output_path: str = ""
    start_time: object = None
    end_time: object = None
    first_frame_mean: object = None
    log_index: int = 0

    @classmethod
    def snapshot(cls, config, output_directory):
        """A new pending entry holding an independent copy of *config*."""
        return cls(
            config=copy.deepcopy(dict(config)),
            output_directory=str(output_directory),
        )

    def duplicate(self):
        """A new pending entry with the same settings."""
        return QueueEntry.snapshot(self.config, self.output_directory)

    def summary(self):
        return summarize(self.config)


def _signed(value):
    return f"{value:+g}" if value else "0"


def _axis_text(config):
    """"X"/"Y"/"Z", or "vec(0.58,0.58,0.58)" for a vector sweep."""
    if config["field_axis"] != VECTOR_AXIS:
        return config["field_axis"]
    # "+ 0.0" avoids printing -0.
    return "vec(" + ",".join(
        f"{n + 0.0:.2g}" for n in config["field_direction"]
    ) + ")"


def summarize(config):
    """One-line description of a run's settings for the queue list and log."""
    scans = config["num_scans"] if config["averaging_enabled"] else 1
    reference = (
        f"ref {_signed(config['reference_field_gauss'])} G"
        if config["reference_enabled"] else "ref off"
    )
    alternate = "alt on" if config["alternate_direction"] else "alt off"
    return (
        f"{_axis_text(config)} {config['field_start']:g}→{config['field_stop']:g} G, "
        f"{config['field_points']} pts, {config['averages']} rep, "
        f"{scans} scan{'s' if scans != 1 else ''}, "
        f"settle {config['settling_time_ms']} ms, {reference}, {alternate}"
        + ("" if config["save_data"] else ", not saved")
    )


def validate_config(config):
    """Return a normalised copy of *config*, or raise ValueError.

    Every key in CONFIG_TYPES must be present with a value of that type
    (ints are accepted for float fields; bools are never accepted as
    numbers). Unknown keys are dropped.
    """
    if not isinstance(config, dict):
        raise ValueError("entry config is not an object")
    clean = {}
    for key, kind in CONFIG_TYPES.items():
        if key not in config:
            raise ValueError(f"missing setting '{key}'")
        value = config[key]
        if kind is bool:
            ok = isinstance(value, bool)
        elif kind is int:
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif kind is float:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            value = float(value) if ok else value
        else:
            ok = isinstance(value, str)
        if not ok:
            raise ValueError(f"setting '{key}' has the wrong type ({value!r})")
        clean[key] = value
    if clean["field_axis"] not in ("X", "Y", "Z", VECTOR_AXIS):
        raise ValueError(
            f"field_axis must be X, Y, Z or {VECTOR_AXIS}, not {clean['field_axis']!r}"
        )
    if clean["field_axis"] == VECTOR_AXIS:
        # Required for a vector sweep, stored normalised; a zero vector is
        # refused. For X/Y/Z it is an unknown key and dropped.
        if "field_direction" not in config:
            raise ValueError("missing setting 'field_direction'")
        try:
            clean["field_direction"] = unit_direction(config["field_direction"])
        except (TypeError, ValueError) as error:
            raise ValueError(f"setting 'field_direction': {error}") from error
    for key in ("field_points", "averages", "num_scans"):
        if clean[key] < 1:
            raise ValueError(f"setting '{key}' must be at least 1")
    return clean


def save_queue_file(path, entries, pause_s, on_failure):
    """Write the queue's settings (not its run status) as JSON."""
    document = {
        "format": QUEUE_FILE_FORMAT,
        "version": QUEUE_FILE_VERSION,
        "pause_between_runs_s": float(pause_s),
        "on_failure": on_failure,
        "entries": [
            {"config": entry.config, "output_directory": entry.output_directory}
            for entry in entries
        ],
    }
    Path(path).write_text(json.dumps(document, indent=2), encoding="utf-8")


def load_queue_file(path):
    """Read a queue file. Returns (entries, pause_s, on_failure).

    Raises ValueError if the file is not a queue file or any entry is
    invalid; nothing is partially loaded.
    """
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"not valid JSON: {error}") from error
    if not isinstance(document, dict) or document.get("format") != QUEUE_FILE_FORMAT:
        raise ValueError("not a Zero Field queue file")
    if document.get("version") != QUEUE_FILE_VERSION:
        raise ValueError(f"unsupported queue file version {document.get('version')!r}")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("queue file has no entry list")
    entries = []
    for number, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict) or not isinstance(raw.get("output_directory"), str):
            raise ValueError(f"entry {number}: missing output_directory")
        try:
            config = validate_config(raw.get("config"))
        except ValueError as error:
            raise ValueError(f"entry {number}: {error}") from error
        entries.append(QueueEntry.snapshot(config, raw["output_directory"]))
    pause = document.get("pause_between_runs_s", 0.0)
    if not isinstance(pause, (int, float)) or isinstance(pause, bool) or pause < 0:
        raise ValueError("pause_between_runs_s must be a number >= 0")
    on_failure = document.get("on_failure", ON_FAILURE_STOP)
    if on_failure not in ON_FAILURE_CHOICES:
        raise ValueError(f"on_failure must be one of {ON_FAILURE_CHOICES}")
    return entries, float(pause), on_failure


class QueueLog:
    """CSV log of one queue execution, one row per finished entry.

    The header is written when the log is created and each row is appended
    and flushed as its run finishes, so a crash keeps every completed row.
    """

    def __init__(self, path):
        self.path = Path(path)
        # BOM so Excel reads the summaries' "→" as UTF-8; rows are appended
        # without one.
        with open(self.path, "w", newline="", encoding="utf-8-sig") as handle:
            csv.writer(handle).writerow(LOG_COLUMNS)

    def append(self, entry):
        duration = ""
        if entry.start_time is not None and entry.end_time is not None:
            duration = f"{(entry.end_time - entry.start_time).total_seconds():.3f}"
        mean = "" if entry.first_frame_mean is None else f"{entry.first_frame_mean:.6g}"
        row = {
            "index": entry.log_index,
            "status": entry.status,
            "start_time": _iso(entry.start_time),
            "end_time": _iso(entry.end_time),
            "duration_s": duration,
            "output_file": entry.output_path,
            "first_frame_mean_counts": mean,
            "summary": entry.summary(),
            "message": entry.message,
        }
        with open(self.path, "a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=LOG_COLUMNS).writerow(row)
            handle.flush()


def _iso(moment):
    return "" if moment is None else moment.isoformat(timespec="seconds")
