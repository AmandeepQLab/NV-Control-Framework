from pylablib.devices import Andor

cam = Andor.AndorSDK3Camera(0)

for name in dir(cam):
    lname = name.lower()
    if (
        "mode" in lname
        or "trigger" in lname
        or "exposure" in lname
        or "shutter" in lname
        or "cycle" in lname
    ):
        print(name)

cam.close()
