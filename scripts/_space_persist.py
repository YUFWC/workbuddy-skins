# -*- coding: utf-8 -*-
"""验证 iframe 注入的持久性：注入 → 强制 reload → 看还在不在。

这一步是为了确认「守护」到底需不需要、以及需要多快。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import inject as inj  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9348
CHECK = "(() => JSON.stringify({" \
        "  cls: document.documentElement.className," \
        "  style: !!document.getElementById('wbas-space-glass')," \
        "  bodyBg: getComputedStyle(document.body).backgroundColor," \
        "  age: Math.round(performance.now())," \
        "  navType: (performance.getEntriesByType('navigation')[0]||{}).type" \
        "}))()"


def probe():
    targets = inj.space_iframe_targets(PORT)
    if not targets:
        return None
    try:
        return json.loads(inj.cdp_evaluate(targets[0]["webSocketDebuggerUrl"], CHECK,
                                           timeout=10.0))
    except Exception as error:
        return {"err": str(error)[:80]}


print("--- 注入前 ---")
print(json.dumps(probe(), ensure_ascii=False))

inj.apply_space_css(PORT, log=lambda m: print("   ", m.strip()))
print("--- 注入后 ---")
print(json.dumps(probe(), ensure_ascii=False))

# 强制 reload iframe（用 location.reload，这在 OOPIF 自己是允许的）
targets = inj.space_iframe_targets(PORT)
if targets:
    try:
        inj.cdp_evaluate(targets[0]["webSocketDebuggerUrl"], "location.reload()", timeout=5.0)
        print("--- 已发出 reload ---")
    except Exception as error:
        print("reload 调用异常（正常，文档正在销毁）：", str(error)[:80])

for wait in (1, 3, 6, 10):
    time.sleep(wait if wait == 1 else wait - 1)
    state = probe()
    if state is None:
        print("[%2ds] iframe target 还没回来（正在导航）" % wait)
    else:
        print("[%2ds] %s" % (wait, json.dumps(state, ensure_ascii=False)))
