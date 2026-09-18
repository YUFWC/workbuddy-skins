"""合成「模型选择器白胶囊」修复前后对比图（含放大细节）。"""
from PIL import Image, ImageDraw, ImageFont
import sys
import os

BEFORE = sys.argv[1] if len(sys.argv) > 1 else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_诊断截图", "bar_before.png"))
AFTER = sys.argv[2] if len(sys.argv) > 2 else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_诊断截图", "bar_after_patch.png"))
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "输入框白胶囊-修复前后对比.png"))

CROP = (1010, 762, 1360, 856)
SCALE = 3


def font(sz, bold=False):
    for p in (r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            continue
    return ImageFont.load_default()


f_title = font(32, True)
f_lab = font(28, True)
f_small = font(22)
f_note = font(23)

imgs = [Image.open(p).convert("RGB") for p in (BEFORE, AFTER)]
crops = [im.crop(CROP).resize(((CROP[2] - CROP[0]) * SCALE, (CROP[3] - CROP[1]) * SCALE), Image.LANCZOS) for im in imgs]
cw, ch = crops[0].size

PAD = 26
LAB_W = 160
GAP = 20
HEAD = 58
FOOT = 118
W = PAD + LAB_W + cw + PAD
H = PAD + HEAD + ch + GAP + ch + FOOT + PAD

canvas = Image.new("RGB", (W, H), (26, 28, 32))
d = ImageDraw.Draw(canvas)
d.text((PAD, PAD - 6), "输入框右下角白胶囊 · 修复前后", font=f_title, fill=(240, 244, 250))

labels = [("修复前", (255, 150, 150)), ("修复后", (140, 235, 170))]
for i, c in enumerate(crops):
    y = PAD + HEAD + i * (ch + GAP)
    x = PAD + LAB_W
    canvas.paste(c, (x, y))
    d.rectangle([x - 2, y - 2, x + cw + 1, y + ch + 1], outline=(92, 98, 108), width=2)
    d.text((PAD, y + ch // 2 - 20), labels[i][0], font=f_lab, fill=labels[i][1])

fy = PAD + HEAD + ch + GAP + ch + 18
lines = [
    ("白胶囊 = .cr-input-toolbar__right（模型选择器底衬），原本是不透明 rgb(255,255,255)",
     (208, 214, 226)),
    ("修复前：胶囊底 255,255,255　与周围玻璃差 +17.5 / +16.5 / +12.8（一眼看得出的白块）",
     (255, 168, 168)),
    ("修复后：胶囊底 237.1,238.1,242.0　与周围玻璃差 −0.4 / −0.5 / −0.2（已是同一片玻璃）",
     (150, 235, 180)),
    ("另：合成鼠标事件逼过 hover，该子树本来就没有任何 hover / active 底色，所以置透明不会丢交互反馈",
     (150, 176, 210)),
]
for i, (t, col) in enumerate(lines):
    d.text((PAD, fy + i * 27), t, font=f_note, fill=col)

canvas.save(OUT)
print("saved", OUT, canvas.size)
