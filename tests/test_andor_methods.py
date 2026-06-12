from pylablib.devices import Andor

cam = Andor.AndorSDK3Camera(0)

for name in dir(cam):
    lname = name.lower()
    if "soft" in lname or "trigger" in lname or "frame" in lname or "image" in lname:
        print(name)

cam.close()
