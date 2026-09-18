# -*- coding: utf-8 -*-
"""探测乐享知识库**文件列表页**的表头行（红框：「文件名称 / 更新时间」）。

用户要求：这一条 → **50% 透明**。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402

PROBE = r"""
(function () {
  var out = { hits: [], table: [], lexiang: {}, meta: {} };
  out.meta.vw = innerWidth; out.meta.vh = innerHeight;

  // 用户红框在截图里约 y=245~272、x=373~1058（视口 1096 宽）
  // 转成 1482 宽视口：SX = 1482/1096 ≈ 1.352
  var SX = 1482 / 1096, SY = 888 / 712;
  var boxX1 = Math.round(373 * SX), boxX2 = Math.round(1058 * SX);
  var boxY1 = Math.round(245 * SY), boxY2 = Math.round(272 * SY);
  out.meta.box = [boxX1, boxY1, boxX2, boxY2];

  // 1) 在红框里取几个点做穿透
  var pts = [];
  for (var x = boxX1 + 40; x < boxX2; x += 170) {
    pts.push([x, Math.round((boxY1 + boxY2) / 2)]);
  }
  var seen = {};
  for (var i = 0; i < pts.length; i++) {
    var stack = document.elementsFromPoint(pts[i][0], pts[i][1]);
    for (var j = 0; j < stack.length; j++) {
      var el = stack[j];
      var cs = getComputedStyle(el);
      var bg = cs.backgroundColor;
      var bgi = cs.backgroundImage;
      if (bg === "rgba(0, 0, 0, 0)" && bgi === "none") continue;
      var r = el.getBoundingClientRect();
      var cls = el.className && el.className.toString ? el.className.toString() : el.tagName;
      var key = cls.slice(0, 70) + "|" + bg;
      if (seen[key]) continue;
      seen[key] = 1;
      out.hits.push({
        pt: pts[i], tag: el.tagName, cls: cls.slice(0, 110),
        bg: bg, bgi: bgi.slice(0, 40), bf: cs.backdropFilter,
        radius: cs.borderRadius, border: cs.borderBottom.slice(0, 30),
        rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
        text: (el.textContent || "").trim().slice(0, 30)
      });
    }
  }

  // 2) 找含「文件名称」「更新时间」的元素
  var all = document.querySelectorAll("*");
  for (var k = 0; k < all.length; k++) {
    var e = all[k];
    if (e.children.length > 3) continue;
    var t = (e.textContent || "").trim();
    if (t !== "文件名称" && t !== "更新时间") continue;
    var r2 = e.getBoundingClientRect();
    if (r2.width < 10) continue;
    var cs2 = getComputedStyle(e);
    var cls2 = e.className && e.className.toString ? e.className.toString() : e.tagName;
    out.table.push({
      tag: e.tagName, cls: cls2.slice(0, 110),
      bg: cs2.backgroundColor,
      rect: [Math.round(r2.x), Math.round(r2.y), Math.round(r2.width), Math.round(r2.height)],
      text: t
    });
    // 往上 4 层看谁有背景
    var cur = e.parentElement;
    for (var d = 0; d < 4 && cur; d++) {
      var cs3 = getComputedStyle(cur);
      var r3 = cur.getBoundingClientRect();
      out.table.push({
        tag: "  ↑" + cur.tagName,
        cls: "  ↑" + (cur.className && cur.className.toString ? cur.className.toString() : "").slice(0, 100),
        bg: cs3.backgroundColor,
        rect: [Math.round(r3.x), Math.round(r3.y), Math.round(r3.width), Math.round(r3.height)],
        text: "  ↑" + (cur.textContent || "").trim().slice(0, 24)
      });
      cur = cur.parentElement;
    }
  }

  // 3) 收集所有 lx- / lexiang- / knowledge 相关类名的背景
  var map = {};
  var all2 = document.querySelectorAll('[class*="lexiang"],[class*="lx-"],[class*="knowledge"],[class*="knowledge-base"]');
  for (var n = 0; n < all2.length; n++) {
    var en = all2[n];
    var names = (en.className && en.className.toString ? en.className.toString() : "").split(/\s+/);
    for (var q = 0; q < names.length; q++) {
      var nm = names[q];
      if (!nm) continue;
      if (nm.indexOf("lexiang") < 0 && nm.indexOf("lx-") < 0
          && nm.indexOf("knowledge") < 0) continue;
      var cs4 = getComputedStyle(en);
      var r4 = en.getBoundingClientRect();
      if (!map[nm]) map[nm] = { count: 0, bg: {}, sample: null };
      map[nm].count++;
      var b = cs4.backgroundColor;
      map[nm].bg[b] = (map[nm].bg[b] || 0) + 1;
      if (!map[nm].sample && r4.width > 40 && b !== "rgba(0, 0, 0, 0)") {
        map[nm].sample = {
          rect: [Math.round(r4.x), Math.round(r4.y), Math.round(r4.width), Math.round(r4.height)],
          bgi: cs4.backgroundImage.slice(0, 36), radius: cs4.borderRadius,
          text: (en.textContent || "").trim().slice(0, 24)
        };
      }
    }
  }
  out.lexiang = map;
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

    print("viewport %sx%s  红框(换算后)=%s" % (d["meta"]["vw"], d["meta"]["vh"], d["meta"]["box"]))

    print("\n=== 红框内穿透（有背景的层）===")
    for h in d["hits"]:
        print("  <%s> bg=%-24s bf=%s" % (h["tag"], h["bg"], h["bf"][:24]))
        print("       cls=%s" % h["cls"])
        print("       bgi=%s radius=%s border=%s rect=%s text=%r"
              % (h["bgi"], h["radius"], h["border"], h["rect"], h["text"]))

    print("\n=== 「文件名称 / 更新时间」元素 + 祖先 ===")
    for t in d["table"]:
        print("  <%s> bg=%-24s %s text=%r" % (t["tag"], t["bg"], t["rect"], t["text"]))
        print("       cls=%s" % t["cls"])

    print("\n=== lexiang/lx/knowledge 类名（只列有非透明背景的）===")
    items = sorted(d["lexiang"].items(), key=lambda kv: -kv[1]["count"])
    for name, v in items:
        nontrans = [b for b in v["bg"] if b not in ("rgba(0, 0, 0, 0)", "transparent")]
        if not nontrans:
            continue
        bgs = ", ".join("%s×%d" % (b, n) for b, n in
                        sorted(v["bg"].items(), key=lambda x: -x[1])[:2])
        print("  ❕ %-44s ×%-3d bg: %s" % (name, v["count"], bgs))
        if v["sample"]:
            print("       sample %s radius=%s bgi=%s text=%r" % (
                v["sample"]["rect"], v["sample"]["radius"],
                v["sample"]["bgi"], v["sample"]["text"]))


if __name__ == "__main__":
    main()
