import numpy as np
import threading
import time
import logging
from contextlib import contextmanager

from andor3 import Andor3
from hardware.camera.streaming import StreamController
from utils.camera_diagnostics import log_camera_snap, log_event


LOGGER = logging.getLogger(__name__)


class AndorNeoAndor3:

    def __init__(self, stream_shutdown_timeout_s=12.0):

        self.cam = None

        self.exposure_time = 0.02
        self.roi = None
        self.binning = 1

        self.latest_frame = None

        self._lock = threading.Lock()
        self._stream_controller = StreamController()
        self.stream_shutdown_timeout_s = stream_shutdown_timeout_s

        # Temporary diagnostic instrumentation, off by default. Only
        # snap_external_frames() (ODMR's external-trigger path) is ever
        # instrumented -- snap() (live view, Zero Field) never touches
        # this, so a live-view session cannot grow this log regardless of
        # the flag. enable_timing_diagnostics() also clears the log, so
        # nothing lingers past the scan that turned it on.
        self._timing_enabled = False
        self._timing_log = []

        # Held-open external acquisition state. See begin_external_acquisition
        # / grab_external_frame / end_external_acquisition below. Any method
        # that touches camera features outside this trio defensively closes
        # an open acquisition first (see each method) so a caller that never
        # learned about this API -- live view's snap(), Zero Field, a future
        # script -- can't be broken by one left open.
        self._acquisition_open = False
        self._acquisition_frames_served = 0
        self._acquisition_start_count = 0
        self._acquisition_stop_count = 0

    @property
    def streaming(self):
        return self._stream_controller.is_streaming

    # =====================================================
    # TIMING DIAGNOSTICS (temporary; off by default)
    # =====================================================

    def enable_timing_diagnostics(self, enabled):
        """Turn per-stage timing of snap_external_frames() on/off.

        Always clears the accumulated log, whether enabling or disabling,
        so growth is bounded to at most one in-flight scan's worth of
        entries and nothing survives being turned off.
        """
        self._timing_enabled = bool(enabled)
        self._timing_log = []

    def pop_timing_log(self):
        """Return and clear the accumulated per-stage timing log."""
        log = self._timing_log
        self._timing_log = []
        return log

    def reset_acquisition_counts(self):
        """Zero the AcquisitionStart/Stop counters (call once per scan)."""
        self._acquisition_start_count = 0
        self._acquisition_stop_count = 0

    def get_acquisition_counts(self):
        """Return (acquisition_start_count, acquisition_stop_count) since
        the last reset_acquisition_counts() call -- how many times the
        camera was actually armed/disarmed, for verifying a scan armed
        once rather than once per frame."""
        return (self._acquisition_start_count, self._acquisition_stop_count)

    # =====================================================
    # HELD-OPEN EXTERNAL ACQUISITION
    # =====================================================
    # Arm once (begin_external_acquisition), pull many frames from the
    # single armed acquisition (grab_external_frame), disarm once
    # (end_external_acquisition). snap_external_frames() below is
    # reimplemented on top of this trio, so existing callers are
    # unaffected; ODMR's scan loop calls the trio directly to avoid
    # re-arming per frame.

    def begin_external_acquisition(self):
        if self.cam is None:
            raise RuntimeError("Camera is not connected")
        if self._acquisition_open:
            return

        timing = self._timing_enabled

        t0 = time.perf_counter() if timing else None
        self.cam.setEnumIndex("TriggerMode", 6)  # External
        if timing:
            self._timing_log.append(("trigger_mode_set", None, time.perf_counter() - t0))

        try:
            t0 = time.perf_counter() if timing else None
            self.cam.flush()
            if timing:
                self._timing_log.append(("flush_pre", None, time.perf_counter() - t0))
        except Exception:
            pass

        t0 = time.perf_counter() if timing else None
        self.cam.queueBuffer(1)
        if timing:
            self._timing_log.append(("queue_buffer", None, time.perf_counter() - t0))

        t0 = time.perf_counter() if timing else None
        self.cam.command("AcquisitionStart")
        self._acquisition_start_count += 1
        if timing:
            self._timing_log.append(("acquisition_start", None, time.perf_counter() - t0))

        self._acquisition_open = True
        self._acquisition_frames_served = 0

    def grab_external_frame(self, timeout_ms=10000):
        if not self._acquisition_open:
            raise RuntimeError(
                "grab_external_frame() called without an open acquisition; "
                "call begin_external_acquisition() first."
            )

        timing = self._timing_enabled

        # Timed unconditionally (not just under the timing-diagnostics
        # flag): the stale-buffer plausibility check below needs this
        # duration on every call, not just when NV_ODMR_TIMING=1.
        wait_t0 = time.perf_counter()
        try:
            # requeue=True re-arms the same buffer for the next trigger;
            # copy=True is required alongside it (SDK wrapper's own
            # docstring) -- without it the returned array is a live view
            # into a buffer the SDK is free to overwrite as soon as it's
            # requeued, which happens before waitBuffer() even returns.
            raw = self.cam.waitBuffer(timeout_ms, copy=True, requeue=True)
        except Exception as error:
            if self._acquisition_frames_served > 0:
                raise RuntimeError(
                    f"grab_external_frame() failed after "
                    f"{self._acquisition_frames_served} frame(s) were already "
                    "served under this AcquisitionStart. The camera stopped "
                    "responding to external triggers partway through a "
                    "held-open acquisition -- either a genuine trigger "
                    "timeout, or the CycleMode='Continuous' assumption "
                    "(that one AcquisitionStart can serve many sequential "
                    "external triggers) is not holding on this camera/SDK. "
                    "If this recurs, fall back to per-point acquisition "
                    "scope (arm once per frequency point instead of once "
                    "per scan)."
                ) from error
            raise
        wait_duration_s = time.perf_counter() - wait_t0

        if timing:
            self._timing_log.append(
                ("wait_buffer", self._acquisition_frames_served, wait_duration_s)
            )

        # A genuine frame cannot complete faster than its own exposure
        # time: the sensor must integrate for exposure_time regardless of
        # trigger-wait or readout, both of which only add to that floor.
        # A stale/pre-filled buffer (a stray gate -- e.g. a leftover
        # repetition of a looping sequence -- fills a buffer between
        # grab_external_frame() calls) returns near-instantly instead,
        # since waitBuffer() only has to notice a buffer already marked
        # complete, not actually wait for one. 0.5x exposure_time is a
        # conservative floor: a real frame is physically incapable of
        # landing below 1.0x, so this leaves a 2x margin against
        # timing/measurement jitter alone, while still rejecting the
        # observed bug (~0.7 ms against an ~186 ms expectation) with
        # roughly another order of magnitude of margin beyond that.
        min_plausible_s = 0.5 * self.exposure_time
        if wait_duration_s < min_plausible_s:
            raise RuntimeError(
                f"grab_external_frame() returned in "
                f"{wait_duration_s * 1000:.3f} ms, faster than the "
                f"{min_plausible_s * 1000:.3f} ms floor implied by the "
                f"{self.exposure_time * 1000:.3f} ms configured exposure. "
                "This buffer was very likely already filled before this "
                "call started waiting -- i.e. a stray camera gate, not the "
                "one belonging to this frame -- and has been rejected "
                "rather than returned as if it were fresh."
            )

        t0 = time.perf_counter() if timing else None
        frame = self._buffer_to_image(raw)
        if timing:
            self._timing_log.append(
                ("buffer_to_image", self._acquisition_frames_served, time.perf_counter() - t0)
            )

        self._acquisition_frames_served += 1

        with self._lock:
            self.latest_frame = frame

        return frame

    def end_external_acquisition(self):
        if not self._acquisition_open:
            return

        # Cleared first: a raise below must not leave the flag falsely
        # True (that would block the defensive self-heal in snap() etc.
        # from ever trying again).
        self._acquisition_open = False
        timing = self._timing_enabled

        try:
            t0 = time.perf_counter() if timing else None
            self.cam.command("AcquisitionStop")
            self._acquisition_stop_count += 1
            if timing:
                self._timing_log.append(("acquisition_stop", None, time.perf_counter() - t0))
        except Exception:
            pass

        try:
            t0 = time.perf_counter() if timing else None
            self.cam.flush()
            if timing:
                self._timing_log.append(("flush_post", None, time.perf_counter() - t0))
        except Exception:
            pass

    @contextmanager
    def external_acquisition(self):
        self.begin_external_acquisition()
        try:
            yield
        finally:
            self.end_external_acquisition()

    # =====================================================
    # CONNECT
    # =====================================================

    def connect(self):

        print("\nConnecting to Andor Neo using andor3...\n")

        self.cam = Andor3()
        self.cam.open(0)

        self.configure_camera()

        print("Connected to Andor Neo using andor3.")

    # =====================================================
    # CONFIGURE CAMERA
    # =====================================================

    def configure_camera(self):

        self.cam.setBool("SensorCooling", True)
        self.cam.setEnumString("ElectronicShutteringMode", "Global")
        self.cam.setEnumString("PixelReadoutRate", "280 MHz")
        self.cam.setBool("Overlap", False)
        self.cam.setBool("SpuriousNoiseFilter", True)
        self.cam.setEnumIndex("SimplePreAmpGainControl", 2)
        self.cam.setEnumString("PixelEncoding", "Mono16")
        self.cam.setEnumString("CycleMode", "Continuous")
        self.cam.setFloat("ExposureTime", self.exposure_time)
        # sCMOS exposure is quantised to the row period -- the SDK may not
        # accept exactly what was requested. Read back what it actually
        # holds rather than trusting the request.
        self.exposure_time = self.cam.getFloat("ExposureTime")

        try:
            self.cam.setBool("IOInvert", False)
        except Exception:
            pass

        try:
            self.cam.setFloat("ExternalTriggerDelay", 0.0)
        except Exception:
            pass
        self.cam.setBool("VerticallyCentreAOI", False)
    # =====================================================
    # BUFFER TO IMAGE
    # =====================================================

    def _buffer_to_image(self, buffer):

        width = self.cam.getInt("AOIWidth")
        height = self.cam.getInt("AOIHeight")
        stride = self.cam.getInt("AOIStride")

        buffer = np.asarray(buffer, dtype=np.uint8)

        image = np.zeros(
            (height, width),
            dtype=np.uint16
        )

        for row in range(height):

            start = row * stride

            row_bytes = buffer[
                start:start + 2 * width
            ]

            image[row, :] = row_bytes.view(
                np.uint16
            )

        return image

    # =====================================================
    # EXPOSURE
    # =====================================================
    def set_exposure(self, exposure_s):

        if self._acquisition_open:
            self.end_external_acquisition()

        if self.cam is not None:
            self.cam.setFloat("ExposureTime", exposure_s)
            # sCMOS exposure is quantised to the row period -- the SDK may
            # not accept exactly what was requested. Read back what it
            # actually holds rather than trusting the request.
            self.exposure_time = self.cam.getFloat("ExposureTime")
        else:
            self.exposure_time = exposure_s

    # =====================================================
    # ROI / BINNING PLACEHOLDERS
    # =====================================================

    def set_roi(self, roi):
        """
        Set camera ROI.

        ROI format:
            (x0, y0, x1, y1)

        where x1 and y1 are exclusive.
        """

        if self._acquisition_open:
            self.end_external_acquisition()

        self.roi = roi

        # Stop acquisition if necessary
        try:
            self.cam.command("AcquisitionStop")
        except Exception:
            pass

        self.cam.setBool("VerticallyCentreAOI", False)

        if roi is None:
            sensor_width = self.cam.getInt("SensorWidth")
            sensor_height = self.cam.getInt("SensorHeight")

            self.cam.setInt("AOILeft", 1)
            self.cam.setInt("AOITop", 1)
            self.cam.setInt("AOIWidth", sensor_width)
            self.cam.setInt("AOIHeight", sensor_height)
            return

        x0, y0, x1, y1 = roi

        width = x1 - x0
        height = y1 - y0

        self.cam.setInt("AOIWidth", width)
        self.cam.setInt("AOIHeight", height)

        # SDK3 uses 1-based coordinates
        self.cam.setInt("AOILeft", x0 + 1)
        self.cam.setInt("AOITop", y0 + 1)
    def set_binning(self, binning):

        if self._acquisition_open:
            self.end_external_acquisition()

        self.binning = binning

        if self.cam is None:
            return

        bin_map = {
            1: 0,
            2: 1,
            3: 2,
            4: 3,
            8: 4
        }

        if binning not in bin_map:
            raise ValueError(f"Unsupported Andor binning: {binning}")

        try:
            self.cam.setEnumIndex("AOIBinning", bin_map[binning])
            LOGGER.info("Andor AOI binning set to %dx%d", binning, binning)

        except Exception:
            LOGGER.exception("Failed to set Andor AOI binning")
            raise

    # =====================================================
    # SOFTWARE SNAP
    # =====================================================

    def snap(self):

        if self._acquisition_open:
            self.end_external_acquisition()

        log_camera_snap(type(self).__name__)

        self.cam.setEnumIndex(
            "TriggerMode",
            4
        )  # Software

        self.cam.queueBuffer(1)

        self.cam.command(
            "AcquisitionStart"
        )

        self.cam.command(
            "SoftwareTrigger"
        )

        raw = self.cam.waitBuffer(
            10000
        )

        frame = self._buffer_to_image(
            raw
        )

        try:
            self.cam.command(
                "AcquisitionStop"
            )
        except Exception:
            pass

        try:
            self.cam.flush()
        except Exception:
            pass

        with self._lock:
            self.latest_frame = frame

        return frame

    # =====================================================
    # EXTERNAL TRIGGER SNAP
    # =====================================================

    def snap_external_trigger(self, timeout_ms=10000):

        frames = self.snap_external_frames(
            nframes=1,
            timeout_ms=timeout_ms
        )

        frame = frames[0]

        with self._lock:
            self.latest_frame = frame

        return frame


    def snap_external_frames(self, nframes, timeout_ms=10000):
        """Arm, grab nframes, disarm. Unchanged signature/behavior for
        existing callers -- internally composed from begin_/grab_/
        end_external_acquisition so there is only one arm/disarm
        implementation to maintain."""

        if self.cam is None:
            raise RuntimeError("Camera is not connected")

        frames = []
        with self.external_acquisition():
            for _ in range(nframes):
                frames.append(self.grab_external_frame(timeout_ms))

        if len(frames) > 0:
            with self._lock:
                self.latest_frame = frames[-1]

        return frames
    
    # =====================================================
    # STREAMING
    # =====================================================

    def start_stream(self):
        if self._acquisition_open:
            self.end_external_acquisition()
        log_event("start_stream", source="live stream", camera_type=type(self).__name__)
        self._stream_controller.start(self._stream_loop)

    def _stream_loop(self):
        log_event("stream_thread_start", source="live stream", camera_type=type(self).__name__)
        try:
            while not self._stream_controller.wait(0):

                try:
                    if not self._stream_controller.acquire_once(self.snap):
                        break

                except Exception as e:

                    print("Andor stream error:")
                    print(e)

                self._stream_controller.wait(0.01)
        finally:
            log_event("stream_thread_exit", source="live stream", camera_type=type(self).__name__)

    def stop_stream(self, timeout_s=None):
        log_event("stop_stream", source="live stream", camera_type=type(self).__name__)
        timeout_s = (
            self.stream_shutdown_timeout_s
            if timeout_s is None
            else timeout_s
        )
        self._stream_controller.stop(timeout_s)

    # =====================================================
    # GET LATEST FRAME
    # =====================================================

    def get_latest_frame(self):

        with self._lock:

            if self.latest_frame is None:
                return None

            return self.latest_frame.copy()

    # =====================================================
    # CLOSE
    # =====================================================

    def close(self):

        if self._acquisition_open:
            self.end_external_acquisition()

        try:
            self.stop_stream()
        except Exception:
            pass

        if self.cam is not None:

            try:
                self.cam.command(
                    "AcquisitionStop"
                )
            except Exception:
                pass

            try:
                self.cam.flush()
            except Exception:
                pass

            self.cam.close()

            self.cam = None
