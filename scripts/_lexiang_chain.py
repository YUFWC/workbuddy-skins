# -*- coding: utf-8 -*-
"""乐享页面：祖先链 + lexiang-* 类名全景，区分外壳与内容卡。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402

PROBE = r"""
(function () {
  var out = { chain: [], classes: {} };

  var node = document.querySelector(".tencent-lexiang-panel");
  var cur = node;
  for (var d = 0; d < 6 && cur; d++) {
    var cs = getComputedStyle(cur);
    var r = cur.getBoundingClientRect();
    out.chain.push({
      d: d, tag: cur.tagName,
      cls: (cur.className && cur.className.toString ? cur.className.toString() : "").slice(0, 110),
      bg: cs.backgroundColor, bgi: cs.backgroundImage.slice(0, 40),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]
    });
    cur = cur.parentElement;
  }

  var all = document.querySelectorAll('[class*="lexiang"]');
  for (var i = 0; i < all.length; i++) {
    var e = all[i];
    var names = (e.className && e.className.toString ? e.className.toString() : "").split(/\s+/);
    for (var n = 0; n < names.length; n++) {
      var nm = names[n];
      if (nm.indexOf("lexiang") < 0) continue;
      var cs2 = getComputedStyle(e);
      var r2 = e.getBoundingClientRect();
      if (!out.classes[nm]) out.classes[nm] = { count: 0, bg: {}, sample: null };
      out.classes[nm].count++;
      var b = cs2.backgroundColor;
      out.classes[nm].bg[b] = (out.classes[nm].bg[b] || 0) + 1;
      if (!out.classes[nm].sample && r2.width > 40) {
        out.classes[nm].sample = {
          rect: [Math.round(r2.x), Math.round(r2.y), Math.round(r2.width), Math.round(r2.height)],
          bgi: cs2.backgroundImage.slice(0, 36),
          radius: cs2.borderRadius,
          text: (e.textContent || "").trim().slice(0, 26)
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

    print("=== tencent-lexiang-panel 祖先链 ===")
    for c in d["chain"]:
        print("  [%d] <%s> bg=%-22s %s" % (c["d"], c["tag"], c["bg"], c["rect"]))
        print("       cls=%s" % c["cls"])

    print("\n=== lexiang-* 类名全景 ===")
    items = sorted(d["classes"].items(), key=lambda kv: -kv[1]["count"])
    for name, v in items:
        nontrans = [b for b in v["bg"] if b not in ("rgba(0, 0, 0, 0)", "transparent")]
        flag = "❕" if nontrans else "  "
        bgs = ", ".join("%s×%d" % (b, n) for b, n in
                        sorted(v["bg"].items(), key=lambda x: -x[1])[:2])
        print("  %s %-46s ×%-3d bg: %s" % (flag, name, v["count"], bgs))
        if v["sample"] and nontrans:
            print("       sample %s radius=%s bgi=%s text=%r" % (
                v["sample"]["rect"], v["sample"]["radius"],
                v["sample"]["bgi"], v["sample"]["text"]))


if __name__ == "__main__":
    main()
