import numpy as np
import matplotlib.pyplot as plt

from experiments.odmr_experiment import ODMRExperiment
from hardware.sim_hardware import build_sim_hardware
from controller.experiment_runner import ExperimentRunner


# -----------------------------
# Build simulated hardware
# -----------------------------
hw = build_sim_hardware()

# -----------------------------
# ODMR configuration
# -----------------------------
config = {
    "f_start": 2.75e9,
    "f_stop": 2.95e9,
    "steps": 20
}

# -----------------------------
# Create experiment
# -----------------------------
exp = ODMRExperiment(hw, config)

# -----------------------------
# Run via controller layer
# -----------------------------
runner = ExperimentRunner()
freqs, signal = runner.run(exp)

# -----------------------------
# Plot result
# -----------------------------
plt.figure()
plt.plot(freqs, signal, marker="o")
plt.xlabel("Microwave Frequency (Hz)")
plt.ylabel("Fluorescence (a.u.)")
plt.title("Simulated ODMR (Runner-based architecture)")
plt.grid(True)
plt.show()