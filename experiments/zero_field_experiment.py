"""Microwave-free widefield fluorescence scan over a magnetic-field axis."""

import copy
import logging
import os
import time
from datetime import datetime, timezone
from collections.abc import Mapping

import numpy as np

from framework.scan_experiment import ScanExperiment
from framework.camera_ownership import exclusive_camera_access
from framework.image_cube import ImageCube
from framework.roi import validate_roi
# Shared with ODMR; still importable from this module.
from framework.field_direction import unit_direction  # noqa: F401


LOGGER = logging.getLogger(__name__)

# field_axis value for a sweep along an arbitrary direction (see
# ZeroFieldExperiment): every axis is commanded at once, B = s * n_hat.
VECTOR_AXIS = "vector"


class ZeroFieldExperiment(ScanExperiment):
    """Acquire fluorescence images while sweeping the magnetic field.

    The sweep runs along one coil axis (field_axis "X"/"Y"/"Z"), or with
    field_axis "vector" along the unit vector n_hat of field_direction:
    each point then commands B = s * n_hat on all three axes at once, where
    s is the point's value of the start/stop linspace (the signed magnitude
    along n_hat).

    Field inputs and the ImageCube scan axis are expressed in gauss.  The
    existing :class:`Magnet` API uses millitesla internally, so values are
    converted only at the hardware boundary.
    """

    scan_axis_name = "Magnetic Field"
    scan_axis_unit = "G"

    # This experiment commands the magnet itself on every scan point. Read
    # by MainWindow when announcing a run, so MagnetControlWindow can warn
    # that manual commands would conflict with the sweep (see CLAUDE.md).
    MAGNET_IS_SWEPT = True

    def __init__(
        self,
        hardware_manager,
        camera,
        magnet,
        field_start,
        field_stop,
        field_points,
        field_axis,
        settling_time_ms=0,
        averages=1,
        acquisition_roi=None,
        metadata=None,
        live_update_callback=None,
        scan_started_callback=None,
        scan_completed_callback=None,
        averaging_enabled=False,
        num_scans=1,
        save_raw_scans=False,
        raw_scan_saver=None,
        zero_other_axes=True,
        stream_scan_path=None,
        stream_path_is_output=False,
        timing_diagnostics=False,
        check_power_supply_errors=False,
        post_flip_settling_ms=0.0,
        alternate_direction=False,
        reference_enabled=False,
        reference_field_gauss=-8.0,
        field_direction=None,
    ):
        config = {
            "settling_time_ms": settling_time_ms,
            "timing_diagnostics": timing_diagnostics,
        }
        hardware = {
            "camera": camera,
            "magnet": magnet,
        }
        super().__init__(hardware, config)

        if str(field_axis).lower() == VECTOR_AXIS:
            axis = VECTOR_AXIS
            # Normalised here, so every command and the metadata use n_hat.
            field_direction = unit_direction(field_direction)
        else:
            axis = field_axis.upper()
            if axis not in ("X", "Y", "Z"):
                raise ValueError("field_axis must be 'X', 'Y', 'Z', or 'vector'.")
            if field_direction is not None:
                raise ValueError(
                    "field_direction applies only to field_axis 'vector'."
                )
        if field_points < 1:
            raise ValueError("field_points must be at least 1.")
        if averages < 1:
            raise ValueError("averages must be at least 1.")
        if not isinstance(averaging_enabled, bool):
            raise ValueError("averaging_enabled must be a boolean.")
        if not isinstance(num_scans, int) or isinstance(num_scans, bool) or num_scans < 1:
            raise ValueError("num_scans must be an integer greater than or equal to 1.")
        if not isinstance(save_raw_scans, bool):
            raise ValueError("save_raw_scans must be a boolean.")
        if not isinstance(zero_other_axes, bool):
            raise ValueError("zero_other_axes must be a boolean.")
        if not isinstance(alternate_direction, bool):
            raise ValueError("alternate_direction must be a boolean.")
        if not isinstance(reference_enabled, bool):
            raise ValueError("reference_enabled must be a boolean.")
        reference_field_gauss = float(reference_field_gauss)
        if not np.isfinite(reference_field_gauss):
            raise ValueError("reference_field_gauss must be finite.")
        post_flip_settling_ms = float(post_flip_settling_ms)
        if not np.isfinite(post_flip_settling_ms) or post_flip_settling_ms < 0:
            raise ValueError("post_flip_settling_ms must be non-negative and finite.")

        self.hardware_manager = hardware_manager
        self.camera = camera
        self.magnet = magnet
        self.field_start = field_start
        self.field_stop = field_stop
        self.field_points = field_points
        self.field_axis = axis
        # Unit vector [nx, ny, nz] for a vector sweep; None for one axis.
        self.field_direction = field_direction
        self.vector_mode = axis == VECTOR_AXIS
        self.averages = averages
        # Acquisition ROI is expressed in full-sensor coordinates and frozen
        # at construction so a GUI change cannot affect a running scan.
        self.acquisition_roi = validate_roi(acquisition_roi)
        self.metadata = {} if metadata is None else dict(metadata)
        self.metadata["acquisition_roi"] = self.acquisition_roi
        self.live_update_callback = live_update_callback
        self.scan_started_callback = scan_started_callback
        self.scan_completed_callback = scan_completed_callback
        self.averaging_enabled = averaging_enabled
        self.num_scans = num_scans
        self.save_raw_scans = save_raw_scans
        self.raw_scan_saver = raw_scan_saver
        self.raw_scan_filenames = []
        self.zero_other_axes = zero_other_axes
        # Bidirectional sweeps: only meaningful with multi-scan averaging.
        # See scan_is_reversed() for which scans run stop -> start.
        self.alternate_direction = alternate_direction
        self._alternating = alternate_direction and averaging_enabled
        # Direction of the sweep currently being acquired.
        self._sweep_reversed = False
        # Running averages of forward-only and reverse-only completed scans,
        # in start -> stop field order; only kept while alternating.
        self._direction_averages = {"forward": None, "reverse": None}
        self._direction_counts = {"forward": 0, "reverse": 0}
        # One entry per completed (averaged) scan, in scan order.
        self._scan_directions = []
        self._scan_point_times_s = []

        # In-point reference normalization: every point is also measured at
        # a fixed reference field on the swept axis, in alternating S,R /
        # R,S order, and signal/reference is stored per pixel. See
        # _run_single_sweep().
        self.reference_enabled = reference_enabled
        self.reference_field_gauss = reference_field_gauss
        # Per-point lists for the reference half, reset per sweep.
        self.reference_order = []
        self.reference_point_time_s = []
        self.reference_relay_flip_occurred = []
        self.reference_relay_flip_axes = []
        self.reference_model_field_vector_mT = []
        self.reference_model_polarity = []
        # Pre-allocated reference / ratio planes of the current sweep.
        self._reference_cube = None
        self._ratio_cube = None
        # Ratio pixels set to NaN because the reference was 0 (this sweep).
        self._ratio_zero_reference_pixels = 0
        # Newest ratio plane, for the live display.
        self.latest_ratio = None
        # Vector of the previous half's command in this sweep, so a repeated
        # identical command (no write) gets no settling sleep.
        self._last_half_vector_mT = None
        # Multi-scan bookkeeping for the reference: per-scan reference times
        # and the ratio's forward-only / reverse-only running averages.
        self._scan_reference_point_times_s = []
        self._ratio_direction_averages = {"forward": None, "reverse": None}
        # Optional callable: scan_index -> Path (or None to skip streaming
        # for that scan). When set, each scan's frames are written to disk
        # incrementally as they're acquired -- HDF5 only, since streaming
        # needs random-access writes .npz has no equivalent for -- so the
        # caller (ZeroFieldWorker) only supplies this for an .h5 destination.
        self.stream_scan_path = stream_scan_path
        # When True, stream_scan_path's file IS this run's final deliverable
        # (a single-scan run streaming directly to the output path), not a
        # disposable per-scan artifact -- it must never be unlinked below
        # regardless of save_raw_scans, and isn't a "raw scan" in the
        # multi-scan sense so it's excluded from raw_scan_filenames too.
        self.stream_path_is_output = stream_path_is_output
        self._stream_writer = None
        # Path streaming last wrote to (or None), read by run() right after
        # _run_single_sweep() returns to decide whether the raw_scan_saver
        # callback would be a redundant, riskier rewrite of an already
        # safely-written file.
        self._last_scan_stream_path = None
        self._configured_field_mT = None
        self._resolved_non_swept_field_mT = None
        # Captured once, from the first scan of a run: what the magnet was
        # actually at before this experiment touched it.
        self._initial_magnet_vector_mT = None
        # One entry per scan, in order, regardless of zero_other_axes.  If
        # cleanup_scan() ever fails to zero the magnet between scans, this is
        # what would reveal it instead of hiding it in an averaged cube.
        self.scan_entry_magnet_vectors_mT = []
        self.latest_field = None
        self.latest_image = None
        self._acquisition_started_at = None

        # Polarity relay flips (see Magnet.set_vector()'s return value).
        # Extra settling applied after any point whose field command flipped
        # a relay -- _switch_polarity() cuts and restores coil current, a far
        # larger transient than an incremental step.
        self.post_flip_settling_ms = post_flip_settling_ms
        # One entry per acquired point of the current sweep, reset per sweep
        # and written to metadata as relay_flip_occurred / relay_flip_axes.
        self.relay_flip_occurred = []
        self.relay_flip_axes = []
        # Flips caused by setup_scan()'s own set_vector(), folded into point
        # 0: they happen immediately before point 0's command, with no
        # settling in between, so point 0 is the point they affect.
        self._setup_flip_axes = frozenset()

        # What the magnet was actually commanded to per point, according to
        # its calibration model -- as opposed to field_values_gauss, the
        # requested linspace. They differ in a branch gap (model = the
        # intercept); in an overlap the field matches but the branch is a
        # matter of sweep history, which model_polarity records. One entry
        # per acquired point, reset per sweep.
        self.model_field_values_gauss = []
        self.model_field_vector_mT = []
        self.model_polarity = []
        # Acquisition time of each point, in seconds since its sweep started,
        # in acquisition order. Reset per sweep.
        self.point_time_s = []
        self._sweep_t0 = None

        # Whether this run successfully armed the camera's held-open
        # software acquisition (see run()/acquire_frame()) -- mirrors
        # ODMRExperiment's own _acquisition_open flag. Distinct from the
        # camera driver's own _acquisition_open: this one tracks whether
        # *this experiment* is entitled to call grab_software_frame(),
        # not whether the camera object happens to be armed right now.
        self._acquisition_open = False

        # =====================================================
        # TIMING DIAGNOSTICS (off by default; temporary instrumentation)
        # =====================================================
        # Enabled via config["timing_diagnostics"] or the NV_ZFE_TIMING=1
        # environment variable -- mirrors ODMRExperiment exactly, with a
        # separate env var so leaving one experiment's timing on doesn't
        # silently start logging the other. Never set by the GUI panel
        # itself (same rule as ODMR's timing_diagnostics).
        self._timing = bool(self.config.get("timing_diagnostics", False)) or (
            os.environ.get("NV_ZFE_TIMING") == "1"
        )
        self._timing_log = []
        self._timing_epoch = None
        # Per-point tagging context, set once per point at the top of the
        # loop body in _run_single_sweep() and read by every _record_timing/
        # _merge_*_log call for that point -- avoids threading scan_index/
        # point_index/field_gauss through every instrumented method's
        # signature individually.
        self._timing_scan_index = None
        self._timing_point_index = None
        self._timing_field_gauss = None

        # =====================================================
        # POWER SUPPLY ERROR-QUEUE CHECKING (off by default; diagnostic)
        # =====================================================
        # Enabled via check_power_supply_errors or the
        # NV_ZFE_CHECK_PS_ERRORS=1 environment variable -- same off-by-
        # default, env-var-fallback shape as timing_diagnostics above, and
        # for the same reason: never set by the GUI panel itself. Checked
        # only at scan start (setup_scan(), right after magnet.enable())
        # and scan end (cleanup_scan(), right before magnet.disable()) --
        # never per point or per write -- so it costs nothing in the hot
        # path even when enabled. See Magnet.check_error_queues().
        self._check_ps_errors = bool(check_power_supply_errors) or (
            os.environ.get("NV_ZFE_CHECK_PS_ERRORS") == "1"
        )

    def timing_enabled(self):
        return self._timing

    def reset_timing(self):
        """Start a fresh timing epoch and log. Call once per run()."""
        self._timing_log = []
        self._timing_epoch = time.perf_counter()

    def pop_timing_log(self):
        """Return and clear accumulated timing records (bounds growth)."""
        log = self._timing_log
        self._timing_log = []
        return log

    def _record_timing(self, stage, avg_index, t0):
        if not self._timing:
            return
        self._timing_log.append({
            "scan_index": self._timing_scan_index,
            "point_index": self._timing_point_index,
            "field_gauss": self._timing_field_gauss,
            "avg_index": avg_index,
            "stage": stage,
            "t_start_rel_s": t0 - self._timing_epoch,
            "duration_s": time.perf_counter() - t0,
        })

    def _merge_magnet_log(self, start_rel):
        """Drain Magnet's per-call timing log, tag with the current point's
        context, and place each entry using a running cursor from
        start_rel -- correct because Magnet.set_vector()'s own entries
        (and the axis entries it merges in) are recorded in strict call
        order with no untimed gap other than the trivially-fast
        field<->current conversion (see MagnetAxis.set_field())."""
        if not self._timing:
            return
        running_t = start_rel
        for stage, duration in self.magnet.pop_timing_log():
            self._timing_log.append({
                "scan_index": self._timing_scan_index,
                "point_index": self._timing_point_index,
                "field_gauss": self._timing_field_gauss,
                "avg_index": None,
                "stage": stage,
                "t_start_rel_s": running_t,
                "duration_s": duration,
            })
            running_t += duration

    def _merge_camera_log(self, avg_index, start_rel):
        """Drain the camera driver's per-snap() sub-stage log immediately
        after each snap() call (see AndorNeoAndor3.snap()/SimCamera.snap())
        -- keeps that log bounded to at most one call's worth of entries
        at a time, same discipline as ODMRExperiment._merge_camera_log."""
        if not self._timing:
            return
        if not hasattr(self.camera, "pop_timing_log"):
            return
        running_t = start_rel
        for stage, _frame_idx, duration in self.camera.pop_timing_log():
            self._timing_log.append({
                "scan_index": self._timing_scan_index,
                "point_index": self._timing_point_index,
                "field_gauss": self._timing_field_gauss,
                "avg_index": avg_index,
                "stage": f"camera.{stage}",
                "t_start_rel_s": running_t,
                "duration_s": duration,
            })
            running_t += duration

    def setup_scan(self):
        # Keep supplies enabled for the sweep. Non-swept axes retain their
        # compensation currents for calibrated B=0; only a reversing axis
        # briefly disables its output for its own safe relay transition.
        self.magnet.enable()

        # Checked before this scan sends any set_vector() traffic, so a
        # queue already non-empty from a prior run (or from enable()
        # itself) is visible in this run's log instead of silently
        # compounding into it. Off by default -- see _check_ps_errors.
        if self._check_ps_errors and hasattr(self.magnet, "check_error_queues"):
            self.magnet.check_error_queues(context="Zero Field scan start")

        # Magnet.get_vector() returns values in millitesla.  Record what was
        # actually there before this scan touches anything, whether or not
        # zero_other_axes ends up overriding it.
        entry_vector_mT = self.magnet.get_vector()
        self.scan_entry_magnet_vectors_mT.append(dict(entry_vector_mT))
        if self._initial_magnet_vector_mT is None:
            self._initial_magnet_vector_mT = dict(entry_vector_mT)

        # A vector sweep commands every axis at every point: none is
        # non-swept, and the resolved vector below is only the setup state.
        non_swept_axes = [
            axis for axis in ("x", "y", "z")
            if not self.vector_mode and axis != self.field_axis.lower()
        ]

        if self.zero_other_axes:
            resolved_field_mT = {"x": 0.0, "y": 0.0, "z": 0.0}
            # Physically zero now, rather than waiting for the first scan
            # point, so the magnet visibly reflects the clean state from the
            # start of the scan rather than whatever was left over.
            #
            # Timed and drained here, immediately -- this is a real call to
            # Magnet.set_vector() distinct from any per-point call, and if
            # left undrained its entries would still be sitting in
            # Magnet._timing_log the next time set_scan_point() drains it,
            # bleeding into and inflating the first point's own count
            # (e.g. an extra axis polarity timing row wrongly
            # attributed to point 0).
            timing = self._timing
            t0 = time.perf_counter() if timing else None
            self._setup_flip_axes = self._as_flip_set(self.magnet.set_vector(
                bx=resolved_field_mT["x"],
                by=resolved_field_mT["y"],
                bz=resolved_field_mT["z"],
            ))
            if timing:
                self._merge_magnet_log(t0 - self._timing_epoch)
        else:
            resolved_field_mT = dict(entry_vector_mT)
            retained_nonzero = {
                axis: resolved_field_mT[axis]
                for axis in non_swept_axes
                if abs(resolved_field_mT[axis]) > 1e-9
            }
            if retained_nonzero:
                LOGGER.warning(
                    "Zero Field scan on axis %s is preserving a non-zero "
                    "retained field on axis(es) %s: %s mT. This scan will be "
                    "taken under a bias vector, not a clean field-only sweep. "
                    "Pass zero_other_axes=True (the default) to avoid this.",
                    self.field_axis, list(retained_nonzero), retained_nonzero,
                )

        # Preserve the resolved values on the two axes outside the scan; only
        # the swept axis changes per point.  set_scan_point() merges into this.
        self._configured_field_mT = resolved_field_mT
        self._resolved_non_swept_field_mT = {
            axis: resolved_field_mT[axis] for axis in non_swept_axes
        }

        forward = np.linspace(
            self.field_start,
            self.field_stop,
            self.field_points,
        )
        # A reverse sweep commands the same fields in stop -> start order;
        # everything per point (flips, settling, model fields) follows it.
        self.scan_vector = forward[::-1].copy() if self._sweep_reversed else forward
        if self._acquisition_started_at is None:
            self._acquisition_started_at = datetime.now(timezone.utc).isoformat()
        self._set_static_metadata()
        self.image_cube.scan_axis_name = self.scan_axis_name
        self.image_cube.scan_axis_unit = self.scan_axis_unit
        self.image_cube.scan_axis_values = self.scan_vector
        self.image_cube.metadata = dict(self.metadata)

    def cleanup_scan(self):
        """Return the magnet to its existing safe disabled state after a scan."""
        # Checked before disable() so this reflects what the sweep itself
        # did to the error queue, not disable()'s own teardown traffic.
        # Off by default -- see _check_ps_errors.
        if self._check_ps_errors and hasattr(self.magnet, "check_error_queues"):
            self.magnet.check_error_queues(context="Zero Field scan end")
        self.magnet.disable()

    def set_scan_point(self, value):
        """Apply the field for this point; return the axes whose relay flipped."""
        bx, by, bz = self._point_vector_mT(value)

        timing = self._timing
        t0 = time.perf_counter() if timing else None
        flipped = self.magnet.set_vector(bx=bx, by=by, bz=bz)
        if timing:
            self._merge_magnet_log(t0 - self._timing_epoch)
        return self._as_flip_set(flipped)

    @staticmethod
    def _as_flip_set(flipped):
        """Normalize set_vector()'s return value; magnet doubles without the
        flip report (returning None or a Mock) count as no flip."""
        return frozenset(flipped) if isinstance(flipped, (set, frozenset)) else frozenset()

    def _point_vector_mT(self, value):
        """The (x, y, z) mT vector set_scan_point(value) commands.

        One axis: that axis at *value* G, the others as resolved by
        setup_scan(). Vector sweep: value * n_hat on all three axes."""
        if self.vector_mode:
            # "+ 0.0" turns -0.0 (negative s on a zero component) into 0.0.
            return tuple(value * 0.1 * n + 0.0 for n in self.field_direction)
        fields_mT = dict(self._configured_field_mT)
        fields_mT[self.field_axis.lower()] = value * 0.1
        return (fields_mT["x"], fields_mT["y"], fields_mT["z"])

    def _per_point_metadata(self):
        """Per-acquired-point lists recorded alongside field_values_gauss."""
        metadata = {
            "relay_flip_occurred": [bool(f) for f in self.relay_flip_occurred],
            "relay_flip_axes": [",".join(sorted(a)) for a in self.relay_flip_axes],
            "model_field_values_gauss": list(self.model_field_values_gauss),
            "model_field_vector_mT": [list(v) for v in self.model_field_vector_mT],
            "model_polarity": [list(p) for p in self.model_polarity],
            "point_time_s": list(self.point_time_s),
        }
        if self.reference_enabled:
            metadata.update({
                "reference_order": list(self.reference_order),
                "reference_point_time_s": list(self.reference_point_time_s),
                "reference_relay_flip_occurred": [
                    bool(f) for f in self.reference_relay_flip_occurred
                ],
                "reference_relay_flip_axes": [
                    ",".join(sorted(a)) for a in self.reference_relay_flip_axes
                ],
                "reference_model_field_vector_mT": [
                    list(v) for v in self.reference_model_field_vector_mT
                ],
                "reference_model_polarity": [
                    list(p) for p in self.reference_model_polarity
                ],
            })
        return metadata

    # Per-point lists that follow the field order of a sweep, and so are
    # reversed (with the image planes) to align a reverse sweep by field.
    # point_time_s / reference_point_time_s are deliberately absent: they
    # stay in acquisition order. Reference keys exist only with reference on.
    _FIELD_ORDERED_KEYS = (
        "field_values_gauss",
        "relay_flip_occurred",
        "relay_flip_axes",
        "model_field_values_gauss",
        "model_field_vector_mT",
        "model_polarity",
        "reference_order",
        "reference_relay_flip_occurred",
        "reference_relay_flip_axes",
        "reference_model_field_vector_mT",
        "reference_model_polarity",
    )

    # Extra datasets holding one plane per point, in the same order as
    # ``data``; reversed with it and averaged per field point like it.
    _PER_POINT_EXTRA_KEYS = ("reference", "ratio")

    # Keys describing one sweep only; dropped from a multi-scan average,
    # which records them per scan instead (scan_point_times_s,
    # scan_reference_point_times_s, scan_directions).
    _PER_SWEEP_ONLY_KEYS = ("point_time_s", "sweep_direction", "reference_point_time_s")

    @staticmethod
    def scan_is_reversed(scan_index, alternate_direction):
        """Whether 1-based *scan_index* sweeps stop -> start.

        With alternation on, scan 1 sweeps start -> stop and scans 2, 4,
        6, ... sweep stop -> start, so a single scan, and the first scan of
        every average, is a forward sweep.
        """
        return bool(alternate_direction) and scan_index % 2 == 0

    @classmethod
    def _field_ordered(cls, acquired_cube, reversed_sweep):
        """Return *acquired_cube* aligned to start -> stop field order.

        A forward sweep is returned as-is. A reverse sweep gets a new cube
        whose image planes (a reversed view, no copy) and per-point field
        lists are flipped, so index j means the same field in both
        directions. point_time_s stays in acquisition order.
        """
        if not reversed_sweep:
            return acquired_cube
        metadata = dict(acquired_cube.metadata)
        for key in cls._FIELD_ORDERED_KEYS:
            if key in metadata:
                metadata[key] = list(metadata[key])[::-1]
        return ImageCube(
            data=acquired_cube.data[::-1],
            axes=acquired_cube.axes,
            metadata=metadata,
            experiment_type=acquired_cube.experiment_type,
            scan_axis_name=acquired_cube.scan_axis_name,
            scan_axis_unit=acquired_cube.scan_axis_unit,
            scan_axis_values=list(acquired_cube.scan_axis_values)[::-1],
            extra_datasets={
                name: value[::-1]
                for name, value in acquired_cube.extra_datasets.items()
                if name in cls._PER_POINT_EXTRA_KEYS
            },
        )

    def _record_completed_scan(self, averaged_cube, ordered_cube, reversed_sweep):
        """Per-scan bookkeeping for a multi-scan average: timestamps,
        directions, and the forward-only / reverse-only running averages
        (of ``data``, and of ``ratio`` when the reference is on)."""
        self._scan_point_times_s.append(
            list(ordered_cube.metadata.get("point_time_s", []))
        )
        if self.reference_enabled:
            self._scan_reference_point_times_s.append(
                list(ordered_cube.metadata.get("reference_point_time_s", []))
            )
        if not self._alternating:
            return
        direction = "reverse" if reversed_sweep else "forward"
        self._scan_directions.append(direction)
        self._direction_counts[direction] += 1
        count = self._direction_counts[direction]
        sources = [(self._direction_averages, ordered_cube.data)]
        if self.reference_enabled and "ratio" in ordered_cube.extra_datasets:
            sources.append(
                (self._ratio_direction_averages, ordered_cube.extra_datasets["ratio"])
            )
        for averages, current in sources:
            if averages[direction] is None:
                averages[direction] = np.array(current, dtype=np.float64, copy=True)
            else:
                self._update_running_average(averages[direction], current, count)
        # A direction with no completed scan gets no dataset at all.
        # reference / ratio (already on averaged_cube) are kept.
        for prefix, averages in (
            ("", self._direction_averages),
            ("ratio_", self._ratio_direction_averages),
        ):
            for name, average in averages.items():
                if average is not None:
                    averaged_cube.extra_datasets[f"{prefix}{name}_average"] = average

    @classmethod
    def _update_running_extras(cls, averaged_cube, ordered_cube, scan_count):
        """Fold one completed scan's reference / ratio planes into the
        multi-scan average, per field point, like ``data``."""
        for name in cls._PER_POINT_EXTRA_KEYS:
            if name in averaged_cube.extra_datasets and name in ordered_cube.extra_datasets:
                cls._update_running_average(
                    averaged_cube.extra_datasets[name],
                    ordered_cube.extra_datasets[name],
                    scan_count,
                )

    @staticmethod
    def _merge_reference_metadata(averaged_cube, acquired_cube, scan_count):
        """Fold one completed scan's reference-half metadata into the
        multi-scan average with the same rules as the signal half: union for
        flips, running mean for model vectors, "mixed" for polarity, and
        "mixed" for a per-point S/R order that differed between scans."""
        average = averaged_cube.metadata
        current = acquired_cube.metadata
        keys = (
            "reference_order", "reference_model_field_vector_mT",
            "reference_model_polarity",
        )
        if any(key not in average or key not in current for key in keys):
            return
        ZeroFieldExperiment._merge_relay_flips(
            averaged_cube, acquired_cube, prefix="reference_"
        )
        average = averaged_cube.metadata

        def mean(old, new):
            if old is None or new is None:
                return None
            return old + (new - old) / scan_count

        averaged_cube.metadata = {
            **average,
            "reference_order": [
                a if a == b else "mixed"
                for a, b in zip(average["reference_order"], current["reference_order"])
            ],
            "reference_model_field_vector_mT": [
                [mean(a, b) for a, b in zip(old, new)]
                for old, new in zip(
                    average["reference_model_field_vector_mT"],
                    current["reference_model_field_vector_mT"],
                )
            ],
            "reference_model_polarity": [
                [a if a == b else "mixed" for a, b in zip(old, new)]
                for old, new in zip(
                    average["reference_model_polarity"],
                    current["reference_model_polarity"],
                )
            ],
        }

    @staticmethod
    def _polarity_symbol(polarity):
        """'+'/'-' for a synchronized relay branch, '?' when unknown."""
        value = getattr(polarity, "value", None)
        return {1: "+", -1: "-"}.get(value, "?")

    def _model_snapshot(self):
        """Modelled state right after a point's set_vector(): the swept
        axis's model field (G), the full model vector (mT), and polarities.
        Model values, not measurements (see MagnetAxis.get_field())."""
        vector = self.magnet.get_vector()
        vector_mT = [vector.get(axis) for axis in ("x", "y", "z")]
        if self.vector_mode:
            # The model vector's component along n_hat: the counterpart of
            # the requested signed magnitude s. The full vector is kept in
            # model_field_vector_mT (it can leave the line in a branch gap).
            swept_mT = (
                None if any(v is None for v in vector_mT)
                else sum(v * n for v, n in zip(vector_mT, self.field_direction))
            )
        else:
            swept_mT = vector.get(self.field_axis.lower())
        swept_gauss = None if swept_mT is None else swept_mT * 10.0
        get_polarities = getattr(self.magnet, "get_polarities", None)
        polarities = get_polarities() if callable(get_polarities) else {}
        polarity = [
            self._polarity_symbol(polarities.get(axis)) for axis in ("x", "y", "z")
        ]
        return swept_gauss, vector_mT, polarity

    @staticmethod
    def _merge_model_fields(averaged_cube, acquired_cube, scan_count):
        """Fold one completed scan's model fields into the multi-scan average.

        Fields: running mean per point, matching how the image is averaged
        (None if any scan had no model value). Polarity can't be averaged:
        an axis whose branch differed between averaged scans becomes "mixed".
        """
        average = averaged_cube.metadata
        current = acquired_cube.metadata
        keys = ("model_field_values_gauss", "model_field_vector_mT", "model_polarity")
        if any(key not in average or key not in current for key in keys):
            return

        def mean(old, new):
            if old is None or new is None:
                return None
            return old + (new - old) / scan_count

        fields = [
            mean(a, b)
            for a, b in zip(average["model_field_values_gauss"], current["model_field_values_gauss"])
        ]
        vectors = [
            [mean(a, b) for a, b in zip(old, new)]
            for old, new in zip(average["model_field_vector_mT"], current["model_field_vector_mT"])
        ]
        polarity = [
            [a if a == b else "mixed" for a, b in zip(old, new)]
            for old, new in zip(average["model_polarity"], current["model_polarity"])
        ]
        averaged_cube.metadata = {
            **average,
            "model_field_values_gauss": fields,
            "model_field_vector_mT": vectors,
            "model_polarity": polarity,
        }

    @staticmethod
    def _merge_relay_flips(averaged_cube, acquired_cube, prefix=""):
        """Fold one completed scan's flips into the multi-scan average: a
        point is flagged if a flip preceded it in any averaged scan, with
        the union of the axes involved. ``prefix="reference_"`` merges the
        reference half's flips the same way."""
        occurred_key = f"{prefix}relay_flip_occurred"
        axes_key = f"{prefix}relay_flip_axes"
        average = averaged_cube.metadata
        current = acquired_cube.metadata
        if occurred_key not in average or occurred_key not in current:
            return
        occurred = [
            bool(a or b)
            for a, b in zip(average[occurred_key], current[occurred_key])
        ]
        axes = [
            ",".join(sorted(set(filter(None, a.split(","))) | set(filter(None, b.split(",")))))
            for a, b in zip(average[axes_key], current[axes_key])
        ]
        averaged_cube.metadata = {
            **average, occurred_key: occurred, axes_key: axes,
        }

    def acquire_frame(self):
        """Acquire and average all fluorescence frames at one field point.

        Uses the camera's already-armed held-open software acquisition
        (see run()) when available, falling back to a plain snap() per
        frame otherwise -- hasattr-guarded so a camera/test-double lacking
        the new driver API (e.g. FakeCamera in existing tests, or an older
        sim) simply never gets the held-open path, exactly mirroring
        ODMRExperiment.acquire_triggered_frame()'s own fallback.
        """
        timing = self._timing
        camera = self.camera
        use_held_open = self._acquisition_open and hasattr(camera, "grab_software_frame")
        frames = []
        for avg_index in range(self.averages):
            t0 = time.perf_counter() if timing else None
            if use_held_open:
                frame = camera.grab_software_frame(timeout_ms=10000)
                stage = "grab_software_frame"
            else:
                frame = camera.snap()
                stage = "snap"
            if timing:
                self._record_timing(stage, avg_index, t0)
                self._merge_camera_log(avg_index, t0 - self._timing_epoch)
            frames.append(frame)
        return np.mean(np.stack(frames), axis=0)

    def process_frame(self, frame):
        """Retain the frame for the live GUI; final analysis is external."""
        self.latest_image = frame
        return None

    def emit_live_update(self, value, _signal):
        self.latest_field = value

        if self.live_update_callback is not None:
            self.live_update_callback(value, self.latest_image)

    def run(self):
        """Execute one or more sweeps and return their averaged ImageCube."""
        if self._timing:
            self.reset_timing()
        run_t0 = time.perf_counter() if self._timing else None

        try:
            with exclusive_camera_access(self.camera):
                self.camera.set_roi(self.acquisition_roi)
                LOGGER.info("Zero Field acquisition ROI: %s", self.acquisition_roi)

                # Enabling here, inside the exclusive_camera_access lease,
                # is what keeps live-view frames out of the camera-side
                # log: that context manager has already stopped live
                # streaming before this point and only resumes it after
                # the lease is released below, so no concurrent snap()
                # call from live view can be captured regardless of this
                # flag's state. See
                # framework/camera_ownership.exclusive_camera_access.
                # Disabling happens in the finally below unconditionally
                # (including on exception) so the flag can never be left
                # on past this lease -- required for that same guarantee
                # to hold on every exit path, not just the normal one.
                if self._timing:
                    if hasattr(self.camera, "enable_timing_diagnostics"):
                        self.camera.enable_timing_diagnostics(True)
                    self.magnet.enable_timing_diagnostics(True)

                # Arm the camera once for the whole run (every scan of a
                # multi-scan average), not diagnostic-gated -- this is the
                # actual speedup, avoiding the ~138 ms arm/disarm SDK cost
                # per frame that plain snap() pays. hasattr-guarded so a
                # camera/test-double lacking the new driver API (FakeCamera
                # in existing tests, an older sim) simply never gets the
                # held-open path -- acquire_frame() falls back to snap().
                # Counts reset first so get_acquisition_counts() at the end
                # of this run reports this run's arms only, mirroring
                # ODMRWorker.start()'s identical reset-then-arm ordering.
                if hasattr(self.camera, "reset_acquisition_counts"):
                    self.camera.reset_acquisition_counts()
                if hasattr(self.camera, "begin_software_acquisition"):
                    acq_t0 = time.perf_counter() if self._timing else None
                    self.camera.begin_software_acquisition()
                    self._acquisition_open = True
                    if self._timing:
                        self._timing_scan_index = None
                        self._timing_point_index = None
                        self._timing_field_gauss = None
                        self._merge_camera_log(None, acq_t0 - self._timing_epoch)

                try:
                    scan_count = self.num_scans if self.averaging_enabled else 1
                    averaged_cube = None
                    completed_scans = 0

                    for scan_index in range(1, scan_count + 1):
                        if self.stop_requested or not self.running:
                            break

                        if self.scan_started_callback is not None:
                            self.scan_started_callback(scan_index, scan_count)

                        scan_t0 = time.perf_counter() if self._timing else None
                        reversed_sweep = self.scan_is_reversed(
                            scan_index, self._alternating
                        )
                        self._sweep_reversed = reversed_sweep
                        self._run_single_sweep(scan_index)
                        if self._timing:
                            self._timing_scan_index = scan_index
                            self._timing_point_index = None
                            self._timing_field_gauss = None
                            self._record_timing("scan_total", None, scan_t0)
                        acquired_cube = self.image_cube
                        stream_path = self._last_scan_stream_path
                        self._finalize_image_cube_metadata(acquired_cube)
                        self._validate_image_cube(acquired_cube)
                        LOGGER.info("Scan %d/%d acquired.", scan_index, scan_count)

                        # A cooperative stop can leave acquired_cube with some but
                        # not all field_points frames -- that's an intentional
                        # partial result (see _validate_image_cube), not a fault,
                        # but it must never be folded into the multi-scan average:
                        # _new_averaged_cube/_update_running_average both assume
                        # every acquired_cube they touch is full-shape.
                        incomplete_sweep = (
                            acquired_cube.data is None
                            or acquired_cube.data.shape[0] != self.field_points
                        )
                        if incomplete_sweep:
                            if averaged_cube is None:
                                averaged_cube = acquired_cube
                            break

                        completed_scans += 1
                        # Average by field, not by acquisition order: a
                        # reverse sweep is flipped to start -> stop first, so
                        # the averaged cube is always in field order.
                        ordered_cube = self._field_ordered(acquired_cube, reversed_sweep)
                        if averaged_cube is None:
                            averaged_cube = (
                                acquired_cube
                                if scan_count == 1
                                else self._new_averaged_cube(ordered_cube)
                            )
                        else:
                            self._update_running_average(
                                averaged_cube.data, ordered_cube.data, completed_scans
                            )
                            self._update_running_extras(
                                averaged_cube, ordered_cube, completed_scans
                            )
                            self._merge_relay_flips(averaged_cube, ordered_cube)
                            self._merge_model_fields(
                                averaged_cube, ordered_cube, completed_scans
                            )
                            self._merge_reference_metadata(
                                averaged_cube, ordered_cube, completed_scans
                            )
                        if scan_count > 1:
                            self._record_completed_scan(
                                averaged_cube, ordered_cube, reversed_sweep
                            )
                        del ordered_cube
                        LOGGER.info("Running average updated.")

                        if stream_path is not None and self.stream_path_is_output:
                            # This file IS the run's deliverable (a single-scan run
                            # streaming directly to the final output path) -- never
                            # delete it, and it isn't a separate "raw scan" artifact
                            # to record either, regardless of save_raw_scans.
                            pass
                        elif self.save_raw_scans:
                            if stream_path is not None:
                                # Streaming already wrote and finalized this scan's
                                # file incrementally; re-saving it via raw_scan_saver
                                # would be a redundant whole-cube rewrite that could
                                # corrupt a file streaming just safely finished.
                                filename = stream_path.name
                            elif self.raw_scan_saver is not None:
                                filename = self.raw_scan_saver(scan_index, acquired_cube)
                            else:
                                raise RuntimeError(
                                    "Saving raw Zero Field scans requires a raw scan saver."
                                )
                            self.raw_scan_filenames.append(str(filename))
                            LOGGER.info("Raw scan saved: %s", filename)
                        elif stream_path is not None:
                            # Streaming always writes a per-scan file for crash
                            # safety regardless of save_raw_scans; if the user
                            # didn't ask to keep it, remove it now that its data is
                            # safely folded into the running average -- matching
                            # the existing contract of no permanent per-scan file
                            # unless requested.
                            stream_path.unlink(missing_ok=True)

                        self._set_averaging_metadata(averaged_cube, completed_scans)
                        if self.scan_completed_callback is not None:
                            self.scan_completed_callback(averaged_cube, scan_index, scan_count)

                        # ``averaged_cube`` is the only image cube intentionally
                        # retained between scans.  Release the completed raw cube.
                        if acquired_cube is not averaged_cube:
                            self.image_cube = None
                            del acquired_cube
                        LOGGER.info("Scan memory released.")

                    if averaged_cube is not None:
                        self._set_averaging_metadata(averaged_cube, completed_scans)
                        self._finalize_image_cube_metadata(averaged_cube)
                        self._validate_image_cube(averaged_cube)
                        self.image_cube = averaged_cube
                finally:
                    # Disarm before disabling timing diagnostics (mirror
                    # image of the enable-then-arm ordering above) so this
                    # teardown's own SDK-call timing is still captured
                    # while it happens on every exit path -- normal
                    # completion, a cooperative stop, or an exception
                    # anywhere in the scan body above.
                    if self._acquisition_open:
                        acq_t0 = time.perf_counter() if self._timing else None
                        if hasattr(self.camera, "end_software_acquisition"):
                            self.camera.end_software_acquisition()
                        self._acquisition_open = False
                        if self._timing:
                            self._timing_scan_index = None
                            self._timing_point_index = None
                            self._timing_field_gauss = None
                            self._merge_camera_log(None, acq_t0 - self._timing_epoch)
                    if self._timing:
                        if hasattr(self.camera, "enable_timing_diagnostics"):
                            self.camera.enable_timing_diagnostics(False)
                        self.magnet.enable_timing_diagnostics(False)
            return self.image_cube
        finally:
            if self._timing:
                self._timing_scan_index = None
                self._timing_point_index = None
                self._timing_field_gauss = None
                self._record_timing("run_total", None, run_t0)

    def _run_single_sweep(self, scan_index):
        """Acquire exactly one averaged fluorescence image per field value."""
        self.state = self.RUNNING
        self.start_time = time.time()
        self.scan_signal = []
        # field_points is the length of the linspace setup_scan() builds, so
        # the cube is allocated once instead of re-copied on every point.
        self.image_cube = ImageCube(
            data=None,
            scan_axis_name=self.scan_axis_name,
            scan_axis_unit=self.scan_axis_unit,
            scan_axis_values=[],
            expected_planes=self.field_points,
        )
        self._stream_writer = None
        self._last_scan_stream_path = None
        self.relay_flip_occurred = []
        self.relay_flip_axes = []
        self._setup_flip_axes = frozenset()
        self.model_field_values_gauss = []
        self.model_field_vector_mT = []
        self.model_polarity = []
        self.point_time_s = []
        self.reference_order = []
        self.reference_point_time_s = []
        self.reference_relay_flip_occurred = []
        self.reference_relay_flip_axes = []
        self.reference_model_field_vector_mT = []
        self.reference_model_polarity = []
        self._ratio_zero_reference_pixels = 0
        self.latest_ratio = None
        self._last_half_vector_mT = None
        self._reference_cube = (
            ImageCube(data=None, expected_planes=self.field_points)
            if self.reference_enabled else None
        )
        self._ratio_cube = (
            ImageCube(data=None, expected_planes=self.field_points)
            if self.reference_enabled else None
        )
        self._sweep_t0 = time.perf_counter()

        # Set before setup_scan() (not just in the point loop below) so
        # setup_scan()'s own magnet.set_vector() call -- when
        # zero_other_axes physically zeroes the non-swept axes -- is
        # correctly tagged as this scan's setup, point_index=None, rather
        # than inheriting stale context left over from the previous scan.
        if self._timing:
            self._timing_scan_index = scan_index
            self._timing_point_index = None
            self._timing_field_gauss = None

        try:
            self.setup_scan()

            if self.stream_scan_path is not None:
                stream_path = self.stream_scan_path(scan_index)
                if stream_path is not None:
                    self._stream_writer = ImageCube.open_streaming_write(
                        stream_path,
                        self.field_points,
                        scan_axis_name=self.scan_axis_name,
                        scan_axis_unit=self.scan_axis_unit,
                        scan_axis_values=self.scan_vector,
                        metadata=dict(self.metadata),
                    )
                    self._last_scan_stream_path = stream_path

            for point_index, field in enumerate(self.scan_vector):
                if self.stop_requested or not self.running:
                    break

                timing = self._timing
                self._timing_scan_index = scan_index
                self._timing_point_index = point_index
                self._timing_field_gauss = field
                point_t0 = time.perf_counter() if timing else None

                # Signal half only, or -- with the reference on -- signal and
                # reference halves alternating S,R / R,S by point index, so
                # linear drift cancels and point i's closing R and point
                # i+1's opening R share a field, with no magnet move between.
                reference_first = self.reference_enabled and point_index % 2 == 1
                if not self.reference_enabled:
                    halves = ("S",)
                elif reference_first:
                    halves = ("R", "S")
                else:
                    halves = ("S", "R")
                acquired = {}
                for half in halves:
                    target = field if half == "S" else self.reference_field_gauss
                    acquired[half] = self._acquire_half(
                        target, "" if half == "S" else "reference_"
                    )

                averaged_image, flipped, snapshot, point_time = acquired["S"]
                model_gauss, model_vector_mT, model_polarity = snapshot
                if self.reference_enabled:
                    reference_image, reference_flipped, reference_snapshot, reference_time = (
                        acquired["R"]
                    )
                    # Computed before anything is stored, so a failure here
                    # can't leave the signal and reference planes misaligned.
                    ratio = self._ratio_plane(averaged_image, reference_image)

                # Store measurement exactly once for this field value.
                self.image_cube.add(averaged_image)
                self.relay_flip_occurred.append(bool(flipped))
                self.relay_flip_axes.append(flipped)
                self.model_field_values_gauss.append(model_gauss)
                self.model_field_vector_mT.append(model_vector_mT)
                self.model_polarity.append(model_polarity)
                self.point_time_s.append(point_time)
                if self.reference_enabled:
                    self._reference_cube.add(reference_image)
                    self._ratio_cube.add(ratio)
                    self.reference_order.append("RS" if reference_first else "SR")
                    self.reference_point_time_s.append(reference_time)
                    self.reference_relay_flip_occurred.append(bool(reference_flipped))
                    self.reference_relay_flip_axes.append(reference_flipped)
                    self.reference_model_field_vector_mT.append(reference_snapshot[1])
                    self.reference_model_polarity.append(reference_snapshot[2])
                    self.latest_ratio = ratio
                    if self._stream_writer is not None:
                        # Before the data plane: planes_written (bumped by
                        # write_plane) then never counts a point whose
                        # reference / ratio planes are missing.
                        self._stream_writer.write_extra_plane(
                            "reference", point_index, reference_image
                        )
                        self._stream_writer.write_extra_plane("ratio", point_index, ratio)
                if self._stream_writer is not None:
                    t0 = time.perf_counter() if timing else None
                    self._stream_writer.write_plane(point_index, averaged_image)
                    if timing:
                        self._record_timing("write_plane", None, t0)

                t0 = time.perf_counter() if timing else None
                signal = self.process_frame(averaged_image)
                if timing:
                    self._record_timing("process_frame", None, t0)
                self.scan_signal.append(signal)

                # Update live display and progress once per stored image.
                t0 = time.perf_counter() if timing else None
                self.emit_live_update(field, signal)
                if timing:
                    self._record_timing("emit_live_update", None, t0)
                self.emit_progress()

                if timing:
                    self._record_timing("point_total", None, point_t0)

            return self.scan_vector, np.asarray(self.scan_signal)

        except Exception:
            self.state = self.ERROR
            raise

        finally:
            self.end_time = time.time()
            # A cooperative stop (or an exception, though that propagates
            # out before anything downstream reads this cube) can leave
            # fewer frames than field_points configured. scan_axis_values
            # and field_values_gauss are written in full at the start of
            # setup_scan(), before any point is acquired, and never
            # revisited -- keep them describing only the frames actually
            # stored, not the full configured sweep. An ImageCube whose
            # scan-axis length disagrees with its own frame count is
            # invalid input to downstream analysis (mean_fluorescence_vs_field
            # requires len(scan_axis_values) == len(data)) regardless of why
            # acquisition stopped early.
            frame_count = (
                0 if self.image_cube.data is None else self.image_cube.data.shape[0]
            )
            if frame_count != len(self.scan_vector):
                self.image_cube.scan_axis_values = self.scan_vector[:frame_count]
                metadata = dict(self.image_cube.metadata)
                if "field_values_gauss" in metadata:
                    metadata["field_values_gauss"] = list(
                        metadata["field_values_gauss"]
                    )[:frame_count]
                self.image_cube.metadata = metadata
            # Release the unfilled part of the pre-allocated buffer after an
            # early stop. No-op for a complete sweep.
            self.image_cube.truncate(frame_count)
            # One entry per stored frame, like field_values_gauss.
            for per_point in (
                self.relay_flip_occurred, self.relay_flip_axes,
                self.model_field_values_gauss, self.model_field_vector_mT,
                self.model_polarity, self.point_time_s,
                self.reference_order, self.reference_point_time_s,
                self.reference_relay_flip_occurred, self.reference_relay_flip_axes,
                self.reference_model_field_vector_mT, self.reference_model_polarity,
            ):
                del per_point[frame_count:]
            if self.reference_enabled:
                # Same plane count and order as data; none if nothing was
                # acquired, matching data=None.
                extras = {}
                if frame_count:
                    for name, cube in (
                        ("reference", self._reference_cube),
                        ("ratio", self._ratio_cube),
                    ):
                        cube.truncate(frame_count)
                        extras[name] = cube.data
                self.image_cube.extra_datasets = extras
                self._reference_cube = None
                self._ratio_cube = None
                if self._ratio_zero_reference_pixels:
                    LOGGER.warning(
                        "Zero Field scan %d: %d ratio pixel(s) set to NaN "
                        "because the reference image was 0 there.",
                        scan_index, self._ratio_zero_reference_pixels,
                    )
            flip_metadata = self._per_point_metadata()
            self.image_cube.metadata = {**self.image_cube.metadata, **flip_metadata}
            if self._stream_writer is not None:
                writer = self._stream_writer
                self._stream_writer = None
                # scan_complete only means "every field point was written";
                # a cooperative stop and a crash both leave it False, so
                # stopped_by_user is what distinguishes intent from failure.
                swept_fully = len(self.scan_signal) == len(self.scan_vector)
                writer.update_metadata(
                    {"stopped_by_user": bool(self.stop_requested), **flip_metadata}
                )
                if swept_fully:
                    writer.finalize()
                else:
                    writer.close()
            try:
                self.cleanup_scan()
            finally:
                self.cleanup()

    def _acquire_half(self, field_gauss, stage_prefix):
        """Command *field_gauss*, settle, and acquire one averaged image.

        One "half" of a point: the signal half (the only one with the
        reference off, behaving exactly as a point always has) or the
        reference half. Returns (image, flipped_axes, model_snapshot,
        acquisition_midpoint_s).

        Every command gets flip detection and post-flip settling. With the
        reference on, a command identical to the previous half's (the R,R
        between consecutive points) still goes through set_vector() --
        the magnet's change-detection cache skips the write -- but gets no
        settling sleep, since nothing moved.
        """
        timing = self._timing
        vector_mT = self._point_vector_mT(field_gauss)
        repeated = (
            self.reference_enabled
            and self._last_half_vector_mT is not None
            and vector_mT == self._last_half_vector_mT
        )

        # Apply magnetic field/current. Flips caused by setup_scan() are
        # folded into the first command of the sweep.
        flipped = (
            self._as_flip_set(self.set_scan_point(field_gauss)) | self._setup_flip_axes
        )
        self._setup_flip_axes = frozenset()
        self._last_half_vector_mT = vector_mT
        # What was actually commanded, read before anything else can
        # change the magnet; stored with the frame.
        snapshot = self._model_snapshot()

        # Wait for field stabilization.
        if self.settling_time_ms > 0 and not repeated:
            t0 = time.perf_counter() if timing else None
            time.sleep(self.settling_time_ms / 1000.0)
            if timing:
                self._record_timing(f"{stage_prefix}settling_sleep", None, t0)

        # A relay flip cut and restored coil current: wait longer.
        # Like the settling sleep above, not interruptible by stop.
        if flipped and self.post_flip_settling_ms > 0:
            t0 = time.perf_counter() if timing else None
            time.sleep(self.post_flip_settling_ms / 1000.0)
            if timing:
                self._record_timing(f"{stage_prefix}post_flip_settling_sleep", None, t0)

        # Acquire averaged fluorescence image. Its time is the midpoint of
        # the acquisition, relative to the sweep start.
        acquire_t0 = time.perf_counter()
        image = self.acquire_frame()
        midpoint = 0.5 * (acquire_t0 + time.perf_counter()) - self._sweep_t0
        if timing and stage_prefix:
            self._record_timing(f"{stage_prefix}acquire", None, acquire_t0)
        return image, flipped, snapshot, midpoint

    def _ratio_plane(self, signal, reference):
        """Per-pixel signal / reference as float64; NaN where reference is 0."""
        signal = np.asarray(signal, dtype=np.float64)
        reference = np.asarray(reference, dtype=np.float64)
        zero = reference == 0
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = signal / reference
        if np.any(zero):
            ratio[zero] = np.nan
            self._ratio_zero_reference_pixels += int(np.count_nonzero(zero))
        return ratio

    def _new_averaged_cube(self, acquired_cube):
        # Per-sweep keys (one scan's timestamps and direction) would mislabel
        # the average; it records them per scan instead.
        metadata = {
            key: value
            for key, value in copy.deepcopy(acquired_cube.metadata).items()
            if key not in self._PER_SWEEP_ONLY_KEYS
        }
        return ImageCube(
            data=np.array(acquired_cube.data, dtype=np.float64, copy=True),
            axes=copy.deepcopy(acquired_cube.axes),
            metadata=metadata,
            experiment_type=acquired_cube.experiment_type,
            scan_axis_name=acquired_cube.scan_axis_name,
            scan_axis_unit=acquired_cube.scan_axis_unit,
            scan_axis_values=copy.deepcopy(acquired_cube.scan_axis_values),
            # Running averages of the reference / ratio planes, owned copies.
            extra_datasets={
                name: np.array(value, dtype=np.float64, copy=True)
                for name, value in acquired_cube.extra_datasets.items()
                if name in self._PER_POINT_EXTRA_KEYS
            },
        )

    @staticmethod
    def _update_running_average(average, current, scan_count):
        """Update ``average`` in place without retaining prior scan images."""
        average += (np.asarray(current, dtype=np.float64) - average) / scan_count

    def _set_averaging_metadata(self, image_cube, completed_scans):
        metadata = dict(image_cube.metadata)
        scan_count = self.num_scans if self.averaging_enabled else 1
        metadata.update(
            {
                "averaging_enabled": self.averaging_enabled,
                "num_scans": self.num_scans,
                "save_raw_scans": self.save_raw_scans,
                "completed_scans": completed_scans,
                # True only once every requested scan has completed --
                # distinct from a single scan's own scan_complete (written
                # by the streaming writer), which just means that one
                # sweep reached its last field point.
                "experiment_complete": completed_scans == scan_count,
                # A stop that arrives after everything already finished
                # doesn't make the result incomplete -- experiment_complete
                # is purely data-driven -- but it's still worth recording
                # that a stop was requested at some point during the run.
                "stopped_by_user": bool(self.stop_requested),
                # Re-read fresh on every call (like the accumulator above),
                # not written once by _set_static_metadata(): a flat key
                # there would freeze at scan 1's value once _new_averaged_cube()
                # deep-copies it, hiding every later scan's entry vector.
                "scan_entry_magnet_vectors_mT": [
                    dict(vector) for vector in self.scan_entry_magnet_vectors_mT
                ],
            }
        )
        # Omitted (not set to []) when stream_path_is_output: there are
        # genuinely no separate raw-scan files in that case, and an empty
        # list would be ambiguous with "raw scans weren't requested" -- the
        # key is already absent in that latter case, so this keeps
        # "key absent" meaning "no raw-scan-filenames concept applies here"
        # consistent everywhere.
        if self.save_raw_scans and not self.stream_path_is_output:
            metadata["raw_scan_filenames"] = list(self.raw_scan_filenames)
        if scan_count > 1:
            # One list per completed scan, each in that scan's acquisition
            # order, for offline drift fitting.
            metadata["scan_point_times_s"] = [
                list(times) for times in self._scan_point_times_s
            ]
            if self.reference_enabled:
                metadata["scan_reference_point_times_s"] = [
                    list(times) for times in self._scan_reference_point_times_s
                ]
        if scan_count > 1 and self._alternating:
            metadata["scan_directions"] = list(self._scan_directions)
            metadata["forward_scan_count"] = self._direction_counts["forward"]
            metadata["reverse_scan_count"] = self._direction_counts["reverse"]
        image_cube.metadata = metadata

    # field_value_definitions entries added only when the reference is on.
    _REFERENCE_DEFINITIONS = {
        "reference": (
            "Dataset: per-point fluorescence image at the reference field "
            "(swept axis at reference_field_gauss, other axes as for the "
            "signal), same plane order and averaging as data."
        ),
        "ratio": (
            "Dataset: per-pixel data / reference for each point (float64). "
            "NaN where the reference was 0. A multi-scan average is the mean "
            "of per-scan ratios, as ODMR averages ON/OFF ratios."
        ),
        "ratio_forward_average": (
            "Dataset (alternate_direction on): mean ratio of forward scans, "
            "start -> stop field order. ratio_reverse_average likewise."
        ),
        "reference_order": (
            "Per point: 'SR' (signal then reference) or 'RS'. Alternates with "
            "the point index in sweep order; 'mixed' in an average whose "
            "scans differed."
        ),
        "reference_point_time_s": (
            "Midpoint of each point's reference acquisition, same clock as "
            "point_time_s. A multi-scan average stores one list per scan as "
            "scan_reference_point_times_s."
        ),
        "reference_relay_flip_occurred": (
            "Whether the reference half's field command flipped a relay "
            "(reference_relay_flip_axes: which). relay_flip_* cover the "
            "signal half."
        ),
        "reference_model_field_vector_mT": (
            "Modelled [x, y, z] field of the reference half, in mT. Not a "
            "measurement. reference_model_polarity: its relay branches."
        ),
    }

    # field_value_definitions entries replaced or added for a vector sweep.
    _VECTOR_DEFINITIONS = {
        "field_direction": (
            "Unit vector [nx, ny, nz] of the sweep direction (field_axis "
            "'vector'), normalised from the requested direction. Each point "
            "commands B = s * field_direction on all three axes."
        ),
        "field_values_gauss": (
            "Requested signed magnitude s along field_direction per point (the "
            "linspace), in G; same as scan_axis_values. The commanded vector "
            "is s * field_direction."
        ),
        "model_field_values_gauss": (
            "Component of the modelled field vector along field_direction, in "
            "G, read right after each point's set_vector(). Differs from the "
            "request where an axis is in a branch gap. Not a measurement; "
            "model_field_vector_mT has the full vector."
        ),
    }
    _VECTOR_REFERENCE_DEFINITION = (
        "Dataset: per-point fluorescence image at the reference field "
        "(reference_field_gauss * field_direction), same plane order and "
        "averaging as data."
    )

    def _set_static_metadata(self):
        """Record the known conditions that define this field sweep."""
        metadata = dict(self.metadata)
        metadata.update(
            {
                "experiment_name": "Zero Field",
                "acquisition_started_at_utc": self._acquisition_started_at,
                "acquisition_roi": self.acquisition_roi,
                "camera_exposure_s": self._camera_setting("exposure_time"),
                "camera_binning": self._camera_setting("binning"),
                "field_axis": self.field_axis,
                # Unit vector [nx, ny, nz] of a vector sweep; None otherwise.
                "field_direction": (
                    None if self.field_direction is None else list(self.field_direction)
                ),
                "field_values_gauss": self.scan_vector.tolist(),
                "field_value_definitions": {
                    "field_values_gauss": (
                        "Requested swept-axis field per point (the linspace), "
                        "in G; same as scan_axis_values."
                    ),
                    "model_field_values_gauss": (
                        "Swept-axis field the magnet was commanded to per its "
                        "calibration model, in G, read right after each "
                        "point's set_vector(). Differs from the request in a "
                        "branch gap (model = the branch intercept). Not a "
                        "measurement."
                    ),
                    "model_field_vector_mT": (
                        "Full modelled [x, y, z] field per point, in mT, read "
                        "the same way. Not a measurement."
                    ),
                    "model_polarity": (
                        "Relay branch per point as [x, y, z]: '+', '-', or '?' "
                        "(unknown); 'mixed' in a multi-scan average whose scans "
                        "differed. In a branch overlap the field matches the "
                        "request on either branch, so this is what shows "
                        "which branch was used."
                    ),
                    "point_time_s": (
                        "Acquisition time of each point (midpoint of its "
                        "frame acquisition), in s since that scan's sweep "
                        "started, in acquisition order. A multi-scan average "
                        "stores one list per scan as scan_point_times_s."
                    ),
                    "field_order": (
                        "A single scan and every per-scan raw file are in "
                        "acquisition order (sweep_direction says which, when "
                        "alternate_direction is on). A multi-scan average, "
                        "including forward_average/reverse_average, is always "
                        "in start -> stop field order, matching "
                        "field_values_gauss, regardless of sweep direction."
                    ),
                },
                "field_point_count": self.field_points,
                "field_start_gauss": self.field_start,
                "field_stop_gauss": self.field_stop,
                "settling_time_ms": self.settling_time_ms,
                "post_flip_settling_ms": self.post_flip_settling_ms,
                "averages_per_point": self.averages,
                "camera_model": type(self.camera).__name__,
                "zero_other_axes": self.zero_other_axes,
                "alternate_direction": self._alternating,
                "reference_enabled": self.reference_enabled,
                "initial_magnet_vector_mT": (
                    dict(self._initial_magnet_vector_mT)
                    if self._initial_magnet_vector_mT is not None
                    else None
                ),
                "non_swept_axis_field_mT": dict(self._resolved_non_swept_field_mT),
            }
        )
        if self._alternating:
            metadata["sweep_direction"] = (
                "reverse" if self._sweep_reversed else "forward"
            )
        if self.reference_enabled:
            metadata["reference_field_gauss"] = self.reference_field_gauss
            definitions = dict(metadata["field_value_definitions"])
            definitions.update(self._REFERENCE_DEFINITIONS)
            metadata["field_value_definitions"] = definitions
        if self.vector_mode:
            # Only for a vector sweep, so single-axis metadata is unchanged.
            definitions = dict(metadata["field_value_definitions"])
            definitions.update(self._VECTOR_DEFINITIONS)
            if self.reference_enabled:
                definitions["reference"] = self._VECTOR_REFERENCE_DEFINITION
            metadata["field_value_definitions"] = definitions
        power_supply_models = self._power_supply_models()
        if power_supply_models:
            metadata["power_supply_models"] = power_supply_models
        magnet_configuration = getattr(self.magnet, "config", None)
        if isinstance(magnet_configuration, Mapping):
            metadata["magnet_configuration"] = copy.deepcopy(magnet_configuration)
        self.metadata = metadata

    def _finalize_image_cube_metadata(self, image_cube):
        """Add image descriptors after the acquired frame shape is known."""
        data = np.asarray(image_cube.data)
        metadata = dict(image_cube.metadata)
        if data.ndim >= 3:
            metadata.update(
                {
                    "image_height_px": int(data.shape[1]),
                    "image_width_px": int(data.shape[2]),
                    "image_dtype": str(data.dtype),
                }
            )
        image_cube.metadata = metadata

    def _validate_image_cube(self, image_cube):
        """Reject incomplete or structurally inconsistent acquired datasets.

        A cooperative stop deliberately yields fewer than field_points
        frames -- that's the correct, intentional result, not a fault -- so
        the frame-count-vs-field_points cardinality check is skipped in that
        case. Everything else (structural type, non-zero dimensions, and
        the data/metadata consistency checks below) stays unconditional:
        those catch real bugs regardless of why acquisition stopped, and
        the field_values_gauss length check in particular naturally stays
        satisfied because _run_single_sweep already truncates
        scan_axis_values/field_values_gauss to the actual acquired count.
        """
        data = image_cube.data
        if self.stop_requested and data is None:
            return
        if not isinstance(data, np.ndarray) or data.ndim != 3:
            raise ValueError(
                "Zero Field ImageCube must contain a 3-D stack of acquired frames."
            )

        frame_count, height, width = data.shape
        if not self.stop_requested and frame_count != self.field_points:
            raise ValueError(
                "Zero Field ImageCube frame count "
                f"({frame_count}) does not match field point count "
                f"({self.field_points})."
            )
        if height < 1 or width < 1:
            raise ValueError("Zero Field ImageCube frames must have non-zero dimensions.")

        # The dense ImageCube stack guarantees one common shape and dtype;
        # compare its recorded descriptors to detect metadata/data divergence.
        metadata = image_cube.metadata
        if len(metadata.get("field_values_gauss", [])) != frame_count:
            raise ValueError(
                "Zero Field metadata field vector length does not match the "
                "number of stored frames."
            )
        if metadata.get("image_height_px") != height or metadata.get("image_width_px") != width:
            raise ValueError(
                "Zero Field image dimensions do not match ImageCube metadata."
            )
        if metadata.get("image_dtype") != str(data.dtype):
            raise ValueError(
                "Zero Field image dtype does not match ImageCube metadata."
            )
        for name in self._PER_POINT_EXTRA_KEYS:
            extra = image_cube.extra_datasets.get(name)
            if extra is not None and np.shape(extra) != data.shape:
                raise ValueError(
                    f"Zero Field {name} planes do not match the data planes."
                )

    def _camera_setting(self, attribute):
        value = getattr(self.camera, attribute, None)
        return value.item() if isinstance(value, np.generic) else value

    def _power_supply_models(self):
        supplies = getattr(self.magnet, "power_supplies", None)
        if not isinstance(supplies, Mapping):
            return {}
        return {
            str(axis): type(power_supply).__name__
            for axis, power_supply in supplies.items()
        }
