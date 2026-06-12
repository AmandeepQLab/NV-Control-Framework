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
cam.set_attribute_value("CycleMode", "Fixed")
cam.set_attribute_value("FrameCount", 1)
cam.set_attribute_value("TriggerMode", "External")
cam.set_attribute_value("IOInvert", False)


print("TriggerMode:", cam.get_attribute_value("TriggerMode"))
print("CycleMode:", cam.get_attribute_value("CycleMode"))
print("ExposureTime:", cam.get_attribute_value("ExposureTime"))
print("ElectronicShutteringMode:", cam.get_attribute_value("ElectronicShutteringMode"))
print("PixelEncoding:", cam.get_attribute_value("PixelEncoding"))

seq = PulseSequence()
seq.add_pulse(
    channel=det_ch,
    start_ns=100_000_000,
    duration_ns=500_000_000
)

pulse.load_sequence(seq)


def fire():
    time.sleep(0.5)
    print("Firing Pulse Streamer...")
    pulse.run()


threading.Thread(target=fire, daemon=True).start()

print("Calling grab...")

frames = cam.grab(1)

frame = np.array(frames[0])

print("Frame received:", frame.shape)
