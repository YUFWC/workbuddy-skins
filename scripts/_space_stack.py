# -*- coding: utf-8 -*-
"""对「资料库」iframe 里的指定坐标做**逐层元素栈**转储。

为什么需要这个：`_space.py probe` 只给"按覆盖率排序的不透明元素"，
但那对"某个具体区域为什么还是白的"帮助有限 —— 需要知道那个像素点上
从内到外每一层是谁、什么背景、多大矩形，才能判断该压哪一层。

用户标注的三个区域（截图是 640px 宽的面板）：
  1) 顶部标题区（「最近」那一行往上的空白）
  2) Tab 栏（最近访问 / 我分享的 / 与我共享）
  3) 表头（名称 / 所有者 / 位置 / 最近访问）

用法：python scripts/_space_stack.py <x> <y> [<x> <y> ...] [--port 9348]
"""
import importlib.util
import json
import os
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("inj", os.path.join(SKILL, "scripts", "inject.py"))
inj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inj)

argv = sys.argv[1:]
PORT = 9348
if "--port" in argv:
    i = argv.index("--port")
    PORT = int(argv[i + 1])
    del argv[i:i + 2]
POINTS = [(int(argv[i]), int(argv[i + 1])) for i in range(0, len(argv) - 1, 2)]
if not POINTS:
    POINTS = [(320, 30), (320, 60), (320, 120), (320, 160), (320, 300)]

STACK = """(() => {
  const pts = %s;
  const out = {};
  for (const [px, py] of pts) {
    const chain = [];
    let el = document.elementFromPoint(px, py), d = 0;
    while (el && d < 14) {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      chain.push({
        d, tag: el.tagName,
        cls: String(el.className).slice(0, 72),
        bg: cs.backgroundColor,
        bgi: cs.backgroundImage === 'none' ? null : cs.backgroundImage.slice(0, 40),
        bf: cs.backdropFilter === 'none' ? null : cs.backdropFilter,
        rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      });
      el = el.parentElement; d += 1;
    }
    out[px + ',' + py] = chain;
  }
  return JSON.stringify(out);
})()""" % json.dumps(POINTS)


def main():
    frames = inj.space_iframe_targets(PORT)
    if not frames:
        print("没找到资料库 iframe（面板可能没打开）")
        return 1
    ws = frames[0]["webSocketDebuggerUrl"]
    print("iframe:", ws[:70])
    data = json.loads(inj.cdp_evaluate(ws, STACK, timeout=45))
    for point, chain in data.items():
        print()
        print("=" * 78)
        print("坐标 (%s)" % point)
        print("=" * 78)
        for item in chain:
            flag = ""
            bg = item["bg"]
            if bg and bg not in ("rgba(0, 0, 0, 0)", "transparent"):
                a = 1.0
                try:
                    if "/" in bg:
                        a = float(bg.rsplit("/", 1)[-1].rstrip(")").strip())
                    else:
                        parts = bg.rstrip(")").split(",")
                        if len(parts) >= 4:
                            a = float(parts[3])
                except Exception:
                    a = 1.0
                if a >= 0.5:
                    flag = "  ⚠️ 不透明"
                elif a >= 0.3:
                    flag = "  ~ 半透"
            if item["bgi"]:
                flag += "  [有渐变]"
            print("  %2d  %-6s %-72s%s" % (item["d"], item["tag"], item["cls"] or "(无类名)", flag))
            print("        bg=%s  rect=%s" % (bg, item["rect"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
