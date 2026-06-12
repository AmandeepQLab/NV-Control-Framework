from hardware.camera.andor_neo_andor3 import AndorNeoAndor3

camera = AndorNeoAndor3()
camera.connect()

cam = camera.cam

cam.setEnumIndex("TriggerMode", 6)      # External
cam.setEnumString("CycleMode", "Continuous")

cam.queueBuffer(5)

print("Starting acquisition...")
cam.command("AcquisitionStart")

for i in range(5):
    buf = cam.waitBuffer(5000)
    print("Frame", i+1)

cam.command("AcquisitionStop")
camera.close()
