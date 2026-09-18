# -*- coding: utf-8 -*-
"""国内版 5.5.6 vs 国际版 5.5.2 的严谨比对（自动探测 asar 数据段偏移）。

⚠️ 关键坑：asar 的 `data_start = 16 + uint32[12:16]` 这条常规公式，
**国际版那份会少 3 个字节**（它的 jsonSize 字段比真实值小 3），
读出来的内容整体错位 3 字节。错位后：
  * sha256 比对会全错（0.1% 相同 —— 假象）
  * 但**字符串搜索仍然能命中**（大部分字节是连续的），所以只看关键词统计会被骗
这里改成"拿一个已知文件试读，找出让它内容正常的 delta"。
"""
import hashlib
import json
import os
import struct
import sys

def _find_install(product):
    """定位某个产品的安装目录。

    从 %LOCALAPPDATA% 推出「Programs\\<product>」的相对尾部，再套到各个
    已存在的盘符上 —— 因为应用可能装在非系统盘（本机实测就在 D 盘）。
    不写死盘符和用户名，换台机器也能用。
    """
    local = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    drive, tail = os.path.splitdrive(os.path.abspath(local))
    tail = tail.lstrip("\\/")
    for letter in ("CDEFGHIJK"):
        root = "%s:\\" % letter
        if not os.path.isdir(root):
            continue
        candidate = os.path.join(root, tail, "Programs", product)
        if os.path.isdir(candidate):
            return candidate
    return ""


CN = _find_install("WorkBuddy")
OS = _find_install("WorkBuddyAI")

PROBES = [
    "conversation-shell", "cr-input-container", "cr-input-toolbar__right", "cr-input-toolbar",
    "wb-home-route", "teams-main-content", "teams-content-wrapper", "teams-container",
    "data-view-id", "data-application-name", "gridViewItem", "conversation-section-label",
    "conversation-list-tab-button", "conversation-agent-card", "conversation-list-invite-friends",
    "cr-code-like-box", "cr-self-bubble", "cr-message-list", "artifact-slot-panel",
    "cr-add-menu", "cr-permission-setting", "cr-model-selector", "cr-send-button",
    "cr-context-usage", "quick-actions__item", "wb-scene-tabs", "collapsible-section-header",
    "cr-collapse", "workbuddy-topbar", "workbuddy-menubar-container", "cr-input-editor-host",
    "monaco-editor", "cr-document", "cr-text-block", "cr-table-block", "cr-inline-code",
    "cr-frame", "cr-agent",
]


class Build:
    def __init__(self, label, base):
        self.label = label
        self.asar = os.path.join(base, "resources", "app.asar")
        with open(self.asar, "rb") as handle:
            head = handle.read(16)
            json_size = struct.unpack("<I", head[12:16])[0]
            handle.seek(16)
            self.meta = json.loads(handle.read(json_size).decode("utf-8"))
        self.nominal = 16 + json_size
        self.entries = dict(self._walk(self.meta))
        self.delta = self._detect_delta()
        self.data_start = self.nominal + self.delta

    @staticmethod
    def _walk(node, prefix=""):
        for name, item in (node.get("files") or {}).items():
            path = prefix + "/" + name
            if "files" in item:
                yield from Build._walk(item, path)
            elif "size" in item and "offset" in item:
                yield path, (int(item["offset"]), int(item["size"]))

    def _detect_delta(self):
        """用 /renderer/index.html 当试纸：找出让内容以 <!DOCTYPE 开头的 delta。"""
        entry = self.entries.get("/renderer/index.html")
        if not entry:
            return 0
        offset = entry[0]
        with open(self.asar, "rb") as handle:
            for delta in range(0, 17):
                handle.seek(self.nominal + offset + delta)
                if handle.read(9) == b"<!DOCTYPE":
                    return delta
        return 0

    def read(self, path, limit=None):
        offset, size = self.entries[path]
        with open(self.asar, "rb") as handle:
            handle.seek(self.data_start + offset)
            return handle.read(size if limit is None else min(size, limit))


def main():
    cn, os_ = Build("CN 国内 5.5.6", CN), Build("OS 国际 5.5.2", OS)
    print("=" * 80)
    print("asar 数据段偏移自检")
    print("=" * 80)
    for b in (cn, os_):
        print("  %-16s 公式算出 %d，实测需 +%d → %d  %s"
              % (b.label, b.nominal, b.delta, b.data_start,
                 "（公式有偏差！）" if b.delta else "（公式正确）"))

    print()
    print("=" * 80)
    print("① 内容重合度（sha256，跳过 >4MB 的巨型文件）")
    print("=" * 80)
    common = set(cn.entries) & set(os_.entries)
    same = diff = skipped = 0
    diffs = []
    for path in sorted(common):
        if cn.entries[path][1] > 4 * 1024 * 1024:
            skipped += 1
            continue
        a, b = cn.read(path), os_.read(path)
        if hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest():
            same += 1
        else:
            diff += 1
            diffs.append(path)
    checked = same + diff
    print("  共有条目 %d，比对 %d（跳过超大 %d）" % (len(common), checked, skipped))
    print("  内容完全相同 %d（%.1f%%） / 不同 %d" % (same, 100.0 * same / max(1, checked), diff))

    print()
    print("=" * 80)
    print("② 差异到底有多大？逐字节看前几个不同的文件")
    print("=" * 80)
    shown = 0
    for path in diffs:
        if shown >= 6:
            break
        if not path.lower().endswith((".js", ".css", ".html", ".json")):
            continue
        a, b = cn.read(path), os.read(path) if False else os_.read(path)
        n = min(len(a), len(b))
        first = next((i for i in range(n) if a[i] != b[i]), None)
        if first is None:
            print("  %-58s 仅长度不同 %d vs %d" % (path[:58], len(a), len(b)))
            shown += 1
            continue
        print("  %s" % path)
        print("     大小 %d vs %d   首个差异字节 @%d" % (len(a), len(b), first))
        print("     国内: %r" % a[max(0, first - 50):first + 60])
        print("     国际: %r" % b[max(0, first - 50):first + 60])
        shown += 1

    print()
    print("=" * 80)
    print("③ 选择器存在性（正确偏移下重算）")
    print("=" * 80)

    def collect(build):
        parts = []
        for path in build.entries:
            if "/renderer/" not in path or not path.lower().endswith((".js", ".css", ".html")):
                continue
            if build.entries[path][1] > 24 * 1024 * 1024:
                continue
            parts.append(build.read(path).decode("utf-8", "replace"))
        return "\n".join(parts)

    tcn, tos = collect(cn), collect(os_)
    print("  国内渲染代码 %.1f MB / 国际 %.1f MB" % (len(tcn) / 1048576, len(tos) / 1048576))
    print()
    print("  %-34s %10s %10s  %s" % ("探针", "国内", "国际", "结论"))
    print("  " + "-" * 74)
    bad = []
    for probe in PROBES:
        a, b = tcn.count(probe), tos.count(probe)
        if a > 0 and b > 0:
            verdict = "两边都有"
        elif a == 0 and b == 0:
            verdict = "两边都没有"
        else:
            verdict = "**只有一边**"
            bad.append(probe)
        print("  %-34s %10d %10d  %s" % (probe, a, b, verdict))
    print()
    print("  " + ("⚠️ 只有一边有的探针: %s" % bad if bad
                  else "✅ 全部 %d 个探针两侧都有 —— 我们的 CSS 选择器两边都命中" % len(PROBES)))

    print()
    print("=" * 80)
    print("④ /renderer/index.html 逐字节")
    print("=" * 80)
    a, b = cn.read("/renderer/index.html"), os_.read("/renderer/index.html")
    print("  大小 %d vs %d   sha 相同: %s" % (len(a), len(b), hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
