# -*- coding: utf-8 -*-
"""乐享知识库「文件列表」视图：tencent-lexiang-catalog__* 类名全景。

用户红框 = .tencent-lexiang-catalog__header（946×36 rgb(255,255,255)），
要求 **50% 透明**（不是全透明，和之前几轮不同）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402

PROBE = r"""
(function () {
  var out = { classes: {}, chain: [] };

  // 1) catalog__header 的祖先链
  var node = document.querySelector(".tencent-lexiang-catalog__header");
  var cur = node;
  for (var d = 0; d < 6 && cur; d++) {
    var cs = getComputedStyle(cur);
    var r = cur.getBoundingClientRect();
    out.chain.push({
      d: d, tag: cur.tagName,
      cls: (cur.className && cur.className.toString ? cur.className.toString() : "").slice(0, 110),
      bg: cs.backgroundColor, bgi: cs.backgroundImage.slice(0, 36),
      border: cs.borderBottomWidth + " " + cs.borderBottomColor,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]
    });
    cur = cur.parentElement;
  }

  // 2) 所有 tencent-lexiang-catalog* / __files* 类名
  var all = document.querySelectorAll('[class*="tencent-lexiang-catalog"],[class*="tencent-lexiang-panel__files"],[class*="tencent-lexiang-file"]');
  for (var i = 0; i < all.length; i++) {
    var e = all[i];
    var names = (e.className && e.className.toString ? e.className.toString() : "").split(/\s+/);
    for (var n = 0; n < names.length; n++) {
      var nm = names[n];
      if (nm.indexOf("catalog") < 0 && nm.indexOf("__files") < 0
          && nm.indexOf("tencent-lexiang-file") < 0) continue;
      var cs2 = getComputedStyle(e);
      var r2 = e.getBoundingClientRect();
      if (!out.classes[nm]) out.classes[nm] = { count: 0, bg: {}, sample: null, bf: null };
      out.classes[nm].count++;
      var b = cs2.backgroundColor;
      out.classes[nm].bg[b] = (out.classes[nm].bg[b] || 0) + 1;
      if (!out.classes[nm].sample && r2.width > 20) {
        out.classes[nm].sample = {
          rect: [Math.round(r2.x), Math.round(r2.y), Math.round(r2.width), Math.round(r2.height)],
          bgi: cs2.backgroundImage.slice(0, 36),
          bf: cs2.backdropFilter,
          radius: cs2.borderRadius,
          border: cs2.borderBottomWidth + " " + cs2.borderBottomColor,
          text: (e.textContent || "").trim().slice(0, 24)
        };
      }
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

    print("=== catalog__header 祖先链 ===")
    for c in d["chain"]:
        print("  [%d] <%s> bg=%-22s border=%s %s"
              % (c["d"], c["tag"], c["bg"], c["border"], c["rect"]))
        print("       cls=%s" % c["cls"])

    print("\n=== catalog / files 类名全景 ===")
    items = sorted(d["classes"].items(), key=lambda kv: -kv[1]["count"])
    for name, v in items:
        nontrans = [b for b in v["bg"] if b not in ("rgba(0, 0, 0, 0)", "transparent")]
        flag = "❕" if nontrans else "  "
        bgs = ", ".join("%s×%d" % (b, n) for b, n in
                        sorted(v["bg"].items(), key=lambda x: -x[1])[:2])
        print("  %s %-50s ×%-3d bg: %s" % (flag, name[:50], v["count"], bgs))
        s = v["sample"]
        if s:
            print("       %s radius=%s bf=%s border=%s bgi=%s text=%r" % (
                s["rect"], s["radius"], s["bf"][:20], s["border"], s["bgi"], s["text"]))


if __name__ == "__main__":
    main()
