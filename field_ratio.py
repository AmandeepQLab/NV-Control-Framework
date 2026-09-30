import sys, numpy as np
from framework.image_cube import ImageCube

c = ImageCube.load(sys.argv[1])
d = np.asarray(c.data, dtype=np.float64)
f = np.asarray(c.scan_axis_values, dtype=float)

lo = d[np.abs(f) <= 1.5].mean(axis=0)          # near zero field
hi = d[np.abs(f) >= 13].mean(axis=0)           # wings
r  = lo / hi

print("planes %d  near-zero %d  wings %d" % (len(f), (np.abs(f)<=1.5).sum(), (np.abs(f)>=13).sum()))
print("mean near-zero %.1f  wings %.1f  ratio %.5f (%+.3f%%)"
      % (lo.mean(), hi.mean(), r.mean(), 100*(r.mean()-1)))
print("ratio map: std %.5f  min %.4f  max %.4f" % (r.std(), r.min(), r.max()))
print("std / |1-mean| = %.2f   (<<1 uniform scaling; >~1 structured)" % (r.std()/abs(1-r.mean())))

n = r.shape[0]; y, x = np.indices(r.shape)
rad = np.hypot(x-(n-1)/2, y-(r.shape[1]-1)/2)
b = np.linspace(0, rad.max(), 6)
for i in range(5):
    m = (rad>=b[i]) & (rad<b[i+1])
    print("  radius %5.1f-%5.1f : ratio %.5f" % (b[i], b[i+1], r[m].mean()))