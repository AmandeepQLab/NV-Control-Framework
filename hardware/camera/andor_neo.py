import numpy as np
import threading
import time

from pylablib.devices import Andor


class AndorNeo:

    def __init__(self):

        # =================================================
        # CAMERA HANDLE
        # =================================================

        self.cam = None

        # =================================================
        # SETTINGS
        # =================================================

        self.exposure_time = 0.01

        self.roi = None

        self.binning = 1

        # =================================================
        # STREAMING
        # =================================================

        self.streaming = False

        self.latest_frame = None

        self._lock = threading.Lock()

        self._stream_thread = None

    # =====================================================
    # CONNECT
    # =====================================================

    def connect(self):

        print("\nConnecting to Andor Neo...\n")

        self.cam = Andor.AndorSDK3Camera(0)

        print("Connected to Andor Neo.")

    # =====================================================
    # EXPOSURE
    # =====================================================

    def set_exposure(self, exposure_s):

        self.exposure_time = exposure_s

        self.cam.set_exposure(exposure_s)

    # =====================================================
    # ROI
    # =====================================================

    def set_roi(self, roi):

        self.roi = roi

    # =====================================================
    # BINNING
    # =====================================================

    def set_binning(self, binning):

        self.binning = binning

    # =====================================================
    # SNAP
    # =====================================================

    def snap(self):

        frame = self.cam.snap()

        frame = np.array(frame)

        with self._lock:

            self.latest_frame = frame

        return frame
    # =====================================================
    # EXTERNAL TRIGGER SNAP
    # =====================================================

    def snap_external_trigger(self):
        """
        Acquire one externally triggered frame.
        Clean camera state before each acquisition.
        """

        try:
            self.cam.stop_acquisition()
        except Exception:
            pass

        try:
            self.cam.clear_acquisition()
        except Exception:
            pass

        self.cam.set_trigger_mode("ext_exp")

        frames = self.cam.grab(1)

        frame = np.array(frames[0])

        try:
            self.cam.clear_acquisition()
        except Exception:
            pass

        with self._lock:
            self.latest_frame = frame

        return frame
    # =====================================================
    # STREAM LOOP
    # =====================================================

    def _stream_loop(self):

        while self.streaming:

            try:

                frame = self.cam.snap()

                frame = np.array(frame)

                with self._lock:

                    self.latest_frame = frame

            except Exception as e:

                print("Camera stream error:")
                print(e)

            time.sleep(0.001)

    # =====================================================
    # START STREAM
    # =====================================================

    def start_stream(self):

        if self.streaming:
            return

        self.streaming = True

        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            daemon=True
        )

        self._stream_thread.start()

    # =====================================================
    # STOP STREAM
    # =====================================================

    def stop_stream(self):

        self.streaming = False

        if self._stream_thread is not None:

            self._stream_thread.join(timeout=1)

    # =====================================================
    # GET FRAME
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

        if self.cam is not None:

            self.cam.close()

            self.cam = None
