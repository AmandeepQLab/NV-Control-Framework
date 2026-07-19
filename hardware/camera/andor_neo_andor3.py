import numpy as np
import threading
import time

from andor3 import Andor3
from hardware.camera.streaming import StreamController
from utils.camera_diagnostics import log_camera_snap, log_event


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

    @property
    def streaming(self):
        return self._stream_controller.is_streaming

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

        try:
            self.cam.setBool("IOInvert", False)
        except Exception:
            pass

        try:
            self.cam.setFloat("ExternalTriggerDelay", 0.0)
        except Exception:
            pass

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

        self.exposure_time = exposure_s

        if self.cam is not None:
            self.cam.setFloat("ExposureTime", exposure_s)

    #        actual = self.cam.getFloat("ExposureTime")
    #        print(f"[Andor] Requested exposure = {exposure_s:.6f} s")
    #        print(f"[Andor] Actual exposure    = {actual:.6f} s")
    
    # =====================================================
    # ROI / BINNING PLACEHOLDERS
    # =====================================================

    def set_roi(self, roi):

        self.roi = roi

    def set_binning(self, binning):

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
            print(f"Unsupported Andor binning: {binning}")
            return

        try:
            self.cam.setEnumIndex("AOIBinning", bin_map[binning])
            print(f"[Andor] AOIBinning set to {binning}x{binning}")

        except Exception as e:
            print("[Andor] Failed to set AOIBinning:")
            print(e)

    # =====================================================
    # SOFTWARE SNAP
    # =====================================================

    def snap(self):

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

        if self.cam is None:
            raise RuntimeError("Camera is not connected")

        frames = []

        try:
            self.cam.setEnumIndex("TriggerMode", 6)  # External

            try:
                self.cam.flush()
            except Exception:
                pass

            self.cam.queueBuffer(nframes)
            self.cam.command("AcquisitionStart")

            for _ in range(nframes):
                raw = self.cam.waitBuffer(timeout_ms)
                frame = self._buffer_to_image(raw)
                frames.append(frame)
               # print(f"Received frame {len(frames)}/{nframes}") #comment out to see the recived frames.

        finally:
            try:
                self.cam.command("AcquisitionStop")
            except Exception:
                pass

            try:
                self.cam.flush()
            except Exception:
                pass

        if len(frames) > 0:
            with self._lock:
                self.latest_frame = frames[-1]

        return frames
    
    # =====================================================
    # STREAMING
    # =====================================================

    def start_stream(self):
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
