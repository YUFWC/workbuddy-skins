# -*- coding: utf-8 -*-
"""探测「乐享知识库」页面（连接授权页）的实心元素。

页面特征：中间「连接乐享作为 AI 知识库」+ 权限说明卡 + 黑色授权按钮。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402

PROBE = r"""
(function () {
  var out = { meta: {}, solids: [], lexiang: [], root: {} };
  out.meta.vw = innerWidth; out.meta.vh = innerHeight;

  // 1) 找含「乐享」「知识库」「授权」文字的关键容器
  var all = document.querySelectorAll("*");
  var seen = {};
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var t = (el.textContent || "").trim();
    if (t.length > 60) continue;                 // 只要文本短的（叶子附近）
    if (t.indexOf("乐享") < 0 && t.indexOf("知识库") < 0
        && t.indexOf("授权") < 0 && t.indexOf("权限") < 0) continue;
    var r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) continue;
    var cs = getComputedStyle(el);
    var cls = el.className && el.className.toString ? el.className.toString() : el.tagName;
    var key = cls.slice(0, 70) + "|" + Math.round(r.width) + "x" + Math.round(r.height);
    if (seen[key]) continue;
    seen[key] = 1;
    out.lexiang.push({
      tag: el.tagName, cls: cls.slice(0, 100),
      bg: cs.backgroundColor, bgi: cs.backgroundImage.slice(0, 40),
      radius: cs.borderRadius,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      text: t.slice(0, 40)
    });
  }

  // 2) 全页实心浅色容器
  var seen2 = {};
  for (var k = 0; k < all.length; k++) {
    var e2 = all[k];
    var cs2 = getComputedStyle(e2);
    var bg = cs2.backgroundColor;
    var m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
    if (!m) continue;
    var R = +m[1], G = +m[2], B = +m[3], A = m[4] === undefined ? 1 : +m[4];
    if (A < 0.9) continue;
    if (R < 235 || G < 235 || B < 235) {
      // 也收深色大块（黑色授权按钮）
      if (!(R < 60 && G < 60 && B < 60)) continue;
    }
    var r2 = e2.getBoundingClientRect();
    if (r2.width < 60 || r2.height < 16) continue;
    if (r2.width * r2.height < 2000) continue;
    var name = (e2.className && e2.className.toString ? e2.className.toString() : "");
    if (!name) name = e2.tagName + "#" + (e2.id || "-");
    var key2 = name.slice(0, 70) + "|" + Math.round(r2.width) + "x" + Math.round(r2.height);
    if (seen2[key2]) continue;
    seen2[key2] = 1;
    out.solids.push({
      tag: e2.tagName, cls: name.slice(0, 110), bg: bg,
      rect: [Math.round(r2.x), Math.round(r2.y), Math.round(r2.width), Math.round(r2.height)],
      text: (e2.textContent || "").trim().slice(0, 26)
    });
  }
  out.solids.sort(function (a, b) { return b.rect[2]*b.rect[3] - a.rect[2]*a.rect[3]; });

  // 3) 主内容区骨架：从 #root 往下找最大那块
  var host = document.querySelector('[class*="teams-main-content"]')
          || document.querySelector('[class*="main-content"]')
          || document.querySelector("#root");
  out.root.cls = host ? (host.className || "").toString().slice(0, 100) : null;
  out.root.kids = [];
  if (host) {
    for (var j = 0; j < host.children.length; j++) {
      var c = host.children[j];
      var cs3 = getComputedStyle(c);
      var r3 = c.getBoundingClientRect();
      out.root.kids.push({
        tag: c.tagName,
        cls: (c.className && c.className.toString ? c.className.toString() : "").slice(0, 110),
        bg: cs3.backgroundColor,
        rect: [Math.round(r3.x), Math.round(r3.y), Math.round(r3.width), Math.round(r3.height)],
        text: (c.textContent || "").trim().slice(0, 30)
      });
    }
  }
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
    ws = page[0]["webSocketDebuggerUrl"]
    d = json.loads(inject.cdp_evaluate(ws, PROBE, timeout=40.0))

    print("viewport %sx%s" % (d["meta"]["vw"], d["meta"]["vh"]))

    print("\n=== 含「乐享/知识库/授权/权限」的元素 ===")
    for h in d["lexiang"][:22]:
        print("  <%s> bg=%-22s radius=%-7s %s" % (h["tag"], h["bg"], h["radius"], h["rect"]))
        print("       cls=%s" % h["cls"])
        print("       bgi=%s text=%r" % (h["bgi"], h["text"]))

    print("\n=== 全页实心容器（浅色 + 深色）top 22 ===")
    for s in d["solids"][:22]:
        print("  <%s> bg=%-22s %s" % (s["tag"], s["bg"], s["rect"]))
        print("       cls=%s" % s["cls"])
        print("       text=%r" % s["text"])

    print("\n=== 主内容区骨架 ===")
    print("  host cls=%s" % d["root"]["cls"])
    for k in d["root"]["kids"]:
        print("    <%s> bg=%-22s %s" % (k["tag"], k["bg"], k["rect"]))
        print("         cls=%s text=%r" % (k["cls"], k["text"]))


if __name__ == "__main__":
    main()
