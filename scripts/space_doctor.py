#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资料库透明化：一键自检 + 修复 + 验证。

为什么单独做这个脚本：
  * `inject.py` 负责"注入"，但它需要重启 WorkBuddy（会打断用户）；
  * 本脚本只做**非破坏性**的事：探端口 → 检查自愈器/样式是否在 → 缺了就补；
  * 而且它会**验证自愈器真的有效**（触发一次 iframe reload 看能不能自己恢复），
    这是沙箱里唯一能可靠做完的验证。

用法（在**外部终端**里跑，不要在沙箱里跑，否则 spawn 相关能力受限）：
    python scripts/space_doctor.py                # 自检 + 修复（推荐）
    python scripts/space_doctor.py --check        # 只读自检，不改任何东西
    python scripts/space_doctor.py --verify-heal  # 额外做一次 iframe reload 自愈验证
    python scripts/space_doctor.py --target cn    # 只处理国内版（oversea=国际版）

退出码：0 一切正常 / 1 有问题但已修 / 2 有问题且修不了 / 3 找不到目标
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import inject as inj  # noqa: E402

PORTS = [9347, 9348, 9349, 9350, 9351, 9352]


def find_targets(only=None):
    """返回 [(port, variant)]，只列开着 CDP 的实例。

    only: 'cn' / 'oversea' / None（全部）。
    变体从主文档 url 推：国际版路径含 WorkBuddyAI，国内版含 WorkBuddy。
    """
    found = []
    for port in PORTS:
        if not inj.cdp_up(port):
            continue
        targets = inj.renderer_targets(port, all_types=True)
        if not targets:
            continue
        page = next((t for t in targets if t.get("type") == "page"), None)
        url = (page or {}).get("url", "")
        variant = "oversea" if "WorkBuddyAI" in url else "cn"
        if only and variant != only:
            continue
        found.append((port, variant))
    return found


def probe(port):
    """读该实例的资料库状态。"""
    info = {"port": port, "iframe": False, "style": False, "cls": None,
            "bodyBg": None, "allowOrigins": None, "iframeUrl": None}
    targets = inj.space_iframe_targets(port)
    if targets:
        info["iframe"] = True
        info["iframeUrl"] = (targets[0].get("url") or "")[:70]
        expr = ("JSON.stringify({has: !!document.getElementById(%s),"
                " cls: document.documentElement.className,"
                " bg: getComputedStyle(document.body).backgroundColor})"
                % json.dumps(inj.SPACE_STYLE_ID))
        try:
            value = inj.cdp_evaluate(targets[0]["webSocketDebuggerUrl"], expr, timeout=10.0)
            parsed = json.loads(value)
            info["style"] = parsed.get("has")
            info["cls"] = parsed.get("cls")
            info["bodyBg"] = parsed.get("bg")
        except Exception as error:
            info["err"] = str(error)[:60]
    # 自愈器是否在主渲染进程里
    pages = [t for t in inj.renderer_targets(port, all_types=True) if t.get("type") == "page"]
    if pages:
        try:
            value = inj.cdp_evaluate(
                pages[0]["webSocketDebuggerUrl"],
                "JSON.stringify(!!window.%s)" % inj.SPACE_SELFHEAL_MARK, timeout=10.0)
            info["selfheal"] = json.loads(value)
            value = inj.cdp_evaluate(
                pages[0]["webSocketDebuggerUrl"],
                "JSON.stringify(window.%s || null)" % inj.SPACE_SELFHEAL_MARK, timeout=10.0)
            state = json.loads(value) or {}
            info["repairs"] = state.get("repairs")
            info["healPort"] = state.get("port")
        except Exception as error:
            info["selfheal"] = None
            info["healErr"] = str(error)[:60]
    return info


def main():
    argv = sys.argv[1:]
    check_only = "--check" in argv
    verify_heal = "--verify-heal" in argv
    only = None
    if "--target" in argv:
        index = argv.index("--target")
        if index + 1 < len(argv):
            only = argv[index + 1]

    print("=" * 62)
    print(" 资料库透明化 · 自检" + ("（只读）" if check_only else ""))
    print("=" * 62)

    instances = find_targets(only)
    if not instances:
        print("[失败] 没找到开着 CDP 端口的 WorkBuddy" +
              ("（--target %s）" % only if only else "") + "。")
        print("       先正常启动 WorkBuddy，或跑一次 inject.py 让它带端口重启。")
        return 3
    print("找到 %d 个实例：%s" % (len(instances), instances))
    print("")

    problems = 0
    fixed = 0
    for port, variant in instances:
        print("--- 端口 %d（%s）---" % (port, variant))
        before = probe(port)
        print("  资料库 iframe 打开 : %s" % ("是" if before["iframe"] else "否（懒加载）"))
        print("  自愈器已装载       : %s" % before.get("selfheal"))
        if before["iframe"]:
            print("  样式是否生效       : %s" % before["style"])
            print("  body 背景          : %s" % before["bodyBg"])
            print("  html class         : %r" % before["cls"])
        if before.get("repairs") is not None:
            print("  已自动补注入次数   : %s" % before["repairs"])

        need_repair = False
        if before.get("selfheal") is not True:
            print("  → 问题：自愈器没装（资料库重载后不会自动补）")
            need_repair = True
            problems += 1
        if before["iframe"] and before["style"] is not True:
            print("  → 问题：样式当前没生效")
            need_repair = True
            problems += 1

        if check_only or not need_repair:
            print("")
            continue

        print("  正在修复 ...")
        heal = inj.install_space_selfheal(port, log=lambda m: print("     " + m.strip()))
        if heal.get("installed") and heal.get("healed"):
            print("  ✅ 自愈器已启用，且首轮补注入成功")
            fixed += 1
        elif heal.get("installed"):
            print("  ⚠️  自愈器装了，但补注入失败 —— 几乎一定是：")
            print("      当前实例启动时**没带** --remote-allow-origins 参数。")
            print("      渲染进程连 CDP 会被 403 拒掉（Chromium 111+ 的 Origin 检查）。")
            print("      解决：关掉这个 WorkBuddy，跑一次 `inject.py --target %s`，" % variant)
            print("            它会带参数重启，之后自愈器就能长期生效。")
        else:
            print("  ❌ 自愈器没装上（拿不到主渲染进程）")
        n = inj.apply_space_css(port, log=lambda m: print("     " + m.strip()))
        if n:
            print("  ✅ 已对当前打开的资料库面板即时注入 %d 个 iframe" % n)
            fixed += 1
        print("")

        if verify_heal:
            print("  --- 自愈能力验证：强制 reload 资料库 iframe ---")
            targets = inj.space_iframe_targets(port)
            if not targets:
                print("     资料库没打开，跳过")
                print("")
                continue
            ws = targets[0]["webSocketDebuggerUrl"]
            try:
                inj.cdp_evaluate(ws, "location.reload()", timeout=5.0)
                print("     已触发 reload")
            except Exception:
                pass
            healed_ok = False
            for wait in (2, 4, 7, 11, 16):
                time.sleep(1 if wait == 2 else (2 if wait == 4 else 3))
                fresh = inj.space_iframe_targets(port)
                if not fresh:
                    continue
                try:
                    value = inj.cdp_evaluate(
                        fresh[0]["webSocketDebuggerUrl"],
                        "JSON.stringify({has: !!document.getElementById(%s), age: Math.round(performance.now())})"
                        % json.dumps(inj.SPACE_STYLE_ID), timeout=8.0)
                    state = json.loads(value)
                except Exception:
                    continue
                print("     [%2ds] 样式=%s 文档年龄=%sms" % (wait, state.get("has"), state.get("age")))
                if state.get("has"):
                    healed_ok = True
                    break
            if healed_ok:
                print("  ✅ 自愈验证通过：reload 后样式自动回来了")
            else:
                print("  ❌ 自愈验证失败：reload 后样式没回来")
                problems += 1
            print("")

    print("=" * 62)
    if problems == 0:
        print("结论：一切正常 ✅")
        return 0
    if fixed:
        print("结论：发现 %d 个问题，已修复 %d 个 ⚠️" % (problems, fixed))
        return 1
    print("结论：发现 %d 个问题，未能修复 ❌" % problems)
    return 2


if __name__ == "__main__":
    sys.exit(main())
