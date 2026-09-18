# -*- coding: utf-8 -*-
"""把用户红框相关元素的计算背景一次性列全 —— 包括采样法没返回的 thead 那层。

为什么要单独查 thead：elementFromPoint 只返回"最上层有背景"的元素，
表头行的单元格 `td` 是透明的，真正上色的 `thead.wb-feed-virtual-thead-pc`
在链上更高位置，采样点到不了。用 querySelector 精确取。
"""
import json
import sys

sys.path.insert(0, ".")
import inject as inj  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9348

SELECTORS = [
    "workspace-tree-section-header-pc",
    "workspace-tree-space-node-pc",
    "workspace-tree-space-wrapper-pc",
    "workspace-tree-list-pc",
    "workspace-tree-wrapper-pc",
    "wb-feed-virtual-thead-pc",
    "doc-feed-table-head-pc",
    "wb-feed-thead-pc",
    "doc-feed-th-pc",
    "doc-feed-table-pc",
    "memory-nav-item-pc",
    "memory-nav-item-active-pc",
    "knowledge-sidebar-pc",
    "memory-sidebar-wrapper-pc",
]

EXPR = """(() => {
  const classes = %s;
  const out = {};
  for (const cls of classes) {
    const hits = [...document.querySelectorAll('.' + cls)];
    out[cls] = hits.slice(0, 4).map((el) => {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {
        tag: el.tagName,
        bg: cs.backgroundColor,
        bgi: cs.backgroundImage === 'none' ? null : cs.backgroundImage.slice(0, 60),
        bf: cs.backdropFilter === 'none' ? null : cs.backdropFilter,
        w: Math.round(r.width), h: Math.round(r.height),
        x: Math.round(r.x), y: Math.round(r.y),
        text: (el.textContent || '').trim().slice(0, 22),
      };
    });
  }
  return JSON.stringify(out, null, 1);
})()""" % json.dumps(SELECTORS)


def main():
    targets = inj.space_iframe_targets(PORT)
    if not targets:
        print("资料库没打开")
        return 1
    data = json.loads(inj.cdp_evaluate(targets[0]["webSocketDebuggerUrl"], EXPR, timeout=25.0))
    for cls, hits in data.items():
        if not hits:
            print("%-34s （页面里没有）" % cls)
            continue
        for i, hit in enumerate(hits):
            print("%-34s [%d] %-8s bg=%-26s %dx%d @%d,%d  bf=%s  %r"
                  % (cls, i, hit["tag"], hit["bg"], hit["w"], hit["h"],
                     hit["x"], hit["y"], hit["bf"], hit["text"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
