from andor3 import Andor3

print("Creating Andor3 object...")

cam = Andor3()

print("Created.")

print("\nAvailable methods/attributes containing:")
for name in dir(cam):
    lname = name.lower()
    if (
        "open" in lname
        or "close" in lname
        or "queue" in lname
        or "wait" in lname
        or "command" in lname
        or "feature" in lname
        or "acquisition" in lname
        or "buffer" in lname
        or "trigger" in lname
        or "set" in lname
        or "get" in lname
    ):
        print(name)
