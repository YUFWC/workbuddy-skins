"""通用取样工具：给若干张同尺寸截图 + 若干坐标点，输出每个文件在这些点的 RGB 和两两差值。
用法：
  python _sample.py "img1.png,img2.png" "927,740;700,740;927,820" [标签...]
"""
from PIL import Image
import numpy as np
import sys

imgs = [s for s in sys.argv[1].split(",") if s]
pts = [tuple(int(v) for v in s.split(",")) for s in sys.argv[2].split(";") if s]

data = {}
for p in imgs:
    a = np.asarray(Image.open(p).convert("RGB")).astype(int)
    data[p] = a
    print(f"{p}: {a.shape[1]}x{a.shape[0]}")

names = [p.split("exp_")[-1].replace(".png", "") if "exp_" in p else p.split("/")[-1] for p in imgs]

print("\n点位采样 (R,G,B):")
head = "point".ljust(12) + "".join(n.ljust(20) for n in names)
print(head)
for (x, y) in pts:
    row = f"({x},{y})".ljust(12)
    for p in imgs:
        c = data[p][y, x]
        row += f"({c[0]},{c[1]},{c[2]})".ljust(20)
    print(row)

# 面板内部 vs 工具条 的色阶差
if len(pts) >= 3:
    print("\n色阶差 Δ (|面板 - 工具条|，取亮度):")
    for i, p in enumerate(imgs):
        a = data[p]
        panel = a[pts[0][1], pts[0][0]].astype(float)
        bar = a[pts[2][1], pts[2][0]].astype(float)
        lum = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
        print(f"  {names[i]}: panel={panel.astype(int)} bar={bar.astype(int)} Δlum={abs(lum(panel)-lum(bar)):.1f}")
