# -*- coding: utf-8 -*-
"""对「资料库」iframe（https://www.workbuddy.cn/space/home）做探测/注入。

关键发现：这个 iframe 是**跨域**的，父文档的 CSS 进不去；
但 Electron 把它暴露成了一个**独立的 CDP target**（type=iframe），
所以我们可以直接 attach 到它，往它自己的 document 里注入 <style>。

用法：
    python scripts/_space.py probe          # 列出 iframe 文档结构与白底来源
    python scripts/_space.py inject         # 注入玻璃样式
    python scripts/_space.py reset          # 移除注入
"""
import importlib.util
import json
import os
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
spec = importlib.util.spec_from_file_location("inj", os.path.join(SKILL, "scripts", "inject.py"))
inj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inj)

PORT = 9348
for i, a in enumerate(sys.argv):
    if a == "--port":
        PORT = int(sys.argv[i + 1])

PROBE = r"""(() => {
  const probe = document.createElement('canvas'); probe.width = probe.height = 1;
  const ctx = probe.getContext('2d');
  const alphaOf = (c) => { if (!c || c === 'transparent') return 0;
    ctx.clearRect(0,0,1,1); ctx.fillStyle='#000'; ctx.fillStyle=c; ctx.fillRect(0,0,1,1);
    return ctx.getImageData(0,0,1,1).data[3]/255; };
  const found = new Map(); let samples = 0;
  for (let y = 20; y < innerHeight - 4; y += 14) {
    for (let x = 10; x < innerWidth - 4; x += 14) {
      samples++;
      let el = document.elementFromPoint(x, y), d = 0;
      while (el && d < 14) {
        const cs = getComputedStyle(el);
        const a = alphaOf(cs.backgroundColor);
        const hasImg = cs.backgroundImage !== 'none';
        if (a >= 0.5 || hasImg) {
          const key = el.tagName.toLowerCase() + '|' + String(el.className).slice(0,70);
          if (!found.has(key)) found.set(key, { key, bg: cs.backgroundColor,
            bgi: hasImg ? cs.backgroundImage.slice(0,46) : null,
            rect: (r=>[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)])(el.getBoundingClientRect()), n: 0 });
          found.get(key).n++;
          break;
        }
        el = el.parentElement; d++;
      }
    }
  }
  return JSON.stringify({
    url: location.href, viewport: [innerWidth, innerHeight],
    htmlBg: getComputedStyle(document.documentElement).backgroundColor,
    bodyBg: getComputedStyle(document.body).backgroundColor,
    bodyCls: String(document.body.className).slice(0, 80),
    samples,
    top: [...found.values()].sort((p,q)=>q.n-p.n).slice(0, 18)
      .map(h => ({ cov: +(h.n/samples*100).toFixed(1), bg: h.bgi||h.bg, el: h.key, rect: h.rect }))
  }, null, 1);
})()"""


def ws_url():
    for t in inj.renderer_targets(PORT, all_types=True):
        if t.get("type") == "iframe":
            return t["webSocketDebuggerUrl"]
    return None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    ws = ws_url()
    if not ws:
        print("没找到 iframe target（资料库面板可能没打开）")
        return 1
    print("iframe target:", ws[:80])
    print()

    if mode == "probe":
        data = json.loads(inj.cdp_evaluate(ws, PROBE, timeout=45))
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    if mode == "inject":
        css_path = os.path.join(SKILL, "assets", "space-glass.css")
        css = open(css_path, encoding="utf-8").read()
        expr = """(() => {
          // 给 iframe 自己的 <html> 打标记，所有规则都以 html.wbas-space 限定
          document.documentElement.classList.add('wbas-space');
          let s = document.getElementById('wbas-space-glass');
          if (!s) { s = document.createElement('style'); s.id = 'wbas-space-glass';
                    (document.head || document.documentElement).appendChild(s); }
          s.textContent = %s;
          return 'ok: css=' + s.textContent.length + ' html=' + document.documentElement.className;
        })()""" % json.dumps(css)
        print(inj.cdp_evaluate(ws, expr, timeout=45))
        return 0

    if mode == "reset":
        expr = """(() => { const s = document.getElementById('wbas-space-glass');
          if (s) { s.remove(); document.documentElement.classList.remove('wbas-space'); return 'removed'; }
          return 'not-found'; })()"""
        print(inj.cdp_evaluate(ws, expr, timeout=45))
        return 0

    print("未知模式:", mode)
    return 1


if __name__ == "__main__":
    sys.exit(main())
