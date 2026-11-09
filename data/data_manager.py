import json
import numpy as np
from pathlib import Path
from datetime import datetime



class DataManager:

    def __init__(self, base_dir="data"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)

    def save_odmr(self, freqs_hz, signal, config, i_off=None, i_on=None):

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        folder = self.base_dir / f"odmr_{timestamp}"
        folder.mkdir()

        np.save(folder / "frequencies_hz.npy", freqs_hz)
        np.save(folder / "signal.npy", signal)

        if i_off is not None:
            np.save(folder / "i_off.npy", i_off)

        if i_on is not None:
            np.save(folder / "i_on.npy", i_on)

        if i_off is not None and i_on is not None:
            csv_data = np.column_stack((freqs_hz, i_off, i_on, signal))
            header = "frequency_hz,I_off,I_on,normalized_signal"
        else:
            csv_data = np.column_stack((freqs_hz, signal))
            header = "frequency_hz,normalized_signal"

        np.savetxt(
            folder / "odmr.csv",
            csv_data,
            delimiter=",",
            header=header,
            comments=""
        )

        with open(folder / "config.json", "w") as f:
            json.dump(config, f, indent=4)

        print(f"[DataManager] ODMR saved to: {folder}")

        return folder

