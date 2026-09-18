# -*- coding: utf-8 -*-
"""点进某个侧栏页面，用 elementFromPoint 找出**真正盖在最上面**的不透明元素。

为什么不用"遍历所有元素看谁不透明"：那样会把垫在最底下的 app 根背景层
（实测一个无类名的 div，rgb(243,245,249)，覆盖 96%）也算成元凶。
elementFromPoint 取的是该像素点最上层的元素，才是视觉上真正盖住壁纸的那个。

用法：python scripts/_top-layer.py 资料库 [--port 9348]
"""
import importlib.util
import json
import os
import sys
import time

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
spec = importlib.util.spec_from_file_location("inj", os.path.join(SKILL, "scripts", "inject.py"))
inj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inj)

argv = sys.argv[1:]
PORT = 9347
if "--port" in argv:
    index = argv.index("--port")
    PORT = int(argv[index + 1])
    del argv[index:index + 2]
PAGE = argv[0] if argv else None

CLICK = """(() => {
  const want = %s;
  const hits = [];
  for (const el of document.querySelectorAll('[data-view-id="sidebar"] *')) {
    if ((el.textContent || '').trim() !== want) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    hits.push(el);
  }
  if (!hits.length) return JSON.stringify({ clicked: false });
  (hits[hits.length - 1].closest('button, [role="button"], a') || hits[hits.length - 1]).click();
  return JSON.stringify({ clicked: true });
})()"""

TOP = """(() => {
  const probe = document.createElement('canvas'); probe.width = probe.height = 1;
  const ctx = probe.getContext('2d');
  const alphaOf = (c) => { if (!c || c === 'transparent') return 0;
    ctx.clearRect(0,0,1,1); ctx.fillStyle = '#000'; ctx.fillStyle = c; ctx.fillRect(0,0,1,1);
    return ctx.getImageData(0,0,1,1).data[3] / 255; };
  const vw = innerWidth, vh = innerHeight;
  const found = new Map();
  for (let y = 40; y < vh - 10; y += 18) {
    for (let x = 10; x < vw - 10; x += 18) {
      let el = document.elementFromPoint(x, y);
      // 往上找第一个有实质背景的祖先（最多 12 层）
      let depth = 0;
      while (el && depth < 12) {
        const cs = getComputedStyle(el);
        const a = alphaOf(cs.backgroundColor);
        const hasImg = cs.backgroundImage !== 'none';
        const ours = String(cs.backdropFilter).indexOf('blur(') >= 0 && Math.abs(a - 0.6076) < 0.02;
        if (!ours && (a >= 0.5 || hasImg)) {
          const key = el.tagName.toLowerCase() + '.' + String(el.className).slice(0, 50);
          if (!found.has(key)) found.set(key, { key, bg: cs.backgroundColor,
            bgi: hasImg ? cs.backgroundImage.slice(0, 34) : null, a: +a.toFixed(2), n: 0,
            rect: (r => [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)])(el.getBoundingClientRect()) });
          found.get(key).n += 1;
          break;
        }
        el = el.parentElement; depth += 1;
      }
    }
  }
  return JSON.stringify([...found.values()].sort((p,q) => q.n - p.n).slice(0, 14));
})()"""


def main():
    ws = inj.renderer_targets(PORT)[0]["webSocketDebuggerUrl"]

    def evaluate(expr, timeout=40):
        return inj.cdp_evaluate(ws, expr, timeout=timeout)

    if PAGE:
        print("切到:", PAGE, evaluate(CLICK % json.dumps(PAGE)))
        time.sleep(2.2)

    data = json.loads(evaluate(TOP))
    print()
    print("真正盖在最上层的不透明元素（按采样点命中数排序）:")
    total = sum(h["n"] for h in data) or 1
    for h in data:
        print()
        print("  命中 %3d 点 (%4.1f%%)  alpha=%.2f  %s" % (h["n"], h["n"] / total * 100, h["a"], h["bgi"] or h["bg"]))
        print("     %s   rect=%s" % (h["key"], h["rect"]))
    if not data:
        print("   （没有任何不透明元素盖在上面 —— 干净）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
