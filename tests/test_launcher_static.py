# -*- coding: utf-8 -*-
"""启动器（inject_launcher.py + 启动美化.bat）的静态与逻辑回归测试。

分两块：
  [1] .bat 静态检查 —— 不执行 bat（沙箱里 cmd 不可靠），只查文本层面的风险：
      编码必须是 GBK 而不是 UTF-8；不能出现真的 chcp 命令；goto 标签配平；
      挑解释器的顺序必须是"系统 Python 在前、托管 Python 在后"。
  [2] inject_launcher 的契约 —— 确认它**复用** inject.py 而不是自己重写一套，
      以及"自动关闭"的三个关键开关都存在。

用法：python tests/test_launcher_static.py
"""
import importlib.util
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
GUI = os.path.join(SKILL, "scripts", "inject_launcher.py")
BAT_GEN = os.path.join(SKILL, "scripts", "_make_launcher_bat.py")

fails = []


def check(name, cond, extra=""):
    print("  %s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                         ("  -> %s" % (extra,)) if extra != "" else ""))
    if not cond:
        fails.append(name)


# ============================================================================
# [1] .bat 静态检查
# ============================================================================
print("=" * 70)
print(" [1] 启动美化.bat 静态检查")
print("=" * 70)

check("GUI 脚本存在", os.path.isfile(GUI), GUI)
check("bat 生成器存在", os.path.isfile(BAT_GEN), BAT_GEN)

# 先从生成器里把两个变体渲染出来（不写盘），这样测的是"将要生成的"内容
sys.path.insert(0, os.path.dirname(BAT_GEN))
spec = importlib.util.spec_from_file_location("bat_gen", BAT_GEN)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

for name, template in gen.VARIANTS:
    print("")
    print("-- %s --" % name)
    raw = gen.render(template)
    text = raw.decode("gbk")

    check("%s：能按 GBK 解码（cmd 默认代码页 936）" % name, True)
    check("%s：不是 UTF-8 BOM" % name, raw[:3] != b"\xef\xbb\xbf")
    bare_lf = raw.count(b"\n") - raw.count(b"\r\n")
    check("%s：行尾统一 CRLF（无裸 LF）" % name, bare_lf == 0, bare_lf)

    # chcp 只能出现在注释里
    chcp_lines = [(i, line) for i, line in enumerate(text.split("\r\n"), 1)
                  if "chcp" in line.lower()]
    real_chcp = [(i, line) for i, line in chcp_lines
                 if not line.strip().lower().startswith("rem")]
    check("%s：没有真正的 chcp 命令" % name, not real_chcp, real_chcp)

    # goto 标签配平
    labels = set(re.findall(r"^:([A-Za-z_][\w-]*)", text, re.M))
    gotos = set(re.findall(r"goto :([A-Za-z_][\w-]*)", text))
    missing = gotos - labels
    check("%s：goto 标签全部有定义" % name, not missing, sorted(missing))
    check("%s：有失败提示分支" % name,
          "no_python" in labels and "no_gui" in labels, sorted(labels))

    # GUI 绝对路径必须内嵌（bat 在桌面，py 在技能目录，不能靠 %~dp0）
    # ⚠️ 文本是 CRLF 的，`$` 在多行模式里匹配不到带 \r 的行尾 —— 用 strip 逐行找
    gui_value = ""
    for line in text.split("\r\n"):
        if line.strip().startswith('set "GUI='):
            gui_value = line.strip()[len('set "GUI='):-1]
            break
    check("%s：内嵌了 GUI 的绝对路径" % name, bool(gui_value), gui_value)
    if gui_value:
        check("%s：内嵌路径就是 inject_launcher.py" % name,
              os.path.normcase(gui_value) == os.path.normcase(GUI),
              gui_value)
        check("%s：没有用 %%~dp0 定位 GUI（bat 和 py 不同目录）" % name,
              "%~dp0" not in gui_value, gui_value)

    # 挑解释器的顺序：系统 Python 必须排在托管 Python 之前
    # （托管版实测没有 tkinter，先挑它必然失败）
    text_lower = text.lower()
    sys_pos = text_lower.find("%localappdata%\\programs\\python")
    managed_pos = text_lower.find("binaries\\python")
    check("%s：系统 Python 候选在托管 Python 之前" % name,
          0 <= sys_pos < managed_pos or managed_pos < 0,
          "system=%s managed=%s" % (sys_pos, managed_pos))
    check("%s：用 import tkinter 做能力探测" % name,
          'import tkinter' in text)
    check("%s：优先 pythonw.exe（不弹黑窗口）" % name,
          "pythonw.exe" in text)

    # 必须有 `if not defined PY` 守卫，否则会挑到最后一个能跑的
    guard_count = text.count("if not defined PY")
    check("%s：候选之间有 if not defined PY 守卫" % name, guard_count >= 2,
          guard_count)

    # 调试变体要保留控制台
    if "调试" in name:
        check("%s：调试变体保留控制台（能看到 traceback）" % name,
              "pause >nul" in text and 'start ""' not in text)
    else:
        check("%s：正常变体用 start 静默拉起" % name, 'start ""' in text)


# ============================================================================
# [2] inject_launcher 的契约
# ============================================================================
print("")
print("=" * 70)
print(" [2] inject_launcher 契约")
print("=" * 70)

source = io.open(GUI, encoding="utf-8").read()

# 必须复用 inject.py，不能自己重写一套进程/端口逻辑
for token, why in [
    ("importlib.util.spec_from_file_location", "用 importlib 按路径加载 inject.py"),
    ("discover_installs", "复用 inject.py 的安装发现"),
    ("read_variant", "复用 inject.py 的变体识别"),
    ("find_live_port", "复用 inject.py 的端口归属判定"),
    ("cdp_up", "复用 inject.py 的 CDP 探活"),
    ("launch_with_cdp", "复用 inject.py 的带参启动"),
    ("force_quit", "复用 inject.py 的强制退出"),
    ("pick_port", "复用 inject.py 的端口挑选"),
]:
    check("inject_launcher 引用了 %s" % why, token in source, token)

# 不能自己写 tasklist / wmic / PowerShell（技能里明确约定走 ctypes）
for banned in ("tasklist", "wmic", "powershell.exe"):
    check("inject_launcher 没用 %s" % banned, banned not in source.lower())

# 三个关键开关
check("有「启动后自动注入」开关", "injectAfterLaunch" in source)
check("有「成功后自动关闭」开关", "closeOnSuccess" in source)
check("默认开启成功后自动关闭",
      re.search(r'"closeOnSuccess":\s*True', source) is not None)
# 「启动成功后自动关闭」必须真的走到 root.destroy。
# ⚠️ 断言写 `root.destroy()` 会失败：代码里是通过 `root.after(1500, root.destroy)`
# 传函数对象（不能写括号，否则立刻执行）。所以只查符号本身。
check("成功路径里真的会关窗（root.destroy 被调用）",
      re.search(r"def finish_ok[\s\S]{0,900}?root\.destroy", source)
      is not None)
check("自动关闭是延迟执行（让用户看到结果）",
      re.search(r"def finish_ok[\s\S]{0,900}?root\.after\(\s*\d+,\s*root\.destroy",
                source) is not None)
check("关窗有 WM_DELETE_WINDOW 协议（存位置）",
      "WM_DELETE_WINDOW" in source)
check("用 <Configure> 兜底存窗口位置（不只靠关窗事件）",
      'bind("<Configure>"' in source)

# 判定"启动成功"必须是 CDP 通了，不是"Popen 没报错"
check("成功判据是 CDP 端口就绪",
      re.search(r"ready_port is None[\s\S]{0,200}return \{\"ok\": False", source)
      is not None)
check("有等待超时上限", "WAIT_TIMEOUT" in source)
check("超时时间与 inject.py 的 90 秒一致",
      re.search(r"WAIT_TIMEOUT\s*=\s*90\.0", source) is not None)

# 线程模型：Tk 只能在主线程操作
check("启动放在后台线程（不卡界面）", "threading.Thread" in source)
check("界面更新通过 root.after 回主线程",
      "root.after(0," in source or "root.after(" in source)
check("日志控件为 daemon 线程",
      re.search(r"threading\.Thread\([\s\S]{0,200}?daemon=True", source)
      is not None)

# 配置持久化
check("配置写在 LOCALAPPDATA 下的 WorkBuddyAmbientSkin",
      "WorkBuddyAmbientSkin" in source)
check("配置写入是原子的（临时文件 + os.replace）",
      "os.replace" in source and "mkstemp" in source)
check("配置损坏时回退默认值而不崩",
      re.search(r"def read_config[\s\S]{0,1500}?except Exception:\s*\n\s*return data",
                source) is not None)
check("支持用环境变量覆盖配置路径（自检用）",
      "WORKBUDDY_AMBIENT_LAUNCHER_CONFIG" in source)

# 中文字体坑：日志区不能直接用 Consolas
check("有中文字体解析函数（Consolas 无中文字形）",
      "resolve_mono_font" in source)
check("日志区用的是解析后的字体而不是 FONT_MONO",
      re.search(r"tk\.Text\([\s\S]{0,400}?font=resolve_mono_font", source)
      is not None)

# 路径输入框：自动探测要回填，但"手工/自动"要按 touched 判断
check("自动探测的路径会回填进输入框", "def refresh_paths" in source)
check("「手工指定」按 touched 判断而不是看框里有没有字",
      re.search(r"manual = variant in touched", source) is not None)
check("只在用户真敲了字才落盘", "KeyRelease" in source)

# 没 tkinter 时要给出人话提示（而不是 traceback）
check("缺 tkinter 时有可读的提示 + 原生弹窗",
      "import tkinter  # noqa" in source and "MessageBoxW" in source)

print("")
print("=" * 70)
if fails:
    print("失败 %d 项：" % len(fails))
    for item in fails:
        print("  - %s" % item)
    raise SystemExit(1)
print("启动器静态检查全部通过 ✅")
