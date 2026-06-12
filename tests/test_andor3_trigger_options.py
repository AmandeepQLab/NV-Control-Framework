from andor3 import Andor3

cam = Andor3()
cam.open(0)

count = cam.getEnumCount("TriggerMode")

print("TriggerMode options:")

for i in range(count):
    try:
        print(i, cam.getEnumStringByIndex("TriggerMode", i))
    except Exception as e:
        print(i, "FAILED", e)

cam.close()
