import numpy as np
import threading
import time
from contextlib import contextmanager

from hardware.camera.streaming import StreamController
from utils.camera_diagnostics import log_camera_snap, log_event


class SimCamera:

    def __init__(
        self,
        image_shape=(64, 64),
        microwave=None,
        stream_shutdown_timeout_s=12.0,
    ):

        # =====================================================
        # CONFIGURATION
        # =====================================================

        self.exposure_time = 0.01

        self.roi = None

        self.binning = 1

        # =====================================================
        # HARDWARE REFERENCES
        # =====================================================

        self.microwave = microwave

        # =====================================================
        # INTERNAL STATE
        # =====================================================

        self.image_shape = image_shape

        self.latest_frame = None

        self._lock = threading.Lock()
        self._stream_controller = StreamController()
        self.stream_shutdown_timeout_s = stream_shutdown_timeout_s

        # Interface parity with AndorNeoAndor3's temporary timing
        # diagnostics (see that class). There are no real SDK stages to
        # split here, so this is a single lumped entry per frame -- enough
        # to exercise the ODMR-side instrumentation in sim without crashing.
        self._timing_enabled = False
        self._timing_log = []

        # Interface parity with AndorNeoAndor3's held-open external
        # acquisition (see that class for the real semantics). There's no
        # real SDK arm/disarm cost to simulate, but the same state machine
        # and defensive self-heal calls are mirrored here so ODMR's new
        # call path -- and the exception/stop-safety tests -- exercise
        # identical logic against sim as against real hardware.
        self._acquisition_open = False
        self._acquisition_frames_served = 0
        self._acquisition_start_count = 0
        self._acquisition_stop_count = 0

        # =====================================================
        # ODMR PHYSICS PARAMETERS
        # =====================================================

        self.base_signal = 1.0

        self.resonance_freq = 2.87e9

        self.linewidth = 5e6

        self.contrast = 0.03

    @property
    def streaming(self):
        return self._stream_controller.is_streaming

    # =========================================================
    # TIMING DIAGNOSTICS (parity stub; see AndorNeoAndor3)
    # =========================================================

    def enable_timing_diagnostics(self, enabled):
        self._timing_enabled = bool(enabled)
        self._timing_log = []

    def pop_timing_log(self):
        log = self._timing_log
        self._timing_log = []
        return log

    def reset_acquisition_counts(self):
        self._acquisition_start_count = 0
        self._acquisition_stop_count = 0

    def get_acquisition_counts(self):
        return (self._acquisition_start_count, self._acquisition_stop_count)

    # =========================================================
    # HELD-OPEN EXTERNAL ACQUISITION (parity stub; see AndorNeoAndor3)
    # =========================================================

    def begin_external_acquisition(self):
        if self._acquisition_open:
            return
        self._acquisition_open = True
        self._acquisition_frames_served = 0
        self._acquisition_start_count += 1
        if self._timing_enabled:
            self._timing_log.append(("acquisition_start", None, 0.0))

    def grab_external_frame(self, timeout_ms=10000):
        if not self._acquisition_open:
            raise RuntimeError(
                "grab_external_frame() called without an open acquisition; "
                "call begin_external_acquisition() first."
            )

        # Timed unconditionally, mirroring AndorNeoAndor3.grab_external_frame
        # -- see that method for the derivation of the 0.5x floor. Always
        # >= exposure_time here since _acquire_and_store_frame() genuinely
        # sleeps for it; this exists so a subclass simulating a stale/
        # pre-filled buffer (an implausibly fast return) exercises the
        # same rejection path sim-side that the real driver has.
        wait_t0 = time.perf_counter()
        frame = self._acquire_and_store_frame()
        wait_duration_s = time.perf_counter() - wait_t0

        if self._timing_enabled:
            self._timing_log.append(
                ("wait_buffer", self._acquisition_frames_served, wait_duration_s)
            )

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

        self._acquisition_frames_served += 1
        return frame

    def end_external_acquisition(self):
        if not self._acquisition_open:
            return
        self._acquisition_open = False
        self._acquisition_stop_count += 1
        if self._timing_enabled:
            self._timing_log.append(("acquisition_stop", None, 0.0))

    @contextmanager
    def external_acquisition(self):
        self.begin_external_acquisition()
        try:
            yield
        finally:
            self.end_external_acquisition()

    # =========================================================
    # CONNECTION
    # =========================================================

    def connect(self):

        self.configure_camera()

        print("\n[SimCamera] Connected (simulated).")

    def configure_camera(self):
        """No SDK-level configuration needed for a synthetic camera."""
        pass

    def close(self):
        if self._acquisition_open:
            self.end_external_acquisition()
        try:
            self.stop_stream()
        except Exception:
            pass

    # =========================================================
    # CONFIGURATION
    # =========================================================

    def set_exposure(self, exposure_s):

        if self._acquisition_open:
            self.end_external_acquisition()

        self.exposure_time = exposure_s

    def set_roi(self, roi):

        if self._acquisition_open:
            self.end_external_acquisition()

        self.roi = roi

    def set_binning(self, binning):

        if self._acquisition_open:
            self.end_external_acquisition()

        self.binning = binning

    # =========================================================
    # ODMR MODEL
    # =========================================================

    def odmr_contrast(self):

        if self.microwave is None:
            return 0

        # MW OFF: ODMRExperiment signals "off" with a deeply attenuated
        # power (-100 dBm) rather than exactly 0, and "on" with the
        # configured power (typically -10..0 dBm). -50 dBm sits well below
        # any realistic "on" power and well above the -100 dBm off
        # convention, so it reliably separates the two.
        power = getattr(self.microwave, "power", None)

        if power is None or power <= -50:
            return 0

        f = self.microwave.frequency

        # Lorentzian dip
        gamma = self.linewidth

        lorentz = (
            1 /
            (
                1 +
                ((f - self.resonance_freq) / gamma) ** 2
            )
        )

        return self.contrast * lorentz

    # =========================================================
    # FRAME GENERATION
    # =========================================================

    def _generate_frame(self):

        # ODMR dip
        dip = self.odmr_contrast()

        signal = self.base_signal * (1 - dip)

        noise = np.random.normal(
            0,
            0.01,
            self.image_shape
        )

        frame = signal + noise
        return self._apply_acquisition_state(frame)

    def _apply_acquisition_state(self, frame):
        """Mirror hardware AOI/binning so simulation follows live settings."""
        if self.roi is not None:
            x0, y0, x1, y1 = self.roi
            frame = frame[y0:y1, x0:x1]

        if self.binning > 1:
            height, width = frame.shape
            height -= height % self.binning
            width -= width % self.binning
            frame = frame[:height, :width]
            frame = frame.reshape(
                height // self.binning,
                self.binning,
                width // self.binning,
                self.binning,
            ).mean(axis=(1, 3))
        return frame

    # =========================================================
    # SNAP
    # =========================================================

    def snap(self):

        if self._acquisition_open:
            self.end_external_acquisition()

        log_camera_snap(type(self).__name__)

        return self._acquire_and_store_frame()

    def _acquire_and_store_frame(self):
        """Shared by snap() and grab_external_frame(). Kept separate from
        snap() so grab_external_frame() (called while _acquisition_open is
        True, by design) doesn't trip snap()'s own defensive self-heal."""

        time.sleep(self.exposure_time)

        frame = self._generate_frame()

        with self._lock:

            self.latest_frame = frame

        return frame

    # =========================================================
    # EXTERNAL TRIGGER SNAP
    # =========================================================

    def snap_external_trigger(self, timeout_ms=10000):
        """External triggering is a hardware-timing concept that doesn't
        apply to a synthetic frame source; behaves like snap().

        Routed through snap_external_frames() (rather than calling snap()
        directly) so it exercises the same call path as the real driver's
        snap_external_trigger() -> snap_external_frames().
        """
        return self.snap_external_frames(nframes=1, timeout_ms=timeout_ms)[0]

    def snap_external_frames(self, nframes, timeout_ms=10000):
        """Arm, grab nframes, disarm -- mirrors AndorNeoAndor3's
        composition on top of begin_/grab_/end_external_acquisition."""

        frames = []
        with self.external_acquisition():
            for _ in range(nframes):
                frames.append(self.grab_external_frame(timeout_ms))
        return frames

    # =========================================================
    # STREAMING
    # =========================================================

    def start_stream(self):
        if self._acquisition_open:
            self.end_external_acquisition()
        log_event("start_stream", source="live stream", camera_type=type(self).__name__)
        self._stream_controller.start(self._stream_loop)

    def _stream_loop(self):
        log_event("stream_thread_start", source="live stream", camera_type=type(self).__name__)
        try:
            while not self._stream_controller.wait(0):
                def acquire():
                    log_event("camera.acquisition", source="live stream", camera_type=type(self).__name__, caller_function="_stream_loop", caller_file=__file__)
                    frame = self._generate_frame()
                    with self._lock:
                        self.latest_frame = frame

                if not self._stream_controller.acquire_once(acquire):
                    break

                self._stream_controller.wait(self.exposure_time)
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

    # =========================================================
    # GET FRAME
    # =========================================================

    def get_latest_frame(self):

        with self._lock:

            if self.latest_frame is None:
                return None

            return self.latest_frame.copy()
