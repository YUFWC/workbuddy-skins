# -*- coding: utf-8 -*-
"""把更新后的 ambient.css 重新注入到**正在运行**的实例（不重启、不丢壁纸）。

用途：改了 ambient.css 想立刻看效果，又不想关掉当前对话窗口。

⚠️ 这种方式只在**本次运行**有效 —— 重启后会由正常的注入流程重新写入
（因为 ambient.css 是文件，下次启动读到的就是新版）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402


def main():
    ports = [int(x) for x in (sys.argv[1:] or ["9348"])]
    css = inject.read_css()
    print("ambient.css: %d 字节" % len(css))
    for token in (".discover-panel-page", ".dc-playbook-card", ".dc-category-tab"):
        print("  含 %-22s -> %s" % (token, token in css))

    themes = inject.list_themes([inject.BUNDLED_THEMES_ROOT, inject.user_themes_root()])
    print("主题 %d 个" % len(themes))

    for port in ports:
        print("\n--- 端口 %d ---" % port)
        targets = inject.renderer_targets(port)
        print("  renderer target: %d 个" % len(targets))
        if not targets:
            print("  没有可注入的渲染 target")
            continue
        try:
            expr = inject.build_install_expression(css, themes, active_id="")
        except Exception as error:
            print("  构建表达式失败: %s" % error)
            continue
        ok = 0
        for t in targets:
            try:
                v = inject.cdp_evaluate(t["webSocketDebuggerUrl"], expr, timeout=120.0)
                if v:
                    ok += 1
            except Exception as error:
                print("  注入失败 (%s): %s" % ((t.get("url") or "")[:40], error))
        print("  注入成功: %d/%d" % (ok, len(targets)))


if __name__ == "__main__":
    main()
