from abc import ABC, abstractmethod


class BaseCamera(ABC):

    def __init__(self):
        self.exposure_time = None
        self.roi = None
        self.binning = None

        self.streaming = False
        self.latest_frame = None

    # ----------------------------
    # CONFIGURATION API (GUI SAFE)
    # ----------------------------

    @abstractmethod
    def set_exposure(self, exposure_s: float):
        pass

    @abstractmethod
    def set_roi(self, roi):
        """
        roi = (x0, x1, y0, y1)
        """
        pass

    @abstractmethod
    def set_binning(self, binning: int):
        pass

    # ----------------------------
    # ACQUISITION API
    # ----------------------------

    @abstractmethod
    def snap(self):
        """
        Single frame acquisition (used in ODMR, calibration, etc.)
        """
        pass

    @abstractmethod
    def start_stream(self):
        """
        Start continuous acquisition (for GUI live view)
        """
        pass

    @abstractmethod
    def stop_stream(self):
        pass

    @abstractmethod
    def get_latest_frame(self):
        """
        Thread-safe access to latest frame
        """
        pass