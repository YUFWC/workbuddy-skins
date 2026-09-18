# -*- coding: utf-8 -*-
"""纯标准库生成启动器图标（.ico），不依赖 Pillow。

图形：深色圆角底 + 中央一个「WB」字样的简化标记 —— 实际画的是一个
渐变圆角方块 + 白色圆点环，和技能里「浮球」的视觉语言一致，小尺寸下也认得出来。

要点（ICO/PNG 手写的坑）：
  * PNG = 签名 + IHDR + IDAT(zlib) + IEND，每行前置一个 filter 字节 0
  * ICO = ICONDIR(6B) + 每个尺寸的 ICONDIRENTRY(16B) + 各图数据
  * **256×256 的宽高字段必须写 0**（ICO 格式用 0 表示 256，写 256 会溢出成 0 之外的怪值）
  * 多尺寸一起打包，Windows 各场景（任务栏/标题栏/Alt+Tab）自己挑
  * 先 4 倍超采样再按 alpha 加权降采样，边缘才平滑

用法：
    python scripts/_make_icon.py [输出路径.ico]
默认写到 assets/launcher.ico。
"""
import os
import struct
import sys
import zlib

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(SKILL, "assets", "launcher.ico")

SIZES = (16, 24, 32, 48, 64, 128, 256)
SS = 4                      # 超采样倍率

# 配色：和 inject_launcher.py 的深色主题一致
BG_TOP = (0x2B, 0x2D, 0x30)
BG_BOTTOM = (0x1E, 0x1F, 0x22)
ACCENT = (0x35, 0x74, 0xF0)     # C_ACCENT
ACCENT2 = (0x7B, 0x5C, 0xD6)    # C_OS（国际版紫）
FG = (0xE8, 0xEC, 0xF2)


def _rounded_alpha(x, y, size, radius):
    """圆角矩形的覆盖率（0..1），用 4 倍超采样后的单点判断。"""
    r = radius
    cx = min(max(x, r), size - r)
    cy = min(max(y, r), size - r)
    dx, dy = x - cx, y - cy
    dist = (dx * dx + dy * dy) ** 0.5
    if dist <= r - 0.5:
        return 1.0
    if dist >= r + 0.5:
        return 0.0
    return r + 0.5 - dist


def render(size):
    """渲染一个 size×size 的 RGBA 像素数组（超采样后降采样）。"""
    import math
    big = size * SS
    radius = big * 0.22
    ring_r = big * 0.26
    ring_w = max(1.0, big * 0.055)
    dot_r = big * 0.062
    dot_orbit = big * 0.30
    cxx = cyy = big / 2.0

    # 圆环的判定带、三个小圆点的坐标都先算好，别丢进像素循环里重复算
    ring_lo, ring_hi = ring_r - ring_w / 2.0, ring_r + ring_w / 2.0
    dot_r2 = dot_r * dot_r
    dots = []
    for k in range(3):
        ang = math.radians(-90 + k * 120)
        dots.append((cxx + math.cos(ang) * dot_orbit,
                     cyy + math.sin(ang) * dot_orbit))
    # 圆环的横向色带也预先打表
    ring_lut = [tuple(int(ACCENT[i] + (ACCENT2[i] - ACCENT[i]) * (x / big))
                      for i in range(3)) for x in range(big)]
    bg_lut = [tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * (y / big))
                    for i in range(3)) for y in range(big)]

    rows = []
    for y in range(big):
        py = y + 0.5
        dy2 = py - cyy
        bg = bg_lut[y]
        row = []
        for x in range(big):
            px = x + 0.5
            alpha = _rounded_alpha(px, py, big, radius)
            if alpha <= 0.0:
                row.append((0.0, 0.0, 0.0, 0.0))
                continue
            dx = px - cxx
            dist = (dx * dx + dy2 * dy2) ** 0.5
            if ring_lo <= dist <= ring_hi:
                r, g, b = ring_lut[x]
            else:
                r, g, b = bg
            for ox, oy in dots:
                ddx, ddy = px - ox, py - oy
                if ddx * ddx + ddy * ddy <= dot_r2:
                    r, g, b = FG
                    break
            row.append((float(r), float(g), float(b), alpha * 255.0))
        rows.append(row)

    # 盒式降采样（按 alpha 加权，避免边缘发黑）
    out = bytearray()
    for y in range(size):
        for x in range(size):
            sr = sg = sb = sa = 0.0
            for dy in range(SS):
                row = rows[y * SS + dy]
                for dx in range(SS):
                    pr, pg, pb, pa = row[x * SS + dx]
                    sr += pr * pa
                    sg += pg * pa
                    sb += pb * pa
                    sa += pa
            if sa > 0:
                out += bytes((int(round(sr / sa)), int(round(sg / sa)),
                              int(round(sb / sa)), int(round(sa / (SS * SS)))))
            else:
                out += b"\x00\x00\x00\x00"
    return bytes(out)


def png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def to_png(size, rgba):
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)                       # filter: None
        raw += rgba[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", ihdr)
            + png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + png_chunk(b"IEND", b""))


def build_ico(images):
    """images: [(size, png_bytes)]，都按 PNG 格式塞进 ICO。"""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries = b""
    payload = b""
    for size, data in images:
        # ⚠️ 256 必须写 0（ICO 的宽高字段是单字节，0 表示 256）
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(data), offset)
        payload += data
        offset += len(data)
    return header + entries + payload


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    images = []
    for size in SIZES:
        rgba = render(size)
        images.append((size, to_png(size, rgba)))
        print("  渲染 %3d×%3d  %6d 字节" % (size, size, len(images[-1][1])))
    data = build_ico(images)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "wb") as handle:
        handle.write(data)
    print("")
    print("已生成 %s（%d 个尺寸，%d 字节）" % (out, len(images), len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
