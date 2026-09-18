# -*- coding: utf-8 -*-
"""实验：主渲染进程（父文档）能否感知 iframe 的 reload？

如果能，守护就可以搬进主渲染进程（它不 reload），不需要外部常驻进程。

流程：
  1. 在主文档装 load 监听 + MutationObserver
  2. 触发 iframe reload
  3. 看主文档里的计数器有没有涨
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import inject as inj  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9348
PROBE = open(os.path.join(HERE, "_probe_parent_load.js"), encoding="utf-8").read()
READ = "JSON.stringify(window.__wbasProbe || null)"


def page_ws():
    for target in inj.renderer_targets(PORT, all_types=True):
        if target.get("type") == "page":
            return target["webSocketDebuggerUrl"]
    return None


def iframe_ws():
    targets = inj.space_iframe_targets(PORT)
    return targets[0]["webSocketDebuggerUrl"] if targets else None


pws = page_ws()
if not pws:
    print("找不到主文档 target")
    sys.exit(1)

print("[1] 在主文档安装探针")
try:
    result = inj.cdp_evaluate(pws, PROBE, timeout=20.0)
    print(result[:900] if result else "(空)")
except Exception as error:
    print("安装失败:", str(error)[:200])
    sys.exit(1)

print("")
print("[2] iframe 首次状态:", inj.cdp_evaluate(iframe_ws(), "Math.round(performance.now())", timeout=8))

print("[3] 触发 iframe reload")
try:
    inj.cdp_evaluate(iframe_ws(), "location.reload()", timeout=5)
except Exception:
    pass

for wait in (2, 4, 7, 11):
    time.sleep(1 if wait == 2 else (2 if wait == 4 else 3))
    try:
        state = inj.cdp_evaluate(pws, READ, timeout=8)
    except Exception as error:
        state = "读不到: %s" % str(error)[:60]
    print("[%2ds] 主文档探针: %s" % (wait, state))
