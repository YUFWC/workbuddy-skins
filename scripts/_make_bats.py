# -*- coding: utf-8 -*-
"""从 scripts/一键美化.bat.src 生成三个一键脚本（GBK + CRLF）。

- 一键美化.bat        自动（只跑了一个就用那个；两个都在跑优先国际版）
- 一键美化-国内版.bat  --target cn
- 一键美化-国际版.bat  --target oversea

本机控制台代码页是 936，UTF-8 的中文 .bat 会乱码，所以必须转 GBK。

模板里用的是 {SCRIPT} / {DOCTOR} / {PY_CHAIN} / {PY_LIST_ECHO} 占位符，
**在生成时**替换成本机实际路径。这样仓库里的模板不含任何开发者目录，
而生成出来的 .bat 又拿到正确的绝对路径。

用法：python scripts/_make_bats.py [输出目录...]
不传目录时默认写到桌面和技能所在目录的上一级。
"""
import os
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(SKILL, "scripts", "一键美化.bat.src")

VARIANTS = [
    ("一键美化.bat", None, "自动（本机装了多个版本时优先国际版）"),
    ("一键美化-国内版.bat", "cn", "国内版 WorkBuddy（--target cn）"),
    ("一键美化-国际版.bat", "oversea", "国际版 WorkBuddy AI（--target oversea）"),
]


def default_dirs():
    """默认输出目录：桌面 + 技能所在目录的上一级。

    不写死某个工作区路径 —— 换台机器、换个解压位置都能用。
    """
    out = []
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        out.append(desktop)
    out.append(os.path.dirname(SKILL))
    return out


def python_candidates():
    """按优先级列出本机可能可用的 Python 解释器（绝对路径）。

    为什么在生成时探测而不是写进模板：解释器位置因机器而异，模板里写死
    既不可移植也会泄露开发者目录。这里只把**实际存在**的路径交给 bat，
    避免生成一堆必然失败的 if exist 分支。

    托管运行时（~/.workbuddy-ai/binaries/python）排在后面 —— 本机实测它是
    精简版、**不带 tkinter**，GUI 用不了，只能当兜底。
    """
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    candidates = []

    # 1) 用户自己装的完整版 Python（带 tkinter，首选）
    for version in ("313", "312", "311", "310"):
        candidates.append(os.path.join(local, "Programs", "Python",
                                       "Python" + version, "python.exe"))
    for version in ("313", "312", "311", "310"):
        candidates.append(r"C:\Python%s\python.exe" % version)

    # 2) WorkBuddy 托管运行时（兜底）
    managed = os.path.join(home, ".workbuddy-ai", "binaries", "python")
    candidates.append(os.path.join(managed, "envs", "default", "Scripts",
                                   "python.exe"))
    versions_dir = os.path.join(managed, "versions")
    if os.path.isdir(versions_dir):
        for name in sorted(os.listdir(versions_dir), reverse=True):
            candidates.append(os.path.join(versions_dir, name, "python.exe"))

    # 去重 + 只保留真实存在的
    seen = set()
    out = []
    for path in candidates:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen or not os.path.isfile(path):
            continue
        seen.add(key)
        out.append(path)
    return out


def build_py_chain(candidates):
    """把候选解释器展开成平铺的 set / goto 序列。

    ⚠️ 不能写成 for 循环：批处理块内的 %VAR% 在解析整个块时就展开，
    `set "PY=%%~C"` 之后再用 %PY% 拿到的还是旧值。平铺最稳。
    """
    lines = []
    for path in candidates:
        lines.append('set "PY=%s"' % path)
        lines.append('if exist "%PY%" goto :try_py')
    lines.append('set "PY="')
    lines.append("goto :no_py")
    return "\n".join(lines)


def build_py_list_echo(candidates):
    return "\n".join("echo          %s" % path for path in candidates)


def fill_placeholder(text, key, value):
    """替换占位符，但**跳过 rem 注释行**。

    两个坑都得绕开：
      * 不能无条件 str.replace —— 注释里只要提到占位符名字就会被一起替换。
        实测踩过：rem 里写了 {PY_CHAIN}，结果候选列表被展开两遍，
        生成出一个重复挑 Python 的 bat。
      * 也不能只匹配「整行就是占位符」—— {SCRIPT} 是嵌在
        `set "SCRIPT={SCRIPT}"` 中间的，那样会漏掉不替换。
    所以规则是：注释行原样保留，其余行正常替换。
    """
    out = []
    for line in text.split("\n"):
        stripped = line.lstrip().lower()
        if stripped == "rem" or stripped.startswith("rem "):
            out.append(line)
            continue
        out.append(line.replace(key, value))
    return "\n".join(out)


def render(template, target, label, candidates):
    text = template.replace(
        'set "TARGET_LABEL=自动（本机装了多个版本时优先国际版）"',
        'set "TARGET_LABEL=%s"' % label)
    text = text.replace(
        'set "TARGET_ARG=%*"',
        'set "TARGET_ARG=--target %s"' % target if target else 'set "TARGET_ARG=%*"')
    text = fill_placeholder(text, "{SCRIPT}",
                            os.path.join(SKILL, "scripts", "inject.py"))
    text = fill_placeholder(text, "{DOCTOR}",
                            os.path.join(SKILL, "scripts", "space_doctor.py"))
    text = fill_placeholder(text, "{PY_CHAIN}", build_py_chain(candidates))
    text = fill_placeholder(text, "{PY_LIST_ECHO}",
                            build_py_list_echo(candidates))
    # 统一成 CRLF 后转 GBK
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk")


def main():
    template = open(SRC, encoding="utf-8").read()
    dirs = sys.argv[1:] or default_dirs()

    candidates = python_candidates()
    if not candidates:
        print("[警告] 本机没找到任何 Python 解释器，生成的 bat 会走到 :no_py 分支")
    else:
        print("将写入 bat 的 Python 候选（%d 个）：" % len(candidates))
        for path in candidates:
            print("  " + path)
    print("")

    written = 0
    for name, target, label in VARIANTS:
        data = render(template, target, label, candidates)
        for directory in dirs:
            if not os.path.isdir(directory):
                print("跳过（目录不存在）：%s" % directory)
                continue
            path = os.path.join(directory, name)
            try:
                with open(path, "wb") as handle:
                    handle.write(data)
            except PermissionError as error:
                print("[失败] 写不进去 %s：%s" % (path, error))
                continue
            print("写入 %-52s %d 字节" % (path, len(data)))
            written += 1
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
