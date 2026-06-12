# tests/test_andor_external_trigger_manual.py

import threading
import time
import numpy as np

from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager
from sequencing.pulse_sequence import PulseSequence


cfg = ConfigManager("config/setupInfo.json")
cfg.load()

hw_manager = HardwareManager(cfg)
hw_manager.initialize()

hw = hw_manager.get_hardware()

camera = hw["camera"]
pulse = hw["pulse_streamer"]
channels = hw["channels"]

det_ch = channels["detector"]   # should be CH5 from your JSON

seq = PulseSequence()

seq.add_pulse(
    channel=det_ch,
    start_ns=100_000_000,      # 100 ms delay
    duration_ns=500_000_000     # 20 ms trigger/gate
)

pulse.load_sequence(seq)

print("Setting Andor trigger mode...")
camera.cam.set_trigger_mode("ext_exp")

print("Setting up acquisition...")
camera.cam.setup_acquisition(nframes=1)

print("Starting acquisition / arming camera...")
camera.cam.start_acquisition()

time.sleep(0.2)

print("Firing Pulse Streamer...")
pulse.run()

print("Waiting for frame...")
camera.cam.wait_for_frame(timeout=5.0)

frame = camera.cam.read_oldest_image()
frame = np.array(frame)

print("Frame received:", frame.shape)

camera.cam.stop_acquisition()
camera.cam.clear_acquisition()
