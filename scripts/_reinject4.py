# -*- coding: utf-8 -*-
"""第四轮：把「完全透明」版本的 space-glass.css 重新注入到运行中的实例。

只做即时注入 + 顺带重装自愈器（这样下次面板 reload 也拿到新 CSS）。
不重启 WorkBuddy、不动壁纸。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject  # noqa: E402


def main():
    ports = [int(x) for x in (sys.argv[1:] or ["9348"])]
    css = inject.read_space_css()

    print("=" * 62)
    print("第四轮重注入 —— 红框 4 处改为完全透明")
    print("CSS 版本长度: %d 字节" % len(css))
    # 断言新写法确实在
    for token in (
        "html.wbas-space .workspace-tree-section-header-pc {\n  background: transparent",
        "html.wbas-space .memory-nav-item-pc,\nhtml.wbas-space .workspace-tree-space-node-pc",
    ):
        print("  校验 token: %s -> %s" % (token.split("\n")[0][:58], token in css))
    print("=" * 62)

    for port in ports:
        print("\n--- 端口 %d ---" % port)
        ok = inject.apply_space_css(port, log=lambda m: print(m))
        print("  即时注入: %s" % ("成功 ✅" if ok else "未生效（面板可能没打开）"))
        if ok:
            heal = inject.install_space_selfheal(port, log=lambda m: print(m))
            print("  自愈器: installed=%s healed=%s port=%s"
                  % (heal.get("installed"), heal.get("healed"), heal.get("port")))


if __name__ == "__main__":
    main()
