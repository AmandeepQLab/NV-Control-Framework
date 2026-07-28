from andor3 import Andor3

print("\nConnecting to Andor Neo...")

cam = Andor3()
cam.open(0)

# -----------------------------
# Configure camera (same as framework)
# -----------------------------
cam.setBool("SensorCooling", True)
cam.setEnumString("ElectronicShutteringMode", "Global")
cam.setEnumString("PixelReadoutRate", "280 MHz")
cam.setBool("Overlap", False)
cam.setBool("SpuriousNoiseFilter", True)
cam.setEnumIndex("SimplePreAmpGainControl", 2)
cam.setEnumString("PixelEncoding", "Mono16")
cam.setEnumString("CycleMode", "Continuous")
cam.setFloat("ExposureTime", 0.020)  # 20 ms

try:
    cam.setBool("IOInvert", False)
except Exception:
    pass

try:
    cam.setFloat("ExternalTriggerDelay", 0.0)
except Exception:
    pass

print("Camera connected.\n")

# -----------------------------
# Print full-frame AOI
# -----------------------------
print("Initial AOI")
print("----------------")
print("Left   :", cam.getInt("AOILeft"))
print("Top    :", cam.getInt("AOITop"))
print("Width  :", cam.getInt("AOIWidth"))
print("Height :", cam.getInt("AOIHeight"))
print()

# -----------------------------
# Set a test ROI
# -----------------------------
print("Setting ROI...")

cam.setBool("VerticallyCentreAOI", False)

cam.setInt("AOIWidth", 700)
cam.setInt("AOIHeight", 600)
cam.setInt("AOILeft", 501)
cam.setInt("AOITop", 401)

print("\nROI after programming")
print("---------------------")
print("Left   :", cam.getInt("AOILeft"))
print("Top    :", cam.getInt("AOITop"))
print("Width  :", cam.getInt("AOIWidth"))
print("Height :", cam.getInt("AOIHeight"))

# -----------------------------
# Acquire one image
# -----------------------------
cam.setEnumIndex("TriggerMode", 4)      # Software Trigger

cam.queueBuffer(1)

cam.command("AcquisitionStart")
cam.command("SoftwareTrigger")

raw = cam.waitBuffer(10000)

width = cam.getInt("AOIWidth")
height = cam.getInt("AOIHeight")
stride = cam.getInt("AOIStride")

print("\nReturned AOI")
print("----------------")
print("Width  :", width)
print("Height :", height)
print("Stride :", stride)

print("\nExpected frame shape:")
print((height, width))

try:
    cam.command("AcquisitionStop")
except Exception:
    pass

try:
    cam.flush()
except Exception:
    pass

cam.close()

print("\nTest completed successfully.")