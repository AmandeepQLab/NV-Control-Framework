"""Count stability check before an experiment starts (GUI-free part).

The samples are the live-view mean counts that the main window's count
statistics already compute (framework/count_statistics.py). This module
only decides whether a sliding window of them is stable, and builds the
record that goes into saved files and the queue log. The Qt side --
collecting samples, starting the live view, the wait bar -- is in
gui/stability_gate.py.

Stable means, over the last ``window_s`` seconds of samples:
- relative std (sample SD, ddof=1, as % of the mean) <= max_std_percent;
- |linear drift| (least-squares slope, as % of the mean per minute)
  <= max_drift_percent_per_min;
and the samples really cover that whole window.
"""

import math
from dataclasses import asdict, dataclass

import numpy as np

ON_TIMEOUT_START = "Start anyway"
ON_TIMEOUT_CANCEL = "Cancel this run"
ON_TIMEOUT_CHOICES = (ON_TIMEOUT_START, ON_TIMEOUT_CANCEL)

# Outcomes recorded in metadata and the queue log. A disabled check is
# recorded only as stability_check_enabled = false ("not used").
OUTCOME_PASSED = "passed"
OUTCOME_TIMEOUT_STARTED = "timed out, started anyway"
OUTCOME_USER_STARTED = "started anyway by user"
OUTCOME_TIMEOUT_CANCELLED = "timed out, run cancelled"
OUTCOME_USER_CANCELLED = "cancelled by user"
OUTCOME_NOT_USED = "not used"

MIN_SAMPLES = 10
# A window counts as covered when its first sample is at most this far
# after the window's start, and its newest sample at most this old.
COVERAGE_SLACK_S = 2.0


@dataclass
class StabilitySettings:
    """The stability-check controls of one experiment window."""

    enabled: bool = False
    window_s: float = 60.0
    max_std_percent: float = 0.30
    max_drift_percent_per_min: float = 0.50
    timeout_s: float = 600.0
    on_timeout: str = ON_TIMEOUT_START

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """Validated settings from a dict (queue files); ValueError if bad."""
        if not isinstance(data, dict):
            raise ValueError("stability settings are not an object")
        defaults = cls()
        values = {}
        for key, default in asdict(defaults).items():
            value = data.get(key, default)
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ValueError(f"stability '{key}' must be true or false")
            elif isinstance(default, float):
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not math.isfinite(value) or value <= 0:
                    raise ValueError(f"stability '{key}' must be a positive number")
                value = float(value)
            elif value not in ON_TIMEOUT_CHOICES:
                raise ValueError(f"stability 'on_timeout' must be one of {ON_TIMEOUT_CHOICES}")
            values[key] = value
        return cls(**values)


def window_statistics(times, values):
    """(std %, drift %/min) of samples; NaN where it can't be computed.

    std % = sample SD (ddof=1) / mean * 100.
    drift = least-squares slope (counts/s) * 60 / mean * 100.
    """
    t = np.asarray(times, dtype=float)
    y = np.asarray(values, dtype=float)
    if y.size < 2:
        return math.nan, math.nan
    mean = float(y.mean())
    if mean == 0 or not math.isfinite(mean):
        return math.nan, math.nan
    std_percent = float(y.std(ddof=1)) / abs(mean) * 100.0
    dt = t - t.mean()
    denominator = float(np.dot(dt, dt))
    if denominator == 0:
        return std_percent, math.nan
    slope = float(np.dot(dt, y - mean)) / denominator
    return std_percent, slope * 60.0 / abs(mean) * 100.0


@dataclass
class Evaluation:
    """One look at the window: what was measured and whether it passes."""

    std_percent: float
    drift_percent_per_min: float
    covered_s: float          # how much of the window the samples span
    n_samples: int
    window_full: bool
    passed: bool


def evaluate(times, values, now, settings, collecting_since):
    """Judge the samples with ``now - window_s < t <= now``.

    *collecting_since* is when the current sample series began (the check's
    start, or the last settings reset); the window can't be full before
    window_s has passed since then.
    """
    t = np.asarray(times, dtype=float)
    y = np.asarray(values, dtype=float)
    start = now - settings.window_s
    keep = t > start
    t, y = t[keep], y[keep]
    std_percent, drift = window_statistics(t, y)
    covered = float(t[-1] - t[0]) if t.size >= 2 else 0.0
    full = bool(
        now - collecting_since >= settings.window_s
        and t.size >= MIN_SAMPLES
        and t[0] - start <= COVERAGE_SLACK_S
        and now - t[-1] <= COVERAGE_SLACK_S
    )
    passed = bool(
        full
        and math.isfinite(std_percent) and std_percent <= settings.max_std_percent
        and math.isfinite(drift) and abs(drift) <= settings.max_drift_percent_per_min
    )
    return Evaluation(std_percent, drift, covered, int(t.size), full, passed)


def record(settings, outcome=None, evaluation=None, wait_s=None):
    """Metadata for a saved file / queue log row.

    A disabled check gives only {"stability_check_enabled": False}, so a run
    without it differs from before by that one key.
    """
    if not settings.enabled:
        return {"stability_check_enabled": False}

    def finite(value):
        return None if value is None or not math.isfinite(value) else float(value)

    return {
        "stability_check_enabled": True,
        "stability_window_s": settings.window_s,
        "stability_max_std_percent": settings.max_std_percent,
        "stability_max_drift_percent_per_min": settings.max_drift_percent_per_min,
        "stability_timeout_s": settings.timeout_s,
        "stability_on_timeout": settings.on_timeout,
        "stability_outcome": outcome,
        "stability_std_percent": finite(evaluation.std_percent) if evaluation else None,
        "stability_drift_percent_per_min": (
            finite(evaluation.drift_percent_per_min) if evaluation else None
        ),
        "stability_wait_s": None if wait_s is None else round(float(wait_s), 3),
    }


def _signed(value, digits):
    # U+2212 minus, as in the status line's specification.
    return f"{value:+.{digits}f}".replace("-", "−")


def status_text(evaluation, settings, elapsed_s):
    """The wait line shown in the experiment window."""
    head = "Waiting for stable counts: "
    tail = f", {elapsed_s:.0f} s elapsed (timeout {settings.timeout_s:g} s)"
    if evaluation is None or evaluation.n_samples == 0:
        return head + "no live-view frames yet" + tail
    std = "--" if not math.isfinite(evaluation.std_percent) else f"{evaluation.std_percent:.2f}%"
    drift = (
        "--" if not math.isfinite(evaluation.drift_percent_per_min)
        else f"{_signed(evaluation.drift_percent_per_min, 2)}%/min"
    )
    text = (
        f"{head}std {std} (limit {settings.max_std_percent:.2f}%), "
        f"drift {drift} (limit {settings.max_drift_percent_per_min:.2f}%/min)"
    )
    if not evaluation.window_full:
        text += f", window {min(evaluation.covered_s, settings.window_s):.0f}/{settings.window_s:g} s"
    return text + tail
