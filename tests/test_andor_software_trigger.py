from pylablib.devices import Andor
import numpy as np

cam = Andor.AndorSDK3Camera(0)

cam.set_trigger_mode("software")
cam.setup_acquisition(nframes=1)
cam.start_acquisition()

print("Sending software trigger...")
cam.send_software_trigger()

cam.wait_for_frame(timeout=5.0)
frame = cam.read_oldest_image()
frame = np.array(frame)

print("Frame received:", frame.shape)

cam.stop_acquisition()
cam.clear_acquisition()
cam.close()
