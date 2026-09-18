"""合成输入框「修复前 / 修复后」对比图（含色阶剖面）。"""
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import sys
import os

BEFORE = sys.argv[1] if len(sys.argv) > 1 else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_诊断截图", "exp_base.png"))
AFTER = sys.argv[2] if len(sys.argv) > 2 else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_诊断截图", "exp_nobf.png"))
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "输入框色差-修复前后对比.png"))

CROP = (480, 680, 1380, 860)   # 输入框及周边
SCALE = 2
X_PROBE = 927

def font(sz, bold=False):
    for p in (r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            continue
    return ImageFont.load_default()

f_title = font(30, True)
f_lab = font(26, True)
f_small = font(21)

imgs = [Image.open(p).convert("RGB") for p in (BEFORE, AFTER)]
crops = [im.crop(CROP).resize(((CROP[2]-CROP[0])*SCALE, (CROP[3]-CROP[1])*SCALE), Image.LANCZOS) for im in imgs]
cw, ch = crops[0].size

PAD = 24
LAB_W = 150
GAP = 18
PROF_W = 250
HEAD = 56
FOOT = 86
W = PAD + LAB_W + cw + GAP + PROF_W + PAD
H = PAD + HEAD + ch + GAP + ch + FOOT + PAD

canvas = Image.new("RGB", (W, H), (26, 28, 32))
d = ImageDraw.Draw(canvas)
d.text((PAD, PAD - 4), "输入框「图层色差」修复对比", font=f_title, fill=(240, 244, 250))

labels = [("修复前", (255, 150, 150)), ("修复后", (140, 235, 170))]

for i, c in enumerate(crops):
    y = PAD + HEAD + i * (ch + GAP)
    x = PAD + LAB_W
    canvas.paste(c, (x, y))
    d.rectangle([x - 2, y - 2, x + cw + 1, y + ch + 1], outline=(90, 95, 105), width=2)
    d.text((PAD, y + ch // 2 - 18), labels[i][0], font=f_lab, fill=labels[i][1])

    # 右侧剖面条：沿 x=X_PROBE 取亮度，横过来画
    a = np.asarray(imgs[i].convert("RGB")).astype(float)
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    y0, y1 = CROP[1], CROP[3]
    px = PAD + LAB_W + cw + GAP
    for yy in range(y0, y1):
        v = int(max(0, min(255, lum[yy, X_PROBE])))
        py = y + int((yy - y0) * SCALE)
        col = (v, v, v) if i == 1 else (v, int(v * 0.86), int(v * 0.86))
        d.rectangle([px, py, px + 60, py + SCALE - 1], fill=col)
    # 标注两条缝的位置
    for seam, name in ((717, "可编辑区上沿"), (781, "可编辑区下沿")):
        py = y + int((seam - y0) * SCALE)
        d.line([px, py, px + PROF_W - 8, py], fill=(255, 210, 90), width=1)
        d.text((px + 68, py - 12), name, font=f_small, fill=(255, 210, 90))
    d.text((px, y + ch + 2), f"亮度剖面 x={X_PROBE}", font=f_small, fill=(170, 176, 186))

# 底部结论
fy = PAD + HEAD + ch + GAP + ch + 14
d.text((PAD, fy), "成因：输入框内部的可编辑区带了 backdrop-filter，Chromium 把父级玻璃底色又合成了一遍 → 上半浮出一块更亮的板",
       font=f_small, fill=(210, 216, 226))
d.text((PAD, fy + 30), "修复前：可编辑区 (232,236,239) ／ 工具条 (220,224,226)，交界处 Δ≈13 的硬色阶缝    →    修复后：整框连续，内部无任何跳变（只剩容器自身的上下边界）",
       font=f_small, fill=(150, 235, 180))

canvas.save(OUT)
print("saved", OUT, canvas.size)
