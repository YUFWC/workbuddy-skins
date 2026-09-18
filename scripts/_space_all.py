# -*- coding: utf-8 -*-
"""把「资料库」iframe 文档里**所有**不透明/带渐变的元素一次性列出来。

为什么不用 elementFromPoint 采样：
   采样法只覆盖"有像素落在采样点上"的元素，小块、被遮挡的、以及
   "压掉上层后才露出来"的层都会漏（本轮就漏了 header / tab-row / thead 三层）。
   这里改成**遍历 DOM 树**，逐个 getComputedStyle，把所有
   `alpha>=0.5` 或 `background-image != none` 的元素全捞出来，
   按面积降序 —— 适合"一次找齐、批量修"。

⚠️ 会附带打印每个元素的**祖先链前 3 层**，用来判断它是不是
   "某个我们已经压平过的容器的子元素"（避免误伤）。

用法：python scripts/_space_all.py [--port 9348] [--min-area 400]
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

ALL = """(() => {
  const alphaOf = (c) => {
    if (!c || c === 'transparent') return 0;
    const m = String(c).match(/rgba?\\(([^)]*)\\)/);
    if (!m) return 1;
    const parts = m[1].split(/[,\\/]+/).map(s => s.trim()).filter(Boolean);
    return parts.length >= 4 ? parseFloat(parts[3]) : 1;
  };
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    const a = alphaOf(cs.backgroundColor);
    const hasImg = cs.backgroundImage !== 'none';
    if (a < 0.5 && !hasImg) continue;
    const r = el.getBoundingClientRect();
    const area = Math.round(r.width * r.height);
    if (area < 100) continue;
    const chain = [];
    let p = el.parentElement, d = 0;
    while (p && d < 3) {
      chain.push(p.tagName.toLowerCase() + '.' + (String(p.className).slice(0, 40) || '(无)'));
      p = p.parentElement; d++;
    }
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: String(el.className).slice(0, 76) || '(无类名)',
      bg: cs.backgroundColor,
      bgi: hasImg ? cs.backgroundImage.slice(0, 50) : null,
      alpha: +a.toFixed(2),
      area,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      above: chain.join(' < '),
    });
  }
  out.sort((p, q) => q.area - p.area);
  return JSON.stringify({ total: out.length, list: out.slice(0, 60) });
})()"""


def main():
    frames = inj.space_iframe_targets(PORT)
    if not frames:
        print("没找到资料库 iframe（面板可能没打开）")
        return 1
    data = json.loads(inj.cdp_evaluate(frames[0]["webSocketDebuggerUrl"], ALL, timeout=60))
    print("iframe 内不透明/带渐变元素共 %d 个，列出前 %d：\n" % (data["total"], len(data["list"])))
    for it in data["list"]:
        mark = "⚠️" if it["alpha"] >= 0.5 else "~ "
        print("%s area=%-8d alpha=%.2f  %s.%s" % (mark, it["area"], it["alpha"], it["tag"], it["cls"]))
        print("      bg=%s%s" % (it["bg"], ("  bgi=" + it["bgi"]) if it["bgi"] else ""))
        print("      rect=%s" % it["rect"])
        print("      上层: %s" % it["above"])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
