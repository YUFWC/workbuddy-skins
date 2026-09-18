# -*- coding: utf-8 -*-
"""像素级验证「灵感」页：标题行白带是否消失。

主文档内的页面，直接 Page.captureScreenshot 即可（不需要 iframe 偏移）。
"""
import base64
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402

# 截图上的红框：标题行那一条白带
BOXES = {
    "标题行整条（红框）":     (264, 86, 1482, 128),
    "「灵感」标题区":         (280, 90, 700, 126),
    "搜索框 + 我的收藏":      (1120, 88, 1465, 128),
    "分类 tab 栏":            (264, 142, 1482, 190),
    "卡片区（对照，应仍是玻璃）": (288, 200, 567, 460),
}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9348
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:%d/json/list" % port, timeout=10) as resp:
        targets = json.loads(resp.read().decode("utf-8"))
    page = [t for t in targets if t.get("type") == "page"]
    if not page:
        print("没有主文档 target")
        return
    ws = page[0]["webSocketDebuggerUrl"]

    data = inject.cdp_command(ws, "Page.captureScreenshot",
                              {"format": "png", "captureBeyondViewport": False},
                              timeout=60.0)
    b64 = data.get("data") or (data.get("result") or {}).get("data")
    if not b64:
        print("截图失败: %s" % str(data)[:200])
        return
    raw = base64.b64decode(b64)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "_insp_after.png")
    with open(out_path, "wb") as fh:
        fh.write(raw)
    print("整屏截图: %s" % os.path.abspath(out_path))

    from PIL import Image
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    print("尺寸 %sx%s" % img.size)
    print("=" * 68)
    print("区域近白像素比例（目标：标题行 ≈0%）")
    print("=" * 68)
    for name, (x1, y1, x2, y2) in BOXES.items():
        box = (max(0, x1), max(0, y1), min(img.size[0], x2), min(img.size[1], y2))
        crop = img.crop(box)
        px = list(crop.getdata())
        nw = sum(1 for r, g, b in px if r >= 245 and g >= 245 and b >= 245)
        # 再统计一次「很浅但不是纯白」的（240~245）
        light = sum(1 for r, g, b in px if 235 <= r < 245 and 235 <= g < 245 and 235 <= b < 245)
        print("  %-24s 纯白 %.2f%% (%d/%d)  次白 %.2f%%  中心色=%s"
              % (name, 100.0 * nw / len(px), nw, len(px),
                 100.0 * light / len(px), px[len(px) // 2]))


if __name__ == "__main__":
    main()
