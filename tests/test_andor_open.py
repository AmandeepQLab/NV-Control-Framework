from pylablib.devices import Andor


print("Testing Andor SDK3 direct open...")

try:
    cam = Andor.AndorSDK3Camera(0)

    print("Camera opened successfully.")

    try:
        info = cam.get_device_info()
        print("Device info:", info)
    except Exception as e:
        print("Could not read device info:")
        print(e)

    cam.close()
    print("Camera closed successfully.")

except Exception as e:
    print("Failed to open SDK3 camera:")
    print(type(e))
    print(e)
