from pylablib.devices import Andor

cam = Andor.AndorSDK3Camera(0)

features = [
    "ElectronicShutteringMode",
    "PixelReadoutRate",
    "Overlap",
    "SimplePreAmpGainControl",
    "PixelEncoding",
    "SpuriousNoiseFilter",
    "CycleMode",
    "FrameCount",
    "TriggerMode",
    "ExposureTime",
]

for feat in features:
    try:
        val = cam.get_attribute_value(feat)
        print(feat, "=", val)
    except Exception as e:
        print(feat, "FAILED:", e)

cam.close()
