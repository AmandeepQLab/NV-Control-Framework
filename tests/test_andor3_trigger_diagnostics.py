from andor3 import Andor3
import time

cam = Andor3()
cam.open(0)

print("Setting Software Trigger mode...")

cam.setEnumIndex("TriggerMode", 4)      # Software
cam.setEnumString("CycleMode", "Fixed")

cam.queueBuffer(1)

print("Starting acquisition...")
cam.command("AcquisitionStart")

print("Waiting 5 seconds...")
time.sleep(5)

print("Trying waitBuffer...")

try:

    buf = cam.waitBuffer(2000)

    print("Frame arrived BEFORE SoftwareTrigger")

except Exception as e:

    print("Timeout as expected:", e)

print("Sending SoftwareTrigger...")

try:

    cam.command("SoftwareTrigger")

    buf = cam.waitBuffer(5000)

    print("Frame received AFTER SoftwareTrigger")

except Exception as e:

    print("Software trigger failed:", e)

cam.command("AcquisitionStop")
cam.close()
