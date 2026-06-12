import threading
import time
import numpy as np

from andor3 import Andor3

from config.config_manager import ConfigManager
from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer
from sequencing.pulse_sequence import PulseSequence


cfg = ConfigManager("config/setupInfo.json")
cfg.load()

ps_cfg = cfg.get("pulseGenerator")

channels = dict(
    zip(
        ps_cfg["channelNames"],
        ps_cfg["channelValues"]
    )
)

det_ch = channels["detector"]

pulse = SwabianPulseStreamer(ps_cfg["ipAddress"])
pulse.connect()

cam = Andor3()
cam.open(0)

try:
    print("Configuring camera...")

    cam.setBool("SensorCooling", True)
    cam.setEnumString("ElectronicShutteringMode", "Global")
    cam.setEnumString("PixelReadoutRate", "280 MHz")
    cam.setBool("Overlap", False)
    cam.setBool("SpuriousNoiseFilter", True)
    cam.setEnumIndex("SimplePreAmpGainControl", 2)
    cam.setEnumString("PixelEncoding", "Mono16")

    # MATLAB-like external acquisition mode
    cam.setEnumString("CycleMode", "Continuous")
    cam.setFloat("ExposureTime", 0.02)

    # Try exact SDK3 External mode
    cam.setEnumIndex("TriggerMode", 6)  # External

    # Extra IO settings
    try:
        cam.setEnumString("IOSelector", "Fire 1")
        print("IOSelector:", cam.getEnumStringByIndex("IOSelector", cam.getEnumIndex("IOSelector")))
    except Exception as e:
        print("Could not set IOSelector:", e)

    try:
        cam.setBool("IOInvert", False)
        print("IOInvert:", cam.getBool("IOInvert"))
    except Exception as e:
        print("Could not set IOInvert:", e)

    try:
        cam.setFloat("ExternalTriggerDelay", 0.0)
        print("ExternalTriggerDelay:", cam.getFloat("ExternalTriggerDelay"))
    except Exception as e:
        print("Could not set ExternalTriggerDelay:", e)

    image_size = cam.getInt("ImageSizeBytes")

    trig_idx = cam.getEnumIndex("TriggerMode")
    trig_name = cam.getEnumStringByIndex("TriggerMode", trig_idx)

    print("ImageSizeBytes:", image_size)
    print("TriggerMode:", trig_idx, trig_name)
    print("ExposureTime:", cam.getFloat("ExposureTime"))
    print("CycleMode:", cam.getEnumStringByIndex("CycleMode", cam.getEnumIndex("CycleMode")))

    seq = PulseSequence()

    seq.add_pulse(
        channel=det_ch,
        start_ns=100_000_000,
        duration_ns=500_000_000
    )

    pulse.load_sequence(seq)

    print("Queue buffer...")
    cam.queueBuffer(1)

    print("Start acquisition...")
    cam.command("AcquisitionStart")

    def fire():
        time.sleep(0.5)
        print("Fire Pulse Streamer...")
        pulse.run()

    threading.Thread(target=fire, daemon=True).start()

    print("Waiting for buffer...")
    cam.command("SoftwareTrigger")
    buf = cam.waitBuffer(10_000)  # ms

    print("Buffer received.")
    print(type(buf))

finally:
    try:
        cam.command("AcquisitionStop")
    except Exception:
        pass

    try:
        cam.flush()
    except Exception:
        pass

    try:
        cam.close()
    except Exception:
        pass
