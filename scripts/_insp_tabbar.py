# -*- coding: utf-8 -*-
"""精确扫描分类 tab 栏（y≈142~192）上方的每一层容器，找出那条浅灰横带的来源。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402

PROBE = r"""
(function () {
  var out = [];

  // 在 tab 栏中段取几个点做逐层穿透（elementFromPoint 只给最上层，所以用 elementsFromPoint）
  var xs = [300, 600, 900, 1300];
  var ys = [150, 166, 180];
  var seenKey = {};
  for (var a = 0; a < xs.length; a++) {
    for (var b = 0; b < ys.length; b++) {
      var stack = document.elementsFromPoint(xs[a], ys[b]);
      for (var c = 0; c < stack.length; c++) {
        var el = stack[c];
        var cs = getComputedStyle(el);
        var bg = cs.backgroundColor;
        var bgi = cs.backgroundImage;
        if (bg === "rgba(0, 0, 0, 0)" && bgi === "none") continue;
        var r = el.getBoundingClientRect();
        if (r.height > 400) continue;      // 排除整页大壳
        var cls = el.className && el.className.toString ? el.className.toString() : el.tagName;
        var key = cls.slice(0, 60) + "|" + bg;
        if (seenKey[key]) continue;
        seenKey[key] = 1;
        out.push({
          pt: [xs[a], ys[b]],
          tag: el.tagName,
          cls: cls.slice(0, 100),
          bg: bg,
          bgi: bgi.slice(0, 40),
          bf: cs.backdropFilter,
          rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
          text: (el.textContent || "").trim().slice(0, 20)
        });
      }
    }
  }

  // 另外：dc-browse-section / dc-category-tabs 的祖先链逐层报
  var chain = [];
  var node = document.querySelector(".dc-category-tabs")
          || document.querySelector('[class*="dc-category-tabs"]')
          || document.querySelector('[class*="scrollListWrapper"]');
  var cur = node;
  for (var d = 0; d < 7 && cur; d++) {
    var cs2 = getComputedStyle(cur);
    var r2 = cur.getBoundingClientRect();
    chain.push({
      d: d, tag: cur.tagName,
      cls: (cur.className && cur.className.toString ? cur.className.toString() : "").slice(0, 100),
      bg: cs2.backgroundColor, bgi: cs2.backgroundImage.slice(0, 44),
      shadow: cs2.boxShadow.slice(0, 40),
      rect: [Math.round(r2.x), Math.round(r2.y), Math.round(r2.width), Math.round(r2.height)]
    });
    cur = cur.parentElement;
  }
  return JSON.stringify({ hits: out, chain: chain });
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

    print("=== tab 栏区域有背景的层（elementsFromPoint 穿透）===")
    for h in data["hits"]:
        print("  <%s> bg=%-24s bf=%s" % (h["tag"], h["bg"], h["bf"][:26]))
        print("       cls=%s" % h["cls"])
        print("       bgi=%s rect=%s text=%r" % (h["bgi"], h["rect"], h["text"]))

    print("\n=== dc-category-tabs 祖先链 ===")
    for c in data["chain"]:
        print("  [%d] <%s> bg=%-24s" % (c["d"], c["tag"], c["bg"]))
        print("       cls=%s" % c["cls"])
        print("       bgi=%s shadow=%s rect=%s" % (c["bgi"], c["shadow"], c["rect"]))


if __name__ == "__main__":
    main()
