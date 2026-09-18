"""垂直剖面：沿给定 x 从 y0 到 y1 逐像素采样，打印亮度剖面并标出最大跳变（色阶缝）。
用法：python _vprofile.py img.png x y0 y1 [step]
"""
from PIL import Image
import numpy as np
import sys

p = sys.argv[1]
x = int(sys.argv[2]); y0 = int(sys.argv[3]); y1 = int(sys.argv[4])
step = int(sys.argv[5]) if len(sys.argv) > 5 else 2

a = np.asarray(Image.open(p).convert("RGB")).astype(float)
lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]

ys = list(range(y0, y1, step))
vals = [lum[y, x] for y in ys]
print(f"{p}  x={x}  y {y0}..{y1}")
print("y      RGB              lum")
for y in ys:
    c = a[y, x].astype(int)
    bar = "#" * max(0, int((lum[y, x] - 100) / 4))
    print(f"{y:5d}  ({c[0]:3d},{c[1]:3d},{c[2]:3d})  {lum[y,x]:6.1f} {bar}")

d = np.abs(np.diff(vals))
k = int(np.argmax(d))
print(f"\n最大单步跳变: y={ys[k]} -> {ys[k+1]}  Δlum={d[k]:.1f}  (每 {step}px)")
# 找所有 >6 的跳变
jumps = [(ys[i], ys[i + 1], d[i]) for i in range(len(d)) if d[i] > 6]
print("跳变 >6 的区段:", [(a_, b_, round(c_, 1)) for a_, b_, c_ in jumps])
