import numpy as np
import threading
import time


class SimCamera:

    def __init__(
        self,
        image_shape=(64, 64),
        microwave=None
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

        self.streaming = False

        self.latest_frame = None

        self._lock = threading.Lock()

        self._stream_thread = None

        # =====================================================
        # ODMR PHYSICS PARAMETERS
        # =====================================================

        self.base_signal = 1.0

        self.resonance_freq = 2.87e9

        self.linewidth = 5e6

        self.contrast = 0.03

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

        time.sleep(self.exposure_time)

        frame = self._generate_frame()

        with self._lock:

            self.latest_frame = frame

        return frame

    # =========================================================
    # STREAMING
    # =========================================================

    def start_stream(self):

        if self.streaming:
            return

        self.streaming = True

        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            daemon=True
        )

        self._stream_thread.start()

    def _stream_loop(self):

        while self.streaming:

            frame = self._generate_frame()

            with self._lock:

                self.latest_frame = frame

            time.sleep(self.exposure_time)

    def stop_stream(self):

        self.streaming = False

        if self._stream_thread:

            self._stream_thread.join(timeout=1)

    # =========================================================
    # GET FRAME
    # =========================================================

    def get_latest_frame(self):

        with self._lock:

            if self.latest_frame is None:
                return None

            return self.latest_frame.copy()