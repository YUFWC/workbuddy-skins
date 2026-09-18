# -*- coding: utf-8 -*-
"""细化探测：discover-panel-page 的骨架 + dc-* 命名空间全景。

目的：把「外壳（该透明）」和「内容卡片（该玻璃）」分开。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402

PROBE = r"""
(function () {
  var out = {};

  // 1) discover-panel-page 的直系骨架（往上 3 层 + 往下 2 层）
  var root = document.querySelector(".discover-panel-page");
  function info(el, depth) {
    var cs = getComputedStyle(el);
    var r = el.getBoundingClientRect();
    var cls = el.className && el.className.toString ? el.className.toString() : "";
    return {
      d: depth, tag: el.tagName, cls: cls.slice(0, 90),
      bg: cs.backgroundColor, bgi: cs.backgroundImage.slice(0, 40),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      kids: el.children.length,
      text: (el.textContent || "").trim().slice(0, 30)
    };
  }
  out.chain = [];
  if (root) {
    var cur = root;
    for (var up = 0; up < 4 && cur; up++) { out.chain.push(info(cur, -up)); cur = cur.parentElement; }
    out.kids = [];
    for (var i = 0; i < root.children.length; i++) {
      out.kids.push(info(root.children[i], 1));
      for (var j = 0; j < root.children[i].children.length; j++) {
        out.kids.push(info(root.children[i].children[j], 2));
      }
    }
  }

  // 2) 收集所有以 dc- 开头的类名，按出现次数 / 背景色归类
  var map = {};
  var all = document.querySelectorAll('[class*="dc-"]');
  for (var k = 0; k < all.length; k++) {
    var e = all[k];
    var names = (e.className && e.className.toString ? e.className.toString() : "").split(/\s+/);
    for (var n = 0; n < names.length; n++) {
      var nm = names[n];
      if (nm.indexOf("dc-") !== 0) continue;
      var cs2 = getComputedStyle(e);
      var r2 = e.getBoundingClientRect();
      if (!map[nm]) map[nm] = { count: 0, bg: {}, sample: null };
      map[nm].count++;
      var b = cs2.backgroundColor;
      map[nm].bg[b] = (map[nm].bg[b] || 0) + 1;
      if (!map[nm].sample && r2.width > 30) {
        map[nm].sample = {
          rect: [Math.round(r2.x), Math.round(r2.y), Math.round(r2.width), Math.round(r2.height)],
          bgi: cs2.backgroundImage.slice(0, 36),
          text: (e.textContent || "").trim().slice(0, 24)
        };
      }
    }
  }
  out.dcClasses = map;
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
    data = json.loads(inject.cdp_evaluate(ws, PROBE, timeout=40.0))

    print("=== discover-panel-page 祖先链（由内向外）===")
    for c in data["chain"]:
        print("  [%+d] <%s> bg=%-22s %s kind=%d" % (
            c["d"], c["tag"], c["bg"], c["rect"], c["kids"]))
        print("        cls=%s" % c["cls"])

    print("\n=== discover-panel-page 子孙骨架 ===")
    for c in data["kids"]:
        pad = "  " * c["d"]
        print("  %s[%d] <%s> bg=%-22s %s" % (pad, c["d"], c["tag"], c["bg"], c["rect"]))
        print("  %s      cls=%s  text=%r" % (pad, c["cls"][:80], c["text"]))

    print("\n=== dc-* 类名全景（按非透明优先）===")
    items = list(data["dcClasses"].items())
    items.sort(key=lambda kv: -kv[1]["count"])
    for name, v in items:
        bgs = ", ".join("%s×%d" % (b, n) for b, n in
                        sorted(v["bg"].items(), key=lambda x: -x[1])[:3])
        flag = "  "
        if any(b not in ("rgba(0, 0, 0, 0)", "transparent") for b in v["bg"]):
            flag = "❕"
        print("  %s %-42s ×%-3d bg: %s" % (flag, name, v["count"], bgs))
        if v["sample"]:
            print("       sample %s bgi=%s text=%r" % (
                v["sample"]["rect"], v["sample"]["bgi"], v["sample"]["text"]))


if __name__ == "__main__":
    main()
