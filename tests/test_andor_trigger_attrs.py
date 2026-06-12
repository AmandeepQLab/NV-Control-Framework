from pylablib.devices import Andor

cam = Andor.AndorSDK3Camera(0)

attrs = cam.get_all_attributes()

for name in attrs:
    lname = name.lower()
    if (
        "trigger" in lname
        or "input" in lname
        or "io" in lname
        or "invert" in lname
        or "polarity" in lname
        or "aux" in lname
        or "ttl" in lname
    ):
        try:
            val = cam.get_attribute_value(name)
            print(name, "=", val)
        except Exception as e:
            print(name, "FAILED:", e)

cam.close()
