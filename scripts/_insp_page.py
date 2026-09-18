# -*- coding: utf-8 -*-
"""探测「灵感」页（主文档内，非 iframe）里哪些元素还是实心白。

策略：
  1) 先在整棵 DOM 里找文本含「灵感」且像标题的元素
  2) 再对候选容器做 getComputedStyle，列出非透明的
  3) 输出每个元素的类名 / 背景 / 尺寸 / 位置，便于写 CSS
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402

PROBE = r"""
(function () {
  var out = { hits: [], solids: [], tabLike: [], meta: {} };
  out.meta.dpr = window.devicePixelRatio;
  out.meta.vw = innerWidth; out.meta.vh = innerHeight;

  // --- 1) 找「灵感」标题 ---
  var all = document.querySelectorAll("*");
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if (el.children.length > 2) continue;          // 只要叶子附近的
    var t = (el.textContent || "").trim();
    if (t !== "灵感" && t.indexOf("灵感页面") < 0) continue;
    var r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 8) continue;
    out.hits.push({
      tag: el.tagName,
      cls: el.className && el.className.toString ? el.className.toString() : "",
      id: el.id || "",
      text: t.slice(0, 30),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]
    });
  }

  // --- 2) 扫 tab 栏附近（y 在 100~150 之间的可点元素）---
  var cands = document.querySelectorAll("button, a, div[class*=tab], div[class*=Tab]");
  for (var j = 0; j < cands.length; j++) {
    var e2 = cands[j];
    var r2 = e2.getBoundingClientRect();
    if (r2.y < 95 || r2.y > 155) continue;
    if (r2.width < 20 || r2.height < 16) continue;
    var cs2 = getComputedStyle(e2);
    out.tabLike.push({
      tag: e2.tagName,
      cls: e2.className && e2.className.toString ? e2.className.toString() : "",
      text: (e2.textContent || "").trim().slice(0, 14),
      bg: cs2.backgroundColor,
      radius: cs2.borderRadius,
      rect: [Math.round(r2.x), Math.round(r2.y), Math.round(r2.width), Math.round(r2.height)]
    });
  }

  // --- 3) 全页扫「实心浅色」容器（排除我们自己的玻璃规则已命中的）---
  var seen = {};
  for (var k = 0; k < all.length; k++) {
    var el3 = all[k];
    var cs3 = getComputedStyle(el3);
    var bg = cs3.backgroundColor;
    if (!bg) continue;
    var m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
    if (!m) continue;
    var R = +m[1], G = +m[2], B = +m[3], A = m[4] === undefined ? 1 : +m[4];
    if (A < 0.9) continue;                      // 半透明不算
    if (R < 235 || G < 235 || B < 235) continue; // 只要浅色/白
    var r3 = el3.getBoundingClientRect();
    if (r3.width < 60 || r3.height < 18) continue;
    if (r3.width * r3.height < 2200) continue;
    var name = (el3.className && el3.className.toString ? el3.className.toString() : "");
    if (!name) name = el3.tagName + "#" + (el3.id || "-");
    var key = name + "|" + Math.round(r3.width) + "x" + Math.round(r3.height);
    if (seen[key]) continue;
    seen[key] = 1;
    out.solids.push({
      tag: el3.tagName,
      cls: name.slice(0, 120),
      bg: bg,
      rect: [Math.round(r3.x), Math.round(r3.y), Math.round(r3.width), Math.round(r3.height)],
      text: (el3.textContent || "").trim().slice(0, 26)
    });
  }
  out.solids.sort(function (a, b) { return b.rect[2]*b.rect[3] - a.rect[2]*a.rect[3]; });
  return JSON.stringify(out);
})()
"""


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
    raw = inject.cdp_evaluate(ws, PROBE, timeout=40.0)
    data = json.loads(raw)

    print("viewport %sx%s" % (data["meta"]["vw"], data["meta"]["vh"]))
    print("\n=== 含「灵感」文字的元素 ===")
    for h in data["hits"]:
        print("  <%s> cls=%r text=%r rect=%s" % (h["tag"], h["cls"][:70], h["text"], h["rect"]))

    print("\n=== tab 栏附近可点元素 (%d) ===" % len(data["tabLike"]))
    for t in data["tabLike"]:
        print("  <%s> bg=%-24s radius=%-6s %s %r" % (
            t["tag"], t["bg"], t["radius"], t["rect"], t["text"]))
        if t["cls"]:
            print("        cls=%s" % t["cls"][:100])

    print("\n=== 全页实心浅色容器 top 24 ===")
    for s in data["solids"][:24]:
        print("  <%s> bg=%-22s %s" % (s["tag"], s["bg"], s["rect"]))
        print("        cls=%s" % s["cls"][:110])
        print("        text=%r" % s["text"])


if __name__ == "__main__":
    main()
