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

det_ch = channels["detector"]
cam = camera.cam

print("Configuring Andor like MATLAB...")

cam.set_attribute_value("ElectronicShutteringMode", "Global")
cam.set_attribute_value("PixelReadoutRate", "280 MHz")
cam.set_attribute_value("Overlap", False)
cam.set_attribute_value("SpuriousNoiseFilter", True)
cam.set_attribute_value("PixelEncoding", "Mono16")
cam.set_attribute_value("CycleMode", "Continuous")
cam.set_attribute_value("TriggerMode", "External")
cam.set_attribute_value("ExposureTime", 0.02)

seq = PulseSequence()
seq.add_pulse(
    channel=det_ch,
    start_ns=100_000_000,
    duration_ns=500_000_000
)

pulse.load_sequence(seq)

print("Setting up acquisition...")
cam.setup_acquisition(nframes=1)

print("Starting acquisition...")
cam.start_acquisition()

time.sleep(0.5)

print("Running Pulse Streamer...")
pulse.run()

print("Waiting for frame...")
cam.wait_for_frame(timeout=10.0)

frame = cam.read_oldest_image()
frame = np.array(frame)

print("Frame received:", frame.shape)

cam.stop_acquisition()
cam.clear_acquisition()
