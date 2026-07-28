from hardware.camera.andor_neo_andor3 import AndorNeoAndor3

# --------------------------------------------------------
# Test ROI functionality of the framework camera class
# --------------------------------------------------------

camera = AndorNeoAndor3()

print("Connecting...")
camera.connect()

print("\nInitial camera AOI")
print("------------------")
print("Left   :", camera.cam.getInt("AOILeft"))
print("Top    :", camera.cam.getInt("AOITop"))
print("Width  :", camera.cam.getInt("AOIWidth"))
print("Height :", camera.cam.getInt("AOIHeight"))

roi = (547, 772, 1260, 1389)

print("\nProgramming ROI:", roi)
camera.set_roi(roi)

print("\nCamera AOI after set_roi()")
print("--------------------------")
print("Left   :", camera.cam.getInt("AOILeft"))
print("Top    :", camera.cam.getInt("AOITop"))
print("Width  :", camera.cam.getInt("AOIWidth"))
print("Height :", camera.cam.getInt("AOIHeight"))

print("\nAcquiring one image...")

frame = camera.snap()

print("\nReturned frame shape:", frame.shape)

camera.cam.close()

print("\nDone.")