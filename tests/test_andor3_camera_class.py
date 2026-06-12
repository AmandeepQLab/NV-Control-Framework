from hardware.camera.andor_neo_andor3 import AndorNeoAndor3

cam = AndorNeoAndor3()
cam.connect()

cam.set_exposure(0.02)

frame = cam.snap()

print("Software snap frame shape:", frame.shape)

cam.close()
