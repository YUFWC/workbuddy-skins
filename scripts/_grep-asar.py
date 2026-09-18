# -*- coding: utf-8 -*-
"""在国际版 app.asar 的渲染代码里搜关键词，找出某个页面用的类名/路由名。

用法：python scripts/_grep-asar.py 我的文件 [关键词2 ...]
"""
import importlib.util
import json
import os
import re
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
spec = importlib.util.spec_from_file_location("cmp", os.path.join(SKILL, "scripts", "_compare-installs.py"))
cmp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmp)


def main():
    words = sys.argv[1:] or ["我的文件"]
    build = cmp.Build("OS", cmp.OS)
    print("扫描 %d 个条目，找关键词: %s" % (len(build.entries), words))
    for word in words:
        print()
        print("=" * 78)
        print("关键词:", word)
        print("=" * 78)
        found = 0
        for path in sorted(build.entries):
            if not path.lower().endswith((".js", ".css", ".html")):
                continue
            if build.entries[path][1] > 8 * 1024 * 1024:
                continue
            text = build.read(path).decode("utf-8", "replace")
            if word not in text:
                continue
            found += 1
            if found > 6:
                continue
            print("\n--- %s ---" % path)
            for match in list(re.finditer(re.escape(word), text))[:3]:
                start = max(0, match.start() - 130)
                snippet = text[start:match.end() + 130].replace("\n", " ")
                print("    ...%s..." % snippet)
            # 顺手把附近的类名捞出来
            classes = set()
            for match in list(re.finditer(re.escape(word), text))[:5]:
                window = text[max(0, match.start() - 1500):match.end() + 1500]
                for cls in re.findall(r'["\']([a-z][a-z0-9]*(?:-[a-z0-9]+){1,4})["\']', window):
                    classes.add(cls)
            interesting = sorted(c for c in classes if any(k in c for k in ("page", "workspace", "file", "disk", "cloud", "drive", "space", "netdisk")))
            if interesting:
                print("    附近可能的类名:", interesting[:20])
        print("\n  命中文件数:", found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
