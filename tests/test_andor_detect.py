from pylablib.devices import Andor

print("Andor module loaded.")
print("Available Andor functions/classes:")

for name in dir(Andor):
    if "SDK3" in name or "Camera" in name or "camera" in name:
        print(name)

print("\nTrying SDK3 camera discovery...")

try:
    cams = Andor.list_cameras_SDK3()
    print("SDK3 cameras:", cams)

except Exception as e:
    print("list_cameras_SDK3 failed:")
    print(e)

try:
    n = Andor.get_cameras_number_SDK3()
    print("Number of SDK3 cameras:", n)

except Exception as e:
    print("get_cameras_number_SDK3 failed:")
    print(e)
