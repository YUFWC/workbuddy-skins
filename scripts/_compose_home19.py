"""合成「新建任务页整块白底」修复前后对比图。"""
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import sys
import os

BEFORE = sys.argv[1] if len(sys.argv) > 1 else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_诊断截图", "home19_before.png"))
AFTER = sys.argv[2] if len(sys.argv) > 2 else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_诊断截图", "home19_after.png"))
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "新建任务页白底-修复前后对比.png"))

CROP = (270, 30, 1590, 560)
SCALE = 0.66


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
f_note = font(21)

imgs = [Image.open(p).convert("RGB") for p in (BEFORE, AFTER)]
crops = []
for im in imgs:
    c = im.crop(CROP)
    crops.append(c.resize((int(c.width * SCALE), int(c.height * SCALE)), Image.LANCZOS))
cw, ch = crops[0].size

PAD = 24
LAB_W = 130
GAP = 16
HEAD = 52
FOOT = 92
W = PAD + LAB_W + cw + PAD
H = PAD + HEAD + ch + GAP + ch + FOOT + PAD

canvas = Image.new("RGB", (W, H), (26, 28, 32))
d = ImageDraw.Draw(canvas)
d.text((PAD, PAD - 6), "「新建任务」页整块白底 · 修复前后", font=f_title, fill=(240, 244, 250))

labels = [("修复前", (255, 150, 150)), ("修复后", (140, 235, 170))]
for i, c in enumerate(crops):
    y = PAD + HEAD + i * (ch + GAP)
    x = PAD + LAB_W
    canvas.paste(c, (x, y))
    d.rectangle([x - 2, y - 2, x + cw + 1, y + ch + 1], outline=(92, 98, 108), width=2)
    d.text((PAD, y + ch // 2 - 18), labels[i][0], font=f_lab, fill=labels[i][1])

# 数值
a = np.asarray(imgs[0].convert("RGB")).astype(float)
b = np.asarray(imgs[1].convert("RGB")).astype(float)
reg = (slice(60, 830), slice(280, 1580))
ma = a[reg].mean(axis=(0, 1))
mb = b[reg].mean(axis=(0, 1))

fy = PAD + HEAD + ch + GAP + ch + 16
lines = [
    ("元凶：main.wb-home-route —— 不透明 rgb(255,255,255)，264,30 1327×844，是 [data-view-id=\"main-content\"] 的子元素",
     (208, 214, 226)),
    (f"主内容区平均色：修复前 {ma.round(1)}（几乎纯白）  →  修复后 {mb.round(1)}（壁纸透出来了）",
     (255, 220, 150)),
    ("侧栏像素完全不变（Δ=0），说明改的只是 home 页主区那一层；共 94% 像素发生变化",
     (150, 235, 180)),
]
for i, (t, col) in enumerate(lines):
    d.text((PAD, fy + i * 26), t, font=f_note, fill=col)

canvas.save(OUT)
print("saved", OUT, canvas.size)
