import sys, numpy as np
from framework.image_cube import ImageCube

c = ImageCube.load(sys.argv[1])
d = np.asarray(c.data, dtype=np.float64)
f = np.asarray(c.scan_axis_values, dtype=float)

print("%8s %12s %10s %10s %10s" % ("B(G)", "mean", "sharpness", "cx", "cy"))
rows = []
for i in range(len(f)):
    img = d[i]
    gy, gx = np.gradient(img)
    sharp = (gx**2 + gy**2).mean() / img.mean()**2     # focus metric
    yy, xx = np.indices(img.shape)
    cx = (img*xx).sum()/img.sum()
    cy = (img*yy).sum()/img.sum()
    rows.append((f[i], img.mean(), sharp, cx, cy))

for r in rows[::3]:
    print("%8.1f %12.1f %10.6f %10.4f %10.4f" % r)

a = np.array(rows)
print()
for j, name in [(2, "sharpness"), (3, "centroid x"), (4, "centroid y")]:
    lo = a[np.abs(a[:,0]) <= 1.5, j].mean()
    hi = a[np.abs(a[:,0]) >= 13, j].mean()
    print("%-12s near-zero %.6f  wings %.6f  change %+.4f%%"
          % (name, lo, hi, 100*(lo-hi)/abs(hi)))