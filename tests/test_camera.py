import time
from hardware.camera.sim_camera import SimCamera


cam = SimCamera()

# -------------------------
# SNAP MODE TEST (ODMR style)
# -------------------------
print("\n[TEST] Snap mode")
frame = cam.snap()
print("Frame shape:", frame.shape)
print("Mean intensity:", frame.mean())

# -------------------------
# STREAM MODE TEST (GUI style)
# -------------------------
print("\n[TEST] Stream mode start")
cam.start_stream()

time.sleep(0.2)

frame2 = cam.get_latest_frame()
print("Stream frame shape:", frame2.shape)
print("Stream mean intensity:", frame2.mean())

cam.stop_stream()

print("\n[TEST] Stream stopped")