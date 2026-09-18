# -*- coding: utf-8 -*-
"""再验一次 OOPIF 上的持久化注入通道。

上一轮结论是「Page.addScriptToEvaluateOnNewDocument 被接受但永不触发」。
这次把所有可能的通道都试一遍，并且**在 reload 之后**认真读结果：

  A) Page.addScriptToEvaluateOnNewDocument   （page 级的"新文档自动执行"）
  B) Page.enable + A 组合
  C) Runtime.addBinding                     （绑定到全局，reload 后是否还在）
  D) 直接看 target 的 type / subtype / 是否 attached

关键判据：reload 后 `document.documentElement.className` 是否带 wbas-space。
"""
import json
import sys
import time

sys.path.insert(0, ".")
import inject as inj  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9348
MARK = "wbas-space"
BOOT = "document.documentElement.classList.add('%s');" % MARK
CHECK = "JSON.stringify({cls:document.documentElement.className,age:Math.round(performance.now())})"


def iframe_ws():
    targets = inj.space_iframe_targets(PORT)
    return targets[0]["webSocketDebuggerUrl"] if targets else None


def check(ws):
    try:
        return json.loads(inj.cdp_evaluate(ws, CHECK, timeout=10.0))
    except Exception as error:
        return {"err": str(error)[:70]}


ws = iframe_ws()
if not ws:
    print("资料库 iframe 没开，先打开它。")
    sys.exit(1)

print("目标 iframe:", ws[:70], "...")
print("reload 前:", json.dumps(check(ws), ensure_ascii=False))

# 先清掉历史痕迹，保证后面看到的是"新文档"的效果
try:
    inj.cdp_evaluate(ws, "document.documentElement.classList.remove('%s')" % MARK, timeout=5)
except Exception:
    pass

print("")
print("[A] Page.addScriptToEvaluateOnNewDocument（不先 Page.enable）")
try:
    result = inj.cdp_command(ws, "Page.addScriptToEvaluateOnNewDocument",
                             {"source": BOOT}, timeout=15)
    print("    返回:", json.dumps(result, ensure_ascii=False)[:200])
except Exception as error:
    print("    异常:", str(error)[:160])

print("[B] Page.enable")
try:
    result = inj.cdp_command(ws, "Page.enable", {}, timeout=15)
    print("    返回:", json.dumps(result, ensure_ascii=False)[:200])
except Exception as error:
    print("    异常:", str(error)[:160])

print("[C] 再来一次 addScriptToEvaluateOnNewDocument（enable 之后）")
try:
    result = inj.cdp_command(ws, "Page.addScriptToEvaluateOnNewDocument",
                             {"source": BOOT}, timeout=15)
    print("    返回:", json.dumps(result, ensure_ascii=False)[:200])
except Exception as error:
    print("    异常:", str(error)[:160])

print("")
print(">>> 触发 reload")
try:
    inj.cdp_evaluate(ws, "location.reload()", timeout=5)
except Exception:
    pass

for seconds in (2, 4, 7):
    time.sleep(seconds if seconds == 2 else seconds - (2 if seconds == 4 else 4))
    fresh = iframe_ws()
    if not fresh:
        print("[%ds] target 未回" % seconds)
        continue
    print("[%ds] %s" % (seconds, json.dumps(check(fresh), ensure_ascii=False)))
