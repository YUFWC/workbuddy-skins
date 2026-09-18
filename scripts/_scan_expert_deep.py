# -*- coding: utf-8 -*-
"""穷举「技能」「连接器」两个 Tab 里所有不透明背景元素，按类名归并。

比 elementFromPoint 采样更彻底：直接把子树里每个元素都量一遍，
这样连被卡片盖住的小控件（搜索框、筛选胶囊、徽标）也能找出来。

用法：python scripts/_scan_expert_deep.py [端口]
"""
import importlib.util
import json
import os
import sys
import time

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("inj", os.path.join(SKILL, "scripts", "inject.py"))
inj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inj)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9347

ENTER_PAGE = """(() => {
  for (const el of document.querySelectorAll('[data-view-id="sidebar"] *')) {
    if ((el.textContent || '').trim() !== '专家·技能·连接器') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    (el.closest('button, [role="button"], a') || el).click();
    return 'clicked';
  }
  return 'not-found';
})()"""

CLICK_INNER = """(() => {
  const want = %s;
  const cands = [];
  for (const el of document.querySelectorAll('.um-tab[role="tab"]')) {
    if ((el.textContent || '').trim() !== want) continue;
    cands.push(el);
  }
  if (!cands.length) return 'not-found';
  cands[0].click();
  return 'clicked';
})()"""

DEEP = """(() => {
  const probe = document.createElement('canvas'); probe.width = probe.height = 1;
  const ctx = probe.getContext('2d');
  const alphaOf = (c) => { if (!c || c === 'transparent') return 0;
    ctx.clearRect(0,0,1,1); ctx.fillStyle = '#000'; ctx.fillStyle = c; ctx.fillRect(0,0,1,1);
    return ctx.getImageData(0,0,1,1).data[3] / 255; };
  const roots = document.querySelectorAll(%s);
  if (!roots.length) return JSON.stringify({ error: 'root not found' });
  const groups = new Map();
  for (const root of roots) {
    const all = [root, ...root.querySelectorAll('*')];
    for (const el of all) {
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      const cs = getComputedStyle(el);
      const a = alphaOf(cs.backgroundColor);
      const hasImg = cs.backgroundImage !== 'none';
      if (a < 0.5 && !hasImg) continue;
      // 跳过我们自己已经处理过的玻璃（backdrop blur + 面板色）
      if (String(cs.backdropFilter).indexOf('blur(') >= 0 && Math.abs(a - 0.6076) < 0.03) continue;
      const key = el.tagName.toLowerCase() + '.' + String(el.className).slice(0, 60);
      if (!groups.has(key)) groups.set(key, {
        t: el.tagName.toLowerCase(), cls: String(el.className).slice(0, 70),
        bg: cs.backgroundColor, a: +a.toFixed(2),
        bgi: hasImg ? cs.backgroundImage.slice(0, 50) : null,
        radius: cs.borderRadius, n: 0, area: 0,
        sampleRect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
        sampleText: (el.textContent || '').trim().slice(0, 20) });
      const g = groups.get(key);
      g.n += 1;
      g.area += r.width * r.height;
    }
  }
  const list = [...groups.values()].map(g => ({ ...g, area: Math.round(g.area) }));
  list.sort((p, q) => q.area - p.area);
  return JSON.stringify({ total: list.length, list: list.slice(0, 30) });
})()"""


def main():
    ws = inj.renderer_targets(PORT)[0]["webSocketDebuggerUrl"]

    def evaluate(expr, timeout=25):
        return inj.cdp_evaluate(ws, expr, timeout=timeout)

    print("进入页面:", evaluate(ENTER_PAGE))
    time.sleep(2.2)

    for name, root_sel in [("技能", "'.skills-view'"), ("连接器", "'.connector-panel'")]:
        print("点击 Tab:", name, evaluate(CLICK_INNER % json.dumps(name)))
        time.sleep(2.0)
        data = json.loads(evaluate(DEEP % root_sel))
        print("=" * 78)
        print("【%s】 不透明元素种类：%s" % (name, data.get("total")))
        if data.get("error"):
            print("   ", data["error"]); continue
        for h in data["list"]:
            print("  n=%-4d area=%-9d a=%.2f  %s" % (h["n"], h["area"], h["a"], h["bgi"] or h["bg"]))
            print("      %s.%s" % (h["t"], h["cls"]))
            print("      rect=%s  radius=%s  text=%r" % (h["sampleRect"], h["radius"], h["sampleText"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
