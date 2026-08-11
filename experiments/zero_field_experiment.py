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


LOGGER = logging.getLogger(__name__)


class ZeroFieldExperiment(ScanExperiment):
    """Acquire fluorescence images while sweeping one magnetic-field axis.

    Field inputs and the ImageCube scan axis are expressed in gauss.  The
    existing :class:`Magnet` API uses millitesla internally, so values are
    converted only at the hardware boundary.
    """

    scan_axis_name = "Magnetic Field"
    scan_axis_unit = "G"

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

        axis = field_axis.upper()
        if axis not in ("X", "Y", "Z"):
            raise ValueError("field_axis must be 'X', 'Y', or 'Z'.")
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

        self.hardware_manager = hardware_manager
        self.camera = camera
        self.magnet = magnet
        self.field_start = field_start
        self.field_stop = field_stop
        self.field_points = field_points
        self.field_axis = axis
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
        # Keep all supplies enabled for the full sweep.  In particular, the
        # two non-swept axes remain at zero current but must not be repeatedly
        # power-cycled by their per-axis zero-field safety path.
        self.magnet.enable()

        # Magnet.get_vector() returns values in millitesla.  Record what was
        # actually there before this scan touches anything, whether or not
        # zero_other_axes ends up overriding it.
        entry_vector_mT = self.magnet.get_vector()
        self.scan_entry_magnet_vectors_mT.append(dict(entry_vector_mT))
        if self._initial_magnet_vector_mT is None:
            self._initial_magnet_vector_mT = dict(entry_vector_mT)

        non_swept_axes = [
            axis for axis in ("x", "y", "z") if axis != self.field_axis.lower()
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
            # (e.g. an extra apply_global_polarity_noop/flip row wrongly
            # attributed to point 0).
            timing = self._timing
            t0 = time.perf_counter() if timing else None
            self.magnet.set_vector(
                bx=resolved_field_mT["x"],
                by=resolved_field_mT["y"],
                bz=resolved_field_mT["z"],
            )
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

        self.scan_vector = np.linspace(
            self.field_start,
            self.field_stop,
            self.field_points,
        )
        if self._acquisition_started_at is None:
            self._acquisition_started_at = datetime.now(timezone.utc).isoformat()
        self._set_static_metadata()
        self.image_cube.scan_axis_name = self.scan_axis_name
        self.image_cube.scan_axis_unit = self.scan_axis_unit
        self.image_cube.scan_axis_values = self.scan_vector
        self.image_cube.metadata = dict(self.metadata)

    def cleanup_scan(self):
        """Return the magnet to its existing safe disabled state after a scan."""
        self.magnet.disable()

    def set_scan_point(self, value):
        # Apply magnetic field/current for this measurement point.
        fields_mT = dict(self._configured_field_mT)
        fields_mT[self.field_axis.lower()] = value * 0.1

        timing = self._timing
        t0 = time.perf_counter() if timing else None
        self.magnet.set_vector(
            bx=fields_mT["x"],
            by=fields_mT["y"],
            bz=fields_mT["z"],
        )
        if timing:
            self._merge_magnet_log(t0 - self._timing_epoch)

    def acquire_frame(self):
        """Acquire and average all fluorescence frames at one field point."""
        timing = self._timing
        frames = []
        for avg_index in range(self.averages):
            t0 = time.perf_counter() if timing else None
            frame = self.camera.snap()
            if timing:
                self._record_timing("snap", avg_index, t0)
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
                        if averaged_cube is None:
                            averaged_cube = (
                                acquired_cube
                                if scan_count == 1
                                else self._new_averaged_cube(acquired_cube)
                            )
                        else:
                            self._update_running_average(
                                averaged_cube.data, acquired_cube.data, completed_scans
                            )
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
        self.image_cube = ImageCube(
            data=None,
            scan_axis_name=self.scan_axis_name,
            scan_axis_unit=self.scan_axis_unit,
            scan_axis_values=[],
        )
        self._stream_writer = None
        self._last_scan_stream_path = None

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

                # Apply magnetic field/current.
                self.set_scan_point(field)

                # Wait for field stabilization.
                if self.settling_time_ms > 0:
                    t0 = time.perf_counter() if timing else None
                    time.sleep(self.settling_time_ms / 1000.0)
                    if timing:
                        self._record_timing("settling_sleep", None, t0)

                # Acquire averaged fluorescence image.
                averaged_image = self.acquire_frame()

                # Store measurement exactly once for this field value.
                self.image_cube.add(averaged_image)
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
            if self._stream_writer is not None:
                writer = self._stream_writer
                self._stream_writer = None
                # scan_complete only means "every field point was written";
                # a cooperative stop and a crash both leave it False, so
                # stopped_by_user is what distinguishes intent from failure.
                swept_fully = len(self.scan_signal) == len(self.scan_vector)
                writer.update_metadata({"stopped_by_user": bool(self.stop_requested)})
                if swept_fully:
                    writer.finalize()
                else:
                    writer.close()
            try:
                self.cleanup_scan()
            finally:
                self.cleanup()

    def _new_averaged_cube(self, acquired_cube):
        return ImageCube(
            data=np.array(acquired_cube.data, dtype=np.float64, copy=True),
            axes=copy.deepcopy(acquired_cube.axes),
            metadata=copy.deepcopy(acquired_cube.metadata),
            experiment_type=acquired_cube.experiment_type,
            scan_axis_name=acquired_cube.scan_axis_name,
            scan_axis_unit=acquired_cube.scan_axis_unit,
            scan_axis_values=copy.deepcopy(acquired_cube.scan_axis_values),
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
        image_cube.metadata = metadata

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
                "field_values_gauss": self.scan_vector.tolist(),
                "field_point_count": self.field_points,
                "field_start_gauss": self.field_start,
                "field_stop_gauss": self.field_stop,
                "settling_time_ms": self.settling_time_ms,
                "averages_per_point": self.averages,
                "camera_model": type(self.camera).__name__,
                "zero_other_axes": self.zero_other_axes,
                "initial_magnet_vector_mT": (
                    dict(self._initial_magnet_vector_mT)
                    if self._initial_magnet_vector_mT is not None
                    else None
                ),
                "non_swept_axis_field_mT": dict(self._resolved_non_swept_field_mT),
            }
        )
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
