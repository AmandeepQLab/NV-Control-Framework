import threading
import time
import numpy as np

from config.config_manager import ConfigManager
from hardware.camera.andor_neo_andor3 import AndorNeoAndor3
from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer
from sequencing.pulse_sequence import PulseSequence


REPEATS = 10
EXPOSURE_S = 0.02
GAP_S = 0.03


cfg = ConfigManager("config/setupInfo.json")
cfg.load()

ps_cfg = cfg.get("pulseGenerator")
channels = dict(zip(ps_cfg["channelNames"], ps_cfg["channelValues"]))

det_ch = channels["detector"]

pulse = SwabianPulseStreamer(ps_cfg["ipAddress"])
pulse.connect()

camera = AndorNeoAndor3()
camera.connect()
camera.set_exposure(EXPOSURE_S)

cam = camera.cam

cam.setEnumIndex("TriggerMode", 3)      # External Exposure
cam.setEnumString("CycleMode", "Continuous")

seq = PulseSequence()

t_ns = 100_000_000
exposure_ns = int(EXPOSURE_S * 1e9)
gap_ns = int(GAP_S * 1e9)

for i in range(REPEATS):
    seq.add_pulse(
        channel=det_ch,
        start_ns=t_ns,
        duration_ns=exposure_ns
    )
    t_ns += exposure_ns + gap_ns

pulse.load_sequence(seq)


def fire():
    time.sleep(0.5)
    print("Running Pulse Streamer sequence...")
    pulse.run()


print(f"Queueing {REPEATS} buffers...")
print(
    "TriggerMode:",
    cam.getEnumIndex("TriggerMode"),
    cam.getEnumStringByIndex(
        "TriggerMode",
        cam.getEnumIndex("TriggerMode")
    )
)

print(
    "CycleMode:",
    cam.getEnumStringByIndex(
        "CycleMode",
        cam.getEnumIndex("CycleMode")
    )
)
cam.queueBuffer(REPEATS)

print("Starting acquisition...")
cam.command("AcquisitionStart")

threading.Thread(target=fire, daemon=True).start()

frames = []

print("Waiting for frames...")

for i in range(REPEATS):
    raw = cam.waitBuffer(10000)
    frame = camera._buffer_to_image(raw)
    frames.append(frame)
    print(f"Frame {i+1}/{REPEATS}: shape={frame.shape}, mean={frame.mean():.2f}")

cam.command("AcquisitionStop")
cam.flush()
camera.close()

frames = np.array(frames)

print("Done.")
print("Frames array shape:", frames.shape)
