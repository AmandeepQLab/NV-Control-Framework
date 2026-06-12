from hardware.camera.andor_neo import AndorNeo

cam = AndorNeo()

cam.connect()

cam.set_exposure(0.01)

frame = cam.snap()

print("Frame shape:", frame.shape)

cam.close()
