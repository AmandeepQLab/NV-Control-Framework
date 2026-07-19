import numpy as np
import threading
import time

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

        self.roi = (
            0,
            image_shape[0],
            0,
            image_shape[1]
        )

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
    # CONFIGURATION
    # =========================================================

    def set_exposure(self, exposure_s):

        self.exposure_time = exposure_s

    def set_roi(self, roi):

        self.roi = roi

    def set_binning(self, binning):

        self.binning = binning

    # =========================================================
    # ODMR MODEL
    # =========================================================

    def odmr_contrast(self):

        if self.microwave is None:
            return 0

        # MW OFF
        if getattr(self.microwave, "power", 0) == 0:
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

        return frame

    # =========================================================
    # SNAP
    # =========================================================

    def snap(self):

        log_camera_snap(type(self).__name__)

        time.sleep(self.exposure_time)

        frame = self._generate_frame()

        with self._lock:

            self.latest_frame = frame

        return frame

    # =========================================================
    # STREAMING
    # =========================================================

    def start_stream(self):
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
