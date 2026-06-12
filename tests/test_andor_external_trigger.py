import threading
import time

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

seq = PulseSequence()

seq.add_pulse(
    channel=det_ch,
    start_ns=1_000_000,   # trigger after 1 ms
    duration_ns=1000
)

pulse.load_sequence(seq)


def fire_trigger():
    time.sleep(0.1)
    pulse.run()


threading.Thread(target=fire_trigger, daemon=True).start()

print("Camera waiting for external trigger...")

frame = camera.snap_external_trigger(timeout_s=5.0)

print("Frame received.")
print("Frame shape:", frame.shape)
