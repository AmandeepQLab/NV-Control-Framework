from pylablib.devices import Andor

cam = Andor.AndorSDK3Camera(0)

print("Camera object attributes containing:")
for name in dir(cam):
    lname = name.lower()
    if "attr" in lname or "feature" in lname or "set_" in lname or "get_" in lname:
        print(name)

cam.close()
