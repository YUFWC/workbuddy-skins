"""裁切放大截图的指定区域，便于人工确认方框位置。

用法：
    python _zoom_annot.py 图.png 输出.png x0,y0,x1,y1 [倍率]

⚠️ 刻意**不给默认输入图** —— 早先版本写死了某张本机剪贴板图片的绝对路径，
既不可移植，也会把开发者的目录结构带进公开仓库。现在必须显式传图。
"""
from PIL import Image
import os
import sys

if len(sys.argv) < 3:
    print(__doc__.strip())
    print("")
    print("例：python _zoom_annot.py shot.png out.png 70,140,960,300 2")
    raise SystemExit(2)

src = sys.argv[1]
out = sys.argv[2]
box = (tuple(int(v) for v in sys.argv[3].split(","))
       if len(sys.argv) > 3 else (70, 140, 960, 300))
scale = int(sys.argv[4]) if len(sys.argv) > 4 else 2

if not os.path.isfile(src):
    raise SystemExit("找不到输入图：%s" % src)

im = Image.open(src).convert("RGB")
c = im.crop(box)
c = c.resize((c.width * scale, c.height * scale), Image.NEAREST)
c.save(out)
print("saved", out, c.size, "crop", box)
