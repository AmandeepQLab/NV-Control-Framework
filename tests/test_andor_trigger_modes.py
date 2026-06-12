from pylablib.devices import Andor

cam = Andor.AndorSDK3Camera(0)

print("Camera opened.")

try:
    print("Current trigger mode:", cam.get_trigger_mode())
except Exception as e:
    print("Could not get trigger mode:", e)

modes_to_try = [
    "int",
    "ext",
    "software",
    "external",
    "external_start",
    "external_exposure",
]

for mode in modes_to_try:
    try:
        cam.set_trigger_mode(mode)
        print("Accepted mode:", mode, "->", cam.get_trigger_mode())
    except Exception as e:
        print("Rejected mode:", mode, "|", e)

cam.close()