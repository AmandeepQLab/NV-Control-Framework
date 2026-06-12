import threading
import time

from config.config_manager import ConfigManager
from hardware.camera.andor_neo_andor3 import AndorNeoAndor3
from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer
from sequencing.pulse_sequence import PulseSequence


cfg = ConfigManager("config/setupInfo.json")
cfg.load()

ps_cfg = cfg.get("pulseGenerator")

channels = dict(
    zip(ps_cfg["channelNames"], ps_cfg["channelValues"])
)

det_ch = channels["detector"]

pulse = SwabianPulseStreamer(ps_cfg["ipAddress"])
pulse.connect()

camera = AndorNeoAndor3()
camera.connect()

seq = PulseSequence()

seq.add_pulse(
    channel=det_ch,
    start_ns=100_000_000,
    duration_ns=500_000_000
)

pulse.load_sequence(seq)


def fire():
    time.sleep(0.5)
    pulse.run()


threading.Thread(target=fire, daemon=True).start()

frame = camera.snap_external_trigger(timeout_ms=10000)

print("External triggered frame shape:", frame.shape)

camera.close()
