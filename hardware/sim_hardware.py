import numpy as np

from hardware.camera.sim_camera import SimCamera


# =========================================================
# SIMULATED MICROWAVE SOURCE
# =========================================================

class SimMicrowave:

    def __init__(self):
        self.frequency = 2.87e9
        self.power = 0

    def set_frequency(self, f):
        self.frequency = f
        print(f"[MW] set frequency = {f/1e9:.6f} GHz")

    def set_power(self, power):
        self.power = power


# =========================================================
# SIMULATED PULSE STREAMER
# =========================================================

class SimPulseStreamer:

    def __init__(self):
        self.sequence = None
        self.digital_outputs = {}

    def load_sequence(self, seq):
        self.sequence = seq

    def run(self):
        print("[PulseStreamer] running sequence...")

    def set_digital_output(self, channel, state):

        self.digital_outputs[channel] = bool(state)


# =========================================================
# HARDWARE FACTORY
# =========================================================

def build_sim_hardware():

    return {
        "microwave": SimMicrowave(),
        "camera": SimCamera(),
        "pulse_streamer": SimPulseStreamer()
    }
