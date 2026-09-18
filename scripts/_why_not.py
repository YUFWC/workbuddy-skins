# -*- coding: utf-8 -*-
"""诊断：为什么 transparent 没盖住 rgba(255,255,255,.34)。

对每个目标元素打印：
  - inline style（行内样式优先级高于一切非 !important 规则）
  - 命中的全部 CSS 规则（按优先级倒序）+ 各自来源
  - 我们的 <style id=wbas-space-glass> 是否存在、里面有没有这条选择器
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402

TARGETS = [
    "doc-feed-th-pc",
    "workspace-tree-space-node-pc",
    "memory-nav-item-pc",
    "workspace-tree-section-header-pc",
]

PROBE = r"""
(function () {
  var classes = %s;
  var out = [];
  var styleEl = document.getElementById("wbas-space-glass");
  out.push({
    kind: "meta",
    htmlClass: document.documentElement.className,
    styleExists: !!styleEl,
    styleLen: styleEl ? styleEl.textContent.length : 0,
    styleHead: styleEl ? styleEl.textContent.slice(0, 160) : null,
    // 我们的表里到底有没有这条选择器
    hasSel: styleEl ? {
      sec:   styleEl.textContent.indexOf(".workspace-tree-section-header-pc") >= 0,
      node:  styleEl.textContent.indexOf(".workspace-tree-space-node-pc") >= 0,
      th:    styleEl.textContent.indexOf(".doc-feed-th-pc") >= 0,
      nav:   styleEl.textContent.indexOf(".memory-nav-item-pc") >= 0
    } : null
  });
  classes.forEach(function (cls) {
    var els = document.querySelectorAll("." + cls);
    Array.prototype.slice.call(els, 0, 2).forEach(function (el, i) {
      var cs = getComputedStyle(el);
      var rules = [];
      for (var s = 0; s < document.styleSheets.length; s++) {
        var sheet = document.styleSheets[s];
        var list;
        try { list = sheet.cssRules; } catch (e) { continue; }
        if (!list) continue;
        for (var r = 0; r < list.length; r++) {
          var rule = list[r];
          if (!rule.selectorText) continue;
          var hit = false;
          try { hit = el.matches(rule.selectorText); } catch (e) { hit = false; }
          if (!hit) continue;
          var bg = rule.style && rule.style.background;
          var bgc = rule.style && rule.style.backgroundColor;
          if (!bg && !bgc) continue;
          rules.push({
            sel: rule.selectorText,
            bg: bg || "",
            bgc: bgc || "",
            imp: (rule.style.getPropertyPriority("background") === "important") ||
                 (rule.style.getPropertyPriority("background-color") === "important"),
            owner: el.ownerDocument === document ? "same" : "?",
            sheet: (sheet.href ? sheet.href : "(inline <style>)")
          });
        }
      }
      out.push({
        kind: "el", cls: cls, idx: i,
        tag: el.tagName,
        inline: el.getAttribute("style"),
        computed: cs.backgroundColor,
        computedBg: cs.backgroundImage,
        rules: rules
      });
    });
  });
  return JSON.stringify(out, null, 1);
})()
"""


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9348
    expr = PROBE % json.dumps(TARGETS)
    targets = inject.space_iframe_targets(port)
    print("找到 %d 个资料库 iframe target" % len(targets))
    for t in targets:
        raw = inject.cdp_evaluate(t["webSocketDebuggerUrl"], expr, timeout=30.0)
        data = json.loads(raw)
        for item in data:
            if item["kind"] == "meta":
                print("\n===== META =====")
                print("  html.class = %r" % item["htmlClass"])
                print("  我们的 <style> 存在: %s  长度 %s" % (item["styleExists"], item["styleLen"]))
                print("  选择器包含: %s" % item["hasSel"])
            else:
                print("\n----- .%s  [%d] <%s> -----" % (item["cls"], item["idx"], item["tag"]))
                print("  inline style : %r" % item["inline"])
                print("  computed bg  : %s" % item["computed"])
                print("  命中含 bg 的规则 %d 条（按文档顺序）:" % len(item["rules"]))
                for r in item["rules"]:
                    print("    - %s" % r["sel"][:110])
                    print("        bg=%r imp=%s" % (r["bg"], r["imp"]))


if __name__ == "__main__":
    main()
