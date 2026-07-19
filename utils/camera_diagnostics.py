"""Temporary file-only diagnostics for camera acquisition ownership."""

import inspect
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path


LOG_PATH = Path(__file__).resolve().parents[1] / "data" / "camera_diagnostics.log"
_LOGGER_NAME = "nv_control.camera_diagnostics"


def _logger():
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def _origin(filename, function):
    path = filename.replace("\\", "/").lower()
    if "zero_field" in path or "zerofield" in function.lower():
        return "Zero Field experiment"
    if "_stream_loop" == function or "/hardware/camera/" in path:
        return "live stream"
    if "/gui/" in path:
        return "GUI"
    return "unknown"


def log_event(event, source="unknown", **details):
    """Append one JSON record without writing diagnostic output to stdout."""
    try:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
            "thread": threading.current_thread().name,
            "source": source,
            **details,
        }
        _logger().info(json.dumps(record, sort_keys=True, default=str))
    except Exception:
        # Diagnostics must never interrupt camera acquisition or an experiment.
        pass


def log_camera_snap(camera_type):
    """Record the direct caller of a public ``camera.snap()`` invocation."""
    try:
        caller = inspect.currentframe().f_back.f_back
        filename = caller.f_code.co_filename
        function = caller.f_code.co_name
        log_event(
            "camera.snap",
            source=_origin(filename, function),
            camera_type=camera_type,
            caller_function=function,
            caller_file=filename,
        )
    except Exception:
        # Diagnostics must never interrupt camera acquisition or an experiment.
        pass
