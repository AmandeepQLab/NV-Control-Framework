import inspect
from andor3 import Andor3

cam = Andor3()
cam.open(0)

print(inspect.signature(cam.queueBuffer))
print(inspect.getsource(cam.queueBuffer))

cam.close()
