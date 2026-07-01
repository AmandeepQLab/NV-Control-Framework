from PyQt6.QtCore import QObject, QTimer, pyqtSignal # type: ignore
import numpy as np # type: ignore

from experiments.odmr_experiment import ODMRExperiment


class ODMRWorker(QObject):

    point_signal = pyqtSignal(object, object)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)
    frequency_signal = pyqtSignal(float)

    def __init__(self, hardware, config):
        super().__init__()

        self.hardware = hardware
        self.config = config

        coarse = np.linspace(
            config["f_start"],
            config["f_stop"],
            config["steps"]
        )

        freq_list = [coarse]

        if config.get("use_dense_region", False):

            # Dip 1
            center = config["dense1_center"]
            width = config["dense1_width"]
            steps = config["dense1_steps"]

            dense1 = np.linspace(
                center - width / 2,
                center + width / 2,
                steps
            )

            dense1 = dense1[
                (dense1 >= config["f_start"])
                & (dense1 <= config["f_stop"])
            ]

            freq_list.append(dense1)

            # Dip 2
            if config.get("num_dense_dips", 1) == 2:

                center = config["dense2_center"]
                width = config["dense2_width"]
                steps = config["dense2_steps"]

                dense2 = np.linspace(
                    center - width / 2,
                    center + width / 2,
                    steps
                )

                dense2 = dense2[
                    (dense2 >= config["f_start"])
                    & (dense2 <= config["f_stop"])
                ]

                freq_list.append(dense2)

        self.freqs_sorted = np.unique(
            np.concatenate(freq_list)
        )
                        

        self.order = np.random.permutation(len(self.freqs_sorted))
        self.freqs = self.freqs_sorted[self.order]

        self.results = np.full(len(self.freqs_sorted), np.nan)
        self.i_off_results = np.full(len(self.freqs_sorted), np.nan)
        self.i_on_results = np.full(len(self.freqs_sorted), np.nan)

        self.n_avg = config.get("averages", 1)

        self.running = True
        self.freq_index = 0

        self.xdata = []
        self.ydata = []

        self.i_off_data = []
        self.i_on_data = []

        self.experiment = ODMRExperiment(
            self.hardware,
            self.config
        )

        self.timer = None

    def start(self):

        self.timer = QTimer()
        self.timer.timeout.connect(
            self.acquire_next_frequency
        )
        self.timer.start(10)

    def stop(self):

        self.running = False
        self.experiment.stop()

        if self.timer is not None:
            self.timer.stop()

        self.finished_signal.emit()

    def acquire_next_frequency(self):

        if not self.running:
            self._finish()
            return

        if self.freq_index >= len(self.freqs):
            self._finish()
            return

        f = self.freqs[self.freq_index]
        sorted_index = self.order[self.freq_index]
        self.progress_signal.emit(self.freq_index + 1, len(self.freqs))
        self.frequency_signal.emit(f / 1e9)

        values = []
        i_off_values = []
        i_on_values = []

        try:

            for _ in range(self.n_avg):

                if not self.running:
                    break

                value, i_off, i_on = self.experiment.acquire_point(
                    f,
                    return_raw=True
                )

                values.append(value)
                i_off_values.append(i_off)
                i_on_values.append(i_on)

        except Exception as e:

            msg = (
                f"ODMR acquisition error at "
                f"{f / 1e9:.6f} GHz:\n{e}"
            )

            print(msg)

            self.running = False

            self.error_signal.emit(msg)

            self._finish()

            return

        if len(values) == 0:
            self._finish()
            return

        avg_value = float(np.mean(values))
        avg_i_off = float(np.mean(i_off_values))
        avg_i_on = float(np.mean(i_on_values))

        self.results[sorted_index] = avg_value
        self.i_off_results[sorted_index] = avg_i_off
        self.i_on_results[sorted_index] = avg_i_on

        valid = ~np.isnan(self.results)

        self.xdata = (self.freqs_sorted[valid] / 1e9).tolist()
        self.ydata = self.results[valid].tolist()

        self.i_off_data = self.i_off_results[valid].tolist()
        self.i_on_data = self.i_on_results[valid].tolist()

        self.point_signal.emit(
            self.xdata.copy(),
            self.ydata.copy()
        )

        self.freq_index += 1

    def _finish(self):

        if self.timer is not None:
            self.timer.stop()

        self.finished_signal.emit()
