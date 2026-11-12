"""Background worker for per-pixel ODMR fitting."""

from PyQt6.QtCore import QObject, pyqtSignal

from analysis.odmr_pixel_fit import fit_odmr_pixels


class ODMRPixelFitWorker(QObject):
    """Run the existing per-pixel fitter away from the GUI thread."""

    started = pyqtSignal()
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, mean_cube, global_fit):
        super().__init__()
        self.mean_cube = mean_cube
        self.global_fit = global_fit

    def run(self):
        self.started.emit()
        try:
            result = fit_odmr_pixels(self.mean_cube, self.global_fit)
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
            return
        self.finished.emit(result)

