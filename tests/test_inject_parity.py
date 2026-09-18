# -*- coding: utf-8 -*-
"""inject.py 与 Node 原生实现的一致性回归测试。

inject.py 把 renderer.mjs 里的 installInRenderer 源码文本原样搬到渲染进程执行，
主题表也按 theme.mjs 的规则重新实现了一遍。只要上游改了一处，
Python 侧就可能悄悄跑偏（最坏情况：主题被判无效而跳过 → 壁纸回退成 paper-aurora）。
所以这里用 Node 原生实现产出基准，逐字段 + 逐字节比对。

用法：python tests/test_inject_parity.py
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
INJECT = os.path.join(SKILL, "scripts", "inject.py")
PARITY_MJS = os.path.join(SKILL, "scripts", "_parity.mjs")

spec = importlib.util.spec_from_file_location("inj", INJECT)
inj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inj)

failures = []


def check(label, left, right):
    if left == right:
        print("  [OK]   %s" % label)
    else:
        print("  [差异] %s\n         node=%r\n         py  =%r" % (label, left, right))
        failures.append(label)


def main():
    node = os.environ.get("WORKBUDDY_NODE") or "node"
    tmp = tempfile.mkdtemp(prefix="wbas-parity-")
    baseline = os.path.join(tmp, "node.json")

    print("=" * 70)
    print(" inject.py 与 Node 原生实现的一致性校验")
    print("=" * 70)

    result = subprocess.run([node, PARITY_MJS, baseline], cwd=SKILL,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("node:", (result.stdout or "").strip() or (result.stderr or "").strip()[-300:])
    if result.returncode != 0 or not os.path.exists(baseline):
        print("[失败] 无法生成 Node 基准数据（node 是否可用？）")
        return 1
    reference = json.load(open(baseline, encoding="utf-8"))

    css = inj.read_css()
    print("\nCSS")
    check("cssLength", reference["cssLength"], len(css))
    check("cssSha256", reference["cssSha256"], hashlib.sha256(css.encode("utf-8")).hexdigest())

    print("\ninstallInRenderer 源码切片")
    fn_source = inj.extract_install_source()
    check("fnSourceSha256", reference["fnSourceSha256"],
          hashlib.sha256(fn_source.encode("utf-8")).hexdigest())

    themes = inj.list_themes([inj.BUNDLED_THEMES_ROOT, reference["userThemesRoot"]])
    entries = inj.theme_entries(themes)

    print("\n完整注入表达式（逐字节）")
    expression = inj.build_install_expression(css, themes, active_id="")
    check("expressionSha256", reference["expressionSha256"],
          hashlib.sha256(expression.encode("utf-8")).hexdigest())

    print("\n主题表")
    check("count", reference["count"], len(entries))
    check("顺序", [t["id"] for t in reference["themes"]], [t["id"] for t in entries])
    by_node = {t["id"]: t for t in reference["themes"]}
    by_py = {t["id"]: t for t in entries}
    check("id 集合", sorted(by_node), sorted(by_py))
    for theme_id in sorted(set(by_node) & set(by_py)):
        left, right = by_node[theme_id], by_py[theme_id]
        for key in sorted(set(left) | set(right)):
            check("%s.%s" % (theme_id, key), left.get(key), right.get(key))

    print("\n" + "=" * 70)
    if failures:
        print("失败 %d 项：%s" % (len(failures), failures))
        return 1
    print("全部一致 ✅ Python 端口与 Node 原生实现输出完全相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
