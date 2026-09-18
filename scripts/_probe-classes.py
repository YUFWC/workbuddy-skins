# -*- coding: utf-8 -*-
"""把一个页面族（如 my-files-*）的所有类名从 app.asar 的 CSS 里抽出来，
再在**当前活着的渲染进程里用假元素逐个探测计算样式** ——
不需要导航到那个页面，就能知道哪些类还是白底。

用法：python scripts/_probe-classes.py my-files [前缀2 ...]
"""
import importlib.util
import json
import os
import re
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))

spec_cmp = importlib.util.spec_from_file_location("cmp", os.path.join(SKILL, "scripts", "_compare-installs.py"))
cmp = importlib.util.module_from_spec(spec_cmp)
spec_cmp.loader.exec_module(cmp)

spec_inj = importlib.util.spec_from_file_location("inj", os.path.join(SKILL, "scripts", "inject.py"))
inj = importlib.util.module_from_spec(spec_inj)
spec_inj.loader.exec_module(inj)

PORT = 9347
_argv = list(sys.argv[1:])
if '--port' in _argv:
    _i = _argv.index('--port'); PORT = int(_argv[_i + 1]); del _argv[_i:_i + 2]


def collect_classes(build, prefixes):
    names = set()
    pattern = re.compile(r"\.((?:" + "|".join(re.escape(p) for p in prefixes) + r")[A-Za-z0-9_-]*)")
    for path in build.entries:
        if not path.endswith(".css"):
            continue
        if build.entries[path][1] > 8 * 1024 * 1024:
            continue
        text = build.read(path).decode("utf-8", "replace")
        names.update(pattern.findall(text))
    return sorted(names)


def main():
    prefixes = _argv or ["my-files"]
    build = cmp.Build("OS", cmp.OS)
    classes = collect_classes(build, prefixes)
    print("从 CSS 里抽出 %d 个类名（前缀 %s）" % (len(classes), prefixes))

    ws = inj.renderer_targets(PORT)[0]["webSocketDebuggerUrl"]
    expression = """(() => {
      const probe = (cls) => {
        const d = document.createElement('div');
        d.className = cls;
        document.body.appendChild(d);
        const cs = getComputedStyle(d);
        const out = { bg: cs.backgroundColor, bgi: cs.backgroundImage === 'none' ? null : cs.backgroundImage.slice(0, 36),
                      bf: cs.backdropFilter === 'none' ? null : cs.backdropFilter };
        d.remove();
        return out;
      };
      const out = {};
      for (const c of %s) out[c] = probe(c);
      return JSON.stringify(out);
    })()""" % json.dumps(classes)
    data = json.loads(inj.cdp_evaluate(ws, expression, timeout=40))

    def alpha(value):
        m = re.search(r"/\s*([\d.]+)\)", value)
        if m:
            return float(m.group(1))
        m = re.search(r"rgba?\(([^)]*)\)", value)
        if m:
            parts = [p.strip() for p in m.group(1).split(",")]
            return float(parts[3]) if len(parts) > 3 else 1.0
        return 0.0

    opaque, glass, clean = [], [], []
    for name, info in sorted(data.items()):
        a = alpha(info["bg"])
        if info["bgi"] or (a >= 0.5 and not info["bf"]):
            opaque.append((name, info))
        elif info["bf"]:
            glass.append(name)
        else:
            clean.append(name)

    print()
    print("=== ⚠️ 还是白的 / 带不透明渐变（需要处理）%d 个 ===" % len(opaque))
    for name, info in opaque:
        print("   %-46s bg=%s%s" % (name, info["bg"], ("  bgi=" + info["bgi"]) if info["bgi"] else ""))
    print()
    print("=== 已经是我们的玻璃 %d 个 ===" % len(glass))
    for name in glass:
        print("   ", name)
    print()
    print("=== 本来就透明 %d 个 ===" % len(clean))
    return 0


if __name__ == "__main__":
    sys.exit(main())
