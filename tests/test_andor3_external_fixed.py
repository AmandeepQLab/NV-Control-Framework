from andor3 import Andor3
import time
from config.config_manager import ConfigManager
from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer
from sequencing.pulse_sequence import PulseSequence
import time

cam = Andor3()
cam.open(0)

print("Setting External Trigger + Fixed mode...")

cam.setEnumIndex("TriggerMode", 6)   # External
cam.setEnumString("CycleMode", "Fixed")
cam.setInt("FrameCount", 1)

print("TriggerMode:", cam.getEnumIndex("TriggerMode"), cam.getEnumStringByIndex("TriggerMode", cam.getEnumIndex("TriggerMode")))
print("CycleMode:", cam.getEnumStringByIndex("CycleMode", cam.getEnumIndex("CycleMode")))
print("FrameCount:", cam.getInt("FrameCount"))

cfg = ConfigManager("config/setupInfo.json")
cfg.load()

ps_cfg = cfg.get("pulseGenerator")
channels = dict(zip(ps_cfg["channelNames"], ps_cfg["channelValues"]))
det_ch = channels["detector"]

pulse = SwabianPulseStreamer(ps_cfg["ipAddress"])
pulse.connect()

seq = PulseSequence()
seq.add_pulse(channel=det_ch, start_ns=0, duration_ns=1000)  # dummy high
pulse.load_sequence(seq)
pulse.run()

time.sleep(1)

cam.queueBuffer(1)

print("Starting acquisition...")
cam.command("AcquisitionStart")

print("Waiting for external trigger without firing Swabian...")

try:
    buf = cam.waitBuffer(5000)
    print("Frame arrived without Swabian trigger")

except Exception as e:
    print("Timeout as expected:", e)

cam.command("AcquisitionStop")
cam.close()
