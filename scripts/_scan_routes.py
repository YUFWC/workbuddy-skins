# -*- coding: utf-8 -*-
"""遍历侧栏所有入口，逐页扫描主内容区里的不透明背景 —— 一次性找出所有"没透明的页面"。

这是「某个页面发白」的首选排查工具：比一页页截图快，也不会漏。
跑完会把界面切回原来那个对话。

用法：
    python scripts/_scan_routes.py [端口]
默认端口 9347（国际版）。国内版若用 9348，就传 9348。
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

LIST_TABS = """(() => {
  const out = [];
  for (const el of document.querySelectorAll('[data-view-id="sidebar"] .conversation-list-tab-button-label')) {
    const r = el.getBoundingClientRect();
    if (r.width < 8) continue;
    out.push((el.textContent || '').trim());
  }
  return JSON.stringify(out);
})()"""

# ⚠️ 必须用 JS 的 .click()：CDP 的 Input.dispatchMouseEvent 在这个应用里点不动
# （早先 _goto_home.mjs 就踩过这个坑）。
CLICK_TAB = """(() => {
  const want = %s;
  const hits = [];
  for (const el of document.querySelectorAll('[data-view-id="sidebar"] *')) {
    if ((el.textContent || '').trim() !== want) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    hits.push(el);
  }
  if (!hits.length) return JSON.stringify({ clicked: false });
  const clickable = hits[hits.length - 1].closest('button, [role="button"], a') || hits[hits.length - 1];
  clickable.click();
  return JSON.stringify({ clicked: true, cls: String(clickable.className).slice(0, 50) });
})()"""

SCAN = """(() => {
  // ⚠️ 必须扫**整个视口**，不能只扫 [data-view-id="main-content"]。
  // 资料库面板是 .sidebar-next，渲染在主内容区**之外** —— 只扫 main-content
  // 会给出"干净"的假结论（真踩过：用户截图明明全白，脚本却说 ✅）。
  //
  // 也不能简单遍历所有元素看谁不透明：那样会把垫在最底下的 app 根背景层
  // （实测一个无类名的 div，rgb(243,245,249)，覆盖 96%）算成元凶。
  // 所以用 elementFromPoint 取每个采样点上**真正盖在最上面**的元素。
  const probe = document.createElement('canvas'); probe.width = probe.height = 1;
  const ctx = probe.getContext('2d');
  const alphaOf = (c) => { if (!c || c === 'transparent') return 0;
    ctx.clearRect(0,0,1,1); ctx.fillStyle = '#000'; ctx.fillStyle = c; ctx.fillRect(0,0,1,1);
    return ctx.getImageData(0,0,1,1).data[3] / 255; };
  const vw = innerWidth, vh = innerHeight;
  const found = new Map();
  let samples = 0;
  for (let y = 40; y < vh - 10; y += 20) {
    for (let x = 10; x < vw - 10; x += 20) {
      samples += 1;
      let el = document.elementFromPoint(x, y);
      let depth = 0;
      while (el && depth < 12) {
        const cs = getComputedStyle(el);
        const a = alphaOf(cs.backgroundColor);
        const hasImg = cs.backgroundImage !== 'none';
        const ours = String(cs.backdropFilter).indexOf('blur(') >= 0 && Math.abs(a - 0.6076) < 0.02;
        if (!ours && (a >= 0.5 || hasImg)) {
          const key = el.tagName.toLowerCase() + '.' + String(el.className).slice(0, 50);
          if (!found.has(key)) found.set(key, { t: el.tagName.toLowerCase(), cls: String(el.className).slice(0, 56),
            bg: cs.backgroundColor, a: +a.toFixed(2), bgi: hasImg ? cs.backgroundImage.slice(0, 44) : null,
            ours: false, n: 0, cov: 0,
            rect: (r => [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)])(el.getBoundingClientRect()) });
          found.get(key).n += 1;
          break;
        }
        el = el.parentElement; depth += 1;
      }
    }
  }
  const list = [...found.values()].map(h => ({ ...h, cov: +(h.n / samples * 100).toFixed(1) }));
  list.sort((p, q) => q.n - p.n);
  return JSON.stringify({ visible: list.slice(0, 10), samples });
})()"""


def main():
    ws = inj.renderer_targets(PORT)[0]["webSocketDebuggerUrl"]

    def evaluate(expr, timeout=25):
        return inj.cdp_evaluate(ws, expr, timeout=timeout)

    tabs = json.loads(evaluate(LIST_TABS))
    print("侧栏入口:", tabs)
    print()

    # 先记下当前**激活的**对话，最后切回去。
    # ⚠️ 不能取第一张卡：会话卡在 DOM 里的顺序 ≠ 当前打开的那个，
    # 早先就是这么把用户切到别的对话去的。
    # 激活态是卡上的 `_selected_*` 类（实测 class 形如
    #   _card_914ll_1 cb-agent-card _selected_914ll_20 _compact_914ll_26）。
    back_title = evaluate("""(() => {
      const cards = [...document.querySelectorAll('[data-view-id="sidebar"] .cb-agent-card, [data-view-id="sidebar"] .conversation-agent-card')];
      const active = cards.find(c => /(^|\\s)_selected_/.test(String(c.className))) || cards[0];
      return active ? (active.textContent || '').trim().slice(0, 24) : '';
    })()""")
    evaluate(CLICK_TAB % json.dumps("新建任务"))
    time.sleep(1.5)

    report = {}
    for name in tabs:
        clicked = json.loads(evaluate(CLICK_TAB % json.dumps(name)))
        time.sleep(1.8)
        data = json.loads(evaluate(SCAN))
        report[name] = data
        print("=" * 74)
        print("【%s】 clicked=%s" % (name, clicked.get("clicked")))
        if "error" in data:
            print("   ", data["error"]); continue
        if not data["visible"]:
            print("    ✅ 可见区域里没有不透明背景（cov≥1%）")
        for h in data["visible"]:
            mark = "（我们的玻璃）" if h.get("ours") else "⚠️ 需要处理"
            print("    %s  cov=%5.1f%%  alpha=%.2f  %s" % (mark, h["cov"], h["a"], h["bgi"] or h["bg"]))
            print("        %s.%s   rect=%s" % (h["t"], h["cls"], h["rect"]))

    print()
    print("=" * 74)
    print("汇总：各页最大的不透明块")
    for name, data in report.items():
        if "error" in data:
            continue
        top = data["visible"][0] if data["visible"] else None
        print("  %-18s %s" % (name, ("%5.1f%%  %s.%s" % (top["cov"], top["t"], top["cls"][:44])) if top else "   --   （干净）"))

    # 切回对话
    if back_title:
        evaluate("""(() => {
          const cards = [...document.querySelectorAll('[data-view-id="sidebar"] .cb-agent-card, [data-view-id="sidebar"] .conversation-agent-card')];
          const el = cards.find(c => (c.textContent || '').indexOf(%s) >= 0);
          if (!el) return 'not-found';
          (el.closest('button, [role="button"], a') || el).click();
          return 'ok';
        })()""" % json.dumps(back_title))
        print("\n已切回对话:", back_title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
