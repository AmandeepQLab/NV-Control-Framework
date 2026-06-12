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

# =====================================================
# Camera settings
# =====================================================

camera.cam.set_exposure(0.02)

try:
    camera.cam.set_shutter("open")
    print("Shutter set to open")
except Exception as e:
    print("Could not set shutter open:")
    print(e)

# Try ext first. If it fails, change to "ext_exp".
camera.cam.set_trigger_mode("ext_exp")

print("Trigger mode:", camera.cam.get_trigger_mode())

# =====================================================
# Pulse sequence
# =====================================================

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

print("Calling grab; camera should wait for external trigger...")

frames = camera.cam.grab(1)

frame = np.array(frames[0])

print("Frame received:", frame.shape)

try:
    camera.cam.clear_acquisition()
except Exception:
    pass
