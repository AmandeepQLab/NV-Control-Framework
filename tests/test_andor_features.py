from pylablib.devices import Andor

cam = Andor.AndorSDK3Camera(0)

print("Camera opened.")

print("\nAvailable camera attributes/methods containing trigger:")
for name in dir(cam):
    if "trigger" in name.lower():
        print(name)

print("\nAvailable camera attributes/methods containing acq:")
for name in dir(cam):
    if "acq" in name.lower():
        print(name)

cam.close()